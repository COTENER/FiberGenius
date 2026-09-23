"""Regresiones de disponibilidad y contexto del Dashboard; BD aislada."""
import csv
import io
import json
from urllib.parse import parse_qs, urlsplit

from django.test import TestCase

from . import tests_revision_pantallas as fixtures
from .models import DetallePuertoODF, InventarioFibra, InventarioODF, InventarioTramo, Ruta
from .views.inventario import dashboard_inventario, get_datos_inventario
from .views.operacion_inventario import _filtro_fibras, _filtro_odfs, exportar_inventario
from .services.puertos import conectar_puerto


class DashboardCorreccionesTests(TestCase):
    setUpTestData = classmethod(fixtures.RevisionPantallasTests.setUpTestData.__func__)
    request = fixtures.RevisionPantallasTests.request
    dashboard = fixtures.RevisionPantallasTests.dashboard
    coordenadas = fixtures.RevisionPantallasTests.coordenadas

    def test_capacidad_sin_puertos_no_es_disponibilidad(self):
        c = self.dashboard()
        self.assertEqual(c['total_puertos'], 0)
        self.assertEqual(c['odfs_con_disponibilidad'], 0)
        self.assertEqual(c['puertos_sin_registrar'], 2)
        self.assertEqual(c['top_odfs_capacidad'][0]['libres'], 0)
        self.assertEqual(_filtro_odfs(self.request({'capacidad': 'disponible'})).count(), 0)
        self.assertEqual(_filtro_odfs(self.request({'capacidad': 'sin_disponibilidad'})).count(), 1)

    def test_disponibilidad_usa_detalle_y_no_cache_ni_capacidad(self):
        DetallePuertoODF.objects.create(odf_obj=self.odf, puerto_odf='1', estado_puerto='LIBRE')
        InventarioODF.objects.filter(pk=self.odf.pk).update(puertos_libres=99)
        c = self.dashboard()
        self.assertEqual(c['puertos_libres'], 1)
        self.assertEqual(c['top_odfs_capacidad'][0]['libres'], 1)
        self.assertEqual(c['puertos_sin_registrar'], 1)
        self.assertEqual(_filtro_odfs(self.request({'capacidad': 'disponible'})).count(), 1)

    def test_site_no_depende_de_capitalizacion(self):
        upper = self.dashboard(site='SITE-A')
        lower = self.dashboard(site='site-a')
        for key in ('total_sites', 'total_odfs', 'total_rutas', 'total_fibras'):
            self.assertEqual(upper[key], lower[key])
        self.assertEqual(lower['site_seleccionado'], 'SITE-A')

    def test_mapa_resuelve_site_oficial_de_odf_y_descarta_texto_viejo(self):
        self.coordenadas()
        InventarioTramo.objects.filter(pk=self.tramo.pk).update(hub_site='OBSOLETO')
        data = json.loads(get_datos_inventario(self.request({'trazado': 'AEREO'})).content)
        self.assertEqual(data['data'][0]['sites_relacionados'], ['SITE-A'])
        self.assertEqual(len(data['sites']), 1)
        self.assertEqual(json.loads(get_datos_inventario(self.request({'trazado': 'SOTERRADO'})).content)['data'], [])

    def test_exportacion_conserva_site_y_trazado(self):
        self.coordenadas()
        InventarioFibra.objects.create(ruta=self.ruta, fibra_numero='F1')
        otra = Ruta.objects.create(nombre='OTRA')
        InventarioFibra.objects.create(ruta=otra, fibra_numero='F2')
        InventarioFibra.objects.create(fibra_numero='F3')
        c = self.dashboard(site='SITE-A', trazado='AEREO')
        params = {k: v[0] for k, v in parse_qs(urlsplit(c['dashboard_links']['export_fibras']).query).items()}
        self.assertEqual(params, {'site': 'SITE-A', 'trazado': 'AEREO'})
        request = self.request(params)
        self.assertEqual(_filtro_fibras(request).count(), c['total_fibras'])
        response = exportar_inventario(request, 'fibras', 'csv')
        body = b''.join(response.streaming_content).decode('utf-8-sig')
        self.assertEqual(len(list(csv.reader(io.StringIO(body)))) - 1, c['total_fibras'])
        self.assertNotIn('trazado', c['dashboard_links']['export_odfs'])

    def test_enlaces_y_presentacion_no_afirman_calidad(self):
        self.coordenadas()
        c = self.dashboard(site='SITE-A', trazado='AEREO')
        for key in ('fibras', 'reservas', 'mapa'):
            self.assertIn('trazado=AEREO', c['dashboard_links'][key])
        self.assertIn('tipo=AEREO', c['dashboard_links']['rutas'])
        self.assertIn('odf_id=', c['top_odfs_capacidad'][0]['url'])
        response = dashboard_inventario(self.request())
        self.assertContains(response, 'Inventario cargado')
        self.assertNotContains(response, 'Inventario operativo')
        self.assertNotContains(response, 'inv-data-pending')
        self.assertNotContains(response, 'Troncales sin geometría completa:')
        self.assertContains(response, 'dashboard-map-tile-warning')

    def test_combinacion_site_trazado_con_fibra_en_otra_troncal(self):
        otra = Ruta.objects.create(nombre='RUTA-B')
        InventarioTramo.objects.create(ruta=otra, tramo_secuencia=1, hub_site='SITE-B')
        self.coordenadas(ruta=otra)
        fibra = InventarioFibra.objects.create(ruta=otra, fibra_numero='F7')
        puerto = DetallePuertoODF.objects.create(odf_obj=self.odf, puerto_odf='1')
        conectar_puerto(puerto_id=puerto.pk, fibra_id=fibra.pk, extremo='A')
        params = {'site': 'SITE-A', 'trazado': 'AEREO'}
        self.assertEqual(_filtro_fibras(self.request(params)).count(), self.dashboard(**params)['total_fibras'])
        self.assertEqual(_filtro_fibras(self.request({'site': 'SITE-A'})).count(), 1)
