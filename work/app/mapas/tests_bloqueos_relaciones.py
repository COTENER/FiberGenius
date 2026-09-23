"""Regresión de bloqueos con relaciones opcionales, ejecutable en SQLite y PG.

La inspección del Query no sustituye una prueba concurrente en PostgreSQL.
Evita que SQLite oculte la reintroducción de FOR UPDATE sobre un OUTER JOIN.
"""
import io
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db.models.sql.compiler import SQLCompiler
from django.test import TestCase
from django.urls import reverse

from .models import (
    DetallePuertoODF, FibraTramo, HubSite, InventarioFibra, InventarioODF,
    InventarioTramo, LoteImportacion, RackFisico, Ruta, SalaTecnica,
    TerminacionFibra,
)
from .services.fibras import (
    actualizar_fibras_masivo, asignar_ruta_fibra, sincronizar_estado_fibra,
)
from .services.puertos import conectar_puerto, conectar_puertos_masivo, desconectar_puerto
from .views.importacion import (
    _procesar_fibras_globales, _procesar_fibras_inventario, _procesar_lote_atomicamente,
)


class BloqueosRelacionesOpcionalesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username='admin-bloqueos', email='locks@example.test', password='test-only',
        )
        cls.ruta = Ruta.objects.create(nombre='TRONCAL-BLOQUEOS')
        cls.tramo = InventarioTramo.objects.create(
            ruta=cls.ruta, tramo_secuencia=1, capacidad_hilos=12,
        )
        cls.fibra = InventarioFibra.objects.create(fibra_numero='F1')
        site = HubSite.objects.create(nombre='SITE-BLOQUEOS')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK')
        odf = InventarioODF.objects.create(rack_obj=rack, odf='ODF-BLOQUEOS', capacidad_puertos=2)
        cls.puertos = [
            DetallePuertoODF.objects.create(odf_obj=odf, puerto_odf=str(n), estado_puerto='LIBRE')
            for n in (1, 2)
        ]

    def setUp(self):
        self.client.force_login(self.admin)
        self.modelos_bloqueados = set()
        self.bloqueos_opcionales = set()
        original = SQLCompiler.as_sql

        def verificar(compiler, *args, **kwargs):
            resultado = original(compiler, *args, **kwargs)
            if compiler.query.select_for_update:
                self.modelos_bloqueados.add(compiler.query.model.__name__)
            # Inspeccionar el SQL incluye los JOIN implícitos del ordering y
            # excluye alias que el compilador haya descartado por innecesarios.
            if compiler.query.select_for_update and ' LEFT OUTER JOIN ' in resultado[0]:
                self.assertEqual(
                    compiler.query.select_for_update_of, ('self',),
                    f'{compiler.query.model.__name__}: bloqueo sin delimitar con JOIN opcional',
                )
                self.bloqueos_opcionales.add(compiler.query.model.__name__)
            return resultado

        parche = patch.object(SQLCompiler, 'as_sql', autospec=True, side_effect=verificar)
        parche.start()
        self.addCleanup(parche.stop)

    def test_control_detecta_consulta_incompatible_incluso_en_sqlite(self):
        with self.assertRaisesMessage(AssertionError, 'bloqueo sin delimitar'):
            list(InventarioFibra.objects.select_for_update().select_related('ruta'))

    def test_conectar_mover_y_desconectar_fibra_sin_troncal(self):
        for puerto in self.puertos:
            conectar_puerto(
                puerto_id=puerto.pk, fibra_id=self.fibra.pk, extremo='A', permitir_mover=True,
            )
        desconectar_puerto(puerto_id=self.puertos[1].pk)
        self.fibra.refresh_from_db()
        self.assertIsNone(self.fibra.ruta_id)
        self.assertFalse(TerminacionFibra.objects.filter(fibra=self.fibra).exists())
        self.assertTrue({'InventarioFibra', 'TerminacionFibra'} <= self.bloqueos_opcionales)

    def test_conexiones_masivas_admiten_extremos_de_fibra_sin_troncal_y_recarga(self):
        conexiones = [
            {'fibra': self.fibra, 'puerto': puerto, 'extremo': extremo}
            for puerto, extremo in zip(self.puertos, ('A', 'B'))
        ]
        conectar_puertos_masivo(conexiones)
        conectar_puertos_masivo(conexiones)
        self.assertEqual(TerminacionFibra.objects.filter(fibra=self.fibra).count(), 2)
        self.assertTrue({'InventarioFibra', 'TerminacionFibra'} <= self.bloqueos_opcionales)

    def test_estado_metadatos_y_asignacion_conservan_bloqueo_sin_crear_recorrido(self):
        sincronizar_estado_fibra(self.fibra)
        actualizar_fibras_masivo([{'fibra': self.fibra, 'cambios': {'observaciones': 'Revisada'}}])
        asignar_ruta_fibra(fibra=self.fibra, ruta=self.ruta)
        self.fibra.refresh_from_db()
        self.assertEqual(self.fibra.ruta_id, self.ruta.pk)
        self.assertEqual(self.fibra.observaciones, 'Revisada')
        self.assertFalse(FibraTramo.objects.filter(fibra=self.fibra).exists())
        self.assertIn('InventarioFibra', self.bloqueos_opcionales)

    def test_csv_global_recarga_fibra_sin_troncal(self):
        contenido = (
            'Codigo Fibra,Fibra,Estado,Servicio\n'
            f'{self.fibra.codigo_fibra},F1,Ocupado,Servicio de prueba\n'
        )
        for _ in range(2):
            resultado = _procesar_fibras_globales(io.BytesIO(contenido.encode()))
            self.assertEqual(resultado['creadas'], 0)
        self.fibra.refresh_from_db()
        self.assertIsNone(self.fibra.ruta_id)
        self.assertEqual(self.fibra.estado, 'OCUPADO')
        self.assertIn('InventarioFibra', self.bloqueos_opcionales)

    def test_lote_con_usuario_opcional_se_completa_una_sola_vez(self):
        for usuario in (None, self.admin):
            with self.subTest(usuario=usuario):
                lote = LoteImportacion.objects.create(
                    tipo='fibras_globales', archivo_origen='prueba.csv', usuario=usuario,
                )
                procesador = Mock(return_value={'total': 1, 'creadas': 1})
                _procesar_lote_atomicamente(lote.pk, procesador, None)
                _procesar_lote_atomicamente(lote.pk, procesador, None)
                procesador.assert_called_once()
                lote.refresh_from_db()
                self.assertEqual(lote.estado, 'COMPLETADO')
                self.assertEqual(lote.filas_creadas, 1)
        self.assertIn('LoteImportacion', self.bloqueos_opcionales)

    def test_csv_fibra_tramo_admite_posicion_existente_sin_fibra(self):
        FibraTramo.objects.create(tramo=self.tramo, numero_hilo='F1')
        contenido = (
            'ruta,secuencia,codigo_fibra,fibra,estado\n'
            f'{self.ruta.nombre},1,{self.fibra.codigo_fibra},F1,Disponible\n'
        )
        _procesar_fibras_inventario(io.BytesIO(contenido.encode()))
        self.assertEqual(FibraTramo.objects.get(tramo=self.tramo, numero_hilo='F1').fibra_id, self.fibra.pk)
        # El guardado masivo bloquea las posiciones sin hidratar la fibra por
        # un LEFT JOIN. Debe existir el bloqueo, no una relación innecesaria.
        # El guard general sigue exigiendo OF self si hubiera un JOIN opcional.
        self.assertIn('FibraTramo', self.modelos_bloqueados)

    def test_edicion_gui_de_fibra_sin_troncal(self):
        response = self.client.post(reverse('api_update_fibra'), {
            'id': self.fibra.pk, 'observaciones': 'Edición GUI',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.fibra.refresh_from_db()
        self.assertEqual(self.fibra.observaciones, 'Edición GUI')
        self.assertIsNone(self.fibra.ruta_id)
        self.assertIn('InventarioFibra', self.bloqueos_opcionales)

    def test_nuevo_tramo_gui_con_nodos_pendientes(self):
        response = self.client.post(reverse('api_create_tramo'), {
            'ruta_id': self.ruta.pk, 'tramo_secuencia': 2,
        }, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        nuevo = InventarioTramo.objects.get(ruta=self.ruta, tramo_secuencia=2)
        self.assertIsNone(nuevo.origen_nodo_id)
        self.assertIsNone(nuevo.destino_nodo_id)
        self.assertIn('InventarioTramo', self.bloqueos_opcionales)
