import io
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now

from .models import (
    DetallePuertoODF,
    HubSite,
    InventarioFibra,
    InventarioODF,
    LoteImportacion,
    RackFisico,
    Ruta,
    SalaTecnica,
)
from .services.importacion_puertos import preparar_operaciones
from .views.importacion import (
    _directorio_importaciones,
    _procesar_fibras_globales,
    _procesar_lote_atomicamente,
    _procesar_puertos_odf_inventario,
    _procesar_ruta_otu,
    _procesar_sites_inventario,
)


def _csv(contenido):
    return io.BytesIO(contenido.encode('utf-8-sig'))


class ContratoParcheImportacionTests(TestCase):
    def setUp(self):
        self.site = HubSite.objects.create(
            nombre='SITE-IMPORT',
            latitud='-23.5000000',
            longitud='-70.4000000',
            direccion='Dirección original',
        )
        sala = SalaTecnica.objects.create(
            hub_site=self.site,
            nombre='SALA-1',
        )
        rack = RackFisico.objects.create(sala=sala, nombre='RACK-1')
        self.odf = InventarioODF.objects.create(
            odf='ODF-IMPORT',
            rack_obj=rack,
            capacidad_puertos=24,
        )
        self.puerto = DetallePuertoODF.objects.create(
            odf_obj=self.odf,
            puerto_odf='1',
            estado_puerto='RESERVADO',
            bandeja='B1',
            tipo_conector='LC',
            patchcord='Sí',
            destino='SWITCH-1',
            observaciones='Original',
        )
        self.fibra = InventarioFibra.objects.create(
            codigo_fibra='FGF-IMPORT-1',
            fibra_numero='F1',
            estado='OCUPADO',
            origen_estado='INFORMADO',
            condicion_fisica='OPERATIVA',
            nombre_fibra='Servicio original',
            tipo_conector='SC',
            observaciones='Nota original',
        )

    def test_celda_vacia_conserva_y_marcador_limpia_site(self):
        _procesar_sites_inventario(_csv('Nombre\nSITE-IMPORT\n'))
        self.site.refresh_from_db()
        self.assertEqual(str(self.site.latitud), '-23.5000000')
        self.assertEqual(self.site.direccion, 'Dirección original')

        _procesar_sites_inventario(_csv(
            'Nombre,Latitud,Longitud,Direccion\n'
            'SITE-IMPORT,__BORRAR__,__BORRAR__,__BORRAR__\n'
        ))
        self.site.refresh_from_db()
        self.assertIsNone(self.site.latitud)
        self.assertIsNone(self.site.longitud)
        self.assertEqual(self.site.direccion, '')

    def test_celda_vacia_conserva_y_marcador_limpia_puerto(self):
        _procesar_puertos_odf_inventario(_csv(
            'ODF,Puerto ODF\nODF-IMPORT,1\n'
        ))
        self.puerto.refresh_from_db()
        self.assertEqual(self.puerto.estado_puerto, 'RESERVADO')
        self.assertEqual(self.puerto.destino, 'SWITCH-1')
        self.assertEqual(self.puerto.bandeja, 'B1')

        _procesar_puertos_odf_inventario(_csv(
            'ODF,Puerto ODF,Bandeja,Destino,Observaciones\n'
            'ODF-IMPORT,1,__BORRAR__,__BORRAR__,__BORRAR__\n'
        ))
        self.puerto.refresh_from_db()
        self.assertEqual(self.puerto.estado_puerto, 'RESERVADO')
        self.assertEqual(self.puerto.bandeja, '')
        self.assertEqual(self.puerto.destino, '')
        self.assertEqual(self.puerto.observaciones, '')

    def test_celda_vacia_conserva_y_marcador_restablece_fibra(self):
        _procesar_fibras_globales(_csv(
            'Codigo Fibra,Fibra\nFGF-IMPORT-1,F1\n'
        ))
        self.fibra.refresh_from_db()
        self.assertEqual(self.fibra.estado, 'OCUPADO')
        self.assertEqual(self.fibra.nombre_fibra, 'Servicio original')
        self.assertEqual(self.fibra.observaciones, 'Nota original')

        _procesar_fibras_globales(_csv(
            'Codigo Fibra,Fibra,Estado,Condicion,Servicio,Conector,Observaciones\n'
            'FGF-IMPORT-1,F1,__BORRAR__,__BORRAR__,__BORRAR__,__BORRAR__,__BORRAR__\n'
        ))
        self.fibra.refresh_from_db()
        self.assertEqual(self.fibra.estado, 'SIN_INFORMACION')
        self.assertEqual(self.fibra.origen_estado, 'NO_INFORMADO')
        self.assertEqual(self.fibra.condicion_fisica, 'SIN_VERIFICAR')
        self.assertEqual(self.fibra.nombre_fibra, '')
        self.assertEqual(self.fibra.tipo_conector, '')
        self.assertEqual(self.fibra.observaciones, '')


