"""Dashboard de calidad: conteos, permisos, filtros y lectura sin mutaciones."""
from unittest.mock import patch
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.models import AnonymousUser, Permission, User
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from . import tests_revision_pantallas as fixtures
from .models import (
    DetallePuertoODF, FibraTramo, HubSite, InventarioFibra,
    InventarioODF, InventarioTramo, RackFisico, Ruta, SalaTecnica,
)
from .services.calidad_dashboard import controles_calidad
from .services.puertos import conectar_puerto
from .views.calidad_informacion import _resumen_visual, calidad_informacion


class CalidadDashboardTests(TestCase):
    setUpTestData = classmethod(fixtures.RevisionPantallasTests.setUpTestData.__func__)
    coordenadas = fixtures.RevisionPantallasTests.coordenadas

    def request(self, params=None, user=None, method='get'):
        request = getattr(RequestFactory(), method)(reverse('calidad_informacion'), params or {})
        request.user = user if user is not None else self.user
        request.resolver_match = resolve(request.path)
        return request

    def contexto(self, **params):
        with patch('mapas.views.calidad_informacion.render', side_effect=lambda r, t, c: c):
            return calidad_informacion(self.request(params))

    def ids(self, codigo, site='', user=None):
        control = next(c for c in controles_calidad(user or self.user, site) if c.codigo == codigo)
        return set(control.queryset.values_list('pk', flat=True))

    def fibra(self, numero='F1', **kwargs):
        return InventarioFibra.objects.create(fibra_numero=numero, **kwargs)

    def puerto(self, numero='1'):
        return DetallePuertoODF.objects.create(odf_obj=self.odf, puerto_odf=numero)

    def test_carga_parcial_es_pendiente_no_inconsistencia(self):
        fibra = self.fibra()
        self.assertEqual(self.ids('fibras_estado'), {fibra.pk})
        self.assertEqual(self.ids('fibras_troncal'), {fibra.pk})
        self.assertEqual(self.ids('fibras_terminaciones'), {fibra.pk})
        self.assertEqual(self.ids('fibras_recorrido'), set())
        self.assertEqual(self.contexto()['total_inconsistencias'], 0)

    def test_estado_informado_y_falla_fisica_no_son_error_de_datos(self):
        self.fibra(estado='OCUPADO', origen_estado='INFORMADO', condicion_fisica='CON_FALLA')
        self.assertEqual(self.ids('fibras_estado'), set())
        self.assertEqual(self.contexto()['total_inconsistencias'], 0)

    def test_terminacion_parcial_y_completa(self):
        fibra = self.fibra()
        conectar_puerto(puerto_id=self.puerto().pk, fibra_id=fibra.pk, extremo='A')
        self.assertEqual(self.ids('fibras_terminaciones'), {fibra.pk})
        conectar_puerto(puerto_id=self.puerto('2').pk, fibra_id=fibra.pk, extremo='B')
        self.assertEqual(self.ids('fibras_terminaciones'), set())
        self.assertEqual(self.ids('puertos_conexion'), set())

    def test_site_no_duplica_fibra_con_ambos_extremos(self):
        fibra = self.fibra()
        for extremo, numero in [('A', '1'), ('B', '2')]:
            conectar_puerto(puerto_id=self.puerto(numero).pk, fibra_id=fibra.pk, extremo=extremo)
        c = self.contexto(site='site-a', control='fibras_troncal')
        self.assertEqual(c['site'], 'SITE-A')
        self.assertEqual(c['seleccionado']['total'], 1)
        self.assertEqual(len(c['registros']), 1)
        self.assertIn(fibra.codigo_fibra, c['registros'][0]['nombre'])

    def test_fibra_sin_ubicacion_se_muestra_solo_sin_filtro(self):
        fibra = self.fibra()
        self.assertEqual(self.ids('fibras_troncal'), {fibra.pk})
        self.assertEqual(self.ids('fibras_troncal', 'SITE-A'), set())

    def test_recorrido_uno_dos_y_tres_tramos_no_autovincula(self):
        fibra = self.fibra(ruta=self.ruta)
        for numero in range(1, 4):
            tramo = self.tramo if numero == 1 else InventarioTramo.objects.create(ruta=self.ruta, tramo_secuencia=numero)
            self.assertEqual(self.ids('fibras_recorrido'), {fibra.pk})
            FibraTramo.objects.create(tramo=tramo, fibra=fibra, numero_hilo='F1')
            self.assertEqual(self.ids('fibras_recorrido'), set())
        self.assertEqual(FibraTramo.objects.count(), 3)

    def test_hilo_no_vinculado_no_cubre_recorrido_y_no_mezcla_fibras(self):
        f1 = self.fibra('F1', ruta=self.ruta)
        f2 = self.fibra('F2', ruta=self.ruta)
        FibraTramo.objects.create(tramo=self.tramo, numero_hilo='F1', fibra=f1)
        FibraTramo.objects.create(tramo=self.tramo, numero_hilo='F2')
        self.assertEqual(self.ids('fibras_recorrido'), {f2.pk})

    def test_sin_tramos_no_se_confunde_con_recorrido_parcial(self):
        ruta = Ruta.objects.create(nombre='SIN-TRAMOS')
        self.fibra(ruta=ruta)
        self.assertEqual(self.ids('troncales_tramos'), {ruta.pk})
        self.assertEqual(self.ids('fibras_recorrido'), set())

    def test_base_impide_troncal_ajena_y_catalogo_detecta_referencia_sin_ruta(self):
        fibra = self.fibra(ruta=self.ruta)
        FibraTramo.objects.create(tramo=self.tramo, fibra=fibra, numero_hilo='F1')
        otra = Ruta.objects.create(nombre='OTRA')
        # No se desactivan guardas: cambiar a otra troncal está protegido.
        with self.assertRaises(IntegrityError), transaction.atomic():
            InventarioFibra.objects.filter(pk=fibra.pk).update(ruta=otra)
        self.assertEqual(self.ids('fibras_tramos_ajenos'), set())
        # El UPDATE directo a NULL sí admite simular un registro legado aislado.
        # El dashboard lo debe detectar sin intentar repararlo.
        InventarioFibra.objects.filter(pk=fibra.pk).update(ruta=None)
        self.assertEqual(self.ids('fibras_tramos_ajenos'), {fibra.pk})

    def test_geografia_cero_uno_y_dos_puntos(self):
        self.assertEqual(self.ids('troncales_geografia'), {self.ruta.pk})
        self.coordenadas(tipos=('AEREO',))
        self.assertEqual(self.ids('troncales_geografia'), {self.ruta.pk})
        self.ruta.coordenadas.all().delete()
        self.coordenadas()
        self.assertEqual(self.ids('troncales_geografia'), set())

    def test_puertos_ausentes_no_se_deducen_del_cache(self):
        self.puerto()
        InventarioODF.objects.filter(pk=self.odf.pk).update(puertos_libres=999)
        c = self.contexto(control='odfs_puertos')
        self.assertEqual(c['seleccionado']['total'], 1)
        self.assertIn('Por registrar: 1', c['registros'][0]['detalle'])
        self.puerto('2')
        self.assertEqual(self.ids('odfs_puertos'), set())

    def test_exceso_de_capacidad_y_capacidad_no_informada(self):
        self.puerto()
        self.puerto('2')
        InventarioODF.objects.filter(pk=self.odf.pk).update(capacidad_puertos=1)
        self.assertEqual(self.ids('odfs_capacidad'), {self.odf.pk})
        InventarioODF.objects.filter(pk=self.odf.pk).update(capacidad_puertos=0)
        self.assertEqual(self.ids('odfs_capacidad'), set())

    def test_ubicacion_sin_asignar_es_pendiente_no_site_sin_coordenadas(self):
        site = HubSite.objects.create(nombre='SIN ASIGNAR')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SIN ASIGNAR')
        rack = RackFisico.objects.create(sala=sala, nombre='SIN ASIGNAR')
        odf = InventarioODF.objects.create(rack_obj=rack, odf='ODF-PENDIENTE')
        self.assertEqual(self.ids('odfs_ubicacion'), {odf.pk})
        self.assertEqual(self.ids('sites_coordenadas'), set())

    def test_coordenadas_cero_validas_y_longitud_faltante(self):
        HubSite.objects.create(nombre='CERO', latitud=0, longitud=0)
        parcial = HubSite.objects.create(nombre='PARCIAL', latitud=0)
        self.assertEqual(self.ids('sites_coordenadas'), {parcial.pk})

    def test_puerto_ocupado_sin_terminacion_y_conectado_libre(self):
        puerto = self.puerto()
        DetallePuertoODF.objects.filter(pk=puerto.pk).update(estado_puerto='OCUPADO')
        self.assertEqual(self.ids('puertos_conexion'), {puerto.pk})
        DetallePuertoODF.objects.filter(pk=puerto.pk).update(estado_puerto='LIBRE')
        conectar_puerto(puerto_id=puerto.pk, fibra_id=self.fibra().pk, extremo='A')
        DetallePuertoODF.objects.filter(pk=puerto.pk).update(estado_puerto='LIBRE')
        self.assertEqual(self.ids('puertos_conexion'), {puerto.pk})

    def test_usuario_solo_odf_no_ve_conteos_de_fibras_ni_puertos(self):
        viewer = User.objects.create(username='solo-odf')
        viewer.user_permissions.add(Permission.objects.get(codename='view_inventarioodf'))
        self.assertEqual([c.codigo for c in controles_calidad(viewer)], ['odfs_ubicacion'])
        response = calidad_informacion(self.request(user=viewer))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Fibras sin estado informado')
        with self.assertRaises(Http404):
            calidad_informacion(self.request({'control': 'fibras_estado'}, user=viewer))

    def test_autenticacion_permiso_y_metodo(self):
        self.assertEqual(calidad_informacion(self.request(user=AnonymousUser())).status_code, 302)
        with self.assertRaises(PermissionDenied):
            calidad_informacion(self.request(user=User.objects.create(username='sin-permisos')))
        self.assertEqual(calidad_informacion(self.request(method='post')).status_code, 405)
        with self.assertRaises(Http404):
            self.contexto(control='desconocido')

    def test_site_desconocido_no_muestra_todo(self):
        self.fibra()
        c = self.contexto(site='NO-EXISTE')
        self.assertTrue(c['site_desconocido'])
        self.assertEqual(c['total_pendientes'], 0)
        self.assertEqual(c['total_inconsistencias'], 0)

    def test_paginacion_busqueda_y_enlaces_conservan_contexto(self):
        for i in range(1, 28):
            self.fibra(f'F{i}', ruta=self.ruta)
        c = self.contexto(control='fibras_estado', site='SITE-A', q='RUTA-A')
        self.assertEqual(len(c['registros']), 25)
        params = parse_qs(urlsplit(c['siguiente']).query)
        self.assertEqual(params, {'site': ['SITE-A'], 'control': ['fibras_estado'], 'q': ['RUTA-A'], 'page': ['2']})
        segunda = self.contexto(control='fibras_estado', site='SITE-A', q='RUTA-A', page='2')
        self.assertEqual(len(segunda['registros']), 2)
        sin_resultados = self.contexto(control='fibras_estado', q='no-coincide')
        self.assertEqual(sin_resultados['registros'], [])
        self.assertEqual(sin_resultados['seleccionado']['total'], 27)

    def test_render_escapa_busqueda_y_muestra_ficha_y_menu(self):
        self.fibra()
        response = calidad_informacion(self.request({'control': 'fibras_estado'}))
        self.assertContains(response, 'data-open-asset-360')
        self.assertContains(response, 'css/calidad-informacion.css')
        self.assertContains(response, 'Calidad de información')
        self.assertContains(response, 'Consulta de solo lectura')
        response = calidad_informacion(self.request({'control': 'fibras_estado', 'q': '<b>texto</b>'}))
        self.assertContains(response, '&lt;b&gt;texto&lt;/b&gt;')
        self.assertNotContains(response, '<b>texto</b>')

    def test_consulta_no_escribe_y_no_hay_n_mas_uno(self):
        self.fibra()
        with CaptureQueriesContext(connection) as pocas:
            calidad_informacion(self.request({'control': 'fibras_estado'}))
        for i in range(2, 26):
            self.fibra(f'F{i}')
        with CaptureQueriesContext(connection) as muchas:
            calidad_informacion(self.request({'control': 'fibras_estado'}))
        self.assertEqual(len(pocas), len(muchas))
        self.assertLess(len(muchas), 35)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT ', 'UPDATE ', 'DELETE ')) for q in muchas))

    def test_cada_control_se_renderiza_incluso_sin_resultados(self):
        for control in controles_calidad(self.user):
            with self.subTest(control=control.codigo):
                self.assertEqual(calidad_informacion(self.request({'control': control.codigo})).status_code, 200)

    def test_vista_inicial_es_visual_y_no_consulta_detalle_por_defecto(self):
        self.fibra()
        contexto = self.contexto()
        self.assertIsNone(contexto['seleccionado'])
        self.assertIsNone(contexto['page_obj'])
        self.assertEqual(contexto['registros'], [])
        response = calidad_informacion(self.request())
        self.assertContains(response, 'class="quality-bars"')
        self.assertContains(response, 'class="quality-ring"')
        self.assertNotContains(response, '<table class="quality-table">')
        self.assertContains(response, 'Vista general')

    def test_enlaces_del_grafico_abren_el_control_y_conservan_site(self):
        self.fibra(ruta=self.ruta)
        contexto = self.contexto(site='SITE-A')
        for tarjeta in contexto['pendientes'] + contexto['inconsistencias']:
            enlace = urlsplit(tarjeta['url'])
            params = {k: v[0] for k, v in parse_qs(enlace.query).items()}
            self.assertEqual(params['site'], 'SITE-A')
            self.assertEqual(enlace.fragment, 'registros-calidad')
            detalle = self.contexto(**params)
            self.assertEqual(detalle['seleccionado']['control'].codigo, tarjeta['control'].codigo)
            self.assertEqual(detalle['seleccionado']['total'], tarjeta['total'])
            self.assertEqual(parse_qs(urlsplit(detalle['url_resumen']).query), {'site': ['SITE-A']})

    def test_anillo_cuenta_controles_no_observaciones(self):
        tarjetas = [
            {'control': SimpleNamespace(codigo='a', grupo='pendientes'), 'total': 20000},
            {'control': SimpleNamespace(codigo='b', grupo='pendientes'), 'total': 100},
            {'control': SimpleNamespace(codigo='c', grupo='inconsistencias'), 'total': 3},
            {'control': SimpleNamespace(codigo='d', grupo='inconsistencias'), 'total': 0},
        ]
        with self.assertNumQueries(0):
            visual = _resumen_visual(tarjetas)
        self.assertEqual(visual['total_pendientes'], 20100)
        self.assertEqual([s['total'] for s in visual['segmentos_controles']], [1, 2, 1])
        self.assertEqual([s['longitud'] for s in visual['segmentos_controles']], [25, 50, 25])
        self.assertEqual([s['desfase'] for s in visual['segmentos_controles']], [0, -25, -75])
        self.assertEqual([t['ancho_barra'] for t in visual['pendientes']], [100, 0.5])

    def test_graficos_vacios_no_dividen_por_cero_ni_inventan_barras(self):
        for tarjetas in ([], [{'control': SimpleNamespace(codigo='a', grupo='pendientes'), 'total': 0}]):
            visual = _resumen_visual(tarjetas)
            self.assertEqual(visual['total_pendientes'], 0)
            self.assertTrue(all(t['ancho_barra'] == 0 for t in visual['pendientes']))
            self.assertEqual(sum(s['total'] for s in visual['segmentos_controles']), len(tarjetas))

    def test_barras_ordenadas_descendentes_sin_perder_controles_cero(self):
        self.fibra()
        visual = self.contexto()
        totales = [t['total'] for t in visual['pendientes']]
        self.assertEqual(totales, sorted(totales, reverse=True))
        self.assertEqual(len(visual['pendientes']), 9)
        self.assertTrue(any(t['ancho_barra'] == 0 for t in visual['pendientes']))
        self.assertTrue(all(0 <= t['ancho_barra'] <= 100 for t in visual['pendientes']))
        self.assertAlmostEqual(sum(s['longitud'] for s in visual['segmentos_controles']), 100)

    def test_numeros_svg_no_se_localizan_con_coma_decimal(self):
        self.fibra()
        response = calidad_informacion(self.request())
        import re
        atributos = re.findall(r'(?:stroke-dasharray|stroke-dashoffset)="([^"]+)"', response.content.decode())
        self.assertTrue(atributos)
        self.assertTrue(all(',' not in valor for valor in atributos))

    def test_anillo_solo_incluye_controles_autorizados(self):
        lector = User.objects.create(username='calidad-solo-puertos')
        lector.user_permissions.add(Permission.objects.get(codename='view_detallepuertoodf'))
        response = calidad_informacion(self.request(user=lector))
        self.assertContains(response, '1 controles: 1 sin casos detectados')
        self.assertNotContains(response, 'Fibras sin estado informado')
        self.assertNotContains(response, 'stroke-dasharray="0 100"')
