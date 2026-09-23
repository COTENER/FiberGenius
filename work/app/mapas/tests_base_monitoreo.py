"""Fundación pasiva: no requiere equipos, red ni tablas OTDR manuales."""
from datetime import timedelta
from importlib import import_module

from django.contrib import admin
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, migrations, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .models import (
    AuditoriaFibra, CanalMonitoreo, DetallePuertoODF, FibraTramo,
    FuenteMonitoreo, InventarioFibra, Ruta, TerminacionFibra,
)
from .services.canales_monitoreo import canales_de_troncal, registrar_canal, retirar_canal
from .services.fibras import asignar_ruta_fibra


@override_settings(FIBERGENIUS_OPERATIONS_UI_ENABLED=False, VEEX_ENABLED=False)
class BaseMonitoreoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.fuente = FuenteMonitoreo.objects.create(nombre='Servidor de laboratorio', proveedor='VeEX')
        cls.ruta = Ruta.objects.create(nombre='TRONCAL-BASE')
        cls.f1 = InventarioFibra.objects.create(ruta=cls.ruta, fibra_numero='F1')
        cls.f17 = InventarioFibra.objects.create(ruta=cls.ruta, fibra_numero='F17')

    def canal(self, identificador='EQUIPO-1/PUERTO-1', fibra=None, **kwargs):
        return registrar_canal(
            fuente=kwargs.pop('fuente', self.fuente),
            identificador_externo=identificador, fibra=fibra or self.f1, **kwargs,
        )

    def test_varios_hilos_por_troncal_sin_puerto_otu_unico(self):
        primero = self.canal()
        segundo = self.canal('EQUIPO-1/PUERTO-2', self.f17)
        canales = list(canales_de_troncal(self.ruta))
        self.assertEqual({c.pk for c in canales}, {primero.pk, segundo.pk})
        self.assertTrue(all(c.ruta.pk == self.ruta.pk for c in canales))
        self.assertEqual(self.ruta.puerto if hasattr(self.ruta, 'puerto') else None, None)

    def test_dos_extremos_de_una_fibra_admiten_canales_distintos(self):
        self.canal('EQUIPO-A/7', extremo_origen='a')
        self.canal('EQUIPO-B/9', extremo_origen='B')
        self.assertEqual(set(self.f1.canales_monitoreo.values_list('extremo_origen', flat=True)), {'A', 'B'})

    def test_provisional_conserva_identidad_cuando_se_completa_troncal(self):
        fibra = InventarioFibra.objects.create(fibra_numero='F3')
        canal = self.canal(fibra=fibra)
        self.assertIsNone(canal.ruta)
        self.assertEqual(canal.extremo_origen, '')
        codigo = canal.codigo
        asignar_ruta_fibra(fibra=fibra, ruta=self.ruta)
        canal.refresh_from_db()
        self.assertEqual(canal.ruta.pk, self.ruta.pk)
        self.assertEqual(canal.codigo, codigo)
        self.assertFalse(FibraTramo.objects.exists())

    def test_ids_externos_se_delimitan_por_fuente(self):
        otra = FuenteMonitoreo.objects.create(nombre='Segundo servidor', proveedor='VeEX')
        self.canal('7')
        otro = self.canal('7', self.f17, fuente=otra)
        self.assertEqual(CanalMonitoreo.objects.count(), 2)
        self.assertNotEqual(self.fuente.codigo, otra.codigo)
        self.assertEqual(otro.fibra_id, self.f17.pk)

    def test_canal_externo_vigente_no_puede_apuntar_a_dos_fibras(self):
        self.canal(' canal-1 ')
        with self.assertRaises(ValidationError):
            self.canal('canal-1', self.f17)
        self.assertEqual(CanalMonitoreo.objects.count(), 1)

    def test_bd_impide_duplicado_aunque_se_omita_save(self):
        self.canal('canal-1')
        with self.assertRaises(IntegrityError), transaction.atomic():
            CanalMonitoreo.objects.bulk_create([
                CanalMonitoreo(fuente=self.fuente, identificador_externo='canal-1', fibra=self.f17),
            ])

    def test_retiro_y_nueva_asociacion_conservan_identidad_anterior(self):
        anterior = self.canal()
        retirado = retirar_canal(canal=anterior)
        nuevo = self.canal(fibra=self.f17)
        self.assertNotEqual(anterior.codigo, nuevo.codigo)
        anterior.refresh_from_db()
        self.assertEqual(anterior.fibra_id, self.f1.pk)
        self.assertFalse(anterior.vigente)
        self.assertEqual(retirar_canal(canal=anterior).retirado_en, retirado.retirado_en)
        self.assertEqual(list(canales_de_troncal(self.ruta).values_list('pk', flat=True)), [nuevo.pk])

    def test_no_se_reasigna_identidad_en_el_mismo_registro(self):
        canal = self.canal()
        for campo, valor in [('fibra', self.f17), ('identificador_externo', 'otro'), ('extremo_origen', 'B')]:
            with self.subTest(campo=campo):
                canal.refresh_from_db()
                setattr(canal, campo, valor)
                with self.assertRaisesRegex(ValidationError, 'identidad del puerto de monitoreo'):
                    canal.save()
        canal.refresh_from_db()
        self.assertEqual(canal.fibra_id, self.f1.pk)

    def test_no_se_reactiva_asociacion_retirada(self):
        canal = retirar_canal(canal=self.canal())
        canal.retirado_en = None
        with self.assertRaisesRegex(ValidationError, 'no se reactiva'):
            canal.save()

    def test_bd_valida_extremo_identificador_y_fecha(self):
        canal = self.canal()
        invalidos = [
            {'extremo_origen': 'C'}, {'identificador_externo': ''},
            {'retirado_en': canal.creado_en - timedelta(seconds=1)},
        ]
        for cambios in invalidos:
            with self.subTest(cambios=cambios), self.assertRaises(IntegrityError), transaction.atomic():
                CanalMonitoreo.objects.filter(pk=canal.pk).update(**cambios)

    def test_modelo_rechaza_identificadores_vacios_y_fuente_vacia(self):
        with self.assertRaises(ValidationError):
            self.canal('   ')
        with self.assertRaises(ValidationError):
            FuenteMonitoreo.objects.create(nombre='   ')

    def test_fibra_y_fuente_referenciadas_no_se_borran_en_cascada(self):
        self.canal()
        with self.assertRaises(ProtectedError):
            self.f1.delete()
        with self.assertRaises(ProtectedError):
            self.fuente.delete()

    def test_canal_no_cambia_estado_ni_crea_inventario(self):
        self.canal()
        self.f1.refresh_from_db()
        self.assertEqual((self.f1.estado, self.f1.origen_estado, self.f1.condicion_fisica),
                         ('SIN_INFORMACION', 'NO_INFORMADO', 'SIN_VERIFICAR'))
        self.assertEqual(InventarioFibra.objects.count(), 2)
        for modelo in (DetallePuertoODF, TerminacionFibra, FibraTramo, AuditoriaFibra):
            self.assertFalse(modelo.objects.exists())

    def test_renombrar_troncal_no_pierde_correlacion(self):
        canal = self.canal()
        self.ruta.nombre = 'TRONCAL-RENOMBRADA'
        self.ruta.save(update_fields=['nombre'])
        canal.refresh_from_db()
        self.assertEqual(canal.ruta.nombre, 'TRONCAL-RENOMBRADA')
        self.assertEqual(canales_de_troncal(self.ruta).get().pk, canal.pk)

    def test_base_no_expone_admin_permisos_ni_activa_webhook(self):
        self.assertEqual(CanalMonitoreo._meta.verbose_name, 'Puerto de monitoreo')
        self.assertEqual(CanalMonitoreo._meta.verbose_name_plural, 'Puertos de monitoreo')
        self.assertFalse(admin.site.is_registered(CanalMonitoreo))
        self.assertFalse(admin.site.is_registered(FuenteMonitoreo))
        self.assertFalse(Permission.objects.filter(
            content_type__app_label='mapas',
            content_type__model__in=['canalmonitoreo', 'fuentemonitoreo'],
        ).exists())
        self.assertEqual(self.client.post(reverse('webhook_alarma'), '{}', content_type='application/json').status_code, 404)

    def test_migracion_solo_crea_tablas_nuevas(self):
        migration = import_module('mapas.migrations.0049_base_monitoreo_futuro').Migration
        self.assertEqual(migration.dependencies, [('mapas', '0048_reglas_aprobadas_inventario')])
        self.assertEqual(len(migration.operations), 2)
        self.assertTrue(all(isinstance(op, migrations.CreateModel) for op in migration.operations))


