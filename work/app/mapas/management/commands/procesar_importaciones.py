import time
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.timezone import now

from mapas.models import LoteImportacion
from mapas.views.importacion import (
    _cerrar_lote_fallido,
    _directorio_importaciones,
    _ejecutar_importacion_en_segundo_plano,
    obtener_procesadores_importacion,
)


class Command(BaseCommand):
    help = (
        'Recupera y procesa importaciones pendientes. Por defecto ejecuta '
        'una pasada; --continuo lo mantiene como worker.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--continuo',
            action='store_true',
            help='Permanece activo y revisa periódicamente la cola.',
        )
        parser.add_argument(
            '--intervalo',
            type=int,
            default=5,
            help='Segundos entre revisiones en modo continuo (mínimo 2).',
        )
        parser.add_argument(
            '--retencion-dias',
            type=int,
            default=int(
                getattr(settings, 'FIBERGENIUS_IMPORT_RETENTION_DAYS', 30)
            ),
            help='Días que se conservan progreso y archivos fallidos.',
        )

    def handle(self, *args, **options):
        intervalo = max(2, int(options['intervalo']))
        while True:
            procesados = self._procesar_pendientes()
            self._limpiar_evidencias(max(1, int(options['retencion_dias'])))
            if not options['continuo']:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Cola revisada: {procesados} lote(s) atendido(s).'
                    )
                )
                return
            time.sleep(intervalo)

    def _procesar_pendientes(self):
        registro = obtener_procesadores_importacion()
        pendientes_dir = _directorio_importaciones() / 'pendientes'
        # Evita tomar el lote en la breve ventana entre crear el registro y
        # terminar de guardar el archivo subido desde la petición web.
        limite_creacion = now() - timedelta(seconds=30)
        lotes = list(
            LoteImportacion.objects.filter(
                estado='PENDIENTE',
                creado_en__lte=limite_creacion,
            ).order_by('creado_en')
        )
        atendidos = 0
        for lote in lotes:
            configuracion = registro.get(lote.tipo)
            if configuracion is None:
                _cerrar_lote_fallido(
                    lote,
                    ValueError(
                        f"El tipo de importación '{lote.tipo}' ya no está disponible."
                    ),
                )
                atendidos += 1
                continue

            candidatos = sorted(
                Path(pendientes_dir).glob(f'{lote.codigo}_*')
            )
            if not candidatos:
                if lote.creado_en <= now() - timedelta(minutes=5):
                    _cerrar_lote_fallido(
                        lote,
                        FileNotFoundError(
                            'No se encontró el archivo temporal de la importación.'
                        ),
                    )
                    atendidos += 1
                continue

            procesador, nombre_amigable = configuracion
            _ejecutar_importacion_en_segundo_plano(
                lote.pk,
                str(lote.codigo),
                lote.tipo,
                nombre_amigable,
                procesador,
                str(candidatos[0]),
            )
            atendidos += 1
        return atendidos

    def _limpiar_evidencias(self, retencion_dias):
        limite = now().timestamp() - (retencion_dias * 24 * 60 * 60)
        base = _directorio_importaciones()
        for subdirectorio in ('progreso', 'fallidos'):
            for archivo in (base / subdirectorio).iterdir():
                try:
                    if archivo.is_file() and archivo.stat().st_mtime < limite:
                        archivo.unlink()
                except OSError:
                    self.stderr.write(
                        f'No se pudo retirar la evidencia antigua {archivo}.'
                    )
