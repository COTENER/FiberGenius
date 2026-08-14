import json

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .models import DetallePuertoODF, InventarioODF
from .services.inventario import ajustar_puertos_a_capacidad
from .views.importacion import (
    _procesar_odfs_inventario,
    _procesar_puertos_odf_inventario,
)


class PuertosAutomaticosODFTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_superuser(
            username="admin-odf-auto",
            password="clave-segura",
            email="admin@example.com",
        )
        self.client.force_login(self.usuario)

    def _crear_odf(self, capacidad=48):
        respuesta = self.client.post(
            reverse("api_create_odf"),
            data=json.dumps(
                {
                    "hub_site": "SITE-AUTO",
                    "sala": "SALA-AUTO",
                    "rack": "RACK-AUTO",
                    "odf": "ODF-AUTO",
                    "capacidad_puertos": capacidad,
                    "tipo_conector": "SC/APC",
                    "estado": "Activo",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        return InventarioODF.objects.get(odf="ODF-AUTO")

    def test_crear_odf_materializa_toda_la_capacidad_como_libre(self):
        odf = self._crear_odf(48)
        puertos = odf.puertos_detalle.order_by("pk")

        self.assertEqual(puertos.count(), 48)
        self.assertEqual(set(puertos.values_list("estado_puerto", flat=True)), {"Libre"})
        self.assertEqual(set(puertos.values_list("tipo_conector", flat=True)), {"SC/APC"})
        self.assertTrue(puertos.filter(puerto_odf="1").exists())
        self.assertTrue(puertos.filter(puerto_odf="48").exists())
        odf.refresh_from_db()
        self.assertEqual(odf.puertos_libres, 48)
        self.assertEqual(odf.puertos_ocupados, 0)

    def test_aumentar_capacidad_crea_solamente_puertos_faltantes(self):
        odf = self._crear_odf(4)
        ajustar_puertos_a_capacidad(odf, 6)

        self.assertEqual(odf.puertos_detalle.count(), 6)
        self.assertTrue(odf.puertos_detalle.filter(puerto_odf="6").exists())

    def test_importar_odf_materializa_la_capacidad_completa(self):
        archivo = SimpleUploadedFile(
            "odfs.csv",
            (
                "hub_site,sala,rack,odf,capacidad_puertos,tipo_conector\n"
                "SITE-CSV,SALA-CSV,RACK-CSV,ODF-CSV,6,SC/APC\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        _procesar_odfs_inventario(archivo)

        odf = InventarioODF.objects.get(odf="ODF-CSV")
        self.assertEqual(odf.puertos_detalle.count(), 6)
        self.assertEqual(
            set(odf.puertos_detalle.values_list("estado_puerto", flat=True)),
            {"Libre"},
        )

    def test_archivo_de_puertos_no_crea_ocupacion_sin_terminacion(self):
        odf = self._crear_odf(2)
        archivo = SimpleUploadedFile(
            "puertos.csv",
            (
                "hub_site,odf,puerto_odf,estado_puerto\n"
                f"{odf.hub_site},{odf.odf},1,Ocupado\n"
            ).encode("utf-8"),
            content_type="text/csv",
        )

        with self.assertRaisesMessage(
            ValueError,
            "un puerto no se importa como Ocupado",
        ):
            _procesar_puertos_odf_inventario(archivo)

    def test_reducir_capacidad_no_elimina_un_puerto_en_uso(self):
        odf = self._crear_odf(6)
        puerto = DetallePuertoODF.objects.get(odf_obj=odf, puerto_odf="6")
        puerto.destino = "SWITCH-CORE / PUERTO-6"
        puerto.save()
        DetallePuertoODF.objects.filter(pk=puerto.pk).update(estado_puerto="Ocupado")

        with self.assertRaises(ValidationError):
            ajustar_puertos_a_capacidad(odf, 4)

        odf.refresh_from_db()
        self.assertEqual(odf.capacidad_puertos, 6)
        self.assertEqual(odf.puertos_detalle.count(), 6)

    def test_api_muestra_nombre_oficial_aunque_el_texto_legacy_difiera(self):
        odf = self._crear_odf(2)
        puerto = DetallePuertoODF.objects.get(odf_obj=odf, puerto_odf="1")
        DetallePuertoODF.objects.filter(pk=puerto.pk).update(
            odf="SITE-INCORRECTO"
        )

        respuesta = self.client.get(
            reverse("api_detalle_puertos", args=[odf.odf])
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["data"][0]["odf"], "ODF-AUTO")
