from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Ruta


class TroncalesSinTramosTests(TestCase):
    def setUp(self):
        self.usuario = get_user_model().objects.create_superuser(
            username='admin-troncales-sin-tramos',
            email='admin@example.invalid',
            password='clave-pruebas',
        )
        self.client.force_login(self.usuario)

    def test_troncal_importada_aparece_antes_de_cargar_tramos(self):
        ruta = Ruta.objects.create(
            nombre='TRONCAL-PENDIENTE-TRAMOS',
            distancia_m=1250,
        )

        respuesta = self.client.get(
            reverse('api_troncales_paginadas'),
            {'q': ruta.nombre, 'page_size': 10},
        )

        self.assertEqual(respuesta.status_code, 200)
        payload = respuesta.json()
        self.assertEqual(payload['pagination']['total'], 1)
        self.assertEqual(payload['summary']['total'], 1)
        self.assertEqual(payload['summary']['tramos'], 0)
        self.assertEqual(payload['data'][0]['nombre'], ruta.nombre)
        self.assertEqual(payload['data'][0]['tramos'], 0)
        self.assertEqual(
            payload['data'][0]['estado'],
            'Sin tramos cargados',
        )

    def test_selector_de_troncales_incluye_rutas_sin_tramos(self):
        ruta = Ruta.objects.create(nombre='TRONCAL-SOLO-RUTA')

        respuesta = self.client.get(reverse('inventario_externo'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn(
            {'id': ruta.pk, 'nombre': ruta.nombre},
            respuesta.context['rutas_inventario'],
        )
