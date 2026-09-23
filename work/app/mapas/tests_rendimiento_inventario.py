"""Paridad funcional de las optimizaciones; sin presupuestos de tiempo frágiles."""
import csv
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

from .models import (AuditoriaFibra, DetallePuertoODF, FibraTramo, HubSite,
                     InventarioFibra, InventarioODF, InventarioTramo, LoteImportacion,
                     RackFisico, Ruta, SalaTecnica)
from .services.importacion_tramos import guardar_asignaciones_tramo_masivo
from .services.puertos import conectar_puerto
from .views.importacion import _procesar_fibras_inventario
from .views.operacion_inventario import (_filas_fibras, _filas_puertos, _filtro_fibras,
                                        _filtro_puertos, _serializar_fibras, exportar_inventario)


class AsignacionesMasivasTests(TestCase):
    def setUp(self):
        self.ruta = Ruta.objects.create(nombre='TRONCAL-MASIVA')
        self.tramos = [InventarioTramo.objects.create(ruta=self.ruta, tramo_secuencia=i,
                       codigo_tramo=f'T{i}', capacidad_hilos=500) for i in range(1, 4)]
        self.usuario = get_user_model().objects.create_user(username='operador-masivo')
        self.lote = LoteImportacion.objects.create(tipo='fibras_inventario', usuario=self.usuario)
        self.fibra = InventarioFibra.objects.create(codigo_fibra='FIB-GLOBAL', fibra_numero='F1', ruta=self.ruta)

    def fila(self, tramo=None, fibra=None, **cambios):
        return dict(fila=2, tramo=tramo or self.tramos[0], fibra_consultada=fibra or self.fibra,
                    numero_hilo='F1', estado='DISPONIBLE', observaciones=None) | cambios

    def guardar(self, filas):
        return guardar_asignaciones_tramo_masivo(filas, lote=self.lote, usuario=self.usuario)

    def test_cobertura_1_2_3_y_prioridad_de_ocupacion(self):
        self.guardar([self.fila()])
        self.fibra.refresh_from_db()
        self.assertEqual(self.fibra.estado, 'SIN_INFORMACION')
        self.guardar([self.fila(tramo=self.tramos[1])])
        self.fibra.refresh_from_db()
        self.assertEqual(self.fibra.estado, 'SIN_INFORMACION')
        self.guardar([self.fila(tramo=self.tramos[2])])
        self.fibra.refresh_from_db()
        self.assertEqual((self.fibra.estado, self.fibra.origen_estado), ('DISPONIBLE', 'INFERIDO_TRAMOS'))
        self.guardar([self.fila(estado='OCUPADO')])
        self.fibra.refresh_from_db()
        self.assertEqual(self.fibra.estado, 'OCUPADO')
        self.tramos[0].refresh_from_db()
        self.assertEqual((self.tramos[0].hilos_ocupados, self.tramos[0].hilos_libres), (1, 0))

    def test_estado_informado_no_se_reemplaza_y_no_se_crean_terminaciones(self):
        InventarioFibra.objects.filter(pk=self.fibra.pk).update(estado='RESERVADO', origen_estado='INFORMADO')
        self.guardar([self.fila(estado='OCUPADO')])
        self.fibra.refresh_from_db()
        self.assertEqual((self.fibra.estado, self.fibra.origen_estado), ('RESERVADO', 'INFORMADO'))
        self.assertFalse(self.fibra.terminaciones.exists())

    def test_recarga_y_parche_no_borran_estado_notas_ni_duplican_auditoria(self):
        self.guardar([self.fila(estado='RESERVADO', observaciones=' Nota ' )])
        self.assertEqual(self.guardar([self.fila(estado=None)]), (0, 0))
        asignacion = FibraTramo.objects.get()
        self.assertEqual((asignacion.estado, asignacion.observaciones), ('RESERVADO', 'Nota'))
        self.assertEqual(AuditoriaFibra.objects.filter(accion='ASIGNAR_TRAMO').count(), 1)
        self.assertEqual(self.guardar([self.fila(observaciones='', estado='SIN_INFORMACION')]), (0, 1))

    def test_renumeracion_conserva_pk_y_identidad_global(self):
        self.guardar([self.fila()])
        pk = FibraTramo.objects.get().pk
        self.guardar([self.fila(numero_hilo='F2')])
        asignacion = FibraTramo.objects.get()
        self.assertEqual((asignacion.pk, asignacion.numero_hilo), (pk, 'F2'))
        self.fibra.refresh_from_db()
        self.assertEqual(self.fibra.fibra_numero, 'F1')

    def test_posicion_sin_fibra_se_reutiliza_sin_dejar_doble_asignacion(self):
        self.guardar([self.fila()])
        antigua = FibraTramo.objects.get()
        vacia = FibraTramo.objects.create(tramo=self.tramos[0], numero_hilo='F2')
        self.guardar([self.fila(numero_hilo='F2', estado='OCUPADO')])
        antigua.refresh_from_db(); vacia.refresh_from_db()
        self.assertEqual((antigua.fibra_id, antigua.estado), (None, 'SIN_INFORMACION'))
        self.assertEqual((vacia.fibra_id, vacia.estado), (self.fibra.pk, 'OCUPADO'))
        self.assertEqual(self.fibra.asignaciones_tramo.count(), 1)
        self.tramos[0].refresh_from_db()
        self.assertEqual((self.tramos[0].hilos_ocupados, self.tramos[0].hilos_libres), (1, 0))

    def test_rechaza_duplicados_y_fibra_ajena(self):
        otra = Ruta.objects.create(nombre='OTRA')
        ajena = InventarioFibra.objects.create(ruta=otra, fibra_numero='F1')
        escenarios = [[self.fila(), self.fila(fila=3)], [self.fila(fibra=ajena)]]
        for filas in escenarios:
            with self.subTest(filas=len(filas)), self.assertRaisesRegex(ValidationError, 'fila'):
                self.guardar(filas)
        self.assertFalse(FibraTramo.objects.exists())

    def test_no_desplaza_otra_fibra(self):
        self.guardar([self.fila()])
        otra = InventarioFibra.objects.create(ruta=self.ruta, fibra_numero='F2')
        with self.assertRaisesRegex(ValidationError, 'otra fibra'):
            self.guardar([self.fila(fibra=otra)])
        self.assertEqual(FibraTramo.objects.get().fibra_id, self.fibra.pk)

    def test_validaciones_modelo_con_fila_y_rollback(self):
        for cambios in [dict(numero_hilo='F501'), dict(numero_hilo='F01'),
                        dict(numero_hilo=''), dict(estado='INCORRECTO')]:
            with self.subTest(cambios=cambios), self.assertRaisesRegex(ValidationError, 'fila 3'):
                self.guardar([self.fila(), self.fila(tramo=self.tramos[1], fila=3, **cambios)])
        self.assertFalse(FibraTramo.objects.exists())
        self.assertFalse(AuditoriaFibra.objects.exists())

    def test_auditoria_oficial_con_actor_lote_y_snapshot(self):
        self.guardar([self.fila()])
        auditoria = AuditoriaFibra.objects.get(accion='ASIGNAR_TRAMO')
        self.assertEqual((auditoria.usuario_id, auditoria.lote_importacion_id, auditoria.origen),
                         (self.usuario.pk, self.lote.pk, 'EXCEL'))
        self.assertIsNone(auditoria.metadatos['anterior'])
        self.assertEqual(auditoria.metadatos['nuevo']['fibra_id'], self.fibra.pk)

    def test_fallo_despues_de_escribir_revierte_filas_auditoria_y_contadores(self):
        with patch('mapas.services.importacion_tramos.sincronizar_estado_fibra', side_effect=RuntimeError('simulado')):
            with self.assertRaisesRegex(RuntimeError, 'simulado'):
                self.guardar([self.fila()])
        self.assertFalse(FibraTramo.objects.exists())
        self.assertFalse(AuditoriaFibra.objects.exists())
        self.tramos[0].refresh_from_db()
        self.assertEqual(self.tramos[0].hilos_libres, 0)

    def test_error_sql_se_informa_sin_guardado_parcial(self):
        with patch.object(FibraTramo.objects, 'bulk_create', side_effect=IntegrityError('conflicto')):
            with self.assertRaisesRegex(ValidationError, 'filas 2–2'):
                self.guardar([self.fila()])
        self.assertFalse(FibraTramo.objects.exists())

    def test_consultas_acotadas_en_fibras_con_estado_informado(self):
        fibras = InventarioFibra.objects.bulk_create([InventarioFibra(
            ruta=self.ruta, fibra_numero=f'F{i}', estado='OCUPADO', origen_estado='INFORMADO'
        ) for i in range(2, 302)])
        with CaptureQueriesContext(connection) as consultas:
            self.guardar([self.fila(fibra=f, numero_hilo=f.fibra_numero, fila=i+2) for i,f in enumerate(fibras)])
        self.assertLess(len(consultas), 45)
        self.assertEqual(FibraTramo.objects.count(), 300)

        def verificar_parametros(execute, sql, params, many, context):
            self.assertLessEqual(len(params or ()), 999)
            return execute(sql, params, many, context)

        # Una recarga recorre bulk_update, cuyos CASE requieren más parámetros
        # por fila que bulk_create. No depender del límite ampliado de SQLite.
        with connection.execute_wrapper(verificar_parametros):
            self.guardar([self.fila(fibra=f, numero_hilo=f.fibra_numero, fila=i+2,
                                   estado='OCUPADO') for i, f in enumerate(fibras)])
        self.assertEqual(FibraTramo.objects.filter(estado='OCUPADO').count(), 300)

    def test_importador_preserva_rollback_de_asignacion_provisional(self):
        provisional = InventarioFibra.objects.create(codigo_fibra='PROVISIONAL', fibra_numero='F5')
        data = f'ruta,fibra,codigo_fibra,secuencia\n{self.ruta.nombre},F501,PROVISIONAL,1\n'
        with self.assertRaisesRegex(ValueError, 'fila 2'):
            _procesar_fibras_inventario(io.BytesIO(data.encode()), self.lote)
        provisional.refresh_from_db()
        self.assertIsNone(provisional.ruta_id)


class ExportacionOptimizadaTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username='exportador', password='test')
        self.site = HubSite.objects.create(nombre='SITE-Á')
        sala = SalaTecnica.objects.create(nombre='SALA', hub_site=self.site)
        rack = RackFisico.objects.create(nombre='RACK', sala=sala)
        self.odf = InventarioODF.objects.create(odf='ODF-1', rack_obj=rack, capacidad_puertos=4)
        self.puertos = [DetallePuertoODF.objects.create(odf_obj=self.odf, puerto_odf=str(i), destino='=1+1' if i==1 else '') for i in range(1, 4)]
        ruta = Ruta.objects.create(nombre='TRONCAL-Á')
        InventarioTramo.objects.create(ruta=ruta, tramo_secuencia=1, codigo_tramo='T1')
        self.fibras = [InventarioFibra.objects.create(ruta=ruta if i==1 else None, fibra_numero='F1') for i in (1, 2)]
        for i,f in enumerate(self.fibras):
            conectar_puerto(puerto_id=self.puertos[i].pk, fibra_id=f.pk, extremo='A')

    def request(self, params=None):
        req = RequestFactory().get('/', params or {}); req.user = self.user
        return req

    def test_puertos_conservan_valores_filtros_orden_y_extremos_oficiales(self):
        for params in ({}, {'site':self.site.nombre}, {'estado':'OCUPADO'}, {'q':self.fibras[0].codigo_fibra},
                       {'odf_id':self.odf.pk,'site':'OTRO'}, {'q':'NO-EXISTE'}):
            with self.subTest(params=params):
                esperado=[]
                for p in _filtro_puertos(self.request(params)):
                    t=next(iter(p.terminaciones_oficiales),None)
                    fibra=f'{t.fibra.nombre_troncal} / {t.fibra.fibra_numero} · Extremo {t.extremo}' if t else ''
                    esperado.append((p.odf_obj.hub_site,p.odf_obj.sala,p.odf_obj.rack,p.odf_obj.odf,p.bandeja,p.puerto_odf,
                                     fibra,p.estado_puerto,p.tipo_conector,p.patchcord,p.destino))
                self.assertEqual(list(_filas_puertos(_filtro_puertos(self.request(params)))), esperado)

    def test_exportar_fibras_no_calcula_urls_ni_trazabilidad_que_no_descarga(self):
        normal = _serializar_fibras(_filtro_fibras(self.request()))
        with patch('mapas.views.operacion_inventario.reverse', side_effect=AssertionError('URL innecesaria')):
            exportacion = _serializar_fibras(_filtro_fibras(self.request()), para_exportacion=True)
        for a,b in zip(normal,exportacion):
            for key in a.keys() - {'detail_url','trazabilidad'}:
                self.assertEqual(a[key],b[key],key)
        self.assertEqual(len(list(_filas_fibras(_filtro_fibras(self.request())))),2)

    def test_descargas_preservan_proteccion_formulas_y_csv_xlsx_equivalentes(self):
        import openpyxl
        responses = [exportar_inventario(self.request(),'puertos',fmt) for fmt in ('csv','xlsx')]
        csv_rows = list(csv.reader(io.StringIO(b''.join(responses[0].streaming_content).decode('utf-8-sig'))))
        workbook = openpyxl.load_workbook(io.BytesIO(responses[1].content),read_only=True)
        xlsx_rows = list(workbook.active.values); workbook.close()
        self.assertEqual(len(csv_rows),len(xlsx_rows))
        self.assertEqual(csv_rows[1][-1], "'=1+1")
        self.assertEqual(xlsx_rows[1][-1], "'=1+1")
        self.assertEqual(csv_rows[0],list(xlsx_rows[0]))
