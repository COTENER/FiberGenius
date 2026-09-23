"""Respuestas transitorias seguras, sin reejecutar operaciones de inventario."""
import json
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError, connection
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .middleware import ActiveUserMiddleware, AuditSecurityMiddleware
from .models import DetallePuertoODF, HubSite, InventarioODF, RackFisico, SalaTecnica
from .services.errores_bd import es_bloqueo_sqlite


@skipUnless(connection.vendor == 'sqlite', 'Ensayo específico de bloqueos de SQLite')
class BloqueosSQLiteHttpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('lock-http', password='test-only-password')
        site = HubSite.objects.create(nombre='LOCK-SITE')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK')
        odf = InventarioODF.objects.create(rack_obj=rack, odf='LOCK-ODF', capacidad_puertos=1)
        cls.puerto = DetallePuertoODF.objects.create(odf_obj=odf, puerto_odf='1', observaciones='Original')

    def setUp(self):
        self.client.force_login(self.user)

    def editar(self):
        return self.client.post(reverse('api_update_puerto'),
            data=json.dumps({'id': self.puerto.pk, 'observaciones': 'Nueva'}), content_type='application/json')

    def test_bloqueo_devuelve_503_y_no_reintenta_guardar(self):
        with patch.object(DetallePuertoODF, 'save', side_effect=OperationalError('database is locked')) as save:
            response = self.editar()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 'BASE_DATOS_OCUPADA')
        self.assertEqual(response['Retry-After'], '2')
        self.assertIn('no-store', response['Cache-Control'])
        save.assert_called_once()
        self.puerto.refresh_from_db()
        self.assertEqual(self.puerto.observaciones, 'Original')

    def test_bloqueo_despues_de_guardar_revierte_la_escritura(self):
        original_save = DetallePuertoODF.save
        def guardar_y_fallar(obj, *args, **kwargs):
            original_save(obj, *args, **kwargs)
            raise OperationalError('database is locked')
        with patch.object(DetallePuertoODF, 'save', guardar_y_fallar):
            response = self.editar()
        self.assertEqual(response.status_code, 503)
        self.puerto.refresh_from_db()
        self.assertEqual(self.puerto.observaciones, 'Original')

    def test_error_inesperado_tambien_revierte_sin_fingir_bloqueo(self):
        original_save = DetallePuertoODF.save
        def guardar_y_fallar(obj, *args, **kwargs):
            original_save(obj, *args, **kwargs)
            raise RuntimeError('fallo posterior')
        with patch.object(DetallePuertoODF, 'save', guardar_y_fallar):
            response = self.editar()
        self.assertEqual(response.status_code, 500)
        self.puerto.refresh_from_db()
        self.assertEqual(self.puerto.observaciones, 'Original')

    def test_gestion_conexion_no_oculta_bloqueo(self):
        with patch('mapas.views.inventario.reservar_puerto', side_effect=OperationalError('database is locked')) as reservar:
            response = self.client.post(reverse('api_gestionar_conexion_puerto'),
                data=json.dumps({'puerto_id': self.puerto.pk, 'accion': 'reservar'}), content_type='application/json')
        self.assertEqual(response.status_code, 503)
        reservar.assert_called_once()
        self.puerto.refresh_from_db()
        self.assertEqual(self.puerto.estado_puerto, 'LIBRE')

    def test_clasificacion_no_confunde_otros_errores_con_bloqueos(self):
        with patch('mapas.services.errores_bd.connection') as db:
            db.vendor = 'sqlite'
            for text in ('database is locked', 'database table is locked: test', 'database schema is locked'):
                self.assertTrue(es_bloqueo_sqlite(OperationalError(text)))
            self.assertFalse(es_bloqueo_sqlite(OperationalError('disk I/O error')))
            self.assertFalse(es_bloqueo_sqlite(ValueError('database is locked')))
            db.vendor = 'postgresql'
            self.assertFalse(es_bloqueo_sqlite(OperationalError('database is locked')))

    def test_middleware_no_oculta_error_ajeno_a_bloqueos(self):
        request = RequestFactory().post(reverse('api_update_puerto'))
        response = AuditSecurityMiddleware(lambda r: HttpResponse()).process_exception(request, OperationalError('disk I/O error'))
        self.assertIsNone(response)

    def test_telemetria_bloqueada_no_impide_navegar(self):
        request = RequestFactory().get('/inventario/')
        request.user = self.user
        cache.delete(f'fibergenius:user-activity:{self.user.pk}')
        with patch('mapas.middleware.UserActivity.objects.update_or_create', side_effect=OperationalError('database is locked')):
            response = ActiveUserMiddleware(lambda r: HttpResponse('ok'))(request)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(cache.get(f'fibergenius:user-activity:{self.user.pk}'))

    def test_telemetria_no_oculta_error_no_transitorio(self):
        request = RequestFactory().get('/inventario/')
        request.user = self.user
        cache.delete(f'fibergenius:user-activity:{self.user.pk}')
        with patch('mapas.middleware.UserActivity.objects.update_or_create', side_effect=OperationalError('disk I/O error')):
            with self.assertRaises(OperationalError):
                ActiveUserMiddleware(lambda r: HttpResponse('ok'))(request)
