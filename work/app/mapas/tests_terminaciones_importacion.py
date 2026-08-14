from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import (
    DetallePuertoODF,
    HubSite,
    InventarioFibra,
    InventarioODF,
    RackFisico,
    Ruta,
    SalaTecnica,
    TerminacionFibra,
)
from .views.importacion import _procesar_terminaciones_fibra


def _csv(nombre, contenido):
    return SimpleUploadedFile(
        nombre,
        contenido.encode('utf-8'),
        content_type='text/csv',
    )


class ImportacionTerminacionesFibraTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_superuser(
            username='admin-importacion-terminaciones',
            email='importacion@example.com',
            password='clave-segura',
        )
        cls.ruta = Ruta.objects.create(nombre='MEL-TC-TEST')
        cls.fibra_1 = InventarioFibra.objects.create(
            ruta=cls.ruta,
            fibra_numero='F1',
            estado='Libre',
            origen_estado='INFORMADO',
        )
        cls.fibra_2 = InventarioFibra.objects.create(
            ruta=cls.ruta,
            fibra_numero='F2',
            estado='Libre',
            origen_estado='INFORMADO',
        )

        cls.odf_a = cls._crear_odf('SITE-A', 'ODF-A')
        cls.odf_b = cls._crear_odf('SITE-B', 'ODF-B')
        cls.puerto_a_1 = cls._crear_puerto(cls.odf_a, '1')
        cls.puerto_a_2 = cls._crear_puerto(cls.odf_a, '2')
        cls.puerto_b_1 = cls._crear_puerto(cls.odf_b, '1')
        cls.puerto_b_2 = cls._crear_puerto(cls.odf_b, '2')

    @classmethod
    def _crear_odf(cls, site_nombre, odf_nombre):
        site = HubSite.objects.create(nombre=site_nombre)
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA-1')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK-1')
        return InventarioODF.objects.create(
            rack_obj=rack,
            odf=odf_nombre,
            capacidad_puertos=2,
            tipo_conector='LC',
            estado='Activo',
        )

    @classmethod
    def _crear_puerto(cls, odf, numero):
        return DetallePuertoODF.objects.create(
            odf_obj=odf,
            odf=odf.odf,
            puerto_odf=numero,
            estado_puerto='Libre',
            tipo_conector='LC',
        )

    def _contenido(self, fibra='F1', puerto_a='1', puerto_b='1'):
        return '\n'.join([
            (
                'Ruta,Fibra,Codigo Fibra,Nodo A,ODF Extremo A,'
                'Puerto Extremo A,Conector A,Nodo B,ODF Extremo B,'
                'Puerto Extremo B,Conector B'
            ),
            (
                f'MEL-TC-TEST,{fibra},{fibra},SITE-A,ODF-A,'
                f'{puerto_a},LC,SITE-B,ODF-B,{puerto_b},LC'
            ),
        ])

    def test_crea_los_dos_extremos_de_una_fibra(self):
        resultado = _procesar_terminaciones_fibra(
            _csv('terminaciones.csv', self._contenido())
        )

        self.assertEqual(resultado['total'], 1)
        self.assertEqual(resultado['creadas'], 2)
        self.assertEqual(
            list(
                TerminacionFibra.objects.filter(fibra=self.fibra_1)
                .order_by('extremo')
                .values_list('extremo', 'puerto_odf__odf_obj__odf', 'puerto_odf__puerto_odf')
            ),
            [('A', 'ODF-A', '1'), ('B', 'ODF-B', '1')],
        )
        self.puerto_a_1.refresh_from_db()
        self.puerto_b_1.refresh_from_db()
        self.assertEqual(self.puerto_a_1.estado_puerto, 'Ocupado')
        self.assertEqual(self.puerto_b_1.estado_puerto, 'Ocupado')

    def test_recarga_no_mueve_un_extremo_implicitamente(self):
        _procesar_terminaciones_fibra(
            _csv('inicial.csv', self._contenido())
        )
        parche = '\n'.join([
            (
                'Ruta,Fibra,ODF Extremo A,Puerto Extremo A,'
                'Conector A,ODF Extremo B,Puerto Extremo B,Conector B'
            ),
            'MEL-TC-TEST,F1,ODF-A,2,,,,',
        ])

        with self.assertRaisesRegex(ValueError, 'operación de movimiento'):
            _procesar_terminaciones_fibra(_csv('parche.csv', parche))

        self.assertEqual(
            TerminacionFibra.objects.get(
                fibra=self.fibra_1,
                extremo='A',
            ).puerto_odf,
            self.puerto_a_1,
        )
        self.assertEqual(
            TerminacionFibra.objects.get(
                fibra=self.fibra_1,
                extremo='B',
            ).puerto_odf,
            self.puerto_b_1,
        )
        self.puerto_a_1.refresh_from_db()
        self.puerto_a_2.refresh_from_db()
        self.assertEqual(self.puerto_a_1.estado_puerto, 'Ocupado')
        self.assertEqual(self.puerto_a_2.estado_puerto, 'Libre')

    def test_rechaza_un_puerto_asignado_a_otra_fibra_sin_cambios(self):
        _procesar_terminaciones_fibra(
            _csv('inicial.csv', self._contenido())
        )

        with self.assertRaisesRegex(ValueError, 'otra terminación'):
            _procesar_terminaciones_fibra(
                _csv(
                    'conflicto.csv',
                    self._contenido(
                        fibra='F2',
                        puerto_a='1',
                        puerto_b='2',
                    ),
                )
            )

        self.assertFalse(
            TerminacionFibra.objects.filter(fibra=self.fibra_2).exists()
        )
        self.assertEqual(TerminacionFibra.objects.count(), 2)

    def test_rechaza_un_extremo_incompleto(self):
        contenido = '\n'.join([
            'Ruta,Fibra,ODF Extremo A,Puerto Extremo A',
            'MEL-TC-TEST,F1,ODF-A,',
        ])

        with self.assertRaisesRegex(ValueError, 'declarar juntos ODF y puerto'):
            _procesar_terminaciones_fibra(
                _csv('incompleto.csv', contenido)
            )

        self.assertFalse(TerminacionFibra.objects.exists())

    def test_endpoint_generico_ejecuta_la_nueva_carga(self):
        self.client.force_login(self.usuario)

        respuesta = self.client.post(
            reverse(
                'cargar_csv',
                kwargs={'tipo_csv': 'terminaciones_fibra'},
            ),
            {'csv_file': _csv('terminaciones.csv', self._contenido())},
        )

        self.assertRedirects(respuesta, reverse('configuracion'))
        self.assertEqual(TerminacionFibra.objects.count(), 2)

    def test_ruta_vacia_crea_hilo_provisional_y_la_recarga_es_idempotente(self):
        contenido = '\n'.join([
            (
                'Ruta,Fibra,ODF Extremo A,Puerto Extremo A,'
                'ODF Extremo B,Puerto Extremo B'
            ),
            ',F9,ODF-A,2,ODF-B,2',
        ])

        resultado = _procesar_terminaciones_fibra(
            _csv('provisional.csv', contenido)
        )

        self.assertEqual(resultado['creadas'], 2)
        fibra = InventarioFibra.objects.get(
            ruta__isnull=True,
            fibra_numero='F9',
        )
        self.assertEqual(
            set(fibra.terminaciones.values_list('extremo', flat=True)),
            {'A', 'B'},
        )
        self.puerto_a_2.refresh_from_db()
        self.puerto_b_2.refresh_from_db()
        self.assertEqual(self.puerto_a_2.estado_puerto, 'Ocupado')
        self.assertEqual(self.puerto_b_2.estado_puerto, 'Ocupado')

        repeticion = _procesar_terminaciones_fibra(
            _csv('provisional-recarga.csv', contenido)
        )
        self.assertEqual(repeticion['creadas'], 0)
        self.assertEqual(repeticion['actualizadas'], 2)
        self.assertEqual(
            InventarioFibra.objects.filter(
                ruta__isnull=True,
                fibra_numero='F9',
            ).count(),
            1,
        )


class GuiTerminacionesFibraTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_superuser(
            username='admin-terminaciones',
            email='admin@example.com',
            password='clave-segura',
        )
        cls.ruta = Ruta.objects.create(nombre='RUTA-PLANTILLA')
        cls.fibra = InventarioFibra.objects.create(
            ruta=cls.ruta,
            fibra_numero='F1',
            estado='Desconocido',
            origen_odf='SITE-A',
            destino='SITE-B',
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def test_configuracion_muestra_card_y_descargas(self):
        respuesta = self.client.get(reverse('configuracion'))

        self.assertContains(respuesta, 'Cargar Terminaciones de Fibra')
        self.assertContains(respuesta, 'Descargar plantilla precargada')
        self.assertContains(
            respuesta,
            '/static/ejemplos/terminaciones_fibra_ejemplo.csv',
        )

    def test_descarga_plantilla_precargada(self):
        respuesta = self.client.get(
            reverse('plantilla_terminaciones_fibra')
        )
        contenido = respuesta.content.decode('utf-8-sig')

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('ODF Extremo A', contenido)
        self.assertIn('ID Fibra', contenido)
        self.assertIn('Estado Global,Condicion Fisica,Servicio,Observaciones', contenido)
        self.assertIn(f'RUTA-PLANTILLA,{self.fibra.pk},F1,F1,SITE-A', contenido)
