from django.test import RequestFactory, TestCase

from .models import (
    FibraTramo,
    InventarioFibra,
    InventarioTramo,
    NodoRed,
    Ruta,
)
from .views.operacion_inventario import (
    _ficha_fibra,
    _ficha_troncal,
    _filtro_fibras,
    _filtro_troncales,
    _resumen_troncales,
)


def _valores(items):
    return {item["label"]: item["value"] for item in items}


class LecturasMultitramoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.factory = RequestFactory()
        cls.ruta = Ruta.objects.create(nombre="RUTA-MULTITRAMO")
        nodos = [
            NodoRed.objects.create(
                tipo="SITE" if indice == 1 else "PUNTO",
                codigo=f"NODO-{indice}",
                nombre="Site de origen" if indice == 1 else "",
            )
            for indice in range(1, 5)
        ]
        cls.tramos = [
            InventarioTramo.objects.create(
                ruta=cls.ruta,
                tramo_secuencia=secuencia,
                codigo_tramo=f"T-{secuencia:02d}",
                origen_nodo=nodos[secuencia - 1],
                destino_nodo=nodos[secuencia],
                origen=nodos[secuencia - 1].codigo,
                destino=nodos[secuencia].codigo,
                tipo_trazado=tipo,
                distancia_m=distancia,
                capacidad_hilos=capacidad,
                capacidad=f"{capacidad} Hilos",
            )
            for secuencia, (tipo, distancia, capacidad) in enumerate(
                (
                    ("AEREO", 1000, 64),
                    ("SOTERRADO", 2000, 48),
                    ("AEREO", 3000, 24),
                ),
                start=1,
            )
        ]
        cls.fibra_completa = InventarioFibra.objects.create(
            ruta=cls.ruta,
            fibra_numero="F1",
            estado="Ocupado",
            origen_estado="INFORMADO",
        )
        cls.fibra_parcial = InventarioFibra.objects.create(
            ruta=cls.ruta,
            fibra_numero="F2",
            estado="Libre",
            origen_estado="INFORMADO",
        )
        for tramo in cls.tramos:
            FibraTramo.objects.create(
                tramo=tramo,
                numero_hilo="F1",
                estado="Ocupado",
                fibra=cls.fibra_completa,
            )
        for tramo in cls.tramos[:2]:
            FibraTramo.objects.create(
                tramo=tramo,
                numero_hilo="F2",
                estado="Libre",
                fibra=cls.fibra_parcial,
            )

    def test_ficha_troncal_usa_capacidad_y_disponibilidad_extremo_a_extremo(self):
        ficha = _ficha_troncal(self.ruta.pk)
        resumen = _valores(ficha["summary"])
        datos_tecnicos = _valores(ficha["sections"][0]["items"])
        cadena = _valores(ficha["chain"])

        self.assertEqual(resumen["Distancia"], "6.00 km")
        self.assertEqual(resumen["Fibras"], "2")
        self.assertEqual(resumen["Fibras libres"], "0")
        self.assertEqual(datos_tecnicos["Capacidad"], "24 Hilos")
        self.assertEqual(cadena["Site de origen"], "Site de origen")
        self.assertEqual(cadena["Destino"], "NODO-4")

    def test_ficha_troncal_no_publica_capacidad_si_falta_un_tramo(self):
        InventarioTramo.objects.filter(pk=self.tramos[1].pk).update(
            capacidad_hilos=None,
            capacidad=None,
        )

        ficha = _ficha_troncal(self.ruta.pk)
        datos_tecnicos = _valores(ficha["sections"][0]["items"])

        self.assertEqual(datos_tecnicos["Capacidad"], "—")

    def test_ficha_separa_estado_global_de_cobertura_parcial(self):
        ficha = _ficha_fibra(self.fibra_parcial.pk)

        self.assertEqual(ficha["status"], "Libre")
        self.assertEqual(_valores(ficha["summary"])["Completitud"], "Recorrido parcial")
        self.assertEqual(_ficha_fibra(self.fibra_completa.pk)["status"], "Ocupado")

    def test_ficha_fibra_legacy_de_un_tramo_conserva_estado_libre(self):
        ruta = Ruta.objects.create(nombre="RUTA-LEGACY-FICHA")
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            capacidad_hilos=4,
            hilos_libres=4,
        )
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero="F1",
            estado="Libre",
            origen_estado="INFORMADO",
        )

        self.assertEqual(_ficha_fibra(fibra.pk)["status"], "Libre")

    def test_resumen_troncales_aplica_la_misma_precedencia_de_distancia(self):
        request = self.factory.get("/", {"q": self.ruta.nombre})
        resumen = _resumen_troncales(_filtro_troncales(request))
        self.assertEqual(resumen["distancia_km"], 6.0)

        Ruta.objects.filter(pk=self.ruta.pk).update(
            distancia_declarada_m=9000,
        )
        resumen = _resumen_troncales(
            _filtro_troncales(self.factory.get("/", {"q": self.ruta.nombre}))
        )
        self.assertEqual(resumen["distancia_km"], 9.0)

    def test_filtro_hibrido_incluye_combinacion_tecnica_y_valor_explicito(self):
        ruta_aerea = Ruta.objects.create(nombre="RUTA-AEREA")
        InventarioTramo.objects.create(
            ruta=ruta_aerea,
            tramo_secuencia=1,
            tipo_trazado="AEREO",
        )
        ruta_hibrida = Ruta.objects.create(nombre="RUTA-HIBRIDA-EXPLICITA")
        InventarioTramo.objects.create(
            ruta=ruta_hibrida,
            tramo_secuencia=1,
            tipo_trazado="HIBRIDO",
        )

        hibridas = set(
            _filtro_troncales(
                self.factory.get("/", {"tipo": "HIBRIDO"})
            ).values_list("nombre", flat=True)
        )
        aereas = set(
            _filtro_troncales(
                self.factory.get("/", {"tipo": "AEREO"})
            ).values_list("nombre", flat=True)
        )

        self.assertEqual(
            hibridas,
            {self.ruta.nombre, ruta_hibrida.nombre},
        )
        self.assertEqual(
            aereas,
            {self.ruta.nombre, ruta_aerea.nombre},
        )

    def test_fibras_se_ordenan_naturalmente_con_fallback_estable(self):
        InventarioFibra.objects.filter(ruta=self.ruta).delete()
        for codigo in (
            "F10",
            "X2",
            "F2",
            "A10",
            "F1",
            "X1",
            "F01",
            f"F{'9' * 49}",
        ):
            InventarioFibra.objects.create(
                ruta=self.ruta,
                fibra_numero=codigo,
                estado="Ocupado",
                origen_estado="INFORMADO",
            )

        codigos = list(
            _filtro_fibras(
                self.factory.get("/", {"ruta": self.ruta.nombre})
            ).values_list("fibra_numero", flat=True)
        )

        self.assertEqual(
            codigos,
            [
                "F01",
                "F1",
                "F2",
                "F10",
                f"F{'9' * 49}",
                "A10",
                "X1",
                "X2",
            ],
        )
