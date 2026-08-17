from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    DetallePuertoODF,
    FibraTramo,
    HubSite,
    InventarioFibra,
    InventarioODF,
    InventarioTramo,
    NodoRed,
    RackFisico,
    Ruta,
    SalaTecnica,
    TerminacionFibra,
)
from .services.calidad import evaluar_completitud_fibra
from .services.trazabilidad import obtener_presentacion_orientada_fibra


class CalidadFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.site_a = cls._site('SITE-CALIDAD-A')
        cls.site_b = cls._site('SITE-CALIDAD-B')
        cls.site_x = cls._site('SITE-CALIDAD-X')
        cls.odf_a = cls._odf(cls.site_a, 'ODF-CALIDAD-A')
        cls.odf_b = cls._odf(cls.site_b, 'ODF-CALIDAD-B')
        cls.odf_x = cls._odf(cls.site_x, 'ODF-CALIDAD-X')
        cls.nodo_a = NodoRed.objects.create(
            tipo='SITE',
            codigo=cls.site_a.nombre,
            hub_site_obj=cls.site_a,
        )
        cls.nodo_b = NodoRed.objects.create(
            tipo='SITE',
            codigo=cls.site_b.nombre,
            hub_site_obj=cls.site_b,
        )

    @classmethod
    def _site(cls, nombre):
        return HubSite.objects.create(nombre=nombre)

    @classmethod
    def _odf(cls, site, nombre):
        sala = SalaTecnica.objects.create(
            hub_site=site,
            nombre=f'SALA-{nombre}',
        )
        rack = RackFisico.objects.create(
            sala=sala,
            nombre=f'RACK-{nombre}',
        )
        return InventarioODF.objects.create(
            rack_obj=rack,
            odf=nombre,
            capacidad_puertos=20,
        )

    def _recorrido(self, nombre, cantidad, asignados=None, posiciones=None):
        ruta = Ruta.objects.create(nombre=nombre)
        nodos = [self.nodo_a]
        for indice in range(1, cantidad):
            nodos.append(NodoRed.objects.create(
                tipo='PUNTO',
                codigo=f'{nombre}-N{indice}',
            ))
        nodos.append(self.nodo_b)
        tramos = []
        for indice in range(cantidad):
            tramos.append(InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=indice + 1,
                codigo_tramo=f'{nombre}-T{indice + 1:03d}',
                origen_nodo=nodos[indice],
                destino_nodo=nodos[indice + 1],
                capacidad_hilos=48,
            ))
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F12',
            estado='Libre',
            origen_estado='INFORMADO',
        )
        asignados = cantidad if asignados is None else asignados
        posiciones = posiciones or ['F12'] * asignados
        for tramo, posicion in zip(tramos[:asignados], posiciones):
            FibraTramo.objects.create(
                tramo=tramo,
                fibra=fibra,
                numero_hilo=posicion,
                estado='Libre',
            )
        return fibra, tramos

    def _terminar(self, fibra, extremo, odf, puerto):
        detalle = DetallePuertoODF.objects.create(
            odf_obj=odf,
            puerto_odf=puerto,
            estado_puerto='Libre',
        )
        TerminacionFibra.objects.create(
            fibra=fibra,
            extremo=extremo,
            puerto_odf=detalle,
        )
        detalle.refresh_from_db()
        return detalle


