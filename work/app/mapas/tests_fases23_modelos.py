import importlib
from types import SimpleNamespace

from django.contrib.admin.sites import AdminSite
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from .admin import InventarioFibraAdmin, InventarioTramoAdmin, NodoRedAdmin
from .models import (
    FibraTramo,
    CoordenadaRuta,
    HubSite,
    InventarioFibra,
    InventarioODF,
    InventarioTramo,
    NodoRed,
    RackFisico,
    Reserva,
    Ruta,
    SalaTecnica,
)
from .services.capacidad import resumen_ruta
from .services.topologia import normalizar_tipo_nodo


class NodoRedModeloTests(TestCase):
    def test_normaliza_tipos_de_nodo_con_acentos_y_espacios(self):
        self.assertEqual(normalizar_tipo_nodo('Cámara'), 'CAMARA')
        self.assertEqual(
            normalizar_tipo_nodo('Caja de empalme'),
            'CAJA_EMPALME',
        )

    def test_tipo_y_codigo_son_unicos_sin_distinguir_mayusculas(self):
        NodoRed.objects.create(
            tipo='SITE',
            codigo=' ER17 ',
            nombre='Estación ER17',
        )

        with self.assertRaises(ValidationError):
            NodoRed.objects.create(
                tipo='site',
                codigo='er17',
                nombre='Duplicado',
            )

        self.assertEqual(NodoRed.objects.get().codigo, 'ER17')

    def test_mismo_codigo_es_valido_para_tipos_distintos(self):
        site = NodoRed.objects.create(tipo='SITE', codigo='A1')
        odf = NodoRed.objects.create(tipo='ODF', codigo='a1')

        self.assertNotEqual(site.pk, odf.pk)
        self.assertEqual(odf.codigo, 'A1')

    def test_identidad_no_cambia_cuando_el_nodo_esta_referenciado(self):
        ruta = Ruta.objects.create(nombre='RUTA-NODO-INMUTABLE')
        nodo = NodoRed.objects.create(
            tipo='SITE',
            codigo='SITE-A',
            nombre='Nombre anterior',
        )
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            origen_nodo=nodo,
        )

        nodo.codigo = 'SITE-B'
        with self.assertRaisesRegex(ValidationError, 'referenciado'):
            nodo.save()

        nodo.refresh_from_db()
        self.assertEqual(nodo.codigo, 'SITE-A')
        nodo.nombre = 'Nombre actualizado'
        nodo.save()
        self.assertEqual(nodo.nombre, 'Nombre actualizado')

    def test_tipo_invalido_esta_protegido_por_constraint(self):
        nodo = NodoRed.objects.create(tipo='SITE', codigo='SITE-VALIDO')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                NodoRed.objects.filter(pk=nodo.pk).update(tipo='INVALIDO')


class FibraTramoModeloTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ruta_a = Ruta.objects.create(nombre='RUTA-A')
        cls.ruta_b = Ruta.objects.create(nombre='RUTA-B')
        cls.tramo_a = InventarioTramo.objects.create(
            ruta=cls.ruta_a,
            tramo_secuencia=1,
            codigo_tramo='TA-01',
            capacidad_hilos=24,
        )
        cls.fibra_a = InventarioFibra.objects.create(
            ruta=cls.ruta_a,
            fibra_numero='F1',
            estado='Ocupado',
            origen_estado='INFORMADO',
        )
        cls.fibra_b = InventarioFibra.objects.create(
            ruta=cls.ruta_b,
            fibra_numero='F1',
            estado='Libre',
            origen_estado='INFORMADO',
        )

    def test_rechaza_fibra_y_tramo_de_rutas_distintas(self):
        with self.assertRaises(ValidationError):
            FibraTramo.objects.create(
                tramo=self.tramo_a,
                numero_hilo='F1',
                estado='Libre',
                fibra=self.fibra_b,
            )

        self.assertFalse(FibraTramo.objects.exists())

    def test_numero_fisico_es_unico_por_tramo_sin_distinguir_mayusculas(self):
        FibraTramo.objects.create(
            tramo=self.tramo_a,
            numero_hilo='F1',
            estado='Ocupado',
            fibra=self.fibra_a,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FibraTramo.objects.bulk_create([
                    FibraTramo(
                        tramo=self.tramo_a,
                        numero_hilo='f1',
                        estado='Libre',
                    )
                ])

        self.assertEqual(FibraTramo.objects.count(), 1)

    def test_una_fibra_logica_no_se_repite_en_el_mismo_tramo(self):
        FibraTramo.objects.create(
            tramo=self.tramo_a,
            numero_hilo='F1',
            estado='Ocupado',
            fibra=self.fibra_a,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FibraTramo.objects.bulk_create([
                    FibraTramo(
                        tramo=self.tramo_a,
                        numero_hilo='F2',
                        estado='Ocupado',
                        fibra=self.fibra_a,
                    )
                ])

        self.assertEqual(FibraTramo.objects.count(), 1)

    def test_modelo_rechaza_formato_o_posicion_fuera_de_capacidad(self):
        with self.assertRaises(ValidationError):
            FibraTramo.objects.create(
                tramo=self.tramo_a,
                numero_hilo='HILO-1',
                estado='Libre',
            )
        with self.assertRaises(ValidationError):
            FibraTramo.objects.create(
                tramo=self.tramo_a,
                numero_hilo='F25',
                estado='Libre',
            )

        self.assertFalse(FibraTramo.objects.exists())

    def test_tramo_con_fibras_no_se_puede_mover_a_otra_ruta(self):
        FibraTramo.objects.create(
            tramo=self.tramo_a,
            numero_hilo='F1',
            estado='Ocupado',
            fibra=self.fibra_a,
        )

        self.tramo_a.ruta = self.ruta_b
        with self.assertRaisesRegex(ValidationError, 'mover el tramo'):
            self.tramo_a.save()

        self.tramo_a.refresh_from_db()
        self.assertEqual(self.tramo_a.ruta_id, self.ruta_a.pk)

    def test_capacidad_no_baja_de_una_posicion_existente(self):
        fibra = InventarioFibra.objects.create(
            ruta=self.ruta_a,
            fibra_numero='F24',
            estado='Libre',
            origen_estado='INFORMADO',
        )
        FibraTramo.objects.create(
            tramo=self.tramo_a,
            numero_hilo='F24',
            estado='Libre',
            fibra=fibra,
        )

        self.tramo_a.capacidad_hilos = 12
        with self.assertRaisesRegex(ValidationError, 'F24'):
            self.tramo_a.save()

        self.tramo_a.refresh_from_db()
        self.assertEqual(self.tramo_a.capacidad_hilos, 24)

    def test_estados_invalidos_estan_protegidos_por_constraints(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InventarioFibra.objects.bulk_create([
                    InventarioFibra(
                        ruta=self.ruta_a,
                        fibra_numero='F2',
                        estado='INVALIDO',
                    ),
                ])

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FibraTramo.objects.bulk_create([
                    FibraTramo(
                        tramo=self.tramo_a,
                        numero_hilo='F2',
                        estado='INVALIDO',
                    ),
                ])


class IdentidadRutaTests(TestCase):
    def test_nombre_ruta_es_unico_sin_distinguir_mayusculas(self):
        Ruta.objects.create(nombre='RUTA-CI')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Ruta.objects.bulk_create([
                    Ruta(nombre='ruta-ci'),
                ])


class DeclaracionRutaTests(TestCase):
    def test_cero_declarado_es_distinto_de_ausencia_de_dato(self):
        ruta = Ruta.objects.create(
            nombre='RUTA-DECLARACION-CERO',
            hilos_ocupados_declarados=0,
            hilos_reservados_declarados=0,
            hilos_libres_declarados=0,
            reservas_declaradas_m=0,
        )

        ruta.refresh_from_db()
        self.assertEqual(ruta.hilos_ocupados_declarados, 0)
        self.assertEqual(ruta.reservas_declaradas_m, 0)
        self.assertIsNone(ruta.capacidad_declarada_hilos)
        self.assertIsNone(ruta.distancia_declarada_m)

    def test_contadores_declarados_no_superan_capacidad(self):
        with self.assertRaisesRegex(
            ValidationError,
            'no puede superar la capacidad declarada',
        ):
            Ruta.objects.create(
                nombre='RUTA-DECLARACION-INVALIDA',
                capacidad_declarada_hilos=24,
                hilos_ocupados_declarados=20,
                hilos_reservados_declarados=3,
                hilos_libres_declarados=2,
            )

        self.assertFalse(
            Ruta.objects.filter(nombre='RUTA-DECLARACION-INVALIDA').exists()
        )

    def test_rechaza_declaraciones_fisicas_negativas_o_capacidad_cero(self):
        ruta = Ruta.objects.create(nombre='RUTA-CONSTRAINT-DECLARACION')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Ruta.objects.filter(pk=ruta.pk).update(
                    distancia_declarada_m=-1,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Ruta.objects.filter(pk=ruta.pk).update(
                    capacidad_declarada_hilos=0,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Ruta.objects.filter(pk=ruta.pk).update(
                    reservas_declaradas_m=-1,
                )


class CompletitudTramoTests(TestCase):
    def setUp(self):
        self.ruta = Ruta.objects.create(nombre='RUTA-COMPLETITUD')
        self.origen = NodoRed.objects.create(tipo='SITE', codigo='ORIGEN-C')
        self.destino = NodoRed.objects.create(tipo='SITE', codigo='DESTINO-C')

    def test_tramo_incompleto_conserva_datos_legacy(self):
        tramo = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=1,
        )

        self.assertEqual(tramo.estado_inventario, 'INCOMPLETO')

    def test_tramo_validado_exige_identidad_extremos_distancia_y_capacidad(self):
        with self.assertRaises(ValidationError) as contexto:
            InventarioTramo.objects.create(
                ruta=self.ruta,
                tramo_secuencia=1,
                estado_inventario='VALIDADO',
            )

        self.assertEqual(
            set(contexto.exception.message_dict),
            {
                'codigo_tramo',
                'origen_nodo',
                'destino_nodo',
                'distancia_m',
                'capacidad_hilos',
            },
        )

    def test_tramo_completo_puede_validarse(self):
        tramo = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=1,
            codigo_tramo='T-C-001',
            origen_nodo=self.origen,
            destino_nodo=self.destino,
            distancia_m=1000,
            capacidad_hilos=24,
            hilos_ocupados=10,
            hilos_reservados=2,
            hilos_libres=12,
            estado_inventario='VALIDADO',
        )

        self.assertEqual(tramo.estado_inventario, 'VALIDADO')

    def test_contadores_de_tramo_no_superan_capacidad(self):
        with self.assertRaisesRegex(
            ValidationError,
            'no puede superar la capacidad del tramo',
        ):
            InventarioTramo.objects.create(
                ruta=self.ruta,
                tramo_secuencia=1,
                codigo_tramo='T-C-INVALIDO',
                origen_nodo=self.origen,
                destino_nodo=self.destino,
                distancia_m=1000,
                capacidad_hilos=12,
                hilos_ocupados=8,
                hilos_reservados=3,
                hilos_libres=2,
                estado_inventario='VALIDADO',
            )


class RecorridoFibraTests(TestCase):
    def test_extremos_del_recorrido_deben_ser_distintos(self):
        ruta = Ruta.objects.create(nombre='RUTA-RECORRIDO')
        nodo = NodoRed.objects.create(tipo='SITE', codigo='NODO-RECORRIDO')

        with self.assertRaisesRegex(
            ValidationError,
            'deben ser distintos',
        ):
            InventarioFibra.objects.create(
                ruta=ruta,
                fibra_numero='F1',
                estado='Libre',
                origen_estado='INFORMADO',
                origen_nodo=nodo,
                destino_nodo=nodo,
            )


class AdministracionIdentidadesTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.ruta = Ruta.objects.create(nombre='RUTA-ADMIN-IDENTIDAD')
        self.nodo = NodoRed.objects.create(tipo='SITE', codigo='SITE-ADMIN')
        self.tramo = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=1,
            codigo_tramo='T-ADMIN-001',
            origen_nodo=self.nodo,
            capacidad_hilos=12,
        )
        self.fibra = InventarioFibra.objects.create(
            ruta=self.ruta,
            fibra_numero='F1',
            estado='Libre',
            origen_estado='INFORMADO',
        )
        FibraTramo.objects.create(
            tramo=self.tramo,
            numero_hilo='F1',
            estado='Libre',
            fibra=self.fibra,
        )

    def test_admin_protege_identidades_ya_relacionadas(self):
        tramo_admin = InventarioTramoAdmin(InventarioTramo, self.site)
        nodo_admin = NodoRedAdmin(NodoRed, self.site)
        fibra_admin = InventarioFibraAdmin(InventarioFibra, self.site)

        self.assertEqual(
            set(tramo_admin.get_readonly_fields(None, self.tramo)),
            {
                'ruta',
                'tramo_secuencia',
                'codigo_tramo',
                'origen_nodo',
                'destino_nodo',
            },
        )
        self.assertEqual(
            set(nodo_admin.get_readonly_fields(None, self.nodo)),
            {'tipo', 'codigo'},
        )
        self.assertEqual(
            set(fibra_admin.get_readonly_fields(None, self.fibra)),
            {'ruta', 'origen_estado', 'codigo_fibra'},
        )


class CapacidadFases23Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ruta = Ruta.objects.create(nombre='RUTA-64-48-24')
        nodos = [
            NodoRed.objects.create(tipo='SITE', codigo=f'NODO-{indice}')
            for indice in range(1, 5)
        ]
        cls.tramos = [
            InventarioTramo.objects.create(
                ruta=cls.ruta,
                tramo_secuencia=secuencia,
                codigo_tramo=f'T-{secuencia:02}',
                origen_nodo=nodos[secuencia - 1],
                destino_nodo=nodos[secuencia],
                capacidad_hilos=capacidad,
            )
            for secuencia, capacidad in enumerate((64, 48, 24), start=1)
        ]
        cls.fibra_completa = InventarioFibra.objects.create(
            ruta=cls.ruta,
            fibra_numero='LOGICA-01',
            estado='Ocupado',
            origen_estado='INFORMADO',
        )
        cls.fibra_parcial = InventarioFibra.objects.create(
            ruta=cls.ruta,
            fibra_numero='LOGICA-02',
            estado='Libre',
            origen_estado='INFORMADO',
        )

        for indice, tramo in enumerate(cls.tramos, start=1):
            FibraTramo.objects.create(
                tramo=tramo,
                numero_hilo=f'F{indice}',
                estado='Ocupado',
                fibra=cls.fibra_completa,
            )
        for indice, tramo in enumerate(cls.tramos[:2], start=10):
            FibraTramo.objects.create(
                tramo=tramo,
                numero_hilo=f'F{indice}',
                estado='Libre',
                fibra=cls.fibra_parcial,
            )

    def test_capacidad_efectiva_es_el_cuello_de_botella(self):
        resumen = resumen_ruta(self.ruta)

        self.assertEqual(resumen['capacidad_min'], 24)
        self.assertEqual(resumen['capacidad_max'], 64)
        self.assertEqual(resumen['capacidad_efectiva'], 24)
        self.assertTrue(resumen['capacidades_completas'])
        self.assertEqual(resumen['tramos_total'], 3)

    def test_capacidad_efectiva_es_desconocida_si_falta_un_tramo(self):
        InventarioTramo.objects.filter(pk=self.tramos[1].pk).update(
            capacidad_hilos=None,
            capacidad=None,
        )

        resumen = resumen_ruta(self.ruta)

        self.assertEqual(resumen['capacidad_min'], 24)
        self.assertEqual(resumen['capacidad_max'], 64)
        self.assertIsNone(resumen['capacidad_efectiva'])
        self.assertFalse(resumen['capacidades_completas'])

    def test_posiciones_ausentes_quedan_sin_inventariar_y_no_libres(self):
        resumen = resumen_ruta(self.ruta)
        resumenes = resumen['resumenes_tramo']

        self.assertEqual(resumenes[self.tramos[0].pk]['libres'], 1)
        self.assertEqual(resumenes[self.tramos[0].pk]['sin_inventariar'], 62)
        self.assertEqual(resumenes[self.tramos[1].pk]['libres'], 1)
        self.assertEqual(resumenes[self.tramos[1].pk]['sin_inventariar'], 46)
        self.assertEqual(resumenes[self.tramos[2].pk]['libres'], 0)
        self.assertEqual(resumenes[self.tramos[2].pk]['sin_inventariar'], 23)

    def test_fibra_logica_en_varios_tramos_no_se_multiplica_y_mide_cobertura(self):
        resumen = resumen_ruta(self.ruta)

        self.assertEqual(resumen['fibras_total'], 2)
        self.assertEqual(resumen['hilos_ocupados'], 1)
        self.assertEqual(resumen['hilos_libres'], 0)
        self.assertEqual(resumen['hilos_sin_estado'], 23)
        self.assertEqual(resumen['hilos_sin_estado_logicos'], 1)
        self.assertEqual(resumen['fibras_cobertura_completa'], 1)
        self.assertEqual(resumen['fibras_sin_cobertura'], 1)
        self.assertFalse(resumen['detalle_fibras_completo'])

    def test_el_detalle_fisico_prevalece_sobre_un_cache_logico_desactualizado(self):
        InventarioFibra.objects.filter(
            pk=self.fibra_completa.pk,
        ).update(estado='Libre')

        resumen = resumen_ruta(self.ruta)

        self.assertEqual(resumen['hilos_ocupados'], 1)
        self.assertEqual(resumen['hilos_libres'], 0)


class BackfillNodoRedLegacyTests(TestCase):
    def test_completa_extremos_exactos_y_otro_sin_inventar_marcadores(self):
        ruta = Ruta.objects.create(nombre='RUTA-BACKFILL-NODOS')
        site = HubSite.objects.create(nombre='SITE-LEGACY')
        sala = SalaTecnica.objects.create(
            hub_site=site,
            nombre='SALA-LEGACY',
        )
        rack = RackFisico.objects.create(
            sala=sala,
            nombre='RACK-LEGACY',
        )
        InventarioODF.objects.create(
            rack_obj=rack,
            odf='ODF-LEGACY',
            capacidad_puertos=12,
        )
        Reserva.objects.create(
            ruta=ruta,
            nombre='Mufa Legacy',
            codigo='MF-1',
            tipo='Mufa',
            latitud='-12.1000000',
            longitud='-77.1000000',
        )
        tramos = [
            InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=1,
                codigo_tramo='T-BACKFILL-001',
                origen='SITE-LEGACY',
                destino='MF-1',
            ),
            InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=2,
                codigo_tramo='T-BACKFILL-002',
                origen='Mufa Legacy',
                destino='Switchgear remoto',
            ),
            InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=3,
                codigo_tramo='T-BACKFILL-003',
                origen='Sin dato',
                destino='Por levantar',
            ),
        ]
        nodo_existente = NodoRed.objects.create(
            tipo='PUNTO',
            codigo='P-EXISTENTE',
        )
        preservado = InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=4,
            codigo_tramo='T-BACKFILL-004',
            origen='SITE-LEGACY',
            destino='—',
            origen_nodo=nodo_existente,
        )
        tramo_odf = InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=5,
            codigo_tramo='T-BACKFILL-005',
            origen='ODF-LEGACY',
            destino='N/A',
        )
        migracion = importlib.import_module(
            'mapas.migrations.0030_integridad_topologia_legacy'
        )

        migracion.validar_y_completar_nodos_legacy(
            apps,
            SimpleNamespace(connection=connection),
        )

        for tramo in tramos:
            tramo.refresh_from_db()
        preservado.refresh_from_db()
        tramo_odf.refresh_from_db()
        primero, segundo, tercero = tramos
        self.assertEqual(primero.origen_nodo.tipo, 'SITE')
        self.assertEqual(primero.origen_nodo.codigo, 'SITE-LEGACY')
        self.assertEqual(primero.destino_nodo.tipo, 'MUFA')
        self.assertEqual(primero.destino_nodo.codigo, 'MF-1')
        self.assertEqual(
            primero.destino_nodo_id,
            segundo.origen_nodo_id,
        )
        self.assertEqual(segundo.destino_nodo.tipo, 'OTRO')
        self.assertEqual(
            segundo.destino_nodo.codigo,
            'SWITCHGEAR REMOTO',
        )
        self.assertIsNone(tercero.origen_nodo_id)
        self.assertIsNone(tercero.destino_nodo_id)
        self.assertEqual(preservado.origen_nodo_id, nodo_existente.pk)
        self.assertIsNone(preservado.destino_nodo_id)
        self.assertEqual(tramo_odf.origen_nodo.tipo, 'ODF')
        self.assertEqual(tramo_odf.origen_nodo.codigo, 'ODF-LEGACY')
        self.assertIsNone(tramo_odf.destino_nodo_id)


