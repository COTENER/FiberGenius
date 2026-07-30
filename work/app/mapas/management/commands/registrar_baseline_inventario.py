import hashlib
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now

from mapas.models import (
    CoordenadaRuta,
    DetallePuertoODF,
    HubSite,
    InventarioFibra,
    InventarioODF,
    InventarioTramo,
    LoteImportacion,
    Reserva,
    Ruta,
)


class Command(BaseCommand):
    help = 'Registra un baseline reconstruido sin fingir una importacion ejecutada desde la GUI.'

    def add_arguments(self, parser):
        parser.add_argument('--manifest', required=True)
        parser.add_argument('--fecha-origen')
        parser.add_argument('--descripcion', default='Carga V5 reconstruida para homologacion')
        parser.add_argument('--usuario')

    def handle(self, *args, **options):
        manifest = Path(options['manifest']).resolve()
        if not manifest.is_file():
            raise CommandError(f'No existe el manifiesto: {manifest}')

        fecha_origen = None
        if options.get('fecha_origen'):
            fecha_origen = parse_datetime(options['fecha_origen'])
            if fecha_origen is None:
                raise CommandError('fecha-origen debe estar en formato ISO 8601.')

        digest = hashlib.sha256()
        with manifest.open('rb') as archivo:
            for bloque in iter(lambda: archivo.read(1024 * 1024), b''):
                digest.update(bloque)
        hash_sha256 = digest.hexdigest()

        if LoteImportacion.objects.filter(
            origen_registro='BASELINE_RECONSTRUIDO',
            hash_sha256=hash_sha256,
        ).exists():
            raise CommandError('Este manifiesto ya tiene un baseline registrado.')

        usuario = None
        if options.get('usuario'):
            try:
                usuario = get_user_model().objects.get(username=options['usuario'])
            except get_user_model().DoesNotExist as exc:
                raise CommandError('El usuario indicado no existe.') from exc

        conteos = {
            'rutas': Ruta.objects.count(),
            'tramos': InventarioTramo.objects.count(),
            'coordenadas': CoordenadaRuta.objects.count(),
            'reservas_elementos': Reserva.objects.count(),
            'sites': HubSite.objects.count(),
            'odfs': InventarioODF.objects.count(),
            'puertos_odf': DetallePuertoODF.objects.count(),
            'fibras': InventarioFibra.objects.count(),
        }
        total = sum(conteos.values())
        lote = LoteImportacion.objects.create(
            tipo='BASELINE_INVENTARIO_V5',
            archivo_origen=manifest.name,
            hash_sha256=hash_sha256,
            estado='COMPLETADO',
            usuario=usuario,
            total_filas=total,
            filas_creadas=0,
            filas_actualizadas=0,
            filas_rechazadas=0,
            origen_registro='BASELINE_RECONSTRUIDO',
            fecha_origen=fecha_origen,
            metadatos_origen={
                'descripcion': options['descripcion'],
                'conteos': conteos,
                'registrado_en': now().isoformat(),
                'nota': 'Registro reconstruido; no representa una importacion ejecutada desde la GUI.',
            },
        )
        self.stdout.write(self.style.SUCCESS(f'Baseline registrado: {lote.codigo}'))
