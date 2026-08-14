import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .models import (
    DetallePuertoODF,
    FibraTramo,
    HubSite,
    InventarioFibra,
    InventarioODF,
    InventarioTramo,
    RackFisico,
    Ruta,
    SalaTecnica,
    TerminacionFibra,
)
from .services.puertos import (
    MovimientoRequiereConfirmacion,
    cancelar_reserva_puerto,
    conectar_puerto,
    desconectar_puerto,
    reservar_puerto,
)


class GestionPuertosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ruta = Ruta.objects.create(nombre='RUTA-GESTION')
        cls.fibra = InventarioFibra.objects.create(
            ruta=cls.ruta,
            fibra_numero='F12',
            estado='Ocupado',
            origen_estado='INFORMADO',
        )
        site = HubSite.objects.create(nombre='SITE-GESTION')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA-1')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK-1')
        cls.odf = InventarioODF.objects.create(
            rack_obj=rack,
            odf='ODF-GESTION',
            capacidad_puertos=3,
        )
        cls.puerto_1 = cls._puerto('1')
        cls.puerto_2 = cls._puerto('2')
        cls.puerto_3 = cls._puerto('3')

    @classmethod
    def _puerto(cls, numero):
        return DetallePuertoODF.objects.create(
            odf_obj=cls.odf,
            puerto_odf=numero,
            estado_puerto='Libre',
        )

    def test_libre_a_ocupado_crea_terminacion_oficial(self):
        terminacion, creada = conectar_puerto(
            puerto_id=self.puerto_1.pk,
            fibra_id=self.fibra.pk,
            extremo='A',
        )
        self.assertTrue(creada)
        self.assertEqual(terminacion.puerto_odf_id, self.puerto_1.pk)
        self.puerto_1.refresh_from_db()
        self.assertEqual(self.puerto_1.estado_puerto, 'Ocupado')
        desconectar_puerto(puerto_id=self.puerto_1.pk)
        self.puerto_1.refresh_from_db()
        self.assertEqual(self.puerto_1.estado_puerto, 'Libre')

    def test_permite_el_mismo_numero_de_hilo_en_provisionales_distintos(self):
        primera, _ = conectar_puerto(
            puerto_id=self.puerto_1.pk,
            fibra_numero='F2',
            extremo='A',
        )
        segunda, _ = conectar_puerto(
            puerto_id=self.puerto_2.pk,
            fibra_numero='F2',
            extremo='A',
        )

        self.assertNotEqual(primera.fibra_id, segunda.fibra_id)
        self.assertEqual(
            InventarioFibra.objects.filter(
                ruta__isnull=True,
                fibra_numero='F2',
            ).count(),
            2,
        )

    def test_mover_requiere_confirmacion_y_libera_puerto_anterior(self):
        conectar_puerto(
            puerto_id=self.puerto_1.pk,
            fibra_id=self.fibra.pk,
            extremo='A',
        )
        with self.assertRaises(MovimientoRequiereConfirmacion):
            conectar_puerto(
                puerto_id=self.puerto_2.pk,
                fibra_id=self.fibra.pk,
                extremo='A',
            )
        conectar_puerto(
            puerto_id=self.puerto_2.pk,
            fibra_id=self.fibra.pk,
            extremo='A',
            permitir_mover=True,
        )
        self.puerto_1.refresh_from_db()
        self.puerto_2.refresh_from_db()
        self.assertEqual(self.puerto_1.estado_puerto, 'Libre')
        self.assertEqual(self.puerto_2.estado_puerto, 'Ocupado')
        self.assertEqual(
            TerminacionFibra.objects.get(
                fibra=self.fibra,
                extremo='A',
            ).puerto_odf_id,
            self.puerto_2.pk,
        )

    def test_desconectar_libera_puerto_y_opcionalmente_fibra(self):
        conectar_puerto(
            puerto_id=self.puerto_1.pk,
            fibra_id=self.fibra.pk,
            extremo='A',
        )
        desconectar_puerto(puerto_id=self.puerto_1.pk, liberar_fibra=False)
        self.puerto_1.refresh_from_db()
        self.fibra.refresh_from_db()
        self.assertEqual(self.puerto_1.estado_puerto, 'Libre')
        self.assertEqual(self.fibra.estado, 'Ocupado')
        self.assertFalse(TerminacionFibra.objects.exists())

        conectar_puerto(
            puerto_id=self.puerto_1.pk,
            fibra_id=self.fibra.pk,
            extremo='A',
        )
        desconectar_puerto(puerto_id=self.puerto_1.pk, liberar_fibra=True)
        self.fibra.refresh_from_db()
        self.assertEqual(self.fibra.estado, 'Libre')

    def test_reservar_cancelar_y_consumir_reserva(self):
        reservar_puerto(puerto_id=self.puerto_1.pk)
        self.puerto_1.refresh_from_db()
        self.assertEqual(self.puerto_1.estado_puerto, 'Reservado')
        cancelar_reserva_puerto(puerto_id=self.puerto_1.pk)
        self.puerto_1.refresh_from_db()
        self.assertEqual(self.puerto_1.estado_puerto, 'Libre')
        reservar_puerto(puerto_id=self.puerto_1.pk)
        conectar_puerto(
            puerto_id=self.puerto_1.pk,
            fibra_id=self.fibra.pk,
            extremo='B',
        )
        self.puerto_1.refresh_from_db()
        self.assertEqual(self.puerto_1.estado_puerto, 'Ocupado')

    def test_no_permite_reservar_un_puerto_conectado(self):
        conectar_puerto(
            puerto_id=self.puerto_1.pk,
            fibra_id=self.fibra.pk,
            extremo='A',
        )
        with self.assertRaises(ValidationError):
            reservar_puerto(puerto_id=self.puerto_1.pk)

    def test_modelo_bloquea_cambio_directo_de_estado(self):
        self.puerto_1.estado_puerto = 'Reservado'
        with self.assertRaises(ValidationError):
            self.puerto_1.save(update_fields=['estado_puerto'])

    def test_crear_y_eliminar_terminacion_sincroniza_estado(self):
        terminacion = TerminacionFibra.objects.create(
            fibra=self.fibra,
            extremo='A',
            puerto_odf=self.puerto_1,
        )
        self.puerto_1.refresh_from_db()
        self.assertEqual(self.puerto_1.estado_puerto, 'Ocupado')
        terminacion.delete()
        self.puerto_1.refresh_from_db()
        self.assertEqual(self.puerto_1.estado_puerto, 'Libre')


class SincronizacionFibraPuertoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = HubSite.objects.create(nombre='SITE-SYNC')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA-SYNC')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK-SYNC')
        cls.odf = InventarioODF.objects.create(
            rack_obj=rack,
            odf='ODF-SYNC',
            capacidad_puertos=20,
        )

    def _crear_recorrido(self, cantidad, estado, sufijo):
        ruta = Ruta.objects.create(nombre=f'RUTA-SYNC-{cantidad}-{sufijo}')
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F12',
            estado=estado,
            origen_estado='INFORMADO',
        )
        asignaciones = []
        for secuencia in range(1, cantidad + 1):
            tramo = InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=secuencia,
                codigo_tramo=f'T{secuencia:03d}-{cantidad}-{sufijo}',
                capacidad_hilos=24,
            )
            asignaciones.append(FibraTramo.objects.create(
                tramo=tramo,
                fibra=fibra,
                numero_hilo='F12',
                estado=estado,
            ))
        puerto = DetallePuertoODF.objects.create(
            odf_obj=self.odf,
            puerto_odf=f'{sufijo}-{cantidad}',
            estado_puerto='Libre',
        )
        return fibra, asignaciones, puerto

    def test_confirmar_conexion_informa_global_sin_reescribir_tramos(self):
        for cantidad in (1, 2, 4):
            with self.subTest(tramos=cantidad):
                fibra, asignaciones, puerto = self._crear_recorrido(
                    cantidad, 'Libre', 'C'
                )
                conectar_puerto(
                    puerto_id=puerto.pk,
                    fibra_id=fibra.pk,
                    extremo='A',
                    ocupar_fibra=True,
                )

                fibra.refresh_from_db()
                self.assertEqual(fibra.estado, 'Ocupado')
                self.assertEqual(
                    list(
                        FibraTramo.objects.filter(
                            pk__in=[item.pk for item in asignaciones]
                        ).values_list('estado', flat=True)
                    ),
                    ['Libre'] * cantidad,
                )

    def test_confirmar_desconexion_informa_global_sin_reescribir_tramos(self):
        for cantidad in (1, 2, 4):
            with self.subTest(tramos=cantidad):
                fibra, asignaciones, puerto = self._crear_recorrido(
                    cantidad, 'Ocupado', 'D'
                )
                conectar_puerto(
                    puerto_id=puerto.pk,
                    fibra_id=fibra.pk,
                    extremo='A',
                )
                desconectar_puerto(puerto_id=puerto.pk, liberar_fibra=True)

                fibra.refresh_from_db()
                self.assertEqual(fibra.estado, 'Libre')
                self.assertEqual(
                    list(
                        FibraTramo.objects.filter(
                            pk__in=[item.pk for item in asignaciones]
                        ).values_list('estado', flat=True)
                    ),
                    ['Ocupado'] * cantidad,
                )

    def test_sin_confirmacion_solo_cambia_el_puerto(self):
        fibra, asignaciones, puerto = self._crear_recorrido(4, 'Libre', 'N')
        conectar_puerto(
            puerto_id=puerto.pk,
            fibra_id=fibra.pk,
            extremo='A',
            ocupar_fibra=False,
        )
        fibra.refresh_from_db()
        self.assertEqual(fibra.estado, 'Libre')
        self.assertEqual(
            set(FibraTramo.objects.filter(
                pk__in=[item.pk for item in asignaciones]
            ).values_list('estado', flat=True)),
            {'Libre'},
        )

        InventarioFibra.objects.filter(pk=fibra.pk).update(estado='Ocupado')
        FibraTramo.objects.filter(
            pk__in=[item.pk for item in asignaciones]
        ).update(estado='Ocupado')
        desconectar_puerto(puerto_id=puerto.pk, liberar_fibra=False)
        fibra.refresh_from_db()
        self.assertEqual(fibra.estado, 'Ocupado')
        self.assertEqual(
            set(FibraTramo.objects.filter(
                pk__in=[item.pk for item in asignaciones]
            ).values_list('estado', flat=True)),
            {'Ocupado'},
        )


class GestionPuertosApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser(
            username='admin-gestion-puertos',
            email='gestion@example.com',
            password='clave-segura',
        )
        cls.ruta = Ruta.objects.create(nombre='RUTA-API-GESTION')
        cls.fibra = InventarioFibra.objects.create(
            ruta=cls.ruta,
            fibra_numero='F1',
            estado='Libre',
            origen_estado='INFORMADO',
        )
        site = HubSite.objects.create(nombre='SITE-API-GESTION')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK')
        odf = InventarioODF.objects.create(
            rack_obj=rack, odf='ODF-API-GESTION', capacidad_puertos=1
        )
        cls.puerto = DetallePuertoODF.objects.create(
            odf_obj=odf, puerto_odf='1', estado_puerto='Libre'
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _post(self, payload):
        return self.client.post(
            reverse('api_gestionar_conexion_puerto'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_api_conecta_y_desconecta(self):
        respuesta = self._post({
            'accion': 'conectar',
            'puerto_id': self.puerto.pk,
            'fibra_id': self.fibra.pk,
            'extremo': 'A',
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(TerminacionFibra.objects.exists())

        respuesta = self._post({
            'accion': 'desconectar',
            'puerto_id': self.puerto.pk,
            'liberar_fibra': True,
        })
        self.assertEqual(respuesta.status_code, 200)
        self.puerto.refresh_from_db()
        self.assertEqual(self.puerto.estado_puerto, 'Libre')

    def test_api_sincroniza_la_fibra_solo_con_confirmacion_explicita(self):
        tramo = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=1,
            codigo_tramo='T001-API-SYNC',
            capacidad_hilos=12,
        )
        asignacion = FibraTramo.objects.create(
            tramo=tramo,
            fibra=self.fibra,
            numero_hilo='F1',
            estado='Libre',
        )
        respuesta = self._post({
            'accion': 'conectar',
            'puerto_id': self.puerto.pk,
            'fibra_id': self.fibra.pk,
            'extremo': 'A',
            'sincronizar_fibra': True,
        })
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()['fibra_sincronizada'])
        self.fibra.refresh_from_db()
        asignacion.refresh_from_db()
        self.assertEqual(self.fibra.estado, 'Ocupado')
        self.assertEqual(self.fibra.origen_estado, 'INFORMADO')
        self.assertEqual(asignacion.estado, 'Libre')

    def test_edicion_generica_no_cambia_estado(self):
        respuesta = self.client.post(
            reverse('api_update_puerto'),
            data=json.dumps({
                'id': self.puerto.pk,
                'puerto_odf': '1',
                'estado_puerto': 'Ocupado',
            }),
            content_type='application/json',
        )
        self.assertEqual(respuesta.status_code, 409)
        self.puerto.refresh_from_db()
        self.assertEqual(self.puerto.estado_puerto, 'Libre')

    def test_pantalla_expone_gestion_separada_del_editor_tecnico(self):
        respuesta = self.client.get(reverse('planta_interna'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'id="port-management"')
        self.assertContains(respuesta, reverse('api_gestionar_conexion_puerto'))
        self.assertContains(respuesta, 'Conectar fibra')
        self.assertContains(respuesta, 'Desconectar y liberar')
        self.assertNotContains(respuesta, 'id="port-management-free-fiber"')
        script = (
            Path(__file__).resolve().parent
            / 'static'
            / 'js'
            / 'planta-interna-paginada.js'
        ).read_text(encoding='utf-8')
        self.assertIn('estado global como Ocupado', script)
        self.assertIn('estado global de ${fiberNumber} como Disponible', script)
        self.assertNotIn('todos sus tramos como Ocupado', script)

    def test_api_paginada_expone_la_conexion_oficial(self):
        conectar_puerto(
            puerto_id=self.puerto.pk,
            fibra_id=self.fibra.pk,
            extremo='A',
        )
        respuesta = self.client.get(reverse('api_puertos_paginados'))
        self.assertEqual(respuesta.status_code, 200)
        puerto = respuesta.json()['data'][0]
        self.assertEqual(puerto['estado'], 'Ocupado')
        self.assertEqual(puerto['conexion']['ruta'], self.ruta.nombre)
        self.assertEqual(puerto['conexion']['fibra'], 'F1')
        self.assertEqual(puerto['conexion']['extremo'], 'A')
        self.assertFalse(puerto['requiere_regularizacion'])

    def test_api_crea_hilo_provisional_y_ocupa_el_puerto(self):
        respuesta = self._post({
            'accion': 'conectar',
            'puerto_id': self.puerto.pk,
            'fibra_numero': 'F2',
            'extremo': 'A',
        })

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.json()['fibra_provisional'])
        fibra = InventarioFibra.objects.get(
            ruta__isnull=True,
            fibra_numero='F2',
        )
        self.assertEqual(fibra.estado, 'Desconocido')
        self.assertTrue(TerminacionFibra.objects.filter(
            fibra=fibra,
            extremo='A',
            puerto_odf=self.puerto,
        ).exists())
        self.puerto.refresh_from_db()
        self.assertEqual(self.puerto.estado_puerto, 'Ocupado')

        listado = self.client.get(
            reverse('api_fibras_paginadas'),
            {'ruta_pendiente': '1'},
        )
        self.assertEqual(listado.status_code, 200)
        item = listado.json()['data'][0]
        self.assertEqual(item['troncal'], 'Troncal pendiente')
        self.assertTrue(item['troncal_pendiente'])
        self.assertEqual(item['origen'], 'Pendiente de orientación')
        self.assertEqual(item['terminacion_a']['odf'], 'ODF-API-GESTION')
        self.assertEqual(item['terminacion_a']['puerto'], '1')

    def test_asignar_troncal_conserva_la_terminacion_provisional(self):
        terminacion, _ = conectar_puerto(
            puerto_id=self.puerto.pk,
            fibra_numero='F2',
            extremo='A',
        )
        fibra = terminacion.fibra

        respuesta = self.client.post(
            reverse('api_update_fibra'),
            data=json.dumps({
                'id': fibra.pk,
                'ruta_nombre': self.ruta.nombre,
                'fibra_numero': 'F2',
                'estado': 'Desconocido',
            }),
            content_type='application/json',
        )

        self.assertEqual(respuesta.status_code, 200)
        fibra.refresh_from_db()
        terminacion.refresh_from_db()
        self.assertEqual(fibra.ruta, self.ruta)
        self.assertEqual(terminacion.puerto_odf, self.puerto)
