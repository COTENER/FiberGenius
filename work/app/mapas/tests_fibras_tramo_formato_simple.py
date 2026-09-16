import io

from django.test import TestCase

from .models import FibraTramo, InventarioFibra, InventarioTramo, Ruta
from .views.importacion import _procesar_fibras_inventario


class FibrasTramoFormatoSimpleTests(TestCase):
    def setUp(self):
        self.ruta = Ruta.objects.create(nombre='TRONCAL-FIBRAS-SIMPLE')
        self.tramo_1 = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=1,
            codigo_tramo='TRAMO-001',
            capacidad_hilos=12,
        )
        self.tramo_2 = InventarioTramo.objects.create(
            ruta=self.ruta,
            tramo_secuencia=2,
            codigo_tramo='TRAMO-002',
            capacidad_hilos=12,
        )

    def _contenido(self, fibra):
        return (
            'ruta,secuencia,codigo_fibra,fibra,estado,observaciones\n'
            f'{self.ruta.nombre},1,{fibra.codigo_fibra},F1,Ocupado,Inicio\n'
            f'{self.ruta.nombre},2,{fibra.codigo_fibra},F2,Ocupado,Continuidad\n'
        ).encode()

    def test_ubica_fibra_por_ruta_y_secuencia_sin_codigo_tramo(self):
        fibra = InventarioFibra.objects.create(
            ruta=self.ruta,
            fibra_numero='F1',
        )

        resultado = _procesar_fibras_inventario(
            io.BytesIO(self._contenido(fibra))
        )

        self.assertEqual(resultado['creadas'], 2)
        self.assertEqual(
            list(
                fibra.asignaciones_tramo.order_by(
                    'tramo__tramo_secuencia'
                ).values_list(
                    'tramo__tramo_secuencia', 'numero_hilo'
                )
            ),
            [(1, 'F1'), (2, 'F2')],
        )

    def test_asigna_troncal_a_fibra_global_que_estaba_pendiente(self):
        fibra = InventarioFibra.objects.create(fibra_numero='F1')

        _procesar_fibras_inventario(
            io.BytesIO(self._contenido(fibra))
        )

        fibra.refresh_from_db()
        self.assertEqual(fibra.ruta_id, self.ruta.pk)
        self.assertEqual(
            FibraTramo.objects.filter(fibra=fibra).count(),
            2,
        )

    def test_secuencia_y_codigo_antiguo_no_pueden_contradecirse(self):
        fibra = InventarioFibra.objects.create(
            ruta=self.ruta,
            fibra_numero='F1',
        )
        contenido = (
            'ruta,secuencia,codigo_tramo,codigo_fibra,fibra,estado\n'
            f'{self.ruta.nombre},1,TRAMO-002,'
            f'{fibra.codigo_fibra},F1,Disponible\n'
        ).encode()

        with self.assertRaisesRegex(ValueError, 'corresponde'):
            _procesar_fibras_inventario(io.BytesIO(contenido))

        self.assertFalse(FibraTramo.objects.exists())
