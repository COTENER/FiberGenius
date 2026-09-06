"""Actualiza mediciones ONMSI usando el modelo relacional de Django."""

import logging
import time
from defusedxml import ElementTree as ET
from datetime import datetime, timezone

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from requests.auth import HTTPBasicAuth

from mapas.models import IDRuta, Medicion

logger = logging.getLogger('mapas')


def _tls_verify():
    return getattr(settings, 'ONMSI_CA_BUNDLE', '') or getattr(settings, 'ONMSI_TLS_VERIFY', True)


def _entero(value):
    try:
        return int(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _decimal(value):
    try:
        return float(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _booleano(value):
    if value in (None, ''):
        return None
    return str(value).strip().lower() in {'true', '1', 'yes', 'si', 'sí'}


def _fecha(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def obtener_datos_api(internal_key):
    base_url = str(settings.MEDICIONES_API_URL).rstrip('/')
    if not base_url:
        raise CommandError('MEDICIONES_API_URL no está configurada.')
    response = requests.get(
        f'{base_url}/{internal_key}/measurements',
        auth=HTTPBasicAuth(settings.MEDICIONES_API_USER, settings.MEDICIONES_API_PASSWORD),
        headers={'User-Agent': 'FiberGenius/RC2'},
        timeout=30,
        verify=_tls_verify(),
    )
    response.raise_for_status()
    return response.content


def parsear_xml(xml_data, node, expected_link_key):
    root = ET.fromstring(xml_data.decode('utf-8'))

    def find_text(element, tag):
        return (
            element.findtext(f'ns:{tag}', namespaces={'ns': 'http://api.topaz.com'})
            or element.findtext(tag)
        )

    items = root.findall('.//measurementOnLink')
    if not items:
        items = root.findall('.//ns:measurementOnLink', {'ns': 'http://api.topaz.com'})

    mediciones = []
    for medicion in items:
        otdr = medicion.find('measurementOnLinkOtdrResult')
        if otdr is None:
            otdr = medicion.find(
                'ns:measurementOnLinkOtdrResult',
                {'ns': 'http://api.topaz.com'},
            )

        link_key = _entero(find_text(medicion, 'linkInternalKey')) or int(expected_link_key)
        acquisition_date = _fecha(find_text(medicion, 'acquisitionDate'))
        measurement_uid = find_text(medicion, 'measurementUid')
        if not measurement_uid:
            sufijo = acquisition_date.strftime('%Y%m%d%H%M%S') if acquisition_date else 'sin-fecha'
            measurement_uid = f'{link_key}-{sufijo}'

        def otdr_text(tag):
            return find_text(otdr, tag) if otdr is not None else None

        mediciones.append({
            'node': node,
            'internal_key': _entero(find_text(medicion, 'internalKey')) or int(expected_link_key),
            'measurement_uid': measurement_uid,
            'link_internal_key': link_key,
            'otu_internal_key': _entero(find_text(medicion, 'otuInternalKey')),
            'acquisition_date': acquisition_date,
            'result': find_text(medicion, 'result'),
            'fiber_length': _decimal(otdr_text('fiberLengthM')),
            'wavelength': _entero(otdr_text('wavelengthNm')),
            'link_loss_alarm': _booleano(otdr_text('linkLossAlarm')),
            'link_loss_value': _decimal(otdr_text('linkLossdB')),
            'linear_att_alarm': _booleano(otdr_text('linearAttAlarm')),
            'linear_att_value': _decimal(otdr_text('linearAttdBkm')),
            'orl_alarm': _booleano(otdr_text('orlAlarm')),
            'orl_value': _decimal(otdr_text('orldB')),
            'events_alarm': _booleano(otdr_text('eventsAlarm')),
            'splitter_detected': _booleano(otdr_text('splitterDetected')),
            'fiber_end_detected': _booleano(otdr_text('fiberEndDetected')),
        })
    return mediciones


@transaction.atomic
def guardar_en_db_sobrescribiendo_por_enlace(mediciones):
    for datos in mediciones:
        link_key = datos['link_internal_key']
        existente = Medicion.objects.filter(link_internal_key=link_key).order_by('-id').first()
        if existente:
            for campo, valor in datos.items():
                setattr(existente, campo, valor)
            existente.save(update_fields=list(datos))
        else:
            Medicion.objects.create(**datos)
    return bool(mediciones)


def procesar_enlace(node, link_key):
    try:
        mediciones = parsear_xml(obtener_datos_api(link_key), node, link_key)
        logger.info('Encontradas %s mediciones para %s.', len(mediciones), node)
        return guardar_en_db_sobrescribiendo_por_enlace(mediciones)
    except (requests.RequestException, ET.ParseError, ValueError) as exc:
        logger.error('Error procesando %s (%s): %s', node, link_key, exc)
        return False


class Command(BaseCommand):
    help = 'Actualiza mon_otdr_mediciones desde ONMSI usando IDs cargados en IDRuta.'

    def add_arguments(self, parser):
        parser.add_argument('--ruta', type=str, help='Nombre exacto de la ruta a procesar')

    def handle(self, *args, **options):
        if Medicion._meta.db_table not in transaction.get_connection().introspection.table_names():
            raise CommandError('Falta la tabla OTDR. Ejecute primero manage.py migrate.')

        consulta = IDRuta.objects.select_related('ruta_obj')
        ruta = options.get('ruta')
        if ruta:
            consulta = consulta.filter(Q(ruta=ruta) | Q(ruta_obj__nombre=ruta))

        enlaces = [
            (identificador.ruta_obj.nombre if identificador.ruta_obj_id else identificador.ruta, identificador.id_onmsi)
            for identificador in consulta.order_by('ruta')
        ]
        if not enlaces:
            raise CommandError('No hay IDs ONMSI cargados para el filtro solicitado.')

        exitosos = 0
        for node, link_key in enlaces:
            self.stdout.write(f'Procesando {node} ({link_key})...')
            exitosos += int(procesar_enlace(node, link_key))
            time.sleep(0.1)

        self.stdout.write(self.style.SUCCESS(
            f'Proceso completado. {exitosos}/{len(enlaces)} enlaces procesados.'
        ))
