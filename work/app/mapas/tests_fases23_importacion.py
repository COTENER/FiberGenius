import json

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import (
    CoordenadaRuta,
    FibraTramo,
    InventarioFibra,
    InventarioTramo,
    NodoRed,
    Ruta,
)
from .services.capacidad import resumen_ruta
from .views.importacion import (
    _procesar_fibras_inventario,
    _procesar_tramos_inventario,
)


def _csv(nombre, contenido):
    return SimpleUploadedFile(
        nombre,
        contenido.encode("utf-8"),
        content_type="text/csv",
    )


class ImportacionTramosFase2Tests(TestCase):
    def setUp(self):
        self.ruta = Ruta.objects.create(nombre="RUTA-FASE-2")
        CoordenadaRuta.objects.create(
            ruta=self.ruta,
            orden=1,
            latitud="-12.1000000",
            longitud="-77.1000000",
            tipo_trazado="AEREO",
            inicio_segmento=True,
            tramo_secuencia=1,
        )
        CoordenadaRuta.objects.create(
            ruta=self.ruta,
            orden=2,
            latitud="-12.2000000",
            longitud="-77.2000000",
            tipo_trazado="SOTERRADO",
            inicio_segmento=True,
            tramo_secuencia=2,
        )

    def _contenido_valido(self):
        return "\n".join([
            (
                "Ruta,Codigo Tramo,Secuencia,Origen Tipo,Origen Codigo,"
                "Destino Tipo,Destino Codigo,Estado,Distancia m,Capacidad,"
                "Tipo Fibra,Mufas,Splitters,Reservas m"
            ),
            (
                "RUTA-FASE-2,T-001,1,SITE,S-A,MUFA,M-1,Activo,1000,64,"
                "G.652D,1,0,20"
            ),
            (
                "RUTA-FASE-2,T-002,2,MUFA,M-1,SITE,S-B,Activo,800,48,"
                "G.652D,0,1,10"
            ),
        ])

    def test_importa_dos_tramos_reutiliza_nodo_y_no_toca_geografia(self):
        geografia_antes = list(
            CoordenadaRuta.objects.filter(ruta=self.ruta)
            .order_by("orden")
            .values(
                "orden",
                "latitud",
                "longitud",
                "tipo_trazado",
                "inicio_segmento",
                "tramo_secuencia",
            )
        )

        resultado = _procesar_tramos_inventario(
            _csv("tramos.csv", self._contenido_valido())
        )

        self.assertEqual(resultado["creadas"], 2)
        self.assertEqual(self.ruta.tramos_inventario.count(), 2)
        self.assertEqual(NodoRed.objects.count(), 3)
        primero, segundo = self.ruta.tramos_inventario.order_by(
            "tramo_secuencia"
        )
        self.assertEqual(primero.destino_nodo_id, segundo.origen_nodo_id)
        self.assertEqual(primero.capacidad_hilos, 64)
        self.assertEqual(segundo.capacidad_hilos, 48)
        self.assertEqual(
            geografia_antes,
            list(
                CoordenadaRuta.objects.filter(ruta=self.ruta)
                .order_by("orden")
                .values(
                    "orden",
                    "latitud",
                    "longitud",
                    "tipo_trazado",
                    "inicio_segmento",
                    "tramo_secuencia",
                )
            ),
        )
        self.assertEqual(resumen_ruta(self.ruta)["capacidad_efectiva"], 48)

    def test_reimportar_es_idempotente(self):
        contenido = self._contenido_valido()
        _procesar_tramos_inventario(_csv("tramos.csv", contenido))

        resultado = _procesar_tramos_inventario(
            _csv("tramos.csv", contenido)
        )

        self.assertEqual(resultado["creadas"], 0)
        self.assertEqual(resultado["actualizadas"], 2)
        self.assertEqual(self.ruta.tramos_inventario.count(), 2)
        self.assertEqual(NodoRed.objects.count(), 3)

    def test_secuencias_deben_empezar_en_uno(self):
        contenido = "\n".join([
            (
                "Ruta,Codigo Tramo,Secuencia,Origen Tipo,Origen Codigo,"
                "Destino Tipo,Destino Codigo,Estado,Distancia m,Capacidad,"
                "Tipo Fibra"
            ),
            (
                "RUTA-FASE-2,T-002,2,SITE,S-A,SITE,S-B,Activo,800,48,"
                "G.652D"
            ),
        ])

        with self.assertRaisesRegex(ValueError, "secuencias"):
            _procesar_tramos_inventario(_csv("tramos.csv", contenido))

        self.assertFalse(InventarioTramo.objects.exists())
        self.assertFalse(NodoRed.objects.exists())

    def test_discontinuidad_revierte_todo_el_archivo(self):
        contenido = "\n".join([
            (
                "Ruta,Codigo Tramo,Secuencia,Origen Tipo,Origen Codigo,"
                "Destino Tipo,Destino Codigo,Estado,Distancia m,Capacidad,"
                "Tipo Fibra"
            ),
            (
                "RUTA-FASE-2,T-001,1,SITE,S-A,MUFA,M-1,Activo,1000,64,"
                "G.652D"
            ),
            (
                "RUTA-FASE-2,T-002,2,MUFA,M-2,SITE,S-B,Activo,800,48,"
                "G.652D"
            ),
        ])

        with self.assertRaisesRegex(ValueError, "no coincide"):
            _procesar_tramos_inventario(_csv("tramos.csv", contenido))

        self.assertFalse(InventarioTramo.objects.exists())
        self.assertFalse(NodoRed.objects.exists())

    def test_no_permite_reducir_capacidad_debajo_de_una_posicion_existente(self):
        origen = NodoRed.objects.create(tipo="SITE", codigo="S-A")
        destino = NodoRed.objects.create(tipo="SITE", codigo="S-B")
        tramo = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=1,
            codigo_tramo="T-001",
            origen_nodo=origen,
            destino_nodo=destino,
            origen="S-A",
            destino="S-B",
            capacidad_hilos=64,
        )
        fibra = InventarioFibra.objects.create(
            ruta=self.ruta,
            fibra_numero="F48",
            estado="OCUPADO",
            origen_estado="INFORMADO",
        )
        FibraTramo.objects.create(
            tramo=tramo,
            numero_hilo="F48",
            estado="OCUPADO",
            fibra=fibra,
        )
        contenido = "\n".join([
            (
                "Ruta,Codigo Tramo,Secuencia,Origen Tipo,Origen Codigo,"
                "Destino Tipo,Destino Codigo,Estado,Distancia m,Capacidad,"
                "Tipo Fibra"
            ),
            (
                "RUTA-FASE-2,T-001,1,SITE,S-A,SITE,S-B,Activo,800,24,"
                "G.652D"
            ),
        ])

        with self.assertRaisesRegex(ValueError, "F48|capacidad"):
            _procesar_tramos_inventario(_csv("tramos.csv", contenido))

        tramo.refresh_from_db()
        self.assertEqual(tramo.capacidad_hilos, 64)

    def test_contadores_omitidos_se_validan_con_los_valores_existentes(self):
        origen = NodoRed.objects.create(tipo="SITE", codigo="S-A")
        destino = NodoRed.objects.create(tipo="SITE", codigo="S-B")
        tramo = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=1,
            codigo_tramo="T-001",
            origen_nodo=origen,
            destino_nodo=destino,
            origen="S-A",
            destino="S-B",
            capacidad_hilos=24,
            hilos_ocupados=0,
            hilos_reservados=20,
            hilos_libres=0,
        )
        contenido = "\n".join([
            (
                "Ruta,Codigo Tramo,Secuencia,Origen Tipo,Origen Codigo,"
                "Destino Tipo,Destino Codigo,Estado,Distancia m,Capacidad,"
                "Tipo Fibra,Hilos Ocupados"
            ),
            (
                "RUTA-FASE-2,T-001,1,SITE,S-A,SITE,S-B,Activo,800,24,"
                "G.652D,10"
            ),
        ])

        with self.assertRaisesRegex(ValueError, "contadores|capacidad"):
            _procesar_tramos_inventario(_csv("tramos.csv", contenido))

        tramo.refresh_from_db()
        self.assertEqual(tramo.hilos_ocupados, 0)
        self.assertEqual(tramo.hilos_reservados, 20)

    def test_formato_legacy_rechaza_contadores_que_exceden_capacidad(self):
        contenido = "\n".join([
            (
                "ruta,estado,distancia,capacidad,tipo_fibra,"
                "hilos_ocupados,hilos_libres"
            ),
            "RUTA-FASE-2,Activo,1000,4,G.652D,0,24",
        ])

        resultado = _procesar_tramos_inventario(
            _csv("tecnicos_legacy.csv", contenido)
        )

        self.assertEqual(resultado["rechazadas"], 1)
        self.assertTrue(
            any(
                "exceden la capacidad declarada" in advertencia
                for advertencia in resultado["advertencias"]
            )
        )
        self.assertFalse(self.ruta.tramos_inventario.exists())

    def test_formato_legacy_actualiza_distancia_tecnica_no_la_geografica(self):
        self.ruta.distancia_m = 1250
        self.ruta.save(update_fields=["distancia_m"])
        tramo = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=1,
            distancia_m=100,
        )
        contenido = "\n".join([
            "ruta,estado,distancia,capacidad,tipo_fibra",
            "RUTA-FASE-2,Activo,999,48,G.652D",
        ])

        _procesar_tramos_inventario(
            _csv("tecnicos_legacy.csv", contenido)
        )

        tramo.refresh_from_db()
        self.ruta.refresh_from_db()
        self.assertEqual(tramo.distancia_m, 999)
        self.assertEqual(self.ruta.distancia_m, 1250)

    def test_formato_legacy_actualiza_el_unico_tramo_aunque_tenga_codigo(self):
        tramo = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=1,
            codigo_tramo="T-LEGACY-001",
            capacidad_hilos=24,
        )
        contenido = "\n".join([
            "ruta,estado,capacidad,tipo_fibra,hilos_ocupados,hilos_libres",
            "RUTA-FASE-2,Activo,48,G.652D,12,36",
        ])

        resultado = _procesar_tramos_inventario(
            _csv("tecnicos_legacy.csv", contenido)
        )

        tramo.refresh_from_db()
        self.assertEqual(resultado["rechazadas"], 0)
        self.assertEqual(tramo.codigo_tramo, "T-LEGACY-001")
        self.assertEqual(tramo.capacidad_hilos, 48)
        self.assertEqual(tramo.hilos_ocupados, 12)
        self.assertEqual(tramo.hilos_libres, 36)

    def test_nombres_contradictorios_del_mismo_nodo_revierten_el_archivo(self):
        contenido = "\n".join([
            (
                "Ruta,Codigo Tramo,Secuencia,Origen Tipo,Origen Codigo,"
                "Origen Nombre,Destino Tipo,Destino Codigo,Destino Nombre,"
                "Estado,Distancia m,Capacidad,Tipo Fibra"
            ),
            (
                "RUTA-FASE-2,T-001,1,SITE,S-A,Site A,MUFA,M-1,"
                "Mufa Norte,Activo,1000,64,G.652D"
            ),
            (
                "RUTA-FASE-2,T-002,2,MUFA,M-1,Mufa Sur,SITE,S-B,"
                "Site B,Activo,800,48,G.652D"
            ),
        ])

        with self.assertRaisesRegex(ValueError, "nombres incoherentes"):
            _procesar_tramos_inventario(_csv("tramos.csv", contenido))

        self.assertFalse(InventarioTramo.objects.exists())
        self.assertFalse(NodoRed.objects.exists())


