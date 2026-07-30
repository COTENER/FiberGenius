"""Actualiza pruebas y eventos OTDR sin SQL específico de MySQL/PostgreSQL."""

import logging
from datetime import datetime

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from mapas.models import EventoOTDRDetalle, Medicion, PruebaOTDR

logger = logging.getLogger('mapas')


def _tls_verify():
    return getattr(settings, 'ONMSI_CA_BUNDLE', '') or getattr(settings, 'ONMSI_TLS_VERIFY', True)


def descargar_json_autenticado(internal_key):
    base_url = str(settings.EVENTOS_API_URL).rstrip('/')
    if not base_url:
        raise CommandError('EVENTOS_API_URL no está configurada.')
    respuesta = requests.get(
        f'{base_url}/{internal_key}/cdm',
        auth=(settings.EVENTOS_API_USER, settings.EVENTOS_API_PASSWORD),
        headers={'User-Agent': 'FiberGenius/RC2'},
        timeout=30,
        verify=_tls_verify(),
    )
    respuesta.raise_for_status()
    return respuesta.json()


def _fecha(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def _datos_resultado(datos):
    pruebas = datos.get('tests') or []
    if not pruebas:
        raise ValueError('Estructura JSON inválida: no se encontraron tests.')
    prueba = pruebas[0]
    medidos = (
        prueba.get('results', {})
        .get('data', {})
        .get('otdrResults', {})
        .get('measuredResults', [])
    )
    if not medidos:
        raise ValueError('No se encontraron resultados OTDR medidos.')
    fibra = (
        prueba.get('configuration', {})
        .get('otdrSettings', {})
        .get('fiber', {})
    )
    return prueba, medidos[0], fibra


@transaction.atomic
def guardar_eventos(datos, node, internal_key):
    prueba_api, resultado, fibra = _datos_resultado(datos)
    defaults = {
        'fecha_adquisicion': _fecha(resultado.get('acqDateTime')),
        'longitud_fibra_m': resultado.get('fiberLengthM'),
        'perdida_total_db': resultado.get('linkLossdB'),
        'orl_db': resultado.get('linkOrldB'),
        'num_eventos': resultado.get('numberOfEvents'),
        'longitud_onda_nm': resultado.get('wavelengthNm'),
        'max_empalme_db': resultado.get('maxSpliceMeasureddB'),
        'max_reflectancia_db': resultado.get('maxReflectanceMeasureddB'),
        'estado_prueba': prueba_api.get('status'),
        'cable_id': fibra.get('cableId'),
        'fibra_id': fibra.get('fiberId'),
        'direccion': fibra.get('direction'),
        'localizacion_a': fibra.get('locationA'),
        'localizacion_b': fibra.get('locationB'),
        'internal_key': str(internal_key),
    }

    prueba = PruebaOTDR.objects.filter(internal_key=str(internal_key)).order_by('-id').first()
    if prueba:
        for campo, valor in defaults.items():
            setattr(prueba, campo, valor)
        prueba.save(update_fields=list(defaults))
    else:
        prueba = PruebaOTDR.objects.create(**defaults)

    EventoOTDRDetalle.objects.filter(node=node).delete()
    eventos = [
        EventoOTDRDetalle(
            prueba=prueba,
            event_id=evento.get('id'),
            id_slm=evento.get('idSlm'),
            event_type=evento.get('eventType'),
            distance_m=evento.get('distanceM'),
            section_length_m=evento.get('sectionLengthM'),
            loss_db=evento.get('lossdB'),
            section_loss_db=evento.get('sectionLossdB'),
            cumulative_loss_db=evento.get('cumulativeLossdB'),
            reflectance_db=evento.get('reflectancedB'),
            slope_dbkm=evento.get('slopedBkm'),
            event_test_status=evento.get('eventTestStatus'),
            loss_alarm_failed=bool(evento.get('lossAlarmFailed', False)),
            refl_alarm_failed=bool(evento.get('reflAlarmFailed', False)),
            group_number=evento.get('groupNumber'),
            refl_saturated=bool(evento.get('reflSaturated', False)),
            refl_peak_distance_m=evento.get('reflPeakDistanceM'),
            node=node,
        )
        for evento in resultado.get('events', [])
    ]
    EventoOTDRDetalle.objects.bulk_create(eventos, batch_size=1000)
    return len(eventos)


class Command(BaseCommand):
    help = 'Actualiza pruebas y eventos ópticos a partir de las mediciones existentes.'

    def add_arguments(self, parser):
        parser.add_argument('--ruta', type=str, help='Nombre exacto de la ruta a procesar')

    def handle(self, *args, **options):
        tablas = set(connection.introspection.table_names())
        requeridas = {
            Medicion._meta.db_table,
            PruebaOTDR._meta.db_table,
            EventoOTDRDetalle._meta.db_table,
        }
        faltantes = requeridas - tablas
        if faltantes:
            raise CommandError(
                'Faltan tablas OTDR. Ejecute primero manage.py migrate: ' + ', '.join(sorted(faltantes))
            )

        mediciones = Medicion.objects.all().values('internal_key', 'node')
        ruta = options.get('ruta')
        if ruta:
            mediciones = mediciones.filter(node=ruta)
        mediciones = list(mediciones.order_by('node'))
        if not mediciones:
            raise CommandError('No se encontraron mediciones para procesar.')

        exitosos = 0
        for medicion in mediciones:
            internal_key = medicion['internal_key']
            node = medicion['node']
            self.stdout.write(f'Descargando eventos para {node} ({internal_key})...')
            try:
                cantidad = guardar_eventos(
                    descargar_json_autenticado(internal_key),
                    node,
                    internal_key,
                )
                self.stdout.write(f'  {cantidad} eventos guardados.')
                exitosos += 1
            except (requests.RequestException, ValueError) as exc:
                logger.error('Error procesando %s (%s): %s', node, internal_key, exc)

        self.stdout.write(self.style.SUCCESS(
            f'Proceso finalizado. {exitosos}/{len(mediciones)} mediciones procesadas.'
        ))