class LimpiezaGeografiaLegacyTests(TestCase):
    def test_colapsa_pseudotramos_y_conserva_cortes_geograficos(self):
        ruta = Ruta.objects.create(nombre='RUTA-PSEUDOTRAMOS')
        tramos = [
            InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=secuencia,
                tipo_trazado='AEREO',
                estado='Activo',
                capacidad='24',
                origen='A',
                destino='B',
                hilos_ocupados=4 if secuencia == 1 else 0,
                hilos_libres=20 if secuencia == 1 else 0,
            )
            for secuencia in (1, 2)
        ]
        CoordenadaRuta.objects.create(
            ruta=ruta,
            orden=1,
            latitud='-12.1000000',
            longitud='-77.1000000',
            tipo_trazado='AEREO',
            inicio_segmento=True,
            tramo_secuencia=1,
            tramo=tramos[0],
        )
        CoordenadaRuta.objects.create(
            ruta=ruta,
            orden=2,
            latitud='-12.2000000',
            longitud='-77.2000000',
            tipo_trazado='AEREO',
            inicio_segmento=True,
            tramo_secuencia=2,
            tramo=tramos[1],
        )
        migracion = importlib.import_module(
            'mapas.migrations.0029_inventario_tecnico_por_tramo'
        )

        migracion.colapsar_tramos_geograficos_legacy(
            apps,
            SimpleNamespace(connection=connection),
        )

        self.assertEqual(ruta.tramos_inventario.count(), 1)
        coordenadas = list(
            ruta.coordenadas.order_by('orden').values_list(
                'tramo_secuencia',
                'inicio_segmento',
                'tipo_trazado',
            )
        )
        self.assertEqual(
            coordenadas,
            [(1, True, 'AEREO'), (2, True, 'AEREO')],
        )
