from django.db import IntegrityError, transaction
from django.test import TestCase

from .models import (
    CoordenadaRuta,
    FibraTramo,
    HubSite,
    InventarioFibra,
    InventarioTramo,
    NodoRed,
    Reserva,
    Ruta,
)


class RestriccionesFuertesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ruta_a = Ruta.objects.create(nombre='RESTRICCIONES-A')
        cls.ruta_b = Ruta.objects.create(nombre='RESTRICCIONES-B')
        cls.nodo_a = NodoRed.objects.create(tipo='SITE', codigo='RF-A')
        cls.nodo_b = NodoRed.objects.create(tipo='SITE', codigo='RF-B')
        cls.nodo_c = NodoRed.objects.create(tipo='SITE', codigo='RF-C')
        cls.tramo_a = InventarioTramo.objects.create(
            ruta=cls.ruta_a,
            tramo_secuencia=1,
            codigo_tramo='RF-T1',
            origen_nodo=cls.nodo_a,
            destino_nodo=cls.nodo_b,
            capacidad_hilos=4,
        )
        cls.fibra_a = InventarioFibra.objects.create(
            ruta=cls.ruta_a,
            fibra_numero='F1',
            estado='DISPONIBLE',
            origen_estado='INFORMADO',
        )
        cls.fibra_b = InventarioFibra.objects.create(
            ruta=cls.ruta_b,
            fibra_numero='F1',
            estado='DISPONIBLE',
            origen_estado='INFORMADO',
        )

    def test_base_rechaza_suma_declarada_mayor_que_capacidad(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Ruta.objects.filter(pk=self.ruta_a.pk).update(
                    capacidad_declarada_hilos=4,
                    hilos_ocupados_declarados=5,
                    hilos_reservados_declarados=None,
                    hilos_libres_declarados=None,
                )

    def test_base_rechaza_contadores_tramo_mayores_que_capacidad(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InventarioTramo.objects.filter(pk=self.tramo_a.pk).update(
                    hilos_ocupados=3,
                    hilos_reservados=1,
                    hilos_libres=1,
                )

    def test_base_rechaza_formato_posicion_y_posicion_fuera_de_capacidad(self):
        for numero in ('H1', 'F5'):
            with self.subTest(numero=numero):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        FibraTramo.objects.bulk_create([
                            FibraTramo(
                                tramo=self.tramo_a,
                                numero_hilo=numero,
                                estado='DISPONIBLE',
                            )
                        ])

    def test_base_rechaza_fibra_y_tramo_de_rutas_distintas(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FibraTramo.objects.bulk_create([
                    FibraTramo(
                        tramo=self.tramo_a,
                        fibra=self.fibra_b,
                        numero_hilo='F1',
                        estado='DISPONIBLE',
                    )
                ])

    def test_base_rechaza_topologia_no_continua(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InventarioTramo.objects.bulk_create([
                    InventarioTramo(
                        ruta=self.ruta_a,
                        tramo_secuencia=2,
                        codigo_tramo='RF-T2',
                        origen_nodo=self.nodo_a,
                        destino_nodo=self.nodo_c,
                        capacidad_hilos=4,
                    )
                ])

    def test_base_rechaza_coordenada_y_reserva_cruzadas(self):
        for modelo, datos in (
            (
                CoordenadaRuta,
                {
                    'ruta': self.ruta_b,
                    'tramo': self.tramo_a,
                    'tramo_secuencia': self.tramo_a.tramo_secuencia,
                    'orden': 1,
                    'latitud': 0,
                    'longitud': 0,
                },
            ),
            (
                Reserva,
                {
                    'ruta': self.ruta_b,
                    'tramo': self.tramo_a,
                    'nombre': 'RESERVA-CRUZADA',
                    'latitud': 0,
                    'longitud': 0,
                },
            ),
        ):
            with self.subTest(modelo=modelo.__name__):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        modelo.objects.bulk_create([modelo(**datos)])


class NormalizacionCanonicaTests(TestCase):
    def test_site_se_enlaza_con_nodo_por_coincidencia_exacta(self):
        site = HubSite.objects.create(nombre='SITE-CANONICO')
        nodo = NodoRed.objects.create(
            tipo='SITE',
            codigo='site-canonico',
        )

        self.assertEqual(nodo.hub_site_obj_id, site.pk)
        self.assertEqual(nodo.codigo, 'SITE-CANONICO')
        self.assertEqual(nodo.nombre, 'SITE-CANONICO')

    def test_tramo_sincroniza_campos_legacy_desde_campos_canonicos(self):
        ruta = Ruta.objects.create(nombre='NORMALIZACION-TRAMO')
        origen = NodoRed.objects.create(tipo='SITE', codigo='CAN-A')
        destino = NodoRed.objects.create(tipo='SITE', codigo='CAN-B')
        tramo = InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            codigo_tramo='CAN-T1',
            origen_nodo=origen,
            destino_nodo=destino,
            capacidad_hilos=24,
            capacidad='dato anterior',
            origen='dato anterior',
            destino='dato anterior',
        )

        self.assertEqual(tramo.capacidad, '24 Hilos')
        self.assertEqual(tramo.origen, 'CAN-A')
        self.assertEqual(tramo.destino, 'CAN-B')

    def test_fibra_no_conserva_extremos_textuales_legacy(self):
        campos = {campo.name for campo in InventarioFibra._meta.get_fields()}

        self.assertNotIn('origen_nodo', campos)
        self.assertNotIn('destino_nodo', campos)
        self.assertNotIn('origen_odf', campos)
        self.assertNotIn('destino', campos)
