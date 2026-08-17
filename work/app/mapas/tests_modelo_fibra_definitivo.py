from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

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
from .services.fibras import (
    asignar_ruta_fibra,
    establecer_estado_fibra_informado,
    restablecer_estado_fibra,
    sincronizar_estado_fibra,
)
from .services.puertos import conectar_puerto
from .views.importacion import (
    _procesar_puertos_odf_inventario,
    _procesar_terminaciones_fibra,
)


def _csv(nombre, contenido):
    return SimpleUploadedFile(
        nombre,
        contenido.encode('utf-8'),
        content_type='text/csv',
    )


class EstadoFibraDefinitivoTests(TestCase):
    def _ruta(self, nombre, cantidad):
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
        return ruta, tramos

    def test_nueva_fibra_no_inventa_disponibilidad(self):
        fibra = InventarioFibra.objects.create(fibra_numero='F1')

        self.assertEqual(fibra.estado, 'SIN_INFORMACION')
        self.assertEqual(fibra.origen_estado, 'NO_INFORMADO')
        self.assertEqual(fibra.condicion_fisica, 'SIN_VERIFICAR')

    def test_modelo_rechaza_estado_conocido_sin_procedencia(self):
        fibra = InventarioFibra(
            fibra_numero='F90',
            estado='OCUPADO',
            origen_estado='NO_INFORMADO',
        )

        with self.assertRaises(ValidationError):
            fibra.full_clean()
        with self.assertRaises(ValidationError):
            fibra.save()

    def test_constraint_rechaza_estado_conocido_sin_procedencia(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            InventarioFibra.objects.bulk_create([
                InventarioFibra(
                    fibra_numero='F91',
                    estado='DISPONIBLE',
                    origen_estado='NO_INFORMADO',
                )
            ])

    def test_combinaciones_de_estado_y_origen_validas(self):
        combinaciones = (
            ('SIN_INFORMACION', 'NO_INFORMADO'),
            ('DISPONIBLE', 'INFORMADO'),
            ('OCUPADO', 'INFORMADO'),
            ('RESERVADO', 'INFORMADO'),
            ('SIN_INFORMACION', 'INFORMADO'),
            ('DISPONIBLE', 'INFERIDO_TRAMOS'),
            ('OCUPADO', 'INFERIDO_TRAMOS'),
            ('RESERVADO', 'INFERIDO_TRAMOS'),
            ('SIN_INFORMACION', 'INFERIDO_TRAMOS'),
        )
        for indice, (estado, origen) in enumerate(combinaciones, start=100):
            with self.subTest(estado=estado, origen=origen):
                fibra = InventarioFibra(
                    fibra_numero=f'F{indice}',
                    estado=estado,
                    origen_estado=origen,
                )
                fibra.full_clean()

    def test_estado_informado_no_es_sobrescrito_por_los_tramos(self):
        ruta, tramos = self._ruta('RUTA-INFORMADA', 2)
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F2',
        )
        for tramo in tramos:
            FibraTramo.objects.create(
                tramo=tramo,
                fibra=fibra,
                numero_hilo='F2',
                estado='OCUPADO',
            )

        establecer_estado_fibra_informado(fibra=fibra, estado='DISPONIBLE')
        sincronizar_estado_fibra(fibra)
        fibra.refresh_from_db()

        self.assertEqual(fibra.estado, 'DISPONIBLE')
        self.assertEqual(fibra.origen_estado, 'INFORMADO')

    def test_restablecer_estado_no_infiere_en_la_misma_accion(self):
        ruta, tramos = self._ruta('RUTA-RECALCULO', 3)
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F3',
            estado='DISPONIBLE',
            origen_estado='INFORMADO',
        )
        for tramo, estado in zip(tramos, ('DISPONIBLE', 'RESERVADO', 'OCUPADO')):
            FibraTramo.objects.create(
                tramo=tramo,
                fibra=fibra,
                numero_hilo='F3',
                estado=estado,
            )

        restablecer_estado_fibra(fibra=fibra)
        fibra.refresh_from_db()

        self.assertEqual(fibra.estado, 'SIN_INFORMACION')
        self.assertEqual(fibra.origen_estado, 'NO_INFORMADO')
        self.assertEqual(
            list(AuditoriaFibra.objects.filter(fibra=fibra).values_list(
                'accion', 'valor_anterior', 'valor_nuevo'
            )),
            [(
                'RESTABLECER_ESTADO',
                'DISPONIBLE/INFORMADO',
                'SIN_INFORMACION/NO_INFORMADO',
            )],
        )

        sincronizar_estado_fibra(fibra, causa='RECALCULO_EXPLICITO')
        fibra.refresh_from_db()
        self.assertEqual(fibra.estado, 'OCUPADO')
        self.assertEqual(fibra.origen_estado, 'INFERIDO_TRAMOS')

    def test_disponible_solo_se_infiere_con_cobertura_completa(self):
        ruta, tramos = self._ruta('RUTA-COBERTURA', 2)
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F4',
        )
        FibraTramo.objects.create(
            tramo=tramos[0],
            fibra=fibra,
            numero_hilo='F4',
            estado='DISPONIBLE',
        )

        sincronizar_estado_fibra(fibra)
        fibra.refresh_from_db()
        self.assertEqual(fibra.estado, 'SIN_INFORMACION')

        FibraTramo.objects.create(
            tramo=tramos[1],
            fibra=fibra,
            numero_hilo='F4',
            estado='DISPONIBLE',
        )
        sincronizar_estado_fibra(fibra)
        fibra.refresh_from_db()
        self.assertEqual(fibra.estado, 'DISPONIBLE')
        self.assertEqual(fibra.origen_estado, 'INFERIDO_TRAMOS')

    def test_asignar_ruta_de_un_tramo_materializa_uno_a_uno_y_audita(self):
        ruta, tramos = self._ruta('RUTA-UN-TRAMO', 1)
        fibra = InventarioFibra.objects.create(
            fibra_numero='F5',
            estado='OCUPADO',
            origen_estado='INFORMADO',
            nombre_fibra='Servicio X',
        )

        fibra, asignada, advertencia = asignar_ruta_fibra(
            fibra=fibra,
            ruta=ruta,
        )

        self.assertTrue(asignada)
        self.assertEqual(advertencia, '')
        detalle = FibraTramo.objects.get(fibra=fibra)
        self.assertEqual(detalle.tramo, tramos[0])
        self.assertEqual(detalle.numero_hilo, 'F5')
        self.assertEqual(detalle.estado, 'OCUPADO')
        fibra.refresh_from_db()
        self.assertEqual(fibra.nombre_fibra, 'Servicio X')
        self.assertTrue(
            AuditoriaFibra.objects.filter(
                fibra=fibra,
                accion='ASIGNAR_RUTA',
            ).exists()
        )

    def test_conflicto_uno_a_uno_conserva_la_ruta_sin_sobrescribir(self):
        ruta, tramos = self._ruta('RUTA-CONFLICTO', 1)
        ocupante = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F99',
        )
        FibraTramo.objects.create(
            tramo=tramos[0],
            fibra=ocupante,
            numero_hilo='F6',
            estado='SIN_INFORMACION',
        )
        fibra = InventarioFibra.objects.create(fibra_numero='F6')

        with self.assertRaises(ValidationError):
            asignar_ruta_fibra(
                fibra=fibra,
                ruta=ruta,
            )

        fibra.refresh_from_db()
        self.assertIsNone(fibra.ruta_id)
        self.assertFalse(FibraTramo.objects.filter(fibra=fibra).exists())
        self.assertEqual(
            FibraTramo.objects.get(tramo=tramos[0], numero_hilo='F6').fibra,
            ocupante,
        )
        self.assertFalse(
            AuditoriaFibra.objects.filter(
                fibra=fibra,
                accion='ASIGNAR_RUTA',
            ).exists()
        )

    def test_ruta_multitramos_no_crea_posiciones_inventadas(self):
        ruta, _ = self._ruta('RUTA-MULTI', 3)
        fibra = InventarioFibra.objects.create(fibra_numero='F7')

        fibra, _, advertencia = asignar_ruta_fibra(fibra=fibra, ruta=ruta)

        self.assertEqual(advertencia, '')
        self.assertFalse(FibraTramo.objects.filter(fibra=fibra).exists())


class ImportacionModeloFibraDefinitivoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = HubSite.objects.create(nombre='SITE-MODELO')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA-MODELO')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK-MODELO')
        cls.odf = InventarioODF.objects.create(
            rack_obj=rack,
            odf='ODF-MODELO',
            capacidad_puertos=4,
        )
        cls.puertos = [
            DetallePuertoODF.objects.create(
                odf_obj=cls.odf,
                puerto_odf=str(numero),
                estado_puerto='LIBRE',
            )
            for numero in range(1, 5)
        ]

    def test_malo_informa_falla_sin_inventar_estado_de_uso(self):
        contenido = '\n'.join([
            'Ruta,Fibra,Codigo Fibra,ODF,Puerto,Extremo,Estado,Servicio,Observaciones',
            ',F8,FGF-BHP-0008,ODF-MODELO,1,A,Malo,Servicio BHP,Falla reportada',
        ])

        _procesar_terminaciones_fibra(_csv('bhp-malo.csv', contenido))

        fibra = InventarioFibra.objects.get(fibra_numero='F8')
        self.assertIsNone(fibra.ruta_id)
        self.assertEqual(fibra.estado, 'SIN_INFORMACION')
        self.assertEqual(fibra.origen_estado, 'NO_INFORMADO')
        self.assertEqual(fibra.condicion_fisica, 'CON_FALLA')
        self.assertEqual(fibra.nombre_fibra, 'Servicio BHP')
        self.assertEqual(fibra.observaciones, 'Falla reportada')
        self.puertos[0].refresh_from_db()
        self.assertEqual(self.puertos[0].estado_puerto, 'OCUPADO')

    def test_estado_y_condicion_se_conservan_como_dimensiones_separadas(self):
        ruta = Ruta.objects.create(nombre='RUTA-BHP-ESTADO')
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F9',
        )
        contenido = '\n'.join([
            'Ruta,ID Fibra,Fibra,Codigo Fibra,ODF,Puerto,Extremo,Estado,Condicion Fisica',
            f'{ruta.nombre},{fibra.pk},F9,{fibra.codigo_fibra},ODF-MODELO,2,A,Ocupado,Con falla',
        ])

        _procesar_terminaciones_fibra(_csv('bhp-estado.csv', contenido))

        fibra.refresh_from_db()
        self.assertEqual(fibra.estado, 'OCUPADO')
        self.assertEqual(fibra.origen_estado, 'INFORMADO')
        self.assertEqual(fibra.condicion_fisica, 'CON_FALLA')

    def test_sin_informacion_queda_no_informado_y_luego_puede_inferirse(self):
        ruta = Ruta.objects.create(nombre='RUTA-BHP-SIN-INFORMACION')
        tramo = InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            codigo_tramo='RUTA-BHP-SIN-INFORMACION-T001',
            capacidad_hilos=12,
        )
        contenido = '\n'.join([
            'Ruta,Fibra,Codigo Fibra,ODF,Puerto,Extremo,Estado',
            f'{ruta.nombre},F11,FGF-BHP-0011,ODF-MODELO,3,A,Sin información',
        ])

        _procesar_terminaciones_fibra(_csv('bhp-sin-info.csv', contenido))

        fibra = InventarioFibra.objects.get(ruta=ruta, fibra_numero='F11')
        self.assertEqual(
            (fibra.estado, fibra.origen_estado),
            ('SIN_INFORMACION', 'NO_INFORMADO'),
        )
        asignacion = FibraTramo.objects.get(fibra=fibra, tramo=tramo)
        asignacion.estado = 'DISPONIBLE'
        asignacion.save(update_fields=['estado'])
        fibra.refresh_from_db()
        self.assertEqual(
            (fibra.estado, fibra.origen_estado),
            ('DISPONIBLE', 'INFERIDO_TRAMOS'),
        )

    def test_dos_f2_sin_ruta_en_odf_distintos_no_se_fusionan_y_reimportan(self):
        site_2 = HubSite.objects.create(nombre='SITE-MODELO-2')
        sala_2 = SalaTecnica.objects.create(hub_site=site_2, nombre='SALA-MODELO-2')
        rack_2 = RackFisico.objects.create(sala=sala_2, nombre='RACK-MODELO-2')
        odf_2 = InventarioODF.objects.create(
            rack_obj=rack_2,
            odf='ODF-MODELO-2',
            capacidad_puertos=2,
        )
        puerto_2 = DetallePuertoODF.objects.create(
            odf_obj=odf_2,
            puerto_odf='2',
            estado_puerto='LIBRE',
        )
        contenido = '\n'.join([
            'Ruta,Fibra,Codigo Fibra,Site,ODF,Puerto,Extremo,Estado',
            ',F2,FGF-SITE-MODELO-0002,SITE-MODELO,ODF-MODELO,4,A,Sin información',
            ',F2,FGF-SITE-MODELO-2-0002,SITE-MODELO-2,ODF-MODELO-2,2,A,Sin información',
        ])

        _procesar_terminaciones_fibra(_csv('dos-f2.csv', contenido))
        ids_primera_carga = set(InventarioFibra.objects.filter(
            ruta__isnull=True,
            fibra_numero='F2',
        ).values_list('pk', flat=True))
        self.assertEqual(len(ids_primera_carga), 2)

        _procesar_terminaciones_fibra(_csv('dos-f2-reimport.csv', contenido))
        self.assertEqual(
            set(InventarioFibra.objects.filter(
                ruta__isnull=True,
                fibra_numero='F2',
            ).values_list('pk', flat=True)),
            ids_primera_carga,
        )
        self.assertEqual(
            self.puertos[3].terminaciones_fibra.get().fibra_id
            in ids_primera_carga,
            True,
        )
        self.assertEqual(
            puerto_2.terminaciones_fibra.get().fibra_id in ids_primera_carga,
            True,
        )

        fibras = list(InventarioFibra.objects.filter(
            pk__in=ids_primera_carga,
        ).order_by('pk'))
        rutas = []
        for indice, fibra in enumerate(fibras, start=1):
            ruta = Ruta.objects.create(nombre=f'RUTA-F2-INDEPENDIENTE-{indice}')
            InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=1,
                codigo_tramo=f'RUTA-F2-INDEPENDIENTE-{indice}-T001',
                capacidad_hilos=12,
            )
            asignar_ruta_fibra(fibra=fibra, ruta=ruta)
            rutas.append(ruta.pk)
        self.assertEqual(
            set(InventarioFibra.objects.filter(
                pk__in=ids_primera_carga,
            ).values_list('ruta_id', flat=True)),
            set(rutas),
        )

    def test_recarga_de_puertos_no_libera_un_puerto_conectado(self):
        fibra = InventarioFibra.objects.create(fibra_numero='F10')
        conectar_puerto(
            puerto_id=self.puertos[2].pk,
            fibra_id=fibra.pk,
            extremo='A',
        )
        contenido = '\n'.join([
            'hub_site,odf,puerto_odf,estado_puerto,observaciones',
            'SITE-MODELO,ODF-MODELO,3,Libre,Metadato actualizado',
        ])

        _procesar_puertos_odf_inventario(
            _csv('puertos-recarga.csv', contenido)
        )

        self.puertos[2].refresh_from_db()
        self.assertEqual(self.puertos[2].estado_puerto, 'OCUPADO')
        self.assertEqual(
            self.puertos[2].observaciones,
            'Metadato actualizado',
        )
