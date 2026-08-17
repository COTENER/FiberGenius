from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import HubSite, InventarioFibra, Ruta
from .views.importacion import (
    _procesar_fibras_globales,
    _procesar_sites_inventario,
)


def _csv(nombre, contenido):
    return SimpleUploadedFile(
        nombre,
        contenido.encode('utf-8'),
        content_type='text/csv',
    )


class ImportadoresGuiadosTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_superuser(
            'admin-importacion-guiada',
            'guiada@example.test',
            'clave-segura',
        )
        self.client.force_login(self.usuario)

    def test_sites_crea_actualiza_y_admite_coordenadas_opcionales(self):
        resultado = _procesar_sites_inventario(_csv(
            'sites.csv',
            'Nombre,Direccion,Latitud,Longitud\n'
            'SITE-A,Direccion A,-12.046374,-77.042793\n'
            'SITE-B,,,\n',
        ))

        self.assertEqual(resultado['creadas'], 2)
        self.assertIsNone(HubSite.objects.get(nombre='SITE-B').latitud)

        repeticion = _procesar_sites_inventario(_csv(
            'sites.csv',
            'Nombre,Direccion,Latitud,Longitud\n'
            'SITE-A,Direccion actualizada,-12.100000,-77.100000\n',
        ))
        self.assertEqual(repeticion['actualizadas'], 1)
        self.assertEqual(
            HubSite.objects.get(nombre='SITE-A').direccion,
            'Direccion actualizada',
        )

    def test_sites_rechaza_coordenada_incompleta_sin_escribir(self):
        with self.assertRaisesMessage(ValueError, 'deben informarse juntas'):
            _procesar_sites_inventario(_csv(
                'sites.csv',
                'Nombre,Latitud,Longitud\nSITE-INVALIDO,-12.1,\n',
            ))
        self.assertFalse(HubSite.objects.filter(nombre='SITE-INVALIDO').exists())

    def test_fibra_global_usa_codigo_estable_y_troncal_opcional(self):
        resultado = _procesar_fibras_globales(_csv(
            'fibras.csv',
            'Codigo Fibra,Fibra,Estado,Condicion,Servicio\n'
            'FGF-0001,F1,Ocupado,Operativa,Servicio principal\n',
        ))

        self.assertEqual(resultado['creadas'], 1)
        fibra = InventarioFibra.objects.get(codigo_fibra='FGF-0001')
        self.assertIsNone(fibra.ruta_id)
        self.assertEqual(fibra.estado, 'OCUPADO')
        self.assertEqual(fibra.origen_estado, 'INFORMADO')

    def test_fibra_global_se_asocia_despues_sin_duplicarse(self):
        ruta = Ruta.objects.create(nombre='TRONCAL-01')
        _procesar_fibras_globales(_csv(
            'fibras.csv',
            'Codigo Fibra,Fibra,Estado\nFGF-0002,F2,Sin informacion\n',
        ))
        resultado = _procesar_fibras_globales(_csv(
            'fibras.csv',
            'Codigo Fibra,Fibra,Estado,Ruta\n'
            'FGF-0002,F2,Disponible,TRONCAL-01\n',
        ))

        self.assertEqual(resultado['actualizadas'], 1)
        self.assertEqual(InventarioFibra.objects.count(), 1)
        fibra = InventarioFibra.objects.get(codigo_fibra='FGF-0002')
        self.assertEqual(fibra.ruta, ruta)
        self.assertEqual(fibra.estado, 'DISPONIBLE')

    def test_fibra_global_no_acepta_posicion_fisica_como_codigo(self):
        with self.assertRaisesMessage(ValueError, 'Codigo Fibra debe ser global'):
            _procesar_fibras_globales(_csv(
                'fibras.csv',
                'Codigo Fibra,Fibra,Estado\nF1,F1,Disponible\n',
            ))

    def test_configuracion_muestra_flujo_plantillas_y_validacion(self):
        respuesta = self.client.get(reverse('configuracion'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Importación guiada del inventario')
        self.assertContains(respuesta, 'Cargar inventario global de fibras')
        self.assertContains(respuesta, 'Descargar plantilla vacía')
        self.assertContains(respuesta, 'Validar archivo')
        self.assertContains(respuesta, 'plantilla_sites.csv')
        self.assertContains(respuesta, 'plantilla_fibras_globales.csv')
