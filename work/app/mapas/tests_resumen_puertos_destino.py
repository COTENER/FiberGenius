"""A free port needs no destination; missing descriptive text is not a fault."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    DetallePuertoODF, HubSite, InventarioFibra, InventarioODF,
    RackFisico, SalaTecnica, TerminacionFibra,
)
from .services.puertos import conectar_puerto
from .views.operacion_inventario import _resumen_puertos


class ResumenPuertosDestinoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_superuser(username='resumen-puertos')
        site = HubSite.objects.create(nombre='SITE-RESUMEN')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK')
        cls.odf = InventarioODF.objects.create(odf='ODF-RESUMEN', rack_obj=rack, capacidad_puertos=9)
        numero = 0
        for estado in ('LIBRE', 'RESERVADO', 'OCUPADO'):
            for destino in (None, '', 'SWITCH-01'):
                numero += 1
                puerto = DetallePuertoODF.objects.create(
                    odf_obj=cls.odf, puerto_odf=str(numero), destino=destino,
                    estado_puerto='LIBRE' if estado == 'OCUPADO' else estado,
                )
                if estado == 'OCUPADO':
                    fibra = InventarioFibra.objects.create(fibra_numero=f'F{numero}')
                    conectar_puerto(puerto_id=puerto.pk, fibra_id=fibra.pk, extremo='A')

    def test_resumen_excluye_libres_con_destino_null_o_vacio(self):
        resumen = _resumen_puertos(DetallePuertoODF.objects.all())
        self.assertEqual(resumen['sin_destino'], 4)
        self.assertEqual(resumen['total'], 9)
        for key in ('libres', 'ocupados', 'reservados'):
            self.assertEqual(resumen[key], 3)

    def test_solo_libres_no_genera_pendientes(self):
        resumen = _resumen_puertos(DetallePuertoODF.objects.filter(estado_puerto='LIBRE'))
        self.assertEqual(resumen['sin_destino'], 0)
        self.assertEqual(resumen['libres'], 3)

    def test_queryset_vacio_da_cero(self):
        resumen = _resumen_puertos(DetallePuertoODF.objects.none())
        self.assertEqual(resumen['sin_destino'], 0)
        self.assertEqual(resumen['total'], 0)

    def test_api_respeta_filtros_y_mantiene_contrato(self):
        self.client.force_login(self.usuario)
        for estado, esperado in (('', 4), ('LIBRE', 0), ('OCUPADO', 2), ('RESERVADO', 2)):
            with self.subTest(estado=estado):
                response = self.client.get(reverse('api_puertos_paginados'), {'estado': estado})
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                self.assertEqual(payload['summary']['sin_destino'], esperado)
                self.assertEqual(payload['summary']['total'], 9 if not estado else 3)

    def test_consulta_no_cambia_destinos_estados_ni_terminaciones(self):
        antes = list(DetallePuertoODF.objects.order_by('pk').values_list('pk', 'estado_puerto', 'destino'))
        conexiones = list(TerminacionFibra.objects.order_by('pk').values_list('pk', 'fibra_id', 'puerto_odf_id', 'extremo'))
        self.assertEqual(len(conexiones), 3)
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(reverse('api_puertos_paginados')).status_code, 200)
        self.assertEqual(antes, list(DetallePuertoODF.objects.order_by('pk').values_list('pk', 'estado_puerto', 'destino')))
        self.assertEqual(conexiones, list(TerminacionFibra.objects.order_by('pk').values_list('pk', 'fibra_id', 'puerto_odf_id', 'extremo')))

    def test_tarjeta_informativa_no_declara_incidencia(self):
        self.client.force_login(self.usuario)
        response = self.client.get(reverse('planta_interna'))
        self.assertContains(response, 'id="ports-stat-review"')
        self.assertContains(response, 'Sin destino informado')
        self.assertContains(response, 'Ocupados o reservados')
        self.assertContains(response, 'No indica una falla de conexión.')
        self.assertNotContains(response, 'Requieren revisión')
