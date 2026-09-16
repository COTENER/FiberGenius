"""Contrato de navegación: agrupar sin modificar procesadores ni permisos."""
from html.parser import HTMLParser
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from .models import InventarioFibra, InventarioODF, LoteImportacion
from .views.importacion import obtener_procesadores_importacion


class Etiquetas(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elementos = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elementos.append((tag, dict(attrs)))


class NavegacionImportacionesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_superuser(username='nav-imports')

    def setUp(self):
        self.client.force_login(self.usuario)
        self.response = self.client.get(reverse('configuracion'))
        self.tags = Etiquetas(self.response.content.decode()).elementos

    def test_cuatro_areas_cerradas_al_inicio(self):
        areas = [a for tag, a in self.tags if tag == 'details' and 'data-import-area' in a]
        self.assertEqual([a['data-import-area'] for a in areas], ['infraestructura', 'fibras', 'recorrido', 'complementos'])
        self.assertTrue(all('open' not in a for a in areas))
        self.assertNotContains(self.response, 'modeToggle')
        self.assertNotContains(self.response, 'Esencial tras cualquier cambio')

    def test_diez_plantillas_sin_ocultar_procesadores_anteriores(self):
        tipos = [a['data-import-type'] for _, a in self.tags if 'data-import-type' in a]
        self.assertEqual(len(tipos), 10)
        self.assertEqual(len(set(tipos)), 10)
        todos = {a['data-open-import'] for _, a in self.tags if 'data-open-import' in a}
        self.assertEqual(todos, set(obtener_procesadores_importacion()))

    def test_botones_accesibles_y_modal_etiquetado(self):
        self.assertTrue(all(tag == 'button' and a.get('type') == 'button'
                            for tag, a in self.tags if 'data-open-import' in a))
        modal = next(a for _, a in self.tags if a.get('id') == 'importModal')
        self.assertEqual(modal['role'], 'dialog')
        self.assertEqual(modal['aria-labelledby'], 'importModalTitle')
        self.assertEqual(modal['aria-modal'], 'true')

    def test_inicio_rapido_no_exige_cargas_intermedias(self):
        self.assertContains(self.response, 'No necesitas cargar antes Sites, puertos vacíos ni fibras globales')
        self.assertContains(self.response, 'Si el ODF ya existe, comienza por el paso 2')
        guide = next(a for _, a in self.tags if a.get('id') == 'odfConnectionGuide')
        self.assertIn('hidden', guide)

    def test_lectura_no_crea_inventario_ni_lotes(self):
        self.assertEqual(InventarioFibra.objects.count(), 0)
        self.assertEqual(InventarioODF.objects.count(), 0)
        self.assertEqual(LoteImportacion.objects.count(), 0)

    def test_integraciones_y_operaciones_separadas(self):
        advanced = next(a for tag, a in self.tags if tag == 'details' and a.get('id') == 'advancedImports')
        self.assertNotIn('open', advanced)
        self.assertContains(self.response, 'Avanzado / Integraciones')
        self.assertContains(self.response, 'no son puertos ODF')
        self.assertContains(self.response, reverse('planta_interna'))
        self.assertContains(self.response, reverse('plantilla_terminaciones_fibra'))

    def test_conserva_progreso_y_validacion_sin_promesas_falsas(self):
        self.assertContains(self.response, 'id="importExecutionPercent"')
        self.assertContains(self.response, 'Validar archivo')
        self.assertContains(self.response, 'Las cabeceras, alias, datos y relaciones se validan únicamente en servidor')
        self.assertContains(self.response, '/validar-csv/')
        self.assertContains(self.response, 'if (importBusy)')
        self.assertContains(self.response, 'fiberGeniusActiveImport')

    def test_recursos_locales_disponibles(self):
        for recurso in ('css/import-navigation.css', 'js/import-navigation.js',
                        'ejemplos/plantilla_odfs.csv', 'ejemplos/plantilla_terminaciones_simplificada.csv'):
            self.assertIsNotNone(finders.find(recurso), recurso)

    def test_usuario_sin_permisos_no_gana_acceso_a_cargar(self):
        limitado = get_user_model().objects.create_user(username='nav-reader')
        self.client.force_login(limitado)
        response = self.client.get(reverse('configuracion'))
        self.assertEqual(response.status_code, 403)
        response = self.client.post(reverse('cargar_csv', args=['odf_inventario']))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(LoteImportacion.objects.exists())