class ImportacionFibrasFase3Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ruta = Ruta.objects.create(nombre="RUTA-FASE-3")
        nodos = [
            NodoRed.objects.create(tipo="SITE", codigo=codigo)
            for codigo in ("N-1", "N-2", "N-3")
        ]
        cls.tramo_1 = InventarioTramo.objects.create(
            ruta=cls.ruta,
            tramo_secuencia=1,
            codigo_tramo="T-001",
            origen_nodo=nodos[0],
            destino_nodo=nodos[1],
            capacidad_hilos=4,
        )
        cls.tramo_2 = InventarioTramo.objects.create(
            ruta=cls.ruta,
            tramo_secuencia=2,
            codigo_tramo="T-002",
            origen_nodo=nodos[1],
            destino_nodo=nodos[2],
            capacidad_hilos=4,
        )
        cls.fibra_1 = InventarioFibra.objects.create(
            ruta=cls.ruta,
            fibra_numero='F1',
            nombre_fibra='SERVICIO-ORIGINAL',
            tipo_conector='SC/APC',
            observaciones='OBSERVACION-GLOBAL',
        )
        cls.fibra_3 = InventarioFibra.objects.create(
            ruta=cls.ruta,
            fibra_numero='F3',
        )

    def _contenido_fibra_completa(self):
        codigo = self.fibra_1.codigo_fibra
        return "\n".join([
            (
                "Ruta,Codigo Tramo,Fibra,Codigo Fibra,Estado,"
                "Tipo de Servicio,Origen,Destino,Conector,Observaciones"
            ),
            (
                f"RUTA-FASE-3,T-001,F1,{codigo},Ocupado,TRANSPORTE,"
                "ODF-A,ODF-B,LC,Primer tramo"
            ),
            (
                f"RUTA-FASE-3,T-002,F2,{codigo},Ocupado,TRANSPORTE,"
                "ODF-A,ODF-B,LC,Segundo tramo"
            ),
        ])

    def test_una_fibra_logica_puede_usar_posiciones_distintas_por_tramo(self):
        resultado = _procesar_fibras_inventario(
            _csv("fibras.csv", self._contenido_fibra_completa())
        )

        self.assertEqual(resultado["total"], 2)
        self.assertEqual(InventarioFibra.objects.count(), 2)
        self.assertEqual(FibraTramo.objects.count(), 2)
        fibra = InventarioFibra.objects.get(fibra_numero='F1')
        self.assertEqual(fibra.nombre_fibra, 'SERVICIO-ORIGINAL')
        self.assertEqual(fibra.tipo_conector, 'SC/APC')
        self.assertEqual(fibra.observaciones, 'OBSERVACION-GLOBAL')
        self.assertEqual(
            list(
                fibra.asignaciones_tramo.order_by(
                    "tramo__tramo_secuencia"
                ).values_list("numero_hilo", flat=True)
            ),
            ["F1", "F2"],
        )
        resumen = resumen_ruta(self.ruta)
        self.assertEqual(resumen["fibras_total"], 2)
        self.assertEqual(resumen["hilos_ocupados"], 1)
        self.assertEqual(resumen["fibras_cobertura_completa"], 1)
        self.assertFalse(resumen["detalle_fibras_completo"])

    def test_codigo_estable_identifica_la_fibra_en_todos_sus_tramos(self):
        codigo = self.fibra_1.codigo_fibra
        contenido = "\n".join([
            "Ruta,Codigo Tramo,Fibra,Codigo Fibra,Estado",
            f"RUTA-FASE-3,T-001,F1,{codigo},Ocupado",
            f"RUTA-FASE-3,T-002,F2,{codigo},Ocupado",
        ])

        resultado = _procesar_fibras_inventario(
            _csv("fibras-codigo-estable.csv", contenido)
        )

        self.assertEqual(resultado["total"], 2)
        self.assertEqual(
            list(
                self.fibra_1.asignaciones_tramo.order_by(
                    "tramo__tramo_secuencia"
                ).values_list("numero_hilo", flat=True)
            ),
            ["F1", "F2"],
        )

    def test_codigo_fibra_es_obligatorio_y_no_admite_una_posicion_fisica(self):
        sin_columna = "\n".join([
            "Ruta,Codigo Tramo,Fibra,Estado",
            "RUTA-FASE-3,T-001,F1,Ocupado",
        ])
        with self.assertRaisesRegex(ValueError, "codigo_fibra"):
            _procesar_fibras_inventario(
                _csv("fibras-sin-codigo.csv", sin_columna)
            )

        codigo_fisico = "\n".join([
            "Ruta,Codigo Tramo,Fibra,Codigo Fibra,Estado",
            "RUTA-FASE-3,T-001,F1,F1,Ocupado",
        ])
        with self.assertRaisesRegex(ValueError, "identidad global estable"):
            _procesar_fibras_inventario(
                _csv("fibras-codigo-fisico.csv", codigo_fisico)
            )

    def test_fibra_parcial_no_se_considera_libre_extremo_a_extremo(self):
        _procesar_fibras_inventario(
            _csv("fibras.csv", self._contenido_fibra_completa())
        )
        parcial = "\n".join([
            (
                "Ruta,Codigo Tramo,Fibra,Codigo Fibra,Estado,"
                "Tipo de Servicio,Origen,Destino,Conector"
            ),
            (
                f"RUTA-FASE-3,T-001,F3,{self.fibra_3.codigo_fibra},Disponible,LIBRE,"
                "ODF-A,ODF-B,LC"
            ),
        ])

        _procesar_fibras_inventario(_csv("parcial.csv", parcial))

        resumen = resumen_ruta(self.ruta)
        self.assertEqual(resumen["fibras_total"], 2)
        self.assertEqual(resumen["hilos_libres"], 0)
        self.assertEqual(resumen["fibras_cobertura_completa"], 1)
        self.assertEqual(resumen["fibras_sin_cobertura"], 1)
        self.assertEqual(
            resumen["resumenes_tramo"][self.tramo_1.pk]["libres"],
            1,
        )
        self.assertEqual(
            resumen["resumenes_tramo"][self.tramo_2.pk]["libres"],
            0,
        )

    def test_estado_invalido_revierte_todas_las_filas(self):
        contenido = self._contenido_fibra_completa().replace(
            f"T-002,F2,{self.fibra_1.codigo_fibra},Ocupado",
            f"T-002,F2,{self.fibra_1.codigo_fibra},INVALIDO",
        )

        with self.assertRaisesRegex(ValueError, "estado"):
            _procesar_fibras_inventario(_csv("fibras.csv", contenido))

        self.assertEqual(InventarioFibra.objects.count(), 2)
        self.assertFalse(FibraTramo.objects.exists())

    def test_sin_informacion_se_importa_y_prevalece_sobre_disponible(self):
        contenido = "\n".join([
            (
                "Ruta,Codigo Tramo,Fibra,Codigo Fibra,Estado,"
                "Tipo de Servicio,Origen,Destino,Conector"
            ),
            (
                f"RUTA-FASE-3,T-001,F3,{self.fibra_3.codigo_fibra},Sin información,LIBRE,"
                "ODF-A,ODF-B,LC"
            ),
            (
                f"RUTA-FASE-3,T-002,F3,{self.fibra_3.codigo_fibra},Disponible,LIBRE,"
                "ODF-A,ODF-B,LC"
            ),
        ])

        resultado = _procesar_fibras_inventario(
            _csv("desconocido.csv", contenido)
        )

        self.assertEqual(resultado["total"], 2)
        fibra = InventarioFibra.objects.get(fibra_numero="F3")
        self.assertEqual(fibra.estado, "SIN_INFORMACION")
        self.assertEqual(
            FibraTramo.objects.get(
                tramo=self.tramo_1,
                numero_hilo="F3",
            ).estado,
            "SIN_INFORMACION",
        )
        resumen = resumen_ruta(self.ruta)
        self.assertEqual(resumen["hilos_libres"], 0)
        self.assertEqual(resumen["hilos_sin_estado"], 4)

    def test_codigo_tramo_es_obligatorio_si_hay_varios_tramos(self):
        contenido = "\n".join([
            (
                "Ruta,Fibra,Codigo Fibra,Estado,Tipo de Servicio,"
                "Origen,Destino,Conector"
            ),
            f"RUTA-FASE-3,F1,{self.fibra_1.codigo_fibra},Disponible,LIBRE,ODF-A,ODF-B,LC",
        ])

        with self.assertRaisesRegex(ValueError, "Codigo Tramo"):
            _procesar_fibras_inventario(_csv("fibras.csv", contenido))

        self.assertEqual(InventarioFibra.objects.count(), 2)

    def test_numero_fisico_no_puede_superar_capacidad_del_tramo(self):
        contenido = "\n".join([
            (
                "Ruta,Codigo Tramo,Fibra,Codigo Fibra,Estado,Tipo de Servicio,"
                "Origen,Destino,Conector"
            ),
            "RUTA-FASE-3,T-001,F5,FGF-CAPACIDAD-0005,Disponible,LIBRE,ODF-A,ODF-B,LC",
        ])

        with self.assertRaisesRegex(ValueError, "supera la capacidad"):
            _procesar_fibras_inventario(_csv("fibras.csv", contenido))

        self.assertEqual(InventarioFibra.objects.count(), 2)

    def test_fibra_global_inexistente_se_reporta_y_no_se_crea(self):
        contenido = "\n".join([
            "Ruta,Codigo Tramo,Fibra,Codigo Fibra,Estado",
            "RUTA-FASE-3,T-001,F4,FGF-INEXISTENTE-0004,Disponible",
        ])

        with self.assertRaisesRegex(ValueError, "cárguela previamente"):
            _procesar_fibras_inventario(_csv("fibra-inexistente.csv", contenido))

        self.assertFalse(InventarioFibra.objects.filter(
            ruta=self.ruta,
            fibra_numero='F4',
        ).exists())
        self.assertFalse(FibraTramo.objects.exists())

    def test_la_carga_es_parche_y_no_borra_asignaciones_omitidas(self):
        _procesar_fibras_inventario(
            _csv("fibras.csv", self._contenido_fibra_completa())
        )
        parcial = "\n".join([
            (
                "Ruta,Codigo Tramo,Fibra,Codigo Fibra,Estado,"
                "Tipo de Servicio,Origen,Destino,Conector"
            ),
            (
                f"RUTA-FASE-3,T-001,F3,{self.fibra_3.codigo_fibra},Disponible,LIBRE,"
                "ODF-A,ODF-B,LC"
            ),
        ])

        resultado = _procesar_fibras_inventario(
            _csv("parcial.csv", parcial)
        )

        self.assertEqual(resultado["modo"], "parche")
        self.assertEqual(FibraTramo.objects.count(), 3)
        self.assertTrue(
            FibraTramo.objects.filter(
                tramo=self.tramo_2,
                numero_hilo="F2",
            ).exists()
        )

    def test_parche_puede_mover_una_fibra_logica_a_otra_posicion_libre(self):
        _procesar_fibras_inventario(
            _csv("fibras.csv", self._contenido_fibra_completa())
        )
        parche = "\n".join([
            (
                "Ruta,Codigo Tramo,Fibra,Codigo Fibra,Estado,"
                "Tipo de Servicio,Origen,Destino,Conector"
            ),
            (
                f"RUTA-FASE-3,T-001,F4,{self.fibra_1.codigo_fibra},Ocupado,TRANSPORTE,"
                "ODF-A,ODF-B,LC"
            ),
        ])

        _procesar_fibras_inventario(_csv("parche.csv", parche))

        fibra = InventarioFibra.objects.get(
            ruta=self.ruta,
            fibra_numero="F1",
        )
        asignacion = FibraTramo.objects.get(
            tramo=self.tramo_1,
            fibra=fibra,
        )
        self.assertEqual(asignacion.numero_hilo, "F4")
        self.assertFalse(
            FibraTramo.objects.filter(
                tramo=self.tramo_1,
                numero_hilo="F1",
            ).exists()
        )


