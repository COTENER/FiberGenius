import io

from django.test import TestCase

from .models import InventarioTramo, NodoRed, Ruta
from .views.importacion import _procesar_tramos_inventario


class TramosFormatoSimpleTests(TestCase):
    def setUp(self):
        self.ruta = Ruta.objects.create(nombre='TRONCAL-SIMPLE')
        self.contenido = (
            'ruta,secuencia,origen,destino\n'
            'TRONCAL-SIMPLE,1,SITE-A,MUFA-01\n'
            'TRONCAL-SIMPLE,2,MUFA-01,SITE-B\n'
        ).encode()

    def test_importa_sin_tipos_ni_codigos_suministrados(self):
        resultado = _procesar_tramos_inventario(
            io.BytesIO(self.contenido)
        )

        self.assertEqual(resultado['creadas'], 2)
        tramos = list(
            self.ruta.tramos_inventario.order_by('tramo_secuencia')
        )
        self.assertEqual(
            [tramo.codigo_tramo for tramo in tramos],
            ['TRAMO-001', 'TRAMO-002'],
        )
        self.assertEqual(tramos[0].destino_nodo_id, tramos[1].origen_nodo_id)
        self.assertEqual(tramos[0].origen_nodo.nombre, 'SITE-A')
        self.assertEqual(tramos[1].destino_nodo.nombre, 'SITE-B')
        self.assertEqual(NodoRed.objects.count(), 3)

    def test_reimportacion_simple_es_idempotente(self):
        _procesar_tramos_inventario(io.BytesIO(self.contenido))

        resultado = _procesar_tramos_inventario(
            io.BytesIO(self.contenido)
        )

        self.assertEqual(resultado['creadas'], 0)
        self.assertEqual(resultado['actualizadas'], 2)
        self.assertEqual(InventarioTramo.objects.count(), 2)
        self.assertEqual(NodoRed.objects.count(), 3)

    def test_recarga_simple_preserva_codigo_interno_existente(self):
        origen = NodoRed.objects.create(tipo='OTRO', codigo='SITE-A')
        destino = NodoRed.objects.create(tipo='OTRO', codigo='SITE-B')
        tramo = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=1,
            codigo_tramo='CODIGO-INTERNO-PREVIO',
            origen_nodo=origen,
            destino_nodo=destino,
        )
        contenido = (
            'ruta,secuencia,origen,destino\n'
            'TRONCAL-SIMPLE,1,SITE-A,SITE-B\n'
        ).encode()

        _procesar_tramos_inventario(io.BytesIO(contenido))

        tramo.refresh_from_db()
        self.assertEqual(tramo.codigo_tramo, 'CODIGO-INTERNO-PREVIO')
