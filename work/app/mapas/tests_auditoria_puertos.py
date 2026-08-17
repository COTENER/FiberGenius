from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.urls import reverse

from .models import (
    AuditoriaPuertoODF,
    DetallePuertoODF,
    HubSite,
    InventarioFibra,
    InventarioODF,
    RackFisico,
    Ruta,
    SalaTecnica,
)
from .services.puertos import (
    cancelar_reserva_puerto,
    conectar_puerto,
    desconectar_puerto,
    reservar_puerto,
)


class AuditoriaPuertosODFTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_superuser(
            username='auditor-puertos',
            email='auditoria@example.com',
            password='clave-segura',
        )
        site = HubSite.objects.create(nombre='SITE-AUD')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA-AUD')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK-AUD')
        odf = InventarioODF.objects.create(
            rack_obj=rack,
            odf='ODF-AUD',
            capacidad_puertos=12,
        )
        cls.puerto_1 = DetallePuertoODF.objects.create(
            odf_obj=odf, puerto_odf='1', estado_puerto='LIBRE'
        )
        cls.puerto_2 = DetallePuertoODF.objects.create(
            odf_obj=odf, puerto_odf='2', estado_puerto='LIBRE'
        )
        cls.fibra = InventarioFibra.objects.create(
            ruta=Ruta.objects.create(nombre='RUTA-AUD'),
            fibra_numero='F12',
            estado='DISPONIBLE',
            origen_estado='INFORMADO',
        )

    def test_registra_conexion_movimiento_y_desconexion(self):
        conectar_puerto(
            puerto_id=self.puerto_1.pk,
            fibra_id=self.fibra.pk,
            extremo='A',
            usuario=self.usuario,
            origen='GUI',
        )
        conexion = AuditoriaPuertoODF.objects.get(accion='CONECTAR')
        self.assertEqual(conexion.usuario, self.usuario)
        self.assertEqual(conexion.origen, 'GUI')
        self.assertIsNone(conexion.puerto_anterior)
        self.assertEqual(conexion.puerto_nuevo, self.puerto_1)
        self.assertEqual((conexion.estado_anterior, conexion.estado_nuevo), ('LIBRE', 'OCUPADO'))
        self.assertEqual(conexion.referencia_fibra, 'RUTA-AUD / F12')

        conectar_puerto(
            puerto_id=self.puerto_2.pk,
            fibra_id=self.fibra.pk,
            extremo='A',
            permitir_mover=True,
            usuario=self.usuario,
            origen='GUI',
        )
        movimiento = AuditoriaPuertoODF.objects.get(accion='MOVER')
        self.assertEqual(movimiento.puerto_anterior, self.puerto_1)
        self.assertEqual(movimiento.puerto_nuevo, self.puerto_2)
        self.assertEqual(
            movimiento.metadatos['estado_puerto_origen_nuevo'],
            'LIBRE',
        )

        desconectar_puerto(
            puerto_id=self.puerto_2.pk,
            usuario=self.usuario,
            origen='GUI',
        )
        desconexion = AuditoriaPuertoODF.objects.get(accion='DESCONECTAR')
        self.assertEqual(desconexion.extremo, 'A')
        self.assertEqual(desconexion.puerto_anterior, self.puerto_2)
        self.assertIsNone(desconexion.puerto_nuevo)
        self.assertEqual((desconexion.estado_anterior, desconexion.estado_nuevo), ('OCUPADO', 'LIBRE'))

    def test_registra_reserva_y_cancelacion(self):
        reservar_puerto(
            puerto_id=self.puerto_1.pk,
            usuario=self.usuario,
            origen='GUI',
        )
        cancelar_reserva_puerto(
            puerto_id=self.puerto_1.pk,
            usuario=self.usuario,
            origen='GUI',
        )
        self.assertEqual(
            list(AuditoriaPuertoODF.objects.values_list('accion', flat=True)),
            ['CANCELAR_RESERVA', 'RESERVAR'],
        )

    def test_evento_es_inmutable(self):
        reservar_puerto(puerto_id=self.puerto_1.pk)
        evento = AuditoriaPuertoODF.objects.get()
        evento.estado_nuevo = 'LIBRE'
        with self.assertRaises(ValidationError):
            evento.save()
        with self.assertRaises(ValidationError):
            evento.delete()

    def test_rollback_elimina_cambio_y_evento(self):
        try:
            with transaction.atomic():
                reservar_puerto(
                    puerto_id=self.puerto_1.pk,
                    usuario=self.usuario,
                    origen='GUI',
                )
                raise RuntimeError('forzar rollback')
        except RuntimeError:
            pass
        self.puerto_1.refresh_from_db()
        self.assertEqual(self.puerto_1.estado_puerto, 'LIBRE')
        self.assertFalse(AuditoriaPuertoODF.objects.exists())

    def test_api_filtra_por_puerto_y_la_pantalla_expone_historial(self):
        reservar_puerto(
            puerto_id=self.puerto_1.pk,
            usuario=self.usuario,
            origen='GUI',
        )
        self.client.force_login(self.usuario)
        respuesta = self.client.get(
            reverse('api_historial_puertos'),
            {'puerto_id': self.puerto_1.pk},
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['pagination']['total'], 1)
        self.assertEqual(respuesta.json()['data'][0]['usuario'], self.usuario.username)
        pagina = self.client.get(reverse('planta_interna'))
        self.assertContains(pagina, 'id="ports-history"')
        self.assertContains(pagina, 'Historial')
