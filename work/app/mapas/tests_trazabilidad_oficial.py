from django.db import IntegrityError, transaction
from django.test import TestCase

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
from .services.trazabilidad import obtener_extremo_fibra, obtener_trazabilidad_fibra
from .services.ubicacion import resolver_site


class TrazabilidadOficialTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ruta = Ruta.objects.create(nombre="RUTA-OFICIAL")
        cls.fibra = InventarioFibra.objects.create(
            ruta=cls.ruta,
            fibra_numero="F12",
        )
        cls.puerto_a = cls._puerto("CP4", "ODF-01", "12")
        cls.puerto_b = cls._puerto("ER314", "ODF-02", "08")

    @classmethod
    def _puerto(cls, site_nombre, odf_nombre, numero):
        site = HubSite.objects.create(nombre=site_nombre)
        sala = SalaTecnica.objects.create(hub_site=site, nombre="SALA-1")
        rack = RackFisico.objects.create(sala=sala, nombre="RACK-1")
        odf = InventarioODF.objects.create(
            rack_obj=rack,
            odf=odf_nombre,
            capacidad_puertos=24,
        )
        return DetallePuertoODF.objects.create(
            odf_obj=odf,
            puerto_odf=numero,
            estado_puerto="LIBRE",
        )

    def test_relaciones_oficiales_definen_ambos_extremos(self):
        TerminacionFibra.objects.create(
            fibra=self.fibra, extremo="A", puerto_odf=self.puerto_a
        )
        TerminacionFibra.objects.create(
            fibra=self.fibra, extremo="B", puerto_odf=self.puerto_b
        )

        resultado = obtener_trazabilidad_fibra(self.fibra)

        self.assertTrue(resultado["completa"])
        self.assertEqual(resultado["extremo_a"]["site"], "CP4")
        self.assertEqual(resultado["extremo_a"]["odf"], "ODF-01")
        self.assertEqual(resultado["extremo_a"]["puerto"], "12")
        self.assertEqual(resultado["extremo_b"]["site"], "ER314")
        self.assertEqual(resultado["extremo_b"]["odf"], "ODF-02")
        self.assertEqual(resultado["extremo_b"]["puerto"], "08")
        self.assertEqual(resultado["extremo_a"]["fuente"], "TERMINACION_FIBRA")
        self.assertNotIn("referencia_legacy", resultado["extremo_a"])

    def test_sin_terminacion_no_inventa_una_referencia(self):
        extremo = obtener_extremo_fibra(self.fibra, "A")

        self.assertFalse(extremo.confirmado)
        self.assertEqual(extremo.fuente, "SIN_DATO")
        self.assertEqual(extremo.etiqueta, "Sin terminación confirmada")

    def test_no_permite_dos_terminaciones_del_mismo_extremo(self):
        TerminacionFibra.objects.create(
            fibra=self.fibra, extremo="A", puerto_odf=self.puerto_a
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TerminacionFibra.objects.bulk_create([
                TerminacionFibra(
                    fibra=self.fibra, extremo="A", puerto_odf=self.puerto_b
                )
            ])

    def test_no_permite_reutilizar_un_puerto(self):
        otra = InventarioFibra.objects.create(
            ruta=self.ruta, fibra_numero="F13"
        )
        TerminacionFibra.objects.create(
            fibra=self.fibra, extremo="A", puerto_odf=self.puerto_a
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TerminacionFibra.objects.bulk_create([
                TerminacionFibra(
                    fibra=otra, extremo="B", puerto_odf=self.puerto_a
                )
            ])

    def test_check_de_base_rechaza_extremo_invalido(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            TerminacionFibra.objects.bulk_create([
                TerminacionFibra(
                    fibra=self.fibra, extremo="X", puerto_odf=self.puerto_a
                )
            ])

    def test_consulta_no_modifica_estado_del_puerto(self):
        TerminacionFibra.objects.create(
            fibra=self.fibra, extremo="A", puerto_odf=self.puerto_a
        )
        self.puerto_a.refresh_from_db()
        self.assertEqual(self.puerto_a.estado_puerto, "OCUPADO")
        obtener_trazabilidad_fibra(self.fibra)
        self.puerto_a.refresh_from_db()
        self.assertEqual(self.puerto_a.estado_puerto, "OCUPADO")

    def test_renombrar_site_se_refleja_en_resolucion_y_trazabilidad(self):
        TerminacionFibra.objects.create(
            fibra=self.fibra,
            extremo="A",
            puerto_odf=self.puerto_a,
        )
        site = self.puerto_a.odf_obj.rack_obj.sala.hub_site
        site.nombre = "CP4-RENOMBRADO"
        site.save(update_fields=["nombre"])

        self.assertEqual(resolver_site(self.puerto_a.odf_obj), site)
        self.assertEqual(
            obtener_trazabilidad_fibra(self.fibra)["extremo_a"]["site"],
            "CP4-RENOMBRADO",
        )
