from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import InventarioTramo, NodoRed, Ruta


class IntegracionOperativaFase5Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.usuario = User.objects.create_superuser(
            username="fase5",
            email="fase5@example.test",
            password="clave-segura",
        )
        cls.nodos = [
            NodoRed.objects.create(
                tipo="SITE" if indice in {1, 3} else "MUFA",
                codigo=f"F5-N{indice}",
                nombre=f"Nodo F5 {indice}",
            )
            for indice in range(1, 4)
        ]
        cls.ruta_oficial = Ruta.objects.create(
            nombre="RUTA-F5-OFICIAL",
            distancia_declarada_m=9000,
            capacidad_declarada_hilos=40,
            hilos_ocupados_declarados=10,
            hilos_reservados_declarados=5,
            hilos_libres_declarados=25,
            reservas_declaradas_m=300,
            fuente_declaracion="Acta de inventario",
        )
        cls._crear_tramos(
            cls.ruta_oficial,
            capacidades=(64, 48),
            distancias=(3000, 2000),
            reservas=(50, 75),
        )

        cls.ruta_segura = Ruta.objects.create(
            nombre="RUTA-F5-CUELLO-BOTELLA",
            capacidad_declarada_hilos=60,
        )
        cls._crear_tramos(
            cls.ruta_segura,
            capacidades=(64, 48),
            distancias=(1000, 1000),
            reservas=(0, 0),
        )

        cls.ruta_incompleta = Ruta.objects.create(
            nombre="RUTA-F5-INCOMPLETA",
        )
        cls._crear_tramos(
            cls.ruta_incompleta,
            capacidades=(64, None),
            distancias=(1000, 1000),
            reservas=(0, 0),
        )

    @classmethod
    def _crear_tramos(
        cls,
        ruta,
        *,
        capacidades,
        distancias,
        reservas,
    ):
        for indice, (capacidad, distancia, reserva) in enumerate(
            zip(capacidades, distancias, reservas),
            start=1,
        ):
            InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=indice,
                codigo_tramo=f"{ruta.nombre}-T{indice}",
                origen_nodo=cls.nodos[indice - 1],
                destino_nodo=cls.nodos[indice],
                origen=cls.nodos[indice - 1].codigo,
                destino=cls.nodos[indice].codigo,
                distancia_m=distancia,
                capacidad_hilos=capacidad,
                capacidad=(
                    f"{capacidad} Hilos"
                    if capacidad is not None
                    else None
                ),
                reservas_m=reserva,
                tipo_trazado="AEREO" if indice == 1 else "SOTERRADO",
            )

    def setUp(self):
        self.client.force_login(self.usuario)

    def _fila_dashboard(self, nombre):
        response = self.client.get(reverse("dashboard_inventario"))
        self.assertEqual(response.status_code, 200)
        return next(
            fila
            for fila in response.context["resumen_rutas"]
            if fila["nombre"] == nombre
        )

    def _fila_api(self, nombre):
        response = self.client.get(
            reverse("api_troncales_paginadas"),
            {"q": nombre, "page_size": 10},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["pagination"]["total"], 1)
        return payload["data"][0]

    def test_valores_oficiales_coinciden_en_dashboard_api_y_panel(self):
        dashboard = self._fila_dashboard(self.ruta_oficial.nombre)
        api = self._fila_api(self.ruta_oficial.nombre)
        panel = self.client.get(
            reverse("api_panel_troncal"),
            {"ruta_id": self.ruta_oficial.pk},
        ).json()

        self.assertEqual(dashboard["distancia_km"], 9)
        self.assertEqual(dashboard["reservas_km"], 0.3)
        self.assertEqual(dashboard["capacidad"], "40 Hilos")
        self.assertEqual(dashboard["total_hilos"], 40)
        self.assertEqual(dashboard["hilos_ocupados"], 10)
        self.assertEqual(dashboard["hilos_reservados"], 5)
        self.assertEqual(dashboard["hilos_libres"], 25)
        self.assertEqual(dashboard["hilos_sin_estado"], 0)
        self.assertEqual(dashboard["fuente_capacidad_operativa"], "declarado_ruta")
        self.assertEqual(dashboard["fuente_distancia"], "declarado_ruta")
        self.assertEqual(dashboard["fuente_reservas"], "declarado_ruta")
        self.assertEqual(dashboard["fuente_estados"], "declarado_ruta")

        self.assertEqual(api["distancia_km"], 9)
        self.assertEqual(api["reserva_km"], 0.3)
        self.assertEqual(api["capacidad"], "40 Hilos")
        self.assertEqual(api["capacidad_efectiva"], 40)
        self.assertEqual(api["hilos_ocupados"], 10)
        self.assertEqual(api["hilos_reservados"], 5)
        self.assertEqual(api["hilos_libres"], 25)
        self.assertEqual(api["hilos_sin_estado"], 0)
        self.assertEqual(api["fuente_capacidad"], "declarado_ruta")
        self.assertEqual(api["fuente_distancia"], "declarado_ruta")
        self.assertEqual(api["fuente_reservas"], "declarado_ruta")
        self.assertEqual(api["fuente_estados"], "declarado_ruta")

        self.assertEqual(panel["ruta"]["distancia_km"], 9)
        self.assertEqual(panel["reservas"]["reserva_m"], 300)
        self.assertEqual(panel["fibras"]["total"], 40)
        self.assertEqual(panel["fibras"]["ocupadas"], 10)
        self.assertEqual(panel["fibras"]["reservadas"], 5)
        self.assertEqual(panel["fibras"]["libres"], 25)
        self.assertEqual(panel["fibras"]["sin_verificar"], 0)

    def test_capacidad_declarada_imposible_usa_cuello_de_botella(self):
        dashboard = self._fila_dashboard(self.ruta_segura.nombre)
        api = self._fila_api(self.ruta_segura.nombre)
        panel = self.client.get(
            reverse("api_panel_troncal"),
            {"ruta_id": self.ruta_segura.pk},
        ).json()

        self.assertEqual(dashboard["capacidad"], "48 Hilos")
        self.assertEqual(dashboard["capacidad_efectiva"], 48)
        self.assertEqual(
            dashboard["fuente_capacidad_operativa"],
            "calculado_tramos_seguro",
        )
        self.assertEqual(
            dashboard["estado_conciliacion_capacidad"],
            "INCONSISTENCIA_CRITICA",
        )
        self.assertEqual(api["capacidad"], "48 Hilos")
        self.assertEqual(api["capacidad_efectiva"], 48)
        self.assertEqual(api["fuente_capacidad"], "calculado_tramos_seguro")
        self.assertEqual(panel["fibras"]["total"], 48)

    def test_capacidad_parcial_no_se_publica_como_capacidad_de_ruta(self):
        dashboard = self._fila_dashboard(self.ruta_incompleta.nombre)
        api = self._fila_api(self.ruta_incompleta.nombre)
        ficha = self.client.get(
            reverse(
                "asset_360",
                args=["troncal", self.ruta_incompleta.pk],
            )
        ).json()["data"]
        capacidad_ficha = next(
            item["value"]
            for item in ficha["sections"][0]["items"]
            if item["label"] == "Capacidad"
        )

        self.assertIsNone(dashboard["capacidad_efectiva"])
        self.assertEqual(dashboard["capacidad"], "N/A")
        self.assertFalse(dashboard["capacidades_completas"])
        self.assertIsNone(api["capacidad_efectiva"])
        self.assertEqual(api["capacidad"], "—")
        self.assertFalse(api["capacidades_completas"])
        self.assertEqual(capacidad_ficha, "—")

    def test_contadores_del_dashboard_cierran_con_capacidad_operativa(self):
        response = self.client.get(reverse("dashboard_inventario"))
        self.assertEqual(response.status_code, 200)
        for fila in response.context["resumen_rutas"]:
            if fila["capacidad_efectiva"] is None:
                continue
            self.assertEqual(
                fila["hilos_ocupados"]
                + fila["hilos_reservados"]
                + fila["hilos_libres"]
                + fila["hilos_sin_estado"],
                fila["capacidad_efectiva"],
                fila["nombre"],
            )

    def test_se_conservan_dashboard_sidebar_pestanas_y_estilos(self):
        dashboard = self.client.get(reverse("dashboard_inventario"))
        externo = self.client.get(reverse("inventario_externo"))

        self.assertContains(dashboard, 'id="sidebar"')
        self.assertContains(dashboard, "dashboard-inventario-v2.css")
        self.assertContains(dashboard, "dashboard-inventario-v3.js")
        self.assertNotContains(
            dashboard,
            "mapa_inventario/partials/dashboard_command_center.html",
        )
        self.assertContains(externo, 'class="network-tabs"')
        self.assertContains(externo, 'data-tab="troncales"')
        self.assertContains(externo, 'data-tab="tramos"')
        self.assertContains(externo, 'id="network-inspector"')
        self.assertContains(externo, "network-inventory.css")
        self.assertContains(externo, 'id="sidebar"')
