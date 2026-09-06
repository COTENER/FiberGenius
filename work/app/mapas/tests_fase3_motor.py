from django.test import TestCase

from .models import (
    CoordenadaRuta,
    FibraTramo,
    InventarioFibra,
    InventarioTramo,
    NodoRed,
    Ruta,
)
from .services.capacidad import resumen_ruta


class MotorOperativoRutaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ruta = Ruta.objects.create(nombre='RUTA-MOTOR-FASE-3')
        cls.nodos = [
            NodoRed.objects.create(tipo='PUNTO', codigo=f'MOTOR-{indice}')
            for indice in range(1, 4)
        ]
        cls.tramos = [
            InventarioTramo.objects.create(
                ruta=cls.ruta,
                tramo_secuencia=indice,
                codigo_tramo=f'M-{indice:03}',
                origen_nodo=cls.nodos[indice - 1],
                destino_nodo=cls.nodos[indice],
                distancia_m=distancia,
                reservas_m=reserva,
                capacidad_hilos=4,
            )
            for indice, (distancia, reserva) in enumerate(
                ((100, 5), (200, 7)),
                start=1,
            )
        ]

    def _fibra(self, numero, estados):
        fibra = InventarioFibra.objects.create(
            ruta=self.ruta,
            fibra_numero=numero,
            estado=estados[0],
            origen_estado='INFORMADO',
        )
        for tramo, estado in zip(self.tramos, estados):
            FibraTramo.objects.create(
                tramo=tramo,
                numero_hilo=numero,
                estado=estado,
                fibra=fibra,
            )
        return fibra

    def test_capacidad_declarada_valida_tiene_prioridad(self):
        for tramo in self.tramos:
            tramo.capacidad_hilos = 8
            tramo.save()
        self.ruta.capacidad_declarada_hilos = 6
        self.ruta.save()

        resumen = resumen_ruta(self.ruta)

        self.assertEqual(resumen['capacidad_calculada'], 8)
        self.assertEqual(resumen['capacidad_declarada'], 6)
        self.assertEqual(resumen['capacidad_efectiva'], 6)
        self.assertEqual(resumen['fuente_capacidad'], 'declarado_ruta')

    def test_declaracion_superior_al_cuello_de_botella_no_es_operativa(self):
        self.ruta.capacidad_declarada_hilos = 12
        self.ruta.save()

        resumen = resumen_ruta(self.ruta)

        self.assertEqual(resumen['capacidad_calculada'], 4)
        self.assertEqual(resumen['capacidad_efectiva'], 4)
        self.assertEqual(
            resumen['fuente_capacidad'],
            'calculado_tramos_seguro',
        )
        self.assertEqual(
            resumen['estado_conciliacion_capacidad'],
            'INCONSISTENCIA_CRITICA',
        )

    def test_prioridad_de_estados_y_cierre_con_capacidad(self):
        self._fibra('F1', ('DISPONIBLE', 'OCUPADO'))
        self._fibra('F2', ('DISPONIBLE', 'RESERVADO'))
        self._fibra('F3', ('DISPONIBLE', 'DISPONIBLE'))
        fibra_incompleta = InventarioFibra.objects.create(
            ruta=self.ruta,
            fibra_numero='F4',
            estado='DISPONIBLE',
            origen_estado='INFORMADO',
        )
        FibraTramo.objects.create(
            tramo=self.tramos[0],
            numero_hilo='F4',
            estado='DISPONIBLE',
            fibra=fibra_incompleta,
        )

        resumen = resumen_ruta(self.ruta)

        self.assertEqual(resumen['hilos_ocupados'], 1)
        self.assertEqual(resumen['hilos_reservados'], 1)
        self.assertEqual(resumen['hilos_libres'], 1)
        self.assertEqual(resumen['hilos_sin_estado'], 1)
        self.assertEqual(
            resumen['hilos_ocupados']
            + resumen['hilos_reservados']
            + resumen['hilos_libres']
            + resumen['hilos_sin_estado'],
            resumen['capacidad_efectiva'],
        )

    def test_declaracion_completa_de_estados_tiene_prioridad(self):
        self._fibra('F1', ('DISPONIBLE', 'OCUPADO'))
        self.ruta.capacidad_declarada_hilos = 4
        self.ruta.hilos_ocupados_declarados = 1
        self.ruta.hilos_reservados_declarados = 1
        self.ruta.hilos_libres_declarados = 2
        self.ruta.save()

        resumen = resumen_ruta(self.ruta)

        self.assertEqual(resumen['fuente_estados'], 'declarado_ruta')
        self.assertEqual(resumen['hilos_ocupados'], 1)
        self.assertEqual(resumen['hilos_reservados'], 1)
        self.assertEqual(resumen['hilos_libres'], 2)
        self.assertEqual(resumen['hilos_sin_estado'], 0)
        self.assertEqual(resumen['hilos_ocupados_calculados'], 1)

    def test_declaracion_parcial_no_mezcla_contadores_con_calculados(self):
        self._fibra('F1', ('DISPONIBLE', 'OCUPADO'))
        self.ruta.hilos_ocupados_declarados = 2
        self.ruta.save()

        resumen = resumen_ruta(self.ruta)

        self.assertFalse(resumen['declaracion_estados_completa'])
        self.assertEqual(resumen['fuente_estados'], 'calculado_fibras')
        self.assertEqual(resumen['hilos_ocupados'], 1)

    def test_distancia_y_reservas_siguen_precedencia(self):
        resumen = resumen_ruta(self.ruta)
        self.assertEqual(resumen['distancia_m'], 300)
        self.assertEqual(resumen['fuente_distancia'], 'calculado_tramos')
        self.assertEqual(resumen['reservas_m'], 12)
        self.assertEqual(resumen['fuente_reservas'], 'calculado_tramos')

        self.ruta.distancia_declarada_m = 350
        self.ruta.reservas_declaradas_m = 20
        self.ruta.save()
        resumen = resumen_ruta(self.ruta)

        self.assertEqual(resumen['distancia_m'], 350)
        self.assertEqual(resumen['fuente_distancia'], 'declarado_ruta')
        self.assertEqual(resumen['reservas_m'], 20)
        self.assertEqual(resumen['fuente_reservas'], 'declarado_ruta')

    def test_geometria_es_fallback_si_distancias_de_tramos_incompletas(self):
        InventarioTramo.objects.filter(pk=self.tramos[1].pk).update(
            distancia_m=None,
        )
        CoordenadaRuta.objects.create(
            ruta=self.ruta,
            orden=1,
            latitud='-12.0000000',
            longitud='-77.0000000',
            inicio_segmento=True,
        )
        CoordenadaRuta.objects.create(
            ruta=self.ruta,
            orden=2,
            latitud='-12.0000000',
            longitud='-77.0010000',
        )

        resumen = resumen_ruta(self.ruta)

        self.assertIsNone(resumen['distancia_tramos_m'])
        self.assertFalse(resumen['distancia_tramos_completa'])
        self.assertEqual(resumen['fuente_distancia'], 'calculado_geometria')
        self.assertGreater(resumen['distancia_m'], 100)
