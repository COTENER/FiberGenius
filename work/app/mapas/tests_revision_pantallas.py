"""Regresiones de Dashboard, filtros compartidos y desconexión desde GUI."""
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from .models import (
    AuditoriaPuertoODF, CoordenadaRuta, DetallePuertoODF, HubSite,
    InventarioFibra, InventarioODF, InventarioTramo, NodoRed, RackFisico,
    Reserva, Ruta, SalaTecnica, TerminacionFibra,
)
from .services.puertos import conectar_puerto, desconectar_puerto
from .views.inventario import (
    dashboard_inventario, get_datos_inventario, gestionar_conexion_puerto, inventario_externo,
)
from .views.operacion_inventario import (
    _ficha_site, _filtro_elementos, _filtro_fibras, _filtro_puertos,
    _filtro_tramos, _filtro_troncales, _serializar_puertos, _serializar_troncales,
    api_mapa_site_navigation,
)


class RevisionPantallasTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create(username='revision-pantallas', is_superuser=True, is_staff=True)
        cls.site = HubSite.objects.create(nombre='SITE-A', latitud='-23', longitud='-69')
        sala = SalaTecnica.objects.create(hub_site=cls.site, nombre='SALA')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK')
        cls.odf = InventarioODF.objects.create(rack_obj=rack, odf='ODF-A', capacidad_puertos=2)
        cls.nodo = NodoRed.objects.create(tipo='ODF', codigo='ODF-A', odf_obj=cls.odf)
        cls.ruta = Ruta.objects.create(nombre='RUTA-A')
        cls.tramo = InventarioTramo.objects.create(ruta=cls.ruta, tramo_secuencia=1, origen_nodo=cls.nodo)

    def request(self, params=None):
        request = RequestFactory().get('/', params or {})
        request.user = self.user
        return request

    def dashboard(self, **params):
        with patch('mapas.views.inventario.render', side_effect=lambda request, template, context: context):
            return dashboard_inventario(self.request(params))

    def coordenadas(self, ruta=None, tipos=('AEREO', 'AEREO')):
        for i, tipo in enumerate(tipos, 1):
            CoordenadaRuta.objects.create(
                ruta=ruta or self.ruta, orden=i, latitud=str(-23-i), longitud='-69',
                tipo_trazado=tipo,
            )

    def test_dashboard_cuenta_troncal_sin_tramos_ni_geografia(self):
        Ruta.objects.create(nombre='RECIEN-CARGADA')
        contexto = self.dashboard()
        self.assertEqual(contexto['total_rutas'], 2)
        self.assertEqual(_filtro_troncales(self.request()).count(), 2)
        self.assertEqual(contexto['total_tramos'], 1)
        self.assertEqual(contexto['total_fibras'], 0)

    def test_destino_multitramo_es_el_ultimo(self):
        InventarioTramo.objects.filter(pk=self.tramo.pk).update(destino='B')
        InventarioTramo.objects.create(ruta=self.ruta, tramo_secuencia=2, origen='B', destino='C')
        self.assertEqual(self.dashboard()['resumen_rutas'][0]['destino'], 'C')
        InventarioTramo.objects.create(ruta=self.ruta, tramo_secuencia=3, origen='C', destino='D')
        self.assertEqual(self.dashboard()['resumen_rutas'][0]['destino'], 'D')

    def test_destino_final_faltante_no_reutiliza_un_destino_intermedio(self):
        InventarioTramo.objects.filter(pk=self.tramo.pk).update(destino='B')
        InventarioTramo.objects.create(ruta=self.ruta, tramo_secuencia=2, origen='B')
        self.assertEqual(self.dashboard()['resumen_rutas'][0]['destino'], 'N/A')

    def test_site_sigue_extremo_odf_en_dashboard_troncales_y_tramos(self):
        request = self.request({'site': self.site.nombre})
        self.assertEqual(self.dashboard(site=self.site.nombre)['total_rutas'], 1)
        self.assertEqual(_filtro_troncales(request).count(), 1)
        self.assertEqual(_filtro_tramos(request).count(), 1)
        self.assertEqual(_filtro_tramos(self.request({'site': 'OTRO'})).count(), 0)

    def test_site_odf_destino_y_textos_legacy(self):
        InventarioTramo.objects.filter(pk=self.tramo.pk).update(origen_nodo=None, destino_nodo=self.nodo)
        self.assertEqual(_filtro_tramos(self.request({'site': 'site-a'})).count(), 1)
        InventarioTramo.objects.filter(pk=self.tramo.pk).update(destino_nodo=None, hub_site='SITE-A')
        self.assertEqual(_filtro_tramos(self.request({'site': 'SITE-A'})).count(), 1)

    def test_site_selecciona_troncal_sin_recortar_su_cantidad_de_tramos(self):
        InventarioTramo.objects.create(ruta=self.ruta, tramo_secuencia=2, origen='B', destino='C')
        filas = _serializar_troncales(_filtro_troncales(self.request({'site': 'SITE-A'})))
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['tramos'], 2)

    def test_site_oficial_no_se_duplica_por_texto_legacy_desactualizado(self):
        InventarioTramo.objects.filter(pk=self.tramo.pk).update(hub_site='SITE-ANTIGUO', origen='SITE-ANTIGUO')
        self.assertEqual(_filtro_tramos(self.request({'site': 'SITE-A'})).count(), 1)
        self.assertEqual(_filtro_tramos(self.request({'site': 'SITE-ANTIGUO'})).count(), 0)

    def test_site_en_fibras_elementos_ficha_y_navegacion(self):
        InventarioFibra.objects.create(ruta=self.ruta, fibra_numero='F1')
        Reserva.objects.create(ruta=self.ruta, nombre='MUFA', latitud='-23', longitud='-69')
        request = self.request({'site': self.site.nombre})
        self.assertEqual(_filtro_fibras(request).count(), 1)
        self.assertEqual(_filtro_elementos(request).count(), 1)
        payload = json.loads(api_mapa_site_navigation(request, self.site.pk).content)
        self.assertEqual(payload['site']['troncales'], 1)
        self.assertIn('RUTA-A', str(_ficha_site(self.site.pk)))

    def test_trazado_geografico_prevalece_en_ambas_pantallas(self):
        InventarioTramo.objects.filter(pk=self.tramo.pk).update(tipo_trazado='SOTERRADO')
        self.coordenadas()
        for tipo, esperado in [('AEREO', 1), ('SOTERRADO', 0), ('HIBRIDO', 0)]:
            self.assertEqual(self.dashboard(trazado=tipo)['total_rutas'], esperado)
            self.assertEqual(_filtro_troncales(self.request({'tipo': tipo})).count(), esperado)
        filas = _serializar_troncales(_filtro_troncales(self.request()))
        self.assertEqual(filas[0]['tipo'], 'AÉREO')

    def test_hibrido_geografico_y_componentes_coinciden(self):
        self.coordenadas(tipos=('AEREO', 'SOTERRADO'))
        for tipo in ('AEREO', 'SOTERRADO', 'HIBRIDO'):
            self.assertEqual(self.dashboard(trazado=tipo)['total_rutas'], 1)
            self.assertEqual(_filtro_troncales(self.request({'tipo': tipo})).count(), 1)
        self.assertEqual(_serializar_troncales(_filtro_troncales(self.request()))[0]['tipo'], 'HÍBRIDO')

    def test_selector_trazado_incluye_geografia_y_mezcla_tecnica(self):
        self.coordenadas()
        with patch('mapas.views.inventario.render', side_effect=lambda r, t, c: c):
            self.assertIn('AEREO', inventario_externo(self.request())['tipos_trazado'])
        InventarioTramo.objects.filter(pk=self.tramo.pk).update(tipo_trazado='AEREO')
        InventarioTramo.objects.create(ruta=self.ruta, tramo_secuencia=2, tipo_trazado='SOTERRADO')
        with patch('mapas.views.inventario.render', side_effect=lambda r, t, c: c):
            self.assertIn('HIBRIDO', inventario_externo(self.request())['tipos_trazado'])

    def test_sin_geografia_usa_tramos_sin_duplicar_conteos(self):
        InventarioTramo.objects.filter(pk=self.tramo.pk).update(tipo_trazado='AEREO')
        InventarioTramo.objects.create(ruta=self.ruta, tramo_secuencia=2, tipo_trazado='SOTERRADO')
        self.assertEqual(self.dashboard(trazado='HIBRIDO')['total_rutas'], 1)
        self.assertEqual(_filtro_troncales(self.request({'tipo': 'Híbrido'})).count(), 1)

    def test_mapa_filtra_site_y_sin_filtro_conserva_toda_la_red(self):
        HubSite.objects.create(nombre='SITE-B', latitud='-30', longitud='-70')
        otra = Ruta.objects.create(nombre='RUTA-B')
        InventarioTramo.objects.create(ruta=otra, tramo_secuencia=1, hub_site='SITE-B')
        self.coordenadas()
        self.coordenadas(ruta=otra)
        payload = json.loads(get_datos_inventario(self.request({'site': 'SITE-A'})).content)
        self.assertEqual([r['nombre'] for r in payload['data']], ['RUTA-A'])
        self.assertEqual([s['nombre'] for s in payload['sites']], ['SITE-A'])
        completo = json.loads(get_datos_inventario(self.request()).content)
        self.assertEqual(len(completo['data']), 2)
        self.assertEqual(len(completo['sites']), 2)

    def test_mapa_site_sin_troncales_sigue_visible(self):
        payload = json.loads(get_datos_inventario(self.request({'site': self.site.nombre})).content)
        self.assertEqual(payload['data'], [])
        self.assertEqual(len(payload['sites']), 1)

    def conexion(self, numero='F1'):
        fibra = InventarioFibra.objects.create(
            ruta=self.ruta, fibra_numero=numero, estado='OCUPADO', origen_estado='INFORMADO',
        )
        puerto, _ = DetallePuertoODF.objects.get_or_create(
            odf_obj=self.odf, puerto_odf='1', defaults={'estado_puerto': 'LIBRE'},
        )
        terminacion, _ = conectar_puerto(puerto_id=puerto.pk, fibra_id=fibra.pk, extremo='A')
        return fibra, puerto, terminacion

    def desconectar_gui(self, puerto, **extra):
        request = RequestFactory().post('/', json.dumps({
            'accion': 'desconectar', 'puerto_id': puerto.pk, **extra,
        }), content_type='application/json')
        request.user = self.user
        return gestionar_conexion_puerto(request)

    def test_gui_exige_identidad_y_serializador_la_entrega(self):
        _, puerto, terminacion = self.conexion()
        fila = _serializar_puertos(list(_filtro_puertos(self.request())))[0]
        self.assertEqual(fila['conexion']['terminacion_id'], terminacion.pk)
        self.assertEqual(self.desconectar_gui(puerto).status_code, 400)
        self.assertTrue(TerminacionFibra.objects.filter(pk=terminacion.pk).exists())

    def test_gui_desconecta_la_terminacion_confirmada(self):
        fibra, puerto, terminacion = self.conexion()
        respuesta = self.desconectar_gui(puerto, terminacion_esperada=terminacion.pk)
        self.assertEqual(respuesta.status_code, 200)
        puerto.refresh_from_db()
        fibra.refresh_from_db()
        self.assertEqual(puerto.estado_puerto, 'LIBRE')
        self.assertEqual(fibra.estado, 'OCUPADO')

    def test_pantalla_antigua_no_desconecta_otra_fibra_ni_cambia_su_estado(self):
        _, puerto, anterior = self.conexion()
        anterior_id = anterior.pk
        desconectar_puerto(puerto_id=puerto.pk)
        fibra_actual, _, actual = self.conexion('F2')
        auditorias = AuditoriaPuertoODF.objects.count()
        respuesta = self.desconectar_gui(
            puerto, terminacion_esperada=anterior_id, sincronizar_fibra=True,
        )
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(json.loads(respuesta.content)['code'], 'CONEXION_DESACTUALIZADA')
        self.assertTrue(TerminacionFibra.objects.filter(pk=actual.pk, puerto_odf=puerto).exists())
        fibra_actual.refresh_from_db()
        puerto.refresh_from_db()
        self.assertEqual(fibra_actual.estado, 'OCUPADO')
        self.assertEqual(puerto.estado_puerto, 'OCUPADO')
        self.assertEqual(AuditoriaPuertoODF.objects.count(), auditorias)

    def test_pantalla_antigua_detecta_conexion_ya_liberada(self):
        _, puerto, terminacion = self.conexion()
        terminacion_id = terminacion.pk
        desconectar_puerto(puerto_id=puerto.pk)
        respuesta = self.desconectar_gui(puerto, terminacion_esperada=terminacion_id)
        self.assertEqual(respuesta.status_code, 409)
        self.assertFalse(TerminacionFibra.objects.filter(puerto_odf=puerto).exists())

    def test_distancia_no_pierde_aristas_en_cortes_visuales(self):
        import pandas as pd
        from .views.importacion import _reemplazar_geografia_ruta
        from .services.capacidad import resumen_ruta

        for tipos, cortes in [
            (['AEREO'] * 4, ['false'] * 4),
            (['AEREO', 'AEREO', 'SOTERRADO', 'SOTERRADO'], ['false'] * 4),
            (['AEREO', 'SOTERRADO', 'HIBRIDO', 'AEREO'], ['false'] * 4),
            (['AEREO'] * 4, ['true'] * 4),
        ]:
            with self.subTest(tipos=tipos, cortes=cortes):
                _reemplazar_geografia_ruta(self.ruta, pd.DataFrame({
                    'latitude': ['-23', '-24', '-25', '-26'],
                    'longitude': ['-69'] * 4,
                    'tipo_trazado': tipos, 'new_seg': cortes,
                }))
                self.ruta.refresh_from_db()
                resumen = resumen_ruta(self.ruta)
                self.assertEqual(resumen['fuente_distancia'], 'calculado_geometria')
                self.assertAlmostEqual(resumen['distancia_m'], self.ruta.distancia_m, places=5)
                self.assertGreater(resumen['distancia_m'], 333000)
                self.assertEqual(self.ruta.coordenadas.count(), 4)

    def test_distancia_declarada_y_tecnica_mantienen_precedencia(self):
        from .services.capacidad import resumen_ruta
        self.coordenadas(tipos=('AEREO', 'SOTERRADO'))
        InventarioTramo.objects.filter(pk=self.tramo.pk).update(distancia_m=100)
        self.assertEqual(resumen_ruta(self.ruta)['distancia_m'], 100)
        self.ruta.distancia_declarada_m = 123
        self.ruta.save(update_fields=['distancia_declarada_m'])
        self.assertEqual(resumen_ruta(self.ruta)['distancia_m'], 123)

    def test_busqueda_y_estado_no_recortan_contadores_de_troncal(self):
        InventarioTramo.objects.filter(pk=self.tramo.pk).update(estado='OPERATIVO', hub_site='BUSQUEDA-EXTREMO')
        InventarioTramo.objects.create(ruta=self.ruta, tramo_secuencia=2, estado='MANTENIMIENTO')
        fibras = [InventarioFibra.objects.create(ruta=self.ruta, fibra_numero=f'F{i}') for i in (1, 2, 3)]
        for i in (1, 2):
            Reserva.objects.create(ruta=self.ruta, nombre=f'RESERVA-{i}', latitud='-23', longitud='-69')
        for params in ({}, {'estado': 'OPERATIVO'}, {'q': 'BUSQUEDA-EXTREMO'}, {'q': fibras[0].codigo_fibra}):
            with self.subTest(params=params):
                rows = _serializar_troncales(_filtro_troncales(self.request(params)))
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]['tramos'], rows[0]['fibras'], rows[0]['reservas']), (2, 3, 2))

    def test_busqueda_sin_site_permite_completar_fibra_desde_otro_site(self):
        site_b = HubSite.objects.create(nombre='SITE-B')
        sala_b = SalaTecnica.objects.create(hub_site=site_b, nombre='SALA')
        rack_b = RackFisico.objects.create(sala=sala_b, nombre='RACK')
        odf_b = InventarioODF.objects.create(rack_obj=rack_b, odf='ODF-B', capacidad_puertos=1)
        puerto_b = DetallePuertoODF.objects.create(odf_obj=odf_b, puerto_odf='1', estado_puerto='LIBRE')
        fibra = InventarioFibra.objects.create(fibra_numero='F7')
        conectar_puerto(puerto_id=puerto_b.pk, fibra_id=fibra.pk, extremo='B')
        self.assertEqual(_filtro_fibras(self.request({'site': 'SITE-A', 'q': fibra.codigo_fibra})).count(), 0)
        self.assertEqual(_filtro_fibras(self.request({'site': '', 'q': fibra.codigo_fibra})).count(), 1)
        puerto_a = DetallePuertoODF.objects.create(odf_obj=self.odf, puerto_odf='1', estado_puerto='LIBRE')
        conectar_puerto(puerto_id=puerto_a.pk, fibra_id=fibra.pk, extremo='A')
        self.assertEqual(fibra.terminaciones.count(), 2)
        fibra.refresh_from_db()
        self.assertIsNone(fibra.ruta_id)

    def test_exportaciones_conservan_contrato_para_descarga_segura(self):
        from .views.operacion_inventario import exportar_inventario
        for recurso in ('odfs', 'puertos', 'fibras', 'troncales', 'tramos', 'elementos'):
            for formato, mime in [('csv', 'text/csv'), ('xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')]:
                with self.subTest(recurso=recurso, formato=formato):
                    response = exportar_inventario(self.request(), recurso, formato)
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response['Content-Type'].startswith(mime))
                    self.assertTrue(response['Content-Disposition'].startswith('attachment;'))