class MigracionBaseMonitoreoTests(TransactionTestCase):
    def test_upgrade_conserva_inventario_y_no_crea_canales_automaticos(self):
        anterior = [('mapas', '0048_reglas_aprobadas_inventario')]
        actual = [('mapas', '0049_base_monitoreo_futuro')]
        executor = MigrationExecutor(connection)
        try:
            executor.migrate(anterior)
            apps = executor.loader.project_state(anterior).apps
            ruta = apps.get_model('mapas', 'Ruta').objects.create(nombre='TRONCAL-PRE-UPGRADE')
            fibra = apps.get_model('mapas', 'InventarioFibra').objects.create(
                ruta=ruta, fibra_numero='F17', codigo_fibra='FIBRA-PRE-UPGRADE',
                estado='OCUPADO', origen_estado='INFORMADO',
            )
            executor = MigrationExecutor(connection)
            executor.migrate(actual)
            apps = executor.loader.project_state(actual).apps
            guardada = apps.get_model('mapas', 'InventarioFibra').objects.get(pk=fibra.pk)
            self.assertEqual((guardada.ruta_id, guardada.codigo_fibra, guardada.fibra_numero, guardada.estado),
                             (ruta.pk, 'FIBRA-PRE-UPGRADE', 'F17', 'OCUPADO'))
            self.assertEqual(apps.get_model('mapas', 'FuenteMonitoreo').objects.count(), 0)
            self.assertEqual(apps.get_model('mapas', 'CanalMonitoreo').objects.count(), 0)
        finally:
            MigrationExecutor(connection).migrate(actual)
