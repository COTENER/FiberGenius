"""Una eliminación rechazada no puede dejar cambios parciales de inventario."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.test import TestCase
from django.urls import reverse

from .models import (
    AuditoriaFibra, AuditoriaPuertoODF, CoordenadaRuta, DetallePuertoODF,
    EmpalmeFibra, FibraTramo, HubSite, InventarioFibra, InventarioODF,
    InventarioTramo, RackFisico, Reserva, Ruta, SalaTecnica, TerminacionFibra,
)
from .services.puertos import conectar_puerto


class EliminacionRutaAtomicaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username='admin-eliminacion', email='audit@example.com', password='test-only',
        )
        cls.ruta = Ruta.objects.create(nombre='TRONCAL-ELIMINACION')
        cls.tramo = InventarioTramo.objects.create(
            ruta=cls.ruta, tramo_secuencia=1, capacidad_hilos=12,
        )
        cls.f1 = InventarioFibra.objects.create(ruta=cls.ruta, fibra_numero='F1')
        cls.f2 = InventarioFibra.objects.create(ruta=cls.ruta, fibra_numero='F2')
        FibraTramo.objects.create(tramo=cls.tramo, fibra=cls.f1, numero_hilo='F1')
        site = HubSite.objects.create(nombre='SITE-ELIMINACION')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK')
        cls.odf = InventarioODF.objects.create(rack_obj=rack, odf='ODF-ELIMINACION', capacidad_puertos=1)
        cls.puerto = DetallePuertoODF.objects.create(
            odf_obj=cls.odf, puerto_odf='1', estado_puerto='LIBRE',
        )
        conectar_puerto(puerto_id=cls.puerto.pk, fibra_id=cls.f1.pk, extremo='A', usuario=cls.admin)

    def setUp(self):
        self.client.force_login(self.admin)
        self.url = reverse('api_delete_ruta')

    def _geografia(self):
        CoordenadaRuta.objects.create(
            ruta=self.ruta, tramo=self.tramo, orden=1, latitud=-23, longitud=-70,
        )
        return Reserva.objects.create(
            ruta=self.ruta, tramo=self.tramo, nombre='MUFA-1', tipo='MUFA',
            latitud=-23, longitud=-70,
        )

    def _snapshot(self):
        # Incluye efectos de señales, contadores, auditorías y relaciones SET_NULL.
        return {
            model.__name__: list(model.objects.order_by('pk').values())
            for model in (
                Ruta, InventarioTramo, InventarioFibra, FibraTramo,
                CoordenadaRuta, Reserva, EmpalmeFibra, TerminacionFibra,
                DetallePuertoODF, InventarioODF, AuditoriaFibra, AuditoriaPuertoODF,
            )
        }

    def _eliminar(self):
        return self.client.post(self.url, {'nombre': self.ruta.nombre}, content_type='application/json')

    def test_empalme_protegido_conserva_todo_el_inventario(self):
        reserva = self._geografia()
        EmpalmeFibra.objects.create(elemento=reserva, fibra_entrada=self.f1, fibra_salida=self.f2)
        anterior = self._snapshot()

        response = self._eliminar()

        self.assertEqual(self._snapshot(), anterior)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['status'], 'error')
        self.assertIn('No se eliminó ningún dato', response.json()['message'])

    def _fallo_despues_de_eliminar_fibras(self, error, status):
        self._geografia()
        anterior = self._snapshot()
        original_delete = QuerySet.delete

        def eliminar_y_fallar(queryset):
            resultado = original_delete(queryset)
            if queryset.model is InventarioFibra:
                raise error
            return resultado

        with patch.object(QuerySet, 'delete', autospec=True, side_effect=eliminar_y_fallar):
            response = self._eliminar()

        self.assertEqual(response.status_code, status)
        self.assertEqual(self._snapshot(), anterior)

    def test_error_inesperado_revierte_incluso_fibras_ya_eliminadas(self):
        self._fallo_despues_de_eliminar_fibras(RuntimeError('Fallo simulado'), 500)

    def test_error_de_validacion_revierte_cambios_previos(self):
        self._fallo_despues_de_eliminar_fibras(ValidationError('Fallo simulado'), 400)

    def test_borrado_total_fallido_restaura_ruta_y_relaciones(self):
        anterior = self._snapshot()
        original_delete = Ruta.delete

        def eliminar_y_fallar(ruta, *args, **kwargs):
            original_delete(ruta, *args, **kwargs)
            raise RuntimeError('Fallo simulado después de eliminar la ruta')

        with patch.object(Ruta, 'delete', autospec=True, side_effect=eliminar_y_fallar):
            response = self._eliminar()

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self._snapshot(), anterior)

    def test_limpieza_valida_conserva_ruta_y_geografia(self):
        reserva = self._geografia()
        response = self._eliminar()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        self.assertTrue(Ruta.objects.filter(pk=self.ruta.pk).exists())
        self.assertTrue(CoordenadaRuta.objects.filter(ruta=self.ruta).exists())
        reserva.refresh_from_db()
        self.assertIsNone(reserva.tramo_id)
        self._assert_inventario_eliminado()

    def test_borrado_total_valido_sin_geografia(self):
        response = self._eliminar()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        self.assertFalse(Ruta.objects.filter(pk=self.ruta.pk).exists())
        self.assertFalse(InventarioTramo.objects.filter(ruta_id=self.ruta.pk).exists())
        self.assertFalse(FibraTramo.objects.exists())
        # La relación con la troncal es SET_NULL: borrar una ruta sin geografía
        # conserva las fibras globales y sus conexiones físicas existentes.
        self.f1.refresh_from_db()
        self.f2.refresh_from_db()
        self.assertIsNone(self.f1.ruta_id)
        self.assertIsNone(self.f2.ruta_id)
        self.assertTrue(TerminacionFibra.objects.filter(fibra=self.f1, puerto_odf=self.puerto).exists())
        self.puerto.refresh_from_db()
        self.odf.refresh_from_db()
        self.assertEqual(self.puerto.estado_puerto, 'OCUPADO')
        self.assertEqual(self.odf.puertos_ocupados, 1)

    def _assert_inventario_eliminado(self):
        self.assertFalse(InventarioTramo.objects.filter(ruta_id=self.ruta.pk).exists())
        self.assertFalse(InventarioFibra.objects.filter(ruta_id=self.ruta.pk).exists())
        self.assertFalse(FibraTramo.objects.exists())
        self.assertFalse(TerminacionFibra.objects.exists())
        self.puerto.refresh_from_db()
        self.odf.refresh_from_db()
        self.assertEqual(self.puerto.estado_puerto, 'LIBRE')
        self.assertEqual(self.odf.puertos_ocupados, 0)
        self.assertEqual(self.odf.puertos_libres, 1)

    def test_json_invalido_no_modifica_datos(self):
        anterior = self._snapshot()
        response = self.client.post(self.url, '{', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._snapshot(), anterior)

    def test_requiere_permiso_para_eliminar(self):
        lector = get_user_model().objects.create_user(username='lector-eliminacion', password='test-only')
        self.client.force_login(lector)
        anterior = self._snapshot()
        self.assertEqual(self._eliminar().status_code, 403)
        self.assertEqual(self._snapshot(), anterior)

    def test_get_no_elimina_inventario(self):
        anterior = self._snapshot()
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self._snapshot(), anterior)
