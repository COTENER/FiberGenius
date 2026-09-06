import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import (
    AuditoriaFibra,
    DetallePuertoODF,
    FibraTramo,
    HubSite,
    InventarioFibra,
    InventarioODF,
    InventarioTramo,
    RackFisico,
    Ruta,
    SalaTecnica,
)
from .services.fibras import asignar_ruta_fibra
from .services.puertos import conectar_puerto, reservar_puerto
from .views.importacion import _procesar_terminaciones_fibra


def _csv(nombre, contenido):
    return SimpleUploadedFile(
        nombre,
        contenido.encode('utf-8'),
        content_type='text/csv',
    )


class ResincronizacionTopologiaTests(TestCase):
    def _recorrido(self, nombre, numero='F1', cantidad=2, informado=False):
        ruta = Ruta.objects.create(nombre=nombre)
        tramos = [
            InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=indice,
                codigo_tramo=f'{nombre}-T{indice:03d}',
                capacidad_hilos=24,
            )
            for indice in range(1, cantidad + 1)
        ]
        datos_estado = (
            {'estado': 'DISPONIBLE', 'origen_estado': 'INFORMADO'}
            if informado else {}
        )
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero=numero,
            **datos_estado,
        )
        asignaciones = [
            FibraTramo.objects.create(
                tramo=tramo,
                fibra=fibra,
                numero_hilo=numero,
                estado='DISPONIBLE',
            )
            for tramo in tramos
        ]
        fibra.refresh_from_db()
        return ruta, tramos, fibra, asignaciones

    def test_agregar_y_eliminar_tramo_resincroniza_solo_afectadas(self):
        ruta, _, fibra, _ = self._recorrido('TOPO-AGREGAR')
        self.assertEqual((fibra.estado, fibra.origen_estado), (
            'DISPONIBLE', 'INFERIDO_TRAMOS'
        ))

        nuevo = InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=3,
            codigo_tramo='TOPO-AGREGAR-T003',
            capacidad_hilos=24,
        )
        fibra.refresh_from_db()
        self.assertEqual((fibra.estado, fibra.origen_estado), (
            'SIN_INFORMACION', 'INFERIDO_TRAMOS'
        ))

        nuevo.delete()
        fibra.refresh_from_db()
        self.assertEqual((fibra.estado, fibra.origen_estado), (
            'DISPONIBLE', 'INFERIDO_TRAMOS'
        ))

    def test_eliminar_fibra_tramo_invalida_cobertura(self):
        _, _, fibra, asignaciones = self._recorrido('TOPO-ELIMINAR-FT')
        asignaciones[-1].delete()
        fibra.refresh_from_db()

        self.assertEqual((fibra.estado, fibra.origen_estado), (
            'SIN_INFORMACION', 'INFERIDO_TRAMOS'
        ))
        self.assertTrue(AuditoriaFibra.objects.filter(
            fibra=fibra,
            accion='INFERIR_ESTADO',
            metadatos__causa='ELIMINAR_FIBRA_TRAMO',
        ).exists())

    def test_mover_fibra_tramo_resincroniza_fibra_vieja_y_nueva(self):
        ruta, tramos, fibra_a, asignaciones_a = self._recorrido(
            'TOPO-MOVER', numero='F1'
        )
        fibra_b = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F2',
        )
        FibraTramo.objects.create(
            tramo=tramos[1],
            fibra=fibra_b,
            numero_hilo='F2',
            estado='DISPONIBLE',
        )

        movida = asignaciones_a[0]
        movida.fibra = fibra_b
        movida.numero_hilo = 'F2'
        movida.save(update_fields=['fibra', 'numero_hilo'])
        fibra_a.refresh_from_db()
        fibra_b.refresh_from_db()

        self.assertEqual(fibra_a.estado, 'SIN_INFORMACION')
        self.assertEqual(fibra_b.estado, 'DISPONIBLE')
        self.assertEqual(fibra_b.origen_estado, 'INFERIDO_TRAMOS')

    def test_topologia_no_sobrescribe_estado_informado(self):
        ruta, _, fibra, _ = self._recorrido(
            'TOPO-INFORMADA', informado=True
        )
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=3,
            codigo_tramo='TOPO-INFORMADA-T003',
            capacidad_hilos=24,
        )
        fibra.refresh_from_db()
        self.assertEqual((fibra.estado, fibra.origen_estado), (
            'DISPONIBLE', 'INFORMADO'
        ))


class ApiParcialFibraTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_superuser(
            username='admin-api-parcial-fibra',
            email='api-parcial@example.com',
            password='clave-segura',
        )
        cls.fibra = InventarioFibra.objects.create(
            fibra_numero='F40',
            estado='DISPONIBLE',
            origen_estado='INFORMADO',
            nombre_fibra='Servicio CCTV',
            observaciones='No borrar',
            tipo_conector='LC',
            condicion_fisica='OPERATIVA',
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _actualizar(self, datos):
        return self.client.post(
            reverse('api_update_fibra'),
            data=json.dumps({'id': self.fibra.pk, **datos}),
            content_type='application/json',
        )

    def test_payload_parcial_conserva_campos_ausentes(self):
        respuesta = self._actualizar({'estado': 'OCUPADO'})
        self.assertEqual(respuesta.status_code, 200)
        self.fibra.refresh_from_db()

        self.assertEqual(self.fibra.nombre_fibra, 'Servicio CCTV')
        self.assertEqual(self.fibra.observaciones, 'No borrar')
        self.assertEqual(self.fibra.tipo_conector, 'LC')
        self.assertEqual(self.fibra.condicion_fisica, 'OPERATIVA')

    def test_vacio_explicito_limpia_texto_y_restablece_condicion(self):
        respuesta = self._actualizar({
            'servicio': '',
            'observaciones': '',
            'tipo_conector': '',
            'condicion_fisica': '',
        })
        self.assertEqual(respuesta.status_code, 200)
        self.fibra.refresh_from_db()

        self.assertEqual(self.fibra.nombre_fibra, '')
        self.assertEqual(self.fibra.observaciones, '')
        self.assertEqual(self.fibra.tipo_conector, '')
        self.assertEqual(self.fibra.condicion_fisica, 'SIN_VERIFICAR')


class ApiParcialPuertoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_superuser(
            username='admin-api-parcial-puerto',
            email='api-parcial-puerto@example.com',
            password='clave-segura',
        )
        site = HubSite.objects.create(nombre='SITE-API-PUERTO')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA-API-PUERTO')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK-API-PUERTO')
        odf = InventarioODF.objects.create(
            rack_obj=rack,
            odf='ODF-API-PUERTO',
            capacidad_puertos=1,
        )
        cls.puerto = DetallePuertoODF.objects.create(
            odf_obj=odf,
            puerto_odf='1',
            bandeja='B1',
            estado_puerto='LIBRE',
            tipo_conector='SC/APC',
            patchcord='Si',
            destino='Router externo',
            observaciones='No borrar',
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def test_payload_parcial_de_conector_conserva_los_demás_campos(self):
        respuesta = self.client.post(
            reverse('api_update_puerto'),
            data=json.dumps({'id': self.puerto.pk, 'conector': 'LC'}),
            content_type='application/json',
        )

        self.assertEqual(respuesta.status_code, 200)
        self.puerto.refresh_from_db()
        self.assertEqual(self.puerto.tipo_conector, 'LC')
        self.assertEqual(self.puerto.puerto_odf, '1')
        self.assertEqual(self.puerto.bandeja, 'B1')
        self.assertEqual(self.puerto.patchcord, 'Si')
        self.assertEqual(self.puerto.destino, 'Router externo')
        self.assertEqual(self.puerto.observaciones, 'No borrar')


class FibraTramoAdminSoloLecturaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = get_user_model().objects.create_superuser(
            username='admin-fibra-tramo-readonly',
            email='fibra-tramo-readonly@example.com',
            password='clave-segura',
        )
        ruta = Ruta.objects.create(nombre='RUTA-ADMIN-FIBRA-TRAMO')
        tramo = InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            codigo_tramo='RUTA-ADMIN-FIBRA-TRAMO-T001',
            capacidad_hilos=12,
        )
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F1',
        )
        cls.asignacion = FibraTramo.objects.create(
            tramo=tramo,
            fibra=fibra,
            numero_hilo='F1',
            estado='SIN_INFORMACION',
        )

    def setUp(self):
        self.client.force_login(self.usuario)

    def test_admin_permite_consultar_pero_no_mutar_fibra_tramo(self):
        self.assertEqual(
            self.client.get(reverse('admin:mapas_fibratramo_changelist')).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse('admin:mapas_fibratramo_add')).status_code,
            403,
        )
        detalle = self.client.get(reverse(
            'admin:mapas_fibratramo_change',
            args=[self.asignacion.pk],
        ))
        self.assertEqual(detalle.status_code, 200)
        self.assertNotContains(detalle, 'name="_save"')
        self.assertEqual(
            self.client.post(reverse(
                'admin:mapas_fibratramo_delete',
                args=[self.asignacion.pk],
            )).status_code,
            403,
        )


class ImportacionBhpEndurecidaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.site_a, cls.odf_a = cls._odf('SITE-BHP-A', 'ODF-BHP-A')
        cls.site_b, cls.odf_b = cls._odf('SITE-BHP-B', 'ODF-BHP-B')
        cls.puertos_a = [cls._puerto(cls.odf_a, numero) for numero in range(1, 9)]
        cls.puertos_b = [cls._puerto(cls.odf_b, numero) for numero in range(1, 9)]

    @classmethod
    def _odf(cls, site_nombre, odf_nombre):
        site = HubSite.objects.create(nombre=site_nombre)
        sala = SalaTecnica.objects.create(hub_site=site, nombre=f'SALA-{odf_nombre}')
        rack = RackFisico.objects.create(sala=sala, nombre=f'RACK-{odf_nombre}')
        odf = InventarioODF.objects.create(
            rack_obj=rack,
            odf=odf_nombre,
            capacidad_puertos=12,
        )
        return site, odf

    @classmethod
    def _puerto(cls, odf, numero):
        return DetallePuertoODF.objects.create(
            odf_obj=odf,
            puerto_odf=str(numero),
            estado_puerto='LIBRE',
        )

    def _ruta_un_tramo(self, nombre):
        ruta = Ruta.objects.create(nombre=nombre)
        tramo = InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            codigo_tramo=f'{nombre}-T001',
            capacidad_hilos=48,
        )
        return ruta, tramo

    def _fila_larga(self, ruta, fibra, puerto, estado='', extremo='A',
                    site='SITE-BHP-A', odf='ODF-BHP-A', extras=''):
        codigo_fibra = (
            InventarioFibra.objects.filter(
                ruta__nombre=ruta,
                fibra_numero=fibra,
            ).values_list('codigo_fibra', flat=True).first()
            or f'FGF-{ruta}-{fibra}'
        )
        cabecera = (
            'Ruta,Fibra,Codigo Fibra,Site,ODF,Puerto,Extremo,Estado,'
            'Condicion Fisica,Servicio,Observaciones'
        )
        fila = (
            f'{ruta},{fibra},{codigo_fibra},{site},{odf},{puerto},{extremo},{estado},'
            f'{extras}'
        )
        return '\n'.join((cabecera, fila))

    def test_ruta_un_tramo_no_materializa_por_importar_terminacion(self):
        casos = (
            ('Disponible', 'DISPONIBLE'),
            ('Ocupado', 'OCUPADO'),
            ('Reservado', 'RESERVADO'),
            ('Sin informacion', 'SIN_INFORMACION'),
        )
        for indice, (entrada, esperado) in enumerate(casos, start=1):
            with self.subTest(estado=entrada):
                ruta, tramo = self._ruta_un_tramo(f'BHP-UNO-{indice}')
                contenido = self._fila_larga(
                    ruta.nombre, f'F{indice}', indice, entrada
                )
                _procesar_terminaciones_fibra(
                    _csv(f'estado-{indice}.csv', contenido)
                )
                fibra = InventarioFibra.objects.get(
                    ruta=ruta,
                    fibra_numero=f'F{indice}',
                )
                self.assertFalse(FibraTramo.objects.filter(fibra=fibra, tramo=tramo).exists())
                self.assertEqual(fibra.estado, esperado)

    def test_importar_terminacion_no_modifica_posicion_ocupada_del_tramo(self):
        ruta, tramo = self._ruta_un_tramo('BHP-ROLLBACK')
        ocupante = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F48',
        )
        FibraTramo.objects.create(
            tramo=tramo,
            fibra=ocupante,
            numero_hilo='F6',
            estado='DISPONIBLE',
        )
        contenido = self._fila_larga(ruta.nombre, 'F6', 6, 'Ocupado')

        _procesar_terminaciones_fibra(_csv('sin-materializar.csv', contenido))

        self.assertTrue(InventarioFibra.objects.filter(
            ruta=ruta,
            fibra_numero='F6',
        ).exists())
        self.assertTrue(self.puertos_a[5].terminaciones_fibra.exists())
        self.assertEqual(FibraTramo.objects.get(tramo=tramo, numero_hilo='F6').fibra_id, ocupante.pk)

    def test_condicion_con_falla_no_altera_estado_ni_regla_uno_a_uno(self):
        ruta, tramo = self._ruta_un_tramo('BHP-CON-FALLA')
        contenido = '\n'.join((
            (
                'Ruta,Fibra,Codigo Fibra,Site,ODF,Puerto,Extremo,Estado,'
                'Condicion Fisica,Servicio,Observaciones'
            ),
            (
                f'{ruta.nombre},F10,FGF-{ruta.nombre}-F10,SITE-BHP-A,ODF-BHP-A,8,A,'
                'Ocupado,Con falla,Telemetria,Falla confirmada'
            ),
        ))

        _procesar_terminaciones_fibra(_csv('con-falla.csv', contenido))

        fibra = InventarioFibra.objects.get(ruta=ruta, fibra_numero='F10')
        self.assertFalse(FibraTramo.objects.filter(fibra=fibra, tramo=tramo).exists())
        self.assertEqual(fibra.estado, 'OCUPADO')
        self.assertEqual(fibra.condicion_fisica, 'CON_FALLA')

    def test_site_informado_se_valida_contra_jerarquia_odf(self):
        ruta = Ruta.objects.create(nombre='BHP-SITE')
        correcto = self._fila_larga(
            ruta.nombre, 'F20', 7, 'Disponible', site='SITE-BHP-A'
        )
        _procesar_terminaciones_fibra(_csv('site-ok.csv', correcto))
        self.assertTrue(InventarioFibra.objects.filter(
            ruta=ruta,
            fibra_numero='F20',
        ).exists())

        incorrecto = self._fila_larga(
            ruta.nombre, 'F21', 8, 'Disponible', site='SITE-BHP-B'
        )
        with self.assertRaises(ValueError):
            _procesar_terminaciones_fibra(_csv('site-error.csv', incorrecto))
        self.assertFalse(InventarioFibra.objects.filter(
            ruta=ruta,
            fibra_numero='F21',
        ).exists())

    def test_filas_a_b_coherentes_se_unifican_y_conflictos_se_rechazan(self):
        cabecera = (
            'Ruta,Fibra,Codigo Fibra,Site,ODF,Puerto,Extremo,Estado,'
            'Condicion Fisica,Servicio,Observaciones'
        )
        ruta = Ruta.objects.create(nombre='BHP-AB-OK')
        contenido = '\n'.join((
            cabecera,
            f'{ruta.nombre},F30,FGF-BHP-AB-0030,SITE-BHP-A,ODF-BHP-A,1,A,Ocupado,,CCTV,Operativa',
            f'{ruta.nombre},F30,FGF-BHP-AB-0030,SITE-BHP-B,ODF-BHP-B,1,B,Ocupado,,CCTV,Operativa',
        ))
        _procesar_terminaciones_fibra(_csv('ab-ok.csv', contenido))
        fibra = InventarioFibra.objects.get(ruta=ruta, fibra_numero='F30')
        self.assertEqual(fibra.terminaciones.count(), 2)
        self.assertEqual(fibra.nombre_fibra, 'CCTV')

        ruta_conflicto = Ruta.objects.create(nombre='BHP-AB-CONFLICTO')
        conflicto = '\n'.join((
            cabecera,
            f'{ruta_conflicto.nombre},F31,FGF-BHP-AB-0031,SITE-BHP-A,ODF-BHP-A,2,A,Ocupado,,CCTV,',
            f'{ruta_conflicto.nombre},F31,FGF-BHP-AB-0031,SITE-BHP-B,ODF-BHP-B,2,B,Disponible,,Datos,',
        ))
        with self.assertRaises(ValueError):
            _procesar_terminaciones_fibra(
                _csv('ab-conflicto.csv', conflicto)
            )
        self.assertFalse(InventarioFibra.objects.filter(
            ruta=ruta_conflicto,
            fibra_numero='F31',
        ).exists())

    def test_reimportacion_no_mueve_y_no_consume_reserva(self):
        ruta = Ruta.objects.create(nombre='BHP-REIMPORTAR')
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F40',
        )
        conectar_puerto(
            puerto_id=self.puertos_a[2].pk,
            fibra_id=fibra.pk,
            extremo='A',
        )
        mover = self._fila_larga(ruta.nombre, 'F40', 4, 'Disponible')
        with self.assertRaises(ValueError):
            _procesar_terminaciones_fibra(_csv('no-mover.csv', mover))
        self.assertEqual(
            fibra.terminaciones.get(extremo='A').puerto_odf_id,
            self.puertos_a[2].pk,
        )

        reservar_puerto(puerto_id=self.puertos_a[4].pk)
        reservada = self._fila_larga(ruta.nombre, 'F41', 5, 'Disponible')
        with self.assertRaises(ValueError):
            _procesar_terminaciones_fibra(_csv('reserva.csv', reservada))
        self.puertos_a[4].refresh_from_db()
        self.assertEqual(self.puertos_a[4].estado_puerto, 'RESERVADO')


class ImportadorPuertosRetiradoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superusuario = get_user_model().objects.create_superuser(
            username='admin-importador-retirado',
            email='retirado@example.com',
            password='clave-segura',
        )
        cls.usuario_add = get_user_model().objects.create_user(
            username='solo-add-puertos',
            password='clave-segura',
        )
        cls.usuario_add.user_permissions.add(
            Permission.objects.get(codename='add_detallepuertoodf')
        )

    def test_importador_antiguo_esta_explicita_y_totalmente_retirado(self):
        self.client.force_login(self.superusuario)
        respuesta = self.client.post(reverse('api_import_puertos'))
        self.assertEqual(respuesta.status_code, 410)

    def test_permiso_add_solo_no_autoriza_modificar_puertos(self):
        self.client.force_login(self.usuario_add)
        respuesta = self.client.post(reverse('api_import_puertos'))
        self.assertEqual(respuesta.status_code, 403)


class RutaUnTramoIdempotenteTests(TestCase):
    def test_misma_ruta_no_materializa_detalle_ni_auditoria_falsa(self):
        ruta = Ruta.objects.create(nombre='UNO-IDEMPOTENTE')
        tramo = InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            codigo_tramo='UNO-IDEMPOTENTE-T001',
            capacidad_hilos=12,
        )
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F3',
            estado='RESERVADO',
            origen_estado='INFORMADO',
        )

        _, asignada, _ = asignar_ruta_fibra(fibra=fibra, ruta=ruta)

        self.assertFalse(asignada)
        self.assertFalse(FibraTramo.objects.filter(fibra=fibra, tramo=tramo).exists())
        self.assertFalse(AuditoriaFibra.objects.filter(
            fibra=fibra,
            accion='ASIGNAR_RUTA',
        ).exists())
