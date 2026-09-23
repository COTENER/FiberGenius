import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from .models import AuditoriaFibra, FibraTramo, InventarioFibra, InventarioTramo, LoteImportacion, Ruta
from .services.fibras import crear_fibra
from .services.importacion_fibras import guardar_fibras_globales_masivo
from .views.importacion import _procesar_fibras_globales


class FibrasMasivasTests(TestCase):
    def setUp(self):
        self.ruta = Ruta.objects.create(nombre='TRONCAL-MASIVA')
        self.usuario = get_user_model().objects.create_user(username='masivo')
        self.lote = LoteImportacion.objects.create(tipo='fibras_globales', usuario=self.usuario)

    def filas(self, cantidad=1, **cambios):
        return [dict(fila=i + 2, codigo=f'GLOBAL-{i}', fibra_numero=f'F{i + 1}',
                     ruta=self.ruta, estado=None, condicion_fisica=None,
                     servicio=None, conector=None, observaciones=None) | cambios
                for i in range(cantidad)]

    def guardar(self, filas, **kwargs):
        return guardar_fibras_globales_masivo(filas, usuario=self.usuario, lote=self.lote, **kwargs)

    def test_crea_sin_inventar_estado_recorrido_ni_terminaciones(self):
        InventarioTramo.objects.create(ruta=self.ruta, tramo_secuencia=1)
        self.assertEqual(self.guardar(self.filas()), (1, 0))
        fibra = InventarioFibra.objects.get()
        self.assertEqual((fibra.estado, fibra.origen_estado), ('SIN_INFORMACION', 'NO_INFORMADO'))
        self.assertEqual(fibra.condicion_fisica, 'SIN_VERIFICAR')
        self.assertFalse(FibraTramo.objects.exists())
        self.assertEqual(list(fibra.auditorias.order_by('pk').values_list('accion', flat=True)),
                         ['CREAR_FIBRA', 'ASIGNAR_RUTA'])
        self.assertTrue(all(a.usuario_id == self.usuario.pk and a.lote_importacion_id == self.lote.pk
                            and a.origen == 'EXCEL' for a in fibra.auditorias.all()))

    def test_auditoria_creacion_equivalente_a_servicio_unitario(self):
        unit = crear_fibra(fibra_numero='F1', codigo_fibra='UNIT', ruta=self.ruta,
                          estado='OCUPADO', usuario=self.usuario, origen='EXCEL', lote_importacion=self.lote)
        self.guardar(self.filas(codigo='BULK', fibra_numero='F2', estado='OCUPADO'))
        bulk = InventarioFibra.objects.get(codigo_fibra='BULK')
        self.assertEqual(list(unit.auditorias.order_by('pk').values_list('accion', flat=True)),
                         list(bulk.auditorias.order_by('pk').values_list('accion', flat=True)))
        self.assertEqual((bulk.estado, bulk.origen_estado), (unit.estado, unit.origen_estado))

    def test_recarga_es_idempotente_y_blancos_preservan(self):
        self.guardar(self.filas(estado='RESERVADO', servicio='Servicio A', conector='LC', observaciones='Nota'))
        self.assertEqual(self.guardar(self.filas()), (0, 0))
        fibra = InventarioFibra.objects.get()
        self.assertEqual((fibra.estado, fibra.nombre_fibra, fibra.tipo_conector, fibra.observaciones),
                         ('RESERVADO', 'Servicio A', 'LC', 'Nota'))
        self.assertEqual(self.guardar(self.filas(servicio='', estado='SIN_INFORMACION')), (0, 1))
        fibra.refresh_from_db()
        self.assertEqual((fibra.nombre_fibra, fibra.estado, fibra.origen_estado), ('', 'SIN_INFORMACION', 'NO_INFORMADO'))

    def test_identidad_sin_codigo_se_resuelve_por_numero_normalizado(self):
        self.guardar(self.filas())
        self.assertEqual(self.guardar(self.filas(codigo='', fibra_numero='F01')), (0, 0))
        self.assertEqual(InventarioFibra.objects.count(), 1)

    def test_numero_normalizado_duplicado_se_rechaza(self):
        filas = self.filas(2)
        filas[1]['fibra_numero'] = 'F01'
        with self.assertRaisesRegex(ValidationError, 'fila 3'):
            self.guardar(filas)
        self.assertFalse(InventarioFibra.objects.exists())
        self.assertFalse(AuditoriaFibra.objects.exists())

    def test_no_se_puede_mover_a_otra_troncal(self):
        self.guardar(self.filas())
        otra = Ruta.objects.create(nombre='OTRA')
        with self.assertRaisesRegex(ValidationError, 'otra troncal'):
            self.guardar(self.filas(ruta=otra))

    def test_ruta_omitida_no_omite_unicidad_de_numero_existente(self):
        self.guardar(self.filas(2))
        with self.assertRaisesRegex(ValidationError, 'fila 2'):
            self.guardar(self.filas(ruta=None, fibra_numero='F2'))

    def test_asigna_provisional_sin_cambiar_su_identidad(self):
        self.guardar(self.filas(ruta=None))
        pk = InventarioFibra.objects.get().pk
        self.assertEqual(self.guardar(self.filas()), (0, 1))
        fibra = InventarioFibra.objects.get()
        self.assertEqual(fibra.pk, pk)
        self.assertEqual(fibra.ruta_id, self.ruta.pk)

    def test_fallo_de_modelo_indica_fila_y_no_guarda_nada(self):
        filas = self.filas(300)
        filas[-1]['fibra_numero'] = 'F' + '9' * 60
        with self.assertRaisesRegex(ValidationError, 'fila 301'):
            self.guardar(filas)
        self.assertFalse(InventarioFibra.objects.exists())
        self.assertFalse(AuditoriaFibra.objects.exists())

    def test_choices_y_longitudes_siguen_validadas(self):
        for cambios in [{'estado': 'FALSO'}, {'condicion_fisica': 'FALSO'}, {'servicio': 'x' * 151},
                        {'conector': 'x' * 101}, {'codigo': 'x' * 65}]:
            with self.subTest(cambios=cambios), self.assertRaisesRegex(ValidationError, 'fila 2'):
                self.guardar(self.filas(**cambios))
        self.assertFalse(InventarioFibra.objects.exists())

    def test_error_sql_segundo_lote_revierte_fibras_y_auditoria(self):
        manager = InventarioFibra.objects
        original = manager.bulk_create
        llamadas = 0
        def fallar(*args, **kwargs):
            nonlocal llamadas
            llamadas += 1
            if llamadas == 2:
                raise IntegrityError('simulated constraint conflict')
            return original(*args, **kwargs)
        with patch.object(manager, 'bulk_create', side_effect=fallar):
            with self.assertRaisesRegex(ValidationError, 'filas 252–301'):
                self.guardar(self.filas(300))
        self.assertFalse(InventarioFibra.objects.exists())
        self.assertFalse(AuditoriaFibra.objects.exists())

    def test_progreso_por_lote_no_equivale_a_commit(self):
        progreso = []
        self.guardar(self.filas(501), progreso=lambda *args: progreso.append(args))
        escrituras = [p for p in progreso if p[0] == 'guardar']
        self.assertEqual([p[1] for p in escrituras], [250, 500, 501])

    def test_consultas_no_crecen_por_fila(self):
        with CaptureQueriesContext(connection) as consultas:
            self.guardar(self.filas(500))
        self.assertLess(len(consultas), 50, f'{len(consultas)} consultas para 500 fibras')

    def test_importador_conserva_fila_en_error_de_modelo(self):
        archivo = io.BytesIO(('codigo_fibra,fibra\nGLOBAL-1,F1\nGLOBAL-2,F' + '9' * 60 + '\n').encode())
        with self.assertRaisesRegex(ValueError, 'fila 3'):
            _procesar_fibras_globales(archivo, self.lote)
        self.assertFalse(InventarioFibra.objects.exists())

    def test_importador_publica_guardado_sin_adelantar_cien(self):
        archivo = io.BytesIO(b'codigo_fibra,fibra\nGLOBAL-1,F1\n')
        with patch('mapas.views.importacion._reportar_progreso') as avance:
            _procesar_fibras_globales(archivo, self.lote)
        porcentajes = [c.args[0] for c in avance.call_args_list]
        self.assertEqual(porcentajes, sorted(porcentajes))
        self.assertEqual(max(porcentajes), 95)
