import csv
import io
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .models import FibraTramo, InventarioFibra, InventarioTramo, Ruta
from .services.capacidad import resumen_ruta
from .views.importacion import (
    _procesar_fibras_inventario,
    _procesar_ruta_otu,
    _procesar_tramos_inventario,
)


DIRECTORIO_PLANTILLAS = (
    Path(__file__).resolve().parents[3]
    / "entregables"
    / "plantillas_inventario_multitramo"
)


def _archivo_csv(nombre):
    contenido = (DIRECTORIO_PLANTILLAS / nombre).read_bytes()
    return SimpleUploadedFile(
        nombre,
        contenido,
        content_type="text/csv",
    )


def _precargar_fibras_globales_bhp():
    """Simula la etapa global BHP, separada del detalle por tramo."""
    contenido = (
        DIRECTORIO_PLANTILLAS / "03_fibras_por_tramo.csv"
    ).read_text(encoding="utf-8-sig")
    for fila in csv.DictReader(io.StringIO(contenido)):
        ruta = Ruta.objects.get(nombre=fila["Ruta"].strip())
        InventarioFibra.objects.get_or_create(
            ruta=ruta,
            fibra_numero=fila["Codigo Fibra"].strip(),
        )


class PlantillasEntregablesTests(TestCase):
    def test_paquete_completo_importa_y_se_puede_recargar(self):
        primera_rutas = _procesar_ruta_otu(
            _archivo_csv("01_troncales_datos_generales.csv")
        )
        primera_tramos = _procesar_tramos_inventario(
            _archivo_csv("02_inventario_tecnico_tramos.csv")
        )
        _precargar_fibras_globales_bhp()
        primera_fibras = _procesar_fibras_inventario(
            _archivo_csv("03_fibras_por_tramo.csv")
        )

        self.assertEqual(primera_rutas["creadas"], 2)
        self.assertEqual(primera_tramos["creadas"], 4)
        self.assertEqual(primera_fibras["creadas"], 11)
        self.assertEqual(Ruta.objects.count(), 2)
        self.assertEqual(InventarioTramo.objects.count(), 4)
        self.assertEqual(InventarioFibra.objects.count(), 5)
        self.assertEqual(FibraTramo.objects.count(), 11)

        ruta = Ruta.objects.get(nombre="TRONCAL-EJEMPLO-01")
        resumen = resumen_ruta(ruta)
        self.assertEqual(resumen["tramos_total"], 3)
        self.assertEqual(resumen["distancia_m"], 3000)
        self.assertEqual(resumen["capacidad_efectiva"], 48)
        self.assertEqual(resumen["hilos_ocupados"], 1)
        self.assertEqual(resumen["hilos_reservados"], 1)
        self.assertEqual(resumen["hilos_libres"], 1)
        self.assertEqual(resumen["hilos_sin_estado"], 45)

        segunda_rutas = _procesar_ruta_otu(
            _archivo_csv("01_troncales_datos_generales.csv")
        )
        segunda_tramos = _procesar_tramos_inventario(
            _archivo_csv("02_inventario_tecnico_tramos.csv")
        )
        segunda_fibras = _procesar_fibras_inventario(
            _archivo_csv("03_fibras_por_tramo.csv")
        )

        self.assertEqual(segunda_rutas["actualizadas"], 2)
        self.assertEqual(segunda_tramos["actualizadas"], 4)
        self.assertEqual(segunda_fibras["actualizadas"], 11)
        self.assertEqual(Ruta.objects.count(), 2)
        self.assertEqual(InventarioTramo.objects.count(), 4)
        self.assertEqual(InventarioFibra.objects.count(), 5)
        self.assertEqual(FibraTramo.objects.count(), 11)