class ApiFibrasFase3Tests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(
            username="operador-fase-3",
            password="clave-segura",
        )
        self.usuario.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="mapas",
                codename__in=(
                    "add_inventariofibra",
                    "change_inventariofibra",
                    "view_inventariofibra",
                    "view_ruta",
                ),
            )
        )
        self.client.force_login(self.usuario)
        self.ruta = Ruta.objects.create(nombre="RUTA-API-FASE-3")
        self.tramo_1 = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=1,
            codigo_tramo="T-API-001",
            capacidad_hilos=4,
        )
        self.tramo_2 = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=2,
            codigo_tramo="T-API-002",
            capacidad_hilos=4,
        )

    def test_creacion_manual_acepta_subconjunto_opcional_de_tramos(self):
        response = self.client.post(
            reverse("api_create_fibra"),
            data=json.dumps({
                "ruta_nombre": self.ruta.nombre,
                "fibra_numero": "F1",
                "estado": "DISPONIBLE",
                "tramo_ids": [self.tramo_1.pk],
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        fibra = InventarioFibra.objects.get(ruta=self.ruta)
        self.assertEqual(
            list(
                fibra.asignaciones_tramo.values_list(
                    "tramo_id",
                    flat=True,
                )
            ),
            [self.tramo_1.pk],
        )
        resumen = resumen_ruta(self.ruta)
        self.assertEqual(resumen["hilos_libres"], 0)
        self.assertEqual(resumen["fibras_sin_cobertura"], 1)

        inventario = self.client.get(
            reverse("api_fibras_paginadas"),
            {"ruta": self.ruta.nombre},
        ).json()
        # El estado global informado conserva prioridad; la cobertura parcial
        # se comunica como indicador independiente.
        self.assertEqual(inventario["summary"]["libres"], 1)
        self.assertEqual(inventario["summary"]["sin_cobertura"], 1)
        self.assertFalse(inventario["data"][0]["cobertura_completa"])

        filtro_libres = self.client.get(
            reverse("api_fibras_paginadas"),
            {"ruta": self.ruta.nombre, "estado": "DISPONIBLE"},
        ).json()
        self.assertEqual(filtro_libres["pagination"]["total"], 1)

        dashboard = self.client.get(reverse("dashboard_inventario"))
        self.assertEqual(dashboard.context["total_fibras"], 4)
        self.assertEqual(dashboard.context["fibras_libres"], 0)
        self.assertEqual(dashboard.context["fibras_sin_estado"], 4)

    def test_subconjunto_invalido_no_deja_fibra_huerfana(self):
        response = self.client.post(
            reverse("api_create_fibra"),
            data=json.dumps({
                "ruta_nombre": self.ruta.nombre,
                "fibra_numero": "F1",
                "estado": "DISPONIBLE",
                "tramo_ids": [self.tramo_1.pk, 999999],
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(InventarioFibra.objects.exists())
        self.assertFalse(FibraTramo.objects.exists())

    def test_importador_upsert_exige_permiso_de_cambio(self):
        usuario_solo_alta = User.objects.create_user(
            username="solo-alta-fase-3",
            password="clave-segura",
        )
        usuario_solo_alta.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="mapas",
                codename="add_inventariofibra",
            )
        )
        self.client.force_login(usuario_solo_alta)

        response = self.client.post(
            reverse("api_import_fibras"),
            {
                "ruta_nombre": self.ruta.nombre,
                "csv_fibras": _csv(
                    "fibras.csv",
                    (
                        "Ruta,Codigo Tramo,Fibra,Estado\n"
                        "RUTA-API-FASE-3,T-API-001,F1,Disponible\n"
                    ),
                ),
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(InventarioFibra.objects.exists())

    def test_dashboard_reconoce_site_desde_nodo_topologico(self):
        nodo = NodoRed.objects.create(
            tipo="SITE",
            codigo="SITE-NUEVO",
            nombre="Site Nuevo",
        )
        self.tramo_1.origen_nodo = nodo
        self.tramo_1.hub_site = ""
        self.tramo_1.save(update_fields=["origen_nodo", "hub_site"])

        response = self.client.get(
            reverse("dashboard_inventario"),
            {"site": "SITE-NUEVO"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_rutas"], 1)
        self.assertEqual(
            response.context["resumen_rutas"][0]["hub_origen"],
            "Site Nuevo",
        )
        self.assertIn(
            "Site Nuevo",
            {
                site["nombre"]
                for site in response.context["sites_disponibles"]
            },
        )


class CompatibilidadCapacidadLegacyTests(TestCase):
    def test_fibra_legacy_de_un_solo_tramo_conserva_cobertura(self):
        ruta = Ruta.objects.create(nombre="RUTA-LEGACY-UN-TRAMO")
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            capacidad_hilos=4,
            hilos_libres=4,
        )
        InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero="F1",
            estado="DISPONIBLE",
            origen_estado="INFORMADO",
        )

        resumen = resumen_ruta(ruta)

        self.assertEqual(resumen["hilos_libres"], 1)
        self.assertEqual(resumen["fibras_cobertura_completa"], 1)
        self.assertEqual(resumen["fibras_sin_cobertura"], 0)
        self.assertEqual(resumen["sin_inventariar"], 3)
        self.assertFalse(resumen["detalle_fibras_completo"])

    def test_contadores_legacy_multitramo_no_se_suman_como_extremo_a_extremo(self):
        ruta = Ruta.objects.create(nombre="RUTA-LEGACY-MULTITRAMO")
        for secuencia in (1, 2):
            InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=secuencia,
                capacidad_hilos=4,
                hilos_ocupados=1,
                hilos_libres=3,
            )

        resumen = resumen_ruta(ruta)

        self.assertEqual(resumen["hilos_ocupados"], 0)
        self.assertEqual(resumen["hilos_libres"], 0)
        self.assertEqual(resumen["fibras_total"], 0)
        self.assertEqual(resumen["sin_inventariar"], 4)

    def test_modelo_rechaza_legacy_inconsistente(self):
        ruta = Ruta.objects.create(nombre="RUTA-LEGACY-INCONSISTENTE")
        with self.assertRaises(ValidationError):
            InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=1,
                capacidad_hilos=4,
                hilos_ocupados=1,
                hilos_libres=5,
            )