class CalidadFibraTests(CalidadFixtureMixin, TestCase):
    def test_clasifica_los_cinco_estados_de_completitud(self):
        ruta_vacia = Ruta.objects.create(nombre='CALIDAD-SIN-RECORRIDO')
        sin_recorrido = InventarioFibra.objects.create(
            ruta=ruta_vacia,
            fibra_numero='F1',
        )
        parcial, _ = self._recorrido('CALIDAD-PARCIAL', 3, asignados=1)
        sin_terminaciones, _ = self._recorrido('CALIDAD-SIN-TERMINACIONES', 2)
        terminacion_parcial, _ = self._recorrido('CALIDAD-TERMINACION-PARCIAL', 2)
        self._terminar(terminacion_parcial, 'A', self.odf_a, 'TP-A')
        completo, _ = self._recorrido('CALIDAD-COMPLETO', 3)
        self._terminar(completo, 'A', self.odf_a, 'C-A')
        self._terminar(completo, 'B', self.odf_b, 'C-B')

        casos = (
            (sin_recorrido, 'SIN_RECORRIDO'),
            (parcial, 'RECORRIDO_PARCIAL'),
            (sin_terminaciones, 'SIN_TERMINACIONES'),
            (terminacion_parcial, 'TERMINACION_PARCIAL'),
            (completo, 'COMPLETO'),
        )
        for fibra, esperado in casos:
            with self.subTest(estado=esperado):
                self.assertEqual(
                    evaluar_completitud_fibra(fibra)['estado'],
                    esperado,
                )

    def test_informa_fibra_tramo_faltante(self):
        fibra, tramos = self._recorrido('CALIDAD-FALTANTE', 3, asignados=2)
        calidad = evaluar_completitud_fibra(fibra)
        hallazgo = next(
            item for item in calidad['hallazgos']
            if item['codigo'] == 'FIBRA_TRAMO_FALTANTE'
        )
        self.assertEqual(hallazgo['severidad'], 'ERROR')
        self.assertEqual(hallazgo['tramos'], [tramos[2].codigo_tramo])

    def test_ruta_de_un_tramo_sin_detalle_no_es_cobertura_completa(self):
        ruta = Ruta.objects.create(nombre='CALIDAD-UNO-SIN-DETALLE')
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            codigo_tramo='CALIDAD-UNO-SIN-DETALLE-T001',
            capacidad_hilos=12,
        )
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F1',
        )

        calidad = evaluar_completitud_fibra(fibra)

        self.assertEqual(calidad['estado'], 'SIN_RECORRIDO')
        self.assertFalse(calidad['completo'])
        self.assertIn(
            'SIN_FIBRA_TRAMO',
            {item['codigo'] for item in calidad['hallazgos']},
        )

    def test_detecta_odf_en_site_incorrecto_y_puerto_inconsistente(self):
        fibra, _ = self._recorrido('CALIDAD-SITE', 1)
        puerto_a = self._terminar(fibra, 'A', self.odf_x, 'SITE-A')
        self._terminar(fibra, 'B', self.odf_b, 'SITE-B')
        DetallePuertoODF.objects.filter(pk=puerto_a.pk).update(
            estado_puerto='Libre'
        )

        calidad = evaluar_completitud_fibra(fibra)
        codigos = {item['codigo'] for item in calidad['hallazgos']}
        self.assertEqual(calidad['estado'], 'COMPLETO')
        self.assertFalse(calidad['valido'])
        self.assertIn('ODF_EN_SITE_INCORRECTO', codigos)
        self.assertIn('PUERTO_FIBRA_INCONSISTENTE', codigos)

    def test_extremos_a_b_pueden_estar_geograficamente_invertidos(self):
        fibra, _ = self._recorrido('CALIDAD-AB-TECNICO', 2)
        self._terminar(fibra, 'A', self.odf_b, 'AB-A')
        self._terminar(fibra, 'B', self.odf_a, 'AB-B')

        calidad = evaluar_completitud_fibra(fibra)
        codigos = {item['codigo'] for item in calidad['hallazgos']}

        self.assertEqual(calidad['estado'], 'COMPLETO')
        self.assertNotIn('ODF_EN_SITE_INCORRECTO', codigos)

        presentacion = obtener_presentacion_orientada_fibra(fibra)
        self.assertEqual(presentacion['origen']['extremo'], 'B')
        self.assertEqual(presentacion['destino']['extremo'], 'A')
        self.assertTrue(presentacion['orientacion_completa'])

    def test_dos_terminaciones_en_el_mismo_site_son_inconsistentes(self):
        fibra, _ = self._recorrido('CALIDAD-MISMO-SITE', 2)
        self._terminar(fibra, 'A', self.odf_a, 'MISMO-A')
        self._terminar(fibra, 'B', self.odf_a, 'MISMO-B')

        calidad = evaluar_completitud_fibra(fibra)
        codigos = {item['codigo'] for item in calidad['hallazgos']}

        self.assertIn('ODF_EN_SITE_INCORRECTO', codigos)
        self.assertFalse(calidad['valido'])
        presentacion = obtener_presentacion_orientada_fibra(fibra)
        self.assertIsNone(presentacion['origen'])
        self.assertIsNone(presentacion['destino'])
        self.assertEqual(len(presentacion['extremos_sin_orientar']), 2)
        self.assertFalse(presentacion['orientacion_completa'])

    def test_un_solo_extremo_es_parcial_sin_contradiccion_de_site(self):
        fibra, _ = self._recorrido('CALIDAD-SOLO-A', 2)
        self._terminar(fibra, 'A', self.odf_a, 'SOLO-A')

        calidad = evaluar_completitud_fibra(fibra)
        codigos = {item['codigo'] for item in calidad['hallazgos']}

        self.assertEqual(calidad['estado'], 'TERMINACION_PARCIAL')
        self.assertNotIn('ODF_EN_SITE_INCORRECTO', codigos)
        presentacion = obtener_presentacion_orientada_fibra(fibra)
        self.assertEqual(presentacion['origen']['extremo'], 'A')
        self.assertIsNone(presentacion['destino'])

    def test_global_ocupado_con_detalle_desconocido_o_libre_reporta_calidad(self):
        desconocida, _ = self._recorrido('CALIDAD-DETALLE-DESCONOCIDO', 2)
        desconocida.estado = 'Ocupado'
        desconocida.origen_estado = 'INFORMADO'
        desconocida.save(update_fields=['estado', 'origen_estado'])
        desconocida.asignaciones_tramo.update(estado='Desconocido')
        codigos_desconocida = {
            item['codigo']
            for item in evaluar_completitud_fibra(desconocida)['hallazgos']
        }
        self.assertIn('DETALLE_TRAMO_PENDIENTE', codigos_desconocida)

        libre, _ = self._recorrido('CALIDAD-GLOBAL-CONTRADICCION', 2)
        libre.estado = 'Ocupado'
        libre.origen_estado = 'INFORMADO'
        libre.save(update_fields=['estado', 'origen_estado'])
        codigos_libre = {
            item['codigo']
            for item in evaluar_completitud_fibra(libre)['hallazgos']
        }
        self.assertIn('ESTADO_FIBRA_TRAMO_DIFERENTE', codigos_libre)

    def test_cambio_de_numero_y_estado_son_advertencias(self):
        fibra, _ = self._recorrido(
            'CALIDAD-CAMBIO-HILO',
            2,
            posiciones=['F12', 'F14'],
        )
        asignacion = fibra.asignaciones_tramo.order_by('tramo__tramo_secuencia').last()
        FibraTramo.objects.filter(pk=asignacion.pk).update(estado='Ocupado')
        InventarioFibra.objects.filter(pk=fibra.pk).update(
            origen_estado='INFORMADO'
        )
        fibra.refresh_from_db()
        self._terminar(fibra, 'A', self.odf_a, 'HILO-A')
        self._terminar(fibra, 'B', self.odf_b, 'HILO-B')

        calidad = evaluar_completitud_fibra(fibra)
        hallazgos = {item['codigo']: item for item in calidad['hallazgos']}
        self.assertEqual(hallazgos['CAMBIO_NUMERO_HILO']['severidad'], 'ADVERTENCIA')
        self.assertEqual(
            hallazgos['ESTADO_FIBRA_TRAMO_DIFERENTE']['severidad'],
            'ADVERTENCIA',
        )
        self.assertTrue(calidad['valido'])


