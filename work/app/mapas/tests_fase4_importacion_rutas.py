from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import Ruta
from .views.importacion import _procesar_ruta_otu


def _csv(contenido, nombre="troncales.csv"):
    return SimpleUploadedFile(
        nombre,
        contenido.encode("utf-8"),
        content_type="text/csv",
    )


class ImportacionRutasFase4Tests(TestCase):
    def test_csv_anterior_sigue_funcionando_y_declara_distancia(self):
        contenido = "\n".join([
            (
                "Ruta,OTU,Distancia (m),OLT,SLOT OLT,PUERTO OLT,"
                "PON,Enlace"
            ),
            "RUTA-LEGACY,,1000,OLT-1,2,3,PON-2-3,SIN_DATO",
        ])

        resultado = _procesar_ruta_otu(_csv(contenido))

        ruta = Ruta.objects.get(nombre="RUTA-LEGACY")
        self.assertEqual(resultado["creadas"], 1)
        self.assertEqual(ruta.distancia_m, 1000)
        self.assertEqual(ruta.distancia_declarada_m, 1000)
        self.assertIsNone(ruta.capacidad_declarada_hilos)
        self.assertEqual(ruta.fuente_declaracion, "Carga CSV")
        self.assertIsNotNone(ruta.fecha_declaracion)

    def test_csv_ampliado_guarda_todos_los_valores_declarados(self):
        contenido = "\n".join([
            (
                "Troncal,Distancia (m),Capacidad,Hilos Ocupados,"
                "Hilos Reservados,Hilos Libres,Reservas (m),"
                "Enlace PON Traxium"
            ),
            (
                "RUTA-AMPLIADA,1800.5,48,12,4,32,120.5,"
                "https://example.test/pon/1"
            ),
        ])

        resultado = _procesar_ruta_otu(_csv(contenido))

        ruta = Ruta.objects.get(nombre="RUTA-AMPLIADA")
        self.assertEqual(resultado["rechazadas"], 0)
        self.assertEqual(ruta.distancia_declarada_m, 1800.5)
        self.assertEqual(ruta.capacidad_declarada_hilos, 48)
        self.assertEqual(ruta.hilos_ocupados_declarados, 12)
        self.assertEqual(ruta.hilos_reservados_declarados, 4)
        self.assertEqual(ruta.hilos_libres_declarados, 32)
        self.assertEqual(ruta.reservas_declaradas_m, 120.5)
        self.assertEqual(ruta.enlace, "https://example.test/pon/1")

    def test_cero_se_conserva_como_valor_confirmado(self):
        contenido = "\n".join([
            (
                "Ruta,Distancia (m),Capacidad,Hilos Ocupados,"
                "Hilos Reservados,Hilos Libres,Reservas (m)"
            ),
            "RUTA-CEROS,0,24,0,0,24,0",
        ])

        _procesar_ruta_otu(_csv(contenido))

        ruta = Ruta.objects.get(nombre="RUTA-CEROS")
        self.assertEqual(ruta.distancia_declarada_m, 0)
        self.assertEqual(ruta.hilos_ocupados_declarados, 0)
        self.assertEqual(ruta.hilos_reservados_declarados, 0)
        self.assertEqual(ruta.hilos_libres_declarados, 24)
        self.assertEqual(ruta.reservas_declaradas_m, 0)

    def test_fila_inconsistente_revierte_el_archivo_completo(self):
        contenido = "\n".join([
            (
                "Ruta,Capacidad,Hilos Ocupados,Hilos Reservados,"
                "Hilos Libres"
            ),
            "RUTA-VALIDA,24,8,4,12",
            "RUTA-INVALIDA,24,20,10,0",
        ])

        with self.assertRaisesRegex(ValueError, "No se realiz"):
            _procesar_ruta_otu(_csv(contenido))

        self.assertFalse(Ruta.objects.filter(nombre="RUTA-VALIDA").exists())
        self.assertFalse(Ruta.objects.filter(nombre="RUTA-INVALIDA").exists())

    def test_columnas_omitidas_no_borran_declaraciones_previas(self):
        ruta = Ruta.objects.create(
            nombre="RUTA-ACTUALIZADA",
            capacidad_declarada_hilos=48,
            hilos_ocupados_declarados=10,
            hilos_reservados_declarados=6,
            hilos_libres_declarados=32,
            fuente_declaracion="Levantamiento inicial",
        )

        resultado = _procesar_ruta_otu(
            _csv("Ruta,OLT\nruta-actualizada,OLT-NUEVA")
        )

        ruta.refresh_from_db()
        self.assertEqual(resultado["actualizadas"], 1)
        self.assertEqual(ruta.olt, "OLT-NUEVA")
        self.assertEqual(ruta.capacidad_declarada_hilos, 48)
        self.assertEqual(ruta.hilos_ocupados_declarados, 10)
        self.assertEqual(ruta.hilos_reservados_declarados, 6)
        self.assertEqual(ruta.hilos_libres_declarados, 32)
        self.assertEqual(ruta.fuente_declaracion, "Levantamiento inicial")


class PresentacionImportacionesFase4Tests(TestCase):
    def setUp(self):
        usuario = User.objects.create_superuser(
            username="fase4",
            email="fase4@example.test",
            password="clave-segura",
        )
        self.client.force_login(usuario)

    def test_tarjetas_renombradas_y_troncales_en_modo_inventario(self):
        response = self.client.get(reverse("configuracion"))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        titulo_troncales = ">Cargar Troncales y Datos Generales</h3>"
        self.assertEqual(html.count(titulo_troncales), 1)
        self.assertNotIn(">Cargar Troncales y Metadatos</h3>", html)
        self.assertEqual(
            html.count(">Cargar Inventario Técnico de Tramos (.csv)</h3>"),
            1,
        )
        self.assertEqual(
            html.count(">Cargar Fibras por Tramo (.csv)</h3>"),
            1,
        )
        self.assertGreater(
            html.index(titulo_troncales),
            html.index(">Importación guiada del inventario</h2>"),
        )
        self.assertIn(
            "class=\"card\" style=\"text-decoration:none; cursor:pointer;\"",
            html,
        )
