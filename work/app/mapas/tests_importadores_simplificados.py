import io

from django.test import TestCase

from .models import (
    CoordenadaRuta,
    FibraTramo,
    InventarioFibra,
    InventarioTramo,
    OTU,
    PuertoOTU,
    Reserva,
    Ruta,
)
from .views.importacion import (
    _asociar_puertos_a_rutas,
    _procesar_coordenadas_csv,
    _procesar_fibras_globales,
    _procesar_fibras_inventario,
    _procesar_reservas,
)


def _csv(contenido):
    return io.BytesIO(contenido.encode('utf-8'))


class ImportadoresSimplificadosTests(TestCase):
    def test_fibra_global_no_inventa_posicion_y_numero_es_unico_por_troncal(self):
        ruta = Ruta.objects.create(nombre='RUTA-BLOQUES')
        tramo = InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            codigo_tramo='RUTA-BLOQUES-T001',
            capacidad_hilos=48,
        )
        with self.assertRaisesMessage(ValueError, 'repetida'):
            _procesar_fibras_globales(_csv(
                'Codigo Fibra,Fibra,Ruta\n'
                'FGF-CABLE-A-F017,F17,RUTA-BLOQUES\n'
                'FGF-CABLE-B-F017,F17,RUTA-BLOQUES\n'
            ))
        self.assertEqual(InventarioFibra.objects.count(), 0)
        resultado = _procesar_fibras_globales(_csv(
            'Codigo Fibra,Fibra,Ruta\n'
            'FGF-CABLE-A-F017,F17,RUTA-BLOQUES\n'
            'FGF-CABLE-B-F017,F18,RUTA-BLOQUES\n'
        ))
        self.assertEqual(resultado['creadas'], 2)
        self.assertEqual(FibraTramo.objects.count(), 0)
        self.assertEqual(
            set(InventarioFibra.objects.values_list('estado', flat=True)),
            {'SIN_INFORMACION'},
        )

        detalle = _procesar_fibras_inventario(_csv(
            'Ruta,Secuencia,Codigo Fibra,Fibra\n'
            'RUTA-BLOQUES,1,FGF-CABLE-A-F017,F1\n'
            'RUTA-BLOQUES,1,FGF-CABLE-B-F017,F19\n'
        ))
        self.assertEqual(detalle['creadas'], 2)
        self.assertEqual(
            set(FibraTramo.objects.filter(tramo=tramo).values_list(
                'numero_hilo', flat=True
            )),
            {'F1', 'F19'},
        )

    def test_condicion_sin_novedad_se_normaliza_como_operativa(self):
        _procesar_fibras_globales(_csv(
            'Codigo Fibra,Fibra,Condicion\n'
            'FGF-SIN-NOVEDAD,F1,SIN_NOVEDAD\n'
        ))
        self.assertEqual(
            InventarioFibra.objects.get(
                codigo_fibra='FGF-SIN-NOVEDAD'
            ).condicion_fisica,
            'OPERATIVA',
        )

    def test_reserva_con_codigo_actualiza_coordenadas_sin_duplicarse(self):
        Ruta.objects.create(nombre='RUTA-RESERVA')
        cabecera = 'Codigo,Ruta,Nombre,Tipo,Reserva (m),Latitud,Longitud\n'
        _procesar_reservas(_csv(
            cabecera
            + 'RES-001,RUTA-RESERVA,MUFA-1,Mufa,,-23.1,-69.1\n'
        ))
        _procesar_reservas(_csv(
            cabecera
            + 'RES-001,RUTA-RESERVA,MUFA-1,Mufa,20,-23.2,-69.2\n'
        ))
        reserva = Reserva.objects.get(codigo='RES-001')
        self.assertEqual(Reserva.objects.count(), 1)
        self.assertEqual(float(reserva.latitud), -23.2)
        self.assertEqual(reserva.reserva_m, 20)

    def test_recorrido_acepta_encabezados_en_espanol(self):
        ruta = Ruta.objects.create(nombre='RUTA-MAPA-ES')
        _procesar_coordenadas_csv(_csv(
            'Ruta,Latitud,Longitud,Tipo trazado,Inicio segmento\n'
            'RUTA-MAPA-ES,-23.1,-69.1,AEREO,Si\n'
            'RUTA-MAPA-ES,-23.2,-69.2,SOTERRADO,Si\n'
        ))
        self.assertEqual(CoordenadaRuta.objects.filter(ruta=ruta).count(), 2)

    def test_asociacion_otu_no_limpia_puertos_omitidos(self):
        otu = OTU.objects.create(nombre='OTU-1')
        ruta_existente = Ruta.objects.create(nombre='RUTA-EXISTENTE', otu=otu)
        ruta_nueva = Ruta.objects.create(nombre='RUTA-NUEVA-OTU', otu=otu)
        puerto_existente = PuertoOTU.objects.create(
            otu=otu, numero=1, ruta_asociada=ruta_existente
        )
        puerto_nuevo = PuertoOTU.objects.create(otu=otu, numero=2)

        _asociar_puertos_a_rutas(_csv(
            'Descripcion,Puerto,Enlaces\n'
            'OTU-1,2,RUTA-NUEVA-OTU\n'
        ))
        puerto_existente.refresh_from_db()
        puerto_nuevo.refresh_from_db()
        self.assertEqual(puerto_existente.ruta_asociada, ruta_existente)
        self.assertEqual(puerto_nuevo.ruta_asociada, ruta_nueva)