class CalidadFibraApiTests(CalidadFixtureMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = get_user_model().objects.create_superuser(
            username='admin-calidad-fibra',
            email='calidad@example.com',
            password='clave-segura',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_api_tabla_y_ficha_360_exponen_el_mismo_diagnostico(self):
        fibra, _ = self._recorrido('CALIDAD-API', 2)
        self._terminar(fibra, 'A', self.odf_a, 'API-A')

        respuesta = self.client.get(
            reverse('api_fibras_paginadas'),
            {'ruta': fibra.ruta.nombre},
        )
        self.assertEqual(respuesta.status_code, 200)
        item = respuesta.json()['data'][0]
        self.assertEqual(item['completitud'], 'Terminación parcial')
        self.assertEqual(item['calidad']['estado'], 'TERMINACION_PARCIAL')
        self.assertEqual(item['calidad']['terminaciones']['confirmadas'], 1)

        ficha = self.client.get(reverse('asset_360', args=['fibra', fibra.pk]))
        self.assertEqual(ficha.status_code, 200)
        data = ficha.json()['data']
        self.assertEqual(data['status'], 'Disponible')
        self.assertIn(
            {'label': 'Completitud', 'value': 'Terminación parcial'},
            data['summary'],
        )
        self.assertEqual(data['sections'][0]['title'], 'Calidad y validaciones')

    def test_pantalla_incorpora_columna_de_completitud(self):
        respuesta = self.client.get(reverse('planta_externa'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, '<th scope="col">Completitud</th>', html=True)
        self.assertContains(respuesta, 'fiber-operations-8')
        self.assertContains(respuesta, 'data-fiber-destination-context')
        self.assertContains(respuesta, 'data-clear-fiber-destination')
        self.assertContains(respuesta, 'data-fiber-state="Libre"')
        self.assertContains(respuesta, 'Estado efectivo de fibras')

    def test_resumen_usa_estado_global_aunque_falte_cobertura_fisica(self):
        fibra, _ = self._recorrido(
            'CALIDAD-RESUMEN-GLOBAL',
            2,
            asignados=1,
        )

        respuesta = self.client.get(
            reverse('api_fibras_paginadas'),
            {'ruta': fibra.ruta.nombre},
        )

        self.assertEqual(respuesta.status_code, 200)
        resumen = respuesta.json()['summary']
        self.assertEqual(resumen['total'], 1)
        self.assertEqual(resumen['libres'], 1)
        self.assertEqual(resumen['sin_estado'], 0)
        self.assertEqual(resumen['sin_cobertura'], 1)
        self.assertEqual(resumen['estados_informados'], 1)
        self.assertEqual(resumen['estados_inferidos'], 0)

    def test_resumen_infiere_estado_desde_tramos_si_global_no_esta_informado(self):
        fibra, _ = self._recorrido('CALIDAD-RESUMEN-INFERIDO', 2)
        InventarioFibra.objects.filter(pk=fibra.pk).update(
            estado='Desconocido',
            origen_estado='NO_INFORMADO',
            destino='DESTINO-INFERIDO',
        )
        FibraTramo.objects.filter(fibra=fibra).update(estado='Ocupado')

        respuesta = self.client.get(
            reverse('api_fibras_paginadas'),
            {'ruta': fibra.ruta.nombre},
        )

        self.assertEqual(respuesta.status_code, 200)
        payload = respuesta.json()
        self.assertEqual(payload['summary']['ocupadas'], 1)
        self.assertEqual(payload['summary']['sin_estado'], 0)
        self.assertEqual(payload['summary']['estados_informados'], 0)
        self.assertEqual(payload['summary']['estados_inferidos'], 1)
        self.assertEqual(payload['data'][0]['estado'], 'Ocupado')
        self.assertEqual(
            payload['data'][0]['origen_estado'],
            'INFERIDO_TRAMOS',
        )
        self.assertEqual(
            payload['summary']['destinos_mayor_uso'][0]['destino'],
            'DESTINO-INFERIDO',
        )
        filtrada = self.client.get(
            reverse('api_fibras_paginadas'),
            {'ruta': fibra.ruta.nombre, 'estado': 'Ocupado'},
        ).json()
        self.assertEqual(filtrada['pagination']['total'], 1)
        ficha = self.client.get(
            reverse('asset_360', args=['fibra', fibra.pk]),
        ).json()['data']
        self.assertEqual(ficha['status'], 'Ocupado')
        self.assertIn(
            {'label': 'Origen del estado', 'value': 'Inferido desde tramos'},
            ficha['summary'],
        )

    def test_destino_mayor_uso_filtra_solo_ocupadas_y_reservadas(self):
        ocupada, _ = self._recorrido('CALIDAD-DESTINO-OCUPADA', 1)
        disponible, _ = self._recorrido('CALIDAD-DESTINO-DISPONIBLE', 1)
        InventarioFibra.objects.filter(pk=ocupada.pk).update(
            estado='Ocupado',
            origen_estado='INFORMADO',
            destino='DESTINO-COMPARTIDO',
        )
        InventarioFibra.objects.filter(pk=disponible.pk).update(
            estado='Libre',
            origen_estado='INFORMADO',
            destino='DESTINO-COMPARTIDO',
        )

        resumen = self.client.get(reverse('api_fibras_paginadas')).json()[
            'summary'
        ]
        destino = next(
            item
            for item in resumen['destinos_mayor_uso']
            if item['destino'] == 'DESTINO-COMPARTIDO'
        )
        self.assertEqual(destino['total'], 1)
        self.assertEqual(destino['total_asociadas'], 2)
        self.assertEqual(destino['porcentaje_uso'], 50.0)

        filtrada = self.client.get(
            reverse('api_fibras_paginadas'),
            {
                'destino': 'DESTINO-COMPARTIDO',
                'en_uso': '1',
            },
        ).json()
        self.assertEqual(filtrada['pagination']['total'], 1)
        self.assertEqual(filtrada['data'][0]['estado'], 'Ocupado')
        destino_filtrado = filtrada['summary']['destinos_mayor_uso'][0]
        self.assertEqual(destino_filtrado['total'], 1)
        self.assertEqual(destino_filtrado['total_asociadas'], 2)

    def test_pantalla_muestra_los_cuatro_estados_globales(self):
        respuesta = self.client.get(reverse('planta_externa'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Disponibles')
        self.assertContains(respuesta, 'Sin información')
        self.assertContains(respuesta, 'fibras-stat-unknown')
        self.assertContains(respuesta, 'fiber-summary-unknown')
        self.assertContains(respuesta, 'value="Desconocido"')
