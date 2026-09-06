"""Prechequeo de solo lectura para ejecutar sobre una base RC1 antes de migrar."""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from mapas.models import (
    CoordenadaRuta,
    DetallePuertoODF,
    IDRuta,
    InventarioODF,
    InventarioTramo,
    Reserva,
    Ruta,
    TrazaReferencia,
)


def _texto(value):
    return str(value or '').strip()


class Command(BaseCommand):
    help = 'Valida datos RC1 sin modificarlos antes de aplicar las migraciones RC2.'

    def handle(self, *args, **options):
        problemas = []

        ids = list(IDRuta.objects.values_list('id_onmsi', flat=True))
        ids_duplicados = [valor for valor, cantidad in Counter(ids).items() if cantidad > 1]
        if ids_duplicados:
            problemas.append(f'IDRuta: {len(ids_duplicados)} ID ONMSI duplicados')

        rutas = {
            nombre.casefold()
            for nombre in Ruta.objects.values_list('nombre', flat=True)
        }
        ids_sin_ruta = sum(
            1
            for nombre in IDRuta.objects.values_list('ruta', flat=True)
            if _texto(nombre).casefold() not in rutas
        )
        if ids_sin_ruta:
            problemas.append(f'IDRuta: {ids_sin_ruta} referencias a rutas inexistentes')

        referencias_activas = Counter(
            TrazaReferencia.objects.filter(activa=True).values_list('ruta_id', flat=True)
        )
        rutas_con_multiples = sum(1 for cantidad in referencias_activas.values() if cantidad > 1)
        if rutas_con_multiples:
            problemas.append(f'Trazas referencia: {rutas_con_multiples} rutas tienen más de una activa')

        odfs = list(InventarioODF.objects.values('id', 'hub_site', 'sala', 'rack', 'odf'))
        claves_odf = Counter(
            (
                _texto(item['hub_site']).casefold(),
                _texto(item['sala']).casefold(),
                _texto(item['rack']).casefold(),
                _texto(item['odf']).casefold(),
            )
            for item in odfs
        )
        incompletos = sum(
            cantidad for clave, cantidad in claves_odf.items() if not all(clave)
        )
        duplicados = sum(1 for cantidad in claves_odf.values() if cantidad > 1)
        if incompletos:
            problemas.append(f'ODF: {incompletos} ubicaciones incompletas')
        if duplicados:
            problemas.append(f'ODF: {duplicados} ubicaciones/nombres duplicados')

        nombres_odf = Counter(_texto(item['odf']).casefold() for item in odfs)
        puertos_problematicos = 0
        for odf_obj_id, odf_texto in DetallePuertoODF.objects.values_list('odf_obj_id', 'odf'):
            if not odf_obj_id and nombres_odf[_texto(odf_texto).casefold()] != 1:
                puertos_problematicos += 1
        if puertos_problematicos:
            problemas.append(
                f'Puertos ODF: {puertos_problematicos} no pueden resolver un único ODF padre'
            )

        tramos_invalidos = InventarioTramo.objects.filter(
            Q(distancia_m__lt=0)
            | Q(mufas__lt=0)
            | Q(splitters__lt=0)
            | Q(reservas_m__lt=0)
            | Q(hilos_ocupados__lt=0)
            | Q(hilos_libres__lt=0)
        ).count()
        if tramos_invalidos:
            problemas.append(f'Tramos: {tramos_invalidos} filas contienen cantidades negativas')

        coordenadas_invalidas = CoordenadaRuta.objects.filter(
            Q(latitud__lt=-90)
            | Q(latitud__gt=90)
            | Q(longitud__lt=-180)
            | Q(longitud__gt=180)
        ).count()
        if coordenadas_invalidas:
            problemas.append(f'Coordenadas: {coordenadas_invalidas} filas fuera de rango')

        reservas_invalidas = Reserva.objects.filter(
            Q(reserva_m__lt=0)
            | Q(latitud__lt=-90)
            | Q(latitud__gt=90)
            | Q(longitud__lt=-180)
            | Q(longitud__gt=180)
        ).count()
        if reservas_invalidas:
            problemas.append(f'Reservas: {reservas_invalidas} filas fuera de rango')

        if problemas:
            for problema in problemas:
                self.stderr.write(self.style.ERROR('ERROR: ' + problema))
            raise CommandError(
                f'Prechequeo RC2 fallido con {len(problemas)} categoría(s) de problemas. '
                'No ejecute migrate hasta corregirlas.'
            )

        self.stdout.write(self.style.SUCCESS(
            'Prechequeo RC2 aprobado. No se modificó ningún dato.'
        ))