class AuditoriaYColaImportacionTests(TestCase):
    def test_fallo_al_cerrar_auditoria_revierte_tambien_los_datos(self):
        archivo = _csv('Ruta\nRUTA-ROLLBACK\n')
        lote = LoteImportacion.objects.create(
            tipo='ruta_otu',
            archivo_origen='rutas.csv',
            estado='PENDIENTE',
        )
        with patch(
            'mapas.views.importacion._cerrar_lote_exitoso',
            side_effect=RuntimeError('fallo de auditoría'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'auditoría'):
                _procesar_lote_atomicamente(
                    lote.pk,
                    _procesar_ruta_otu,
                    archivo,
                )
        self.assertFalse(Ruta.objects.filter(nombre='RUTA-ROLLBACK').exists())
        lote.refresh_from_db()
        self.assertEqual(lote.estado, 'PENDIENTE')

    def test_worker_recupera_un_lote_pendiente(self):
        directorio = settings.BASE_DIR / '.tmp-tests-progress'
        with override_settings(DATA_ROOT=directorio):
            lote = LoteImportacion.objects.create(
                tipo='ruta_otu',
                archivo_origen='rutas.csv',
                estado='PENDIENTE',
            )
            LoteImportacion.objects.filter(pk=lote.pk).update(
                creado_en=now() - timedelta(minutes=1)
            )
            pendiente = (
                _directorio_importaciones()
                / 'pendientes'
                / f'{lote.codigo}_rutas.csv'
            )
            pendiente.write_bytes(b'Ruta\nRUTA-RECUPERADA\n')

            call_command('procesar_importaciones', verbosity=0)

            lote.refresh_from_db()
            self.assertEqual(lote.estado, 'COMPLETADO')
            self.assertTrue(
                Ruta.objects.filter(nombre='RUTA-RECUPERADA').exists()
            )
            self.assertFalse(pendiente.exists())
            (_directorio_importaciones() / 'progreso' / f'{lote.codigo}.json').unlink(
                missing_ok=True
            )


class RendimientoOperacionesPuertoTests(TestCase):
    def test_preparacion_masiva_mantiene_consultas_acotadas(self):
        site = HubSite.objects.create(nombre='SITE-Q')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA-Q')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK-Q')
        odf = InventarioODF.objects.create(
            odf='ODF-Q',
            rack_obj=rack,
            capacidad_puertos=50,
        )
        DetallePuertoODF.objects.bulk_create([
            DetallePuertoODF(odf_obj=odf, puerto_odf=str(numero))
            for numero in range(1, 31)
        ])
        filas = [
            (
                numero + 1,
                {
                    'site': 'SITE-Q',
                    'odf': 'ODF-Q',
                    'puerto': str(numero),
                    'accion': 'Reservar',
                },
            )
            for numero in range(1, 31)
        ]

        with CaptureQueriesContext(connection) as consultas:
            operaciones, errores = preparar_operaciones(filas)

        self.assertEqual(len(operaciones), 30)
        self.assertEqual(errores, [])
        self.assertLessEqual(len(consultas), 6)
