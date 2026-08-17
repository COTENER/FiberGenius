"""Pruebas que requieren bloqueos y constraints reales de PostgreSQL."""

import threading

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TransactionTestCase

from .models import (
    AuditoriaFibra,
    DetallePuertoODF,
    FibraTramo,
    HubSite,
    InventarioFibra,
    InventarioODF,
    InventarioTramo,
    RackFisico,
    Ruta,
    SalaTecnica,
    TerminacionFibra,
)
from .services.fibras import (
    asignar_ruta_fibra,
    establecer_estado_fibra_informado,
    sincronizar_estado_fibra,
)
from .services.puertos import (
    conectar_puerto,
    desconectar_puerto,
    reservar_puerto,
)
from .views.importacion import _procesar_terminaciones_fibra


def _solo_postgresql(test):
    return test if connection.vendor == 'postgresql' else __import__('unittest').skip(
        'Requiere PostgreSQL real.'
    )(test)


@_solo_postgresql
class ConcurrenciaModeloFibraPostgreSQLTests(TransactionTestCase):
    reset_sequences = True

    def _odf(self, cantidad=4):
        site = HubSite.objects.create(nombre='PG-SITE')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='PG-SALA')
        rack = RackFisico.objects.create(sala=sala, nombre='PG-RACK')
        odf = InventarioODF.objects.create(
            rack_obj=rack,
            odf='PG-ODF',
            capacidad_puertos=cantidad,
        )
        puertos = [
            DetallePuertoODF.objects.create(
                odf_obj=odf,
                puerto_odf=str(numero),
                estado_puerto='LIBRE',
            )
            for numero in range(1, cantidad + 1)
        ]
        return site, odf, puertos

    def _hilo(self, funcion, errores):
        close_old_connections()
        try:
            funcion()
        except Exception as exc:  # se verifica el tipo en cada escenario
            errores.append(exc)
        finally:
            close_old_connections()

    def _ejecutar_simultaneas(self, *funciones):
        barrera = threading.Barrier(len(funciones))
        errores = []

        def ejecutar(funcion):
            barrera.wait(10)
            funcion()

        hilos = [
            threading.Thread(
                target=self._hilo,
                args=(lambda fn=funcion: ejecutar(fn), errores),
                daemon=True,
            )
            for funcion in funciones
        ]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(10)
        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        self.assertFalse(any('deadlock' in str(exc).casefold() for exc in errores))
        return errores

    def _assert_puertos_coherentes(self, puertos):
        for puerto in puertos:
            puerto.refresh_from_db()
            ocupado = TerminacionFibra.objects.filter(puerto_odf=puerto).exists()
            self.assertEqual(puerto.estado_puerto == 'Ocupado', ocupado)

    def test_informado_gana_a_inferencia_y_no_deja_auditoria_falsa(self):
        ruta = Ruta.objects.create(nombre='PG-ESTADO')
        tramo = InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            codigo_tramo='PG-ESTADO-T001',
            capacidad_hilos=12,
        )
        fibra = InventarioFibra.objects.create(ruta=ruta, fibra_numero='F1')
        FibraTramo.objects.create(
            tramo=tramo,
            fibra=fibra,
            numero_hilo='F1',
            estado='DISPONIBLE',
        )
        AuditoriaFibra.objects.all().delete()
        bloqueo_tomado = threading.Event()
        liberar = threading.Event()
        errores = []

        def informar():
            with transaction.atomic():
                bloqueada = InventarioFibra.objects.select_for_update().get(pk=fibra.pk)
                establecer_estado_fibra_informado(
                    fibra=bloqueada,
                    estado='OCUPADO',
                )
                bloqueo_tomado.set()
                liberar.wait(10)

        def inferir():
            bloqueo_tomado.wait(10)
            sincronizar_estado_fibra(
                InventarioFibra.objects.get(pk=fibra.pk),
                causa='PRUEBA_CONCURRENCIA',
            )

        hilo_informar = threading.Thread(
            target=self._hilo, args=(informar, errores), daemon=True
        )
        hilo_inferir = threading.Thread(
            target=self._hilo, args=(inferir, errores), daemon=True
        )
        hilo_informar.start()
        hilo_inferir.start()
        self.assertTrue(bloqueo_tomado.wait(10))
        liberar.set()
        hilo_informar.join(10)
        hilo_inferir.join(10)

        self.assertFalse(hilo_informar.is_alive() or hilo_inferir.is_alive())
        self.assertEqual(errores, [])
        fibra.refresh_from_db()
        self.assertEqual((fibra.estado, fibra.origen_estado), (
            'Ocupado', 'INFORMADO'
        ))
        self.assertFalse(AuditoriaFibra.objects.filter(
            fibra=fibra,
            accion='INFERIR_ESTADO',
            metadatos__causa='PRUEBA_CONCURRENCIA',
        ).exists())

    def test_dos_conexiones_al_mismo_puerto_dejan_un_solo_ocupante(self):
        _, _, puertos = self._odf()
        fibras = [
            InventarioFibra.objects.create(fibra_numero=f'F{numero}')
            for numero in (1, 2)
        ]
        barrera = threading.Barrier(2)
        errores = []

        def conectar(fibra_id):
            barrera.wait(10)
            conectar_puerto(
                puerto_id=puertos[0].pk,
                fibra_id=fibra_id,
                extremo='A',
            )

        hilos = [
            threading.Thread(
                target=self._hilo,
                args=(lambda pk=fibra.pk: conectar(pk), errores),
                daemon=True,
            )
            for fibra in fibras
        ]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(10)

        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        self.assertEqual(TerminacionFibra.objects.filter(
            puerto_odf=puertos[0]
        ).count(), 1)
        self.assertEqual(len(errores), 1)
        self.assertIsInstance(errores[0], (ValidationError, IntegrityError))

    def test_misma_fibra_extremo_en_dos_puertos_deja_una_terminacion(self):
        _, _, puertos = self._odf()
        fibra = InventarioFibra.objects.create(fibra_numero='F-MISMA')
        errores = self._ejecutar_simultaneas(
            lambda: conectar_puerto(
                puerto_id=puertos[0].pk, fibra_id=fibra.pk, extremo='A'
            ),
            lambda: conectar_puerto(
                puerto_id=puertos[1].pk, fibra_id=fibra.pk, extremo='A'
            ),
        )
        self.assertEqual(TerminacionFibra.objects.filter(
            fibra=fibra, extremo='A'
        ).count(), 1)
        self.assertEqual(len(errores), 1)
        self._assert_puertos_coherentes(puertos[:2])

    def test_conectar_y_desconectar_mismo_puerto_no_corrompe(self):
        _, _, puertos = self._odf()
        fibra = InventarioFibra.objects.create(fibra_numero='F-CON-DES')
        conectar_puerto(
            puerto_id=puertos[0].pk, fibra_id=fibra.pk, extremo='A'
        )
        errores = self._ejecutar_simultaneas(
            lambda: conectar_puerto(
                puerto_id=puertos[0].pk, fibra_id=fibra.pk, extremo='A'
            ),
            lambda: desconectar_puerto(puerto_id=puertos[0].pk),
        )
        self.assertLessEqual(len(errores), 1)
        self.assertLessEqual(TerminacionFibra.objects.filter(
            fibra=fibra, extremo='A'
        ).count(), 1)
        self._assert_puertos_coherentes(puertos[:1])

    def test_mover_y_desconectar_origen_no_generan_deadlock(self):
        _, _, puertos = self._odf()
        fibra = InventarioFibra.objects.create(fibra_numero='F-MOV-DES')
        conectar_puerto(
            puerto_id=puertos[0].pk, fibra_id=fibra.pk, extremo='A'
        )
        errores = self._ejecutar_simultaneas(
            lambda: conectar_puerto(
                puerto_id=puertos[1].pk,
                fibra_id=fibra.pk,
                extremo='A',
                permitir_mover=True,
            ),
            lambda: desconectar_puerto(puerto_id=puertos[0].pk),
        )
        self.assertLessEqual(len(errores), 1)
        self.assertLessEqual(TerminacionFibra.objects.filter(
            fibra=fibra, extremo='A'
        ).count(), 1)
        self._assert_puertos_coherentes(puertos[:2])

    def test_conexion_compite_con_movimiento_al_mismo_destino(self):
        _, _, puertos = self._odf()
        fibra_a = InventarioFibra.objects.create(fibra_numero='F-MOVER')
        fibra_b = InventarioFibra.objects.create(fibra_numero='F-CONECTAR')
        conectar_puerto(
            puerto_id=puertos[0].pk, fibra_id=fibra_a.pk, extremo='A'
        )
        errores = self._ejecutar_simultaneas(
            lambda: conectar_puerto(
                puerto_id=puertos[1].pk,
                fibra_id=fibra_a.pk,
                extremo='A',
                permitir_mover=True,
            ),
            lambda: conectar_puerto(
                puerto_id=puertos[1].pk, fibra_id=fibra_b.pk, extremo='A'
            ),
        )
        self.assertEqual(TerminacionFibra.objects.filter(
            puerto_odf=puertos[1]
        ).count(), 1)
        self.assertEqual(len(errores), 1)
        self._assert_puertos_coherentes(puertos[:2])

    def test_reserva_concurrente_se_consume_o_se_rechaza_sin_inconsistencia(self):
        _, _, puertos = self._odf()
        fibra = InventarioFibra.objects.create(fibra_numero='F-RESERVA')
        errores = self._ejecutar_simultaneas(
            lambda: reservar_puerto(puerto_id=puertos[0].pk),
            lambda: conectar_puerto(
                puerto_id=puertos[0].pk, fibra_id=fibra.pk, extremo='A'
            ),
        )
        self.assertLessEqual(len(errores), 1)
        self._assert_puertos_coherentes(puertos[:1])

    def test_dos_asignaciones_ruta_hilo_dejan_una_sola_fibra(self):
        ruta = Ruta.objects.create(nombre='PG-ASIGNAR')
        InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            codigo_tramo='PG-ASIGNAR-T001',
            capacidad_hilos=12,
        )
        fibras = [
            InventarioFibra.objects.create(fibra_numero='F3')
            for _ in range(2)
        ]
        barrera = threading.Barrier(2)
        errores = []

        def asignar(fibra_id):
            barrera.wait(10)
            asignar_ruta_fibra(
                fibra=InventarioFibra.objects.get(pk=fibra_id),
                ruta=Ruta.objects.get(pk=ruta.pk),
            )

        hilos = [
            threading.Thread(
                target=self._hilo,
                args=(lambda pk=fibra.pk: asignar(pk), errores),
                daemon=True,
            )
            for fibra in fibras
        ]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(10)

        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        self.assertEqual(InventarioFibra.objects.filter(
            ruta=ruta,
            fibra_numero='F3',
        ).count(), 1)
        self.assertEqual(FibraTramo.objects.filter(
            tramo__ruta=ruta,
            numero_hilo='F3',
        ).count(), 1)
        self.assertEqual(len(errores), 1)

    def test_movimientos_cruzados_no_generan_deadlock_ni_corrupcion(self):
        _, _, puertos = self._odf()
        fibra_a = InventarioFibra.objects.create(fibra_numero='F1')
        fibra_b = InventarioFibra.objects.create(fibra_numero='F2')
        conectar_puerto(
            puerto_id=puertos[0].pk,
            fibra_id=fibra_a.pk,
            extremo='A',
        )
        conectar_puerto(
            puerto_id=puertos[1].pk,
            fibra_id=fibra_b.pk,
            extremo='A',
        )
        barrera = threading.Barrier(2)
        errores = []

        def mover(fibra_id, puerto_id):
            barrera.wait(10)
            conectar_puerto(
                puerto_id=puerto_id,
                fibra_id=fibra_id,
                extremo='A',
                permitir_mover=True,
            )

        hilos = (
            threading.Thread(
                target=self._hilo,
                args=(lambda: mover(fibra_a.pk, puertos[1].pk), errores),
                daemon=True,
            ),
            threading.Thread(
                target=self._hilo,
                args=(lambda: mover(fibra_b.pk, puertos[0].pk), errores),
                daemon=True,
            ),
        )
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(10)

        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        self.assertEqual(len(errores), 2)
        self.assertFalse(any('deadlock' in str(exc).casefold() for exc in errores))
        self.assertEqual(TerminacionFibra.objects.count(), 2)
        self.assertEqual(
            TerminacionFibra.objects.get(fibra=fibra_a).puerto_odf_id,
            puertos[0].pk,
        )
        self.assertEqual(
            TerminacionFibra.objects.get(fibra=fibra_b).puerto_odf_id,
            puertos[1].pk,
        )

    def test_importacion_concurrente_no_borra_edicion_de_estado(self):
        site, odf, puertos = self._odf()
        ruta = Ruta.objects.create(nombre='PG-IMPORTAR')
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F4',
            nombre_fibra='Servicio original',
        )
        barrera = threading.Barrier(2)
        errores = []

        def importar():
            barrera.wait(10)
            contenido = '\n'.join((
                'Ruta,ID Fibra,Fibra,Codigo Fibra,Site,ODF,Puerto,Extremo,Observaciones',
                f'{ruta.nombre},{fibra.pk},F4,{fibra.codigo_fibra},{site.nombre},{odf.odf},'
                f'{puertos[2].puerto_odf},A,Importado',
            ))
            _procesar_terminaciones_fibra(SimpleUploadedFile(
                'pg-import.csv',
                contenido.encode('utf-8'),
                content_type='text/csv',
            ))

        def editar():
            barrera.wait(10)
            establecer_estado_fibra_informado(
                fibra=InventarioFibra.objects.get(pk=fibra.pk),
                estado='OCUPADO',
            )

        hilos = (
            threading.Thread(
                target=self._hilo, args=(importar, errores), daemon=True
            ),
            threading.Thread(
                target=self._hilo, args=(editar, errores), daemon=True
            ),
        )
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(10)

        self.assertTrue(all(not hilo.is_alive() for hilo in hilos))
        self.assertEqual(errores, [])
        fibra.refresh_from_db()
        self.assertEqual((fibra.estado, fibra.origen_estado), (
            'Ocupado', 'INFORMADO'
        ))
        self.assertEqual(fibra.observaciones, 'Importado')

    def test_importacion_bhp_revierte_todo_ante_conflicto_fisico(self):
        site, odf, puertos = self._odf()
        ruta = Ruta.objects.create(nombre='PG-ROLLBACK-BHP')
        fibra_valida = InventarioFibra.objects.create(
            ruta=ruta, fibra_numero='F-VALIDA'
        )
        fibra_conflicto = InventarioFibra.objects.create(
            ruta=ruta, fibra_numero='F-CONFLICTO'
        )
        ocupante = InventarioFibra.objects.create(fibra_numero='F-OCUPANTE')
        conectar_puerto(
            puerto_id=puertos[1].pk,
            fibra_id=ocupante.pk,
            extremo='A',
        )
        contenido = '\n'.join((
            'Ruta,ID Fibra,Fibra,Codigo Fibra,Site,ODF,Puerto,Extremo',
            f'{ruta.nombre},{fibra_valida.pk},F-VALIDA,{fibra_valida.codigo_fibra},{site.nombre},'
            f'{odf.odf},{puertos[0].puerto_odf},A',
            f'{ruta.nombre},{fibra_conflicto.pk},F-CONFLICTO,{fibra_conflicto.codigo_fibra},{site.nombre},'
            f'{odf.odf},{puertos[1].puerto_odf},A',
        ))

        with self.assertRaises((ValidationError, IntegrityError)):
            _procesar_terminaciones_fibra(SimpleUploadedFile(
                'pg-rollback-bhp.csv',
                contenido.encode('utf-8'),
                content_type='text/csv',
            ))

        self.assertFalse(TerminacionFibra.objects.filter(
            fibra=fibra_valida,
        ).exists())
        self.assertEqual(TerminacionFibra.objects.filter(
            puerto_odf=puertos[1],
        ).count(), 1)

    def test_importacion_bhp_compite_con_movimiento_gui_sin_duplicar(self):
        site, odf, puertos = self._odf()
        ruta = Ruta.objects.create(nombre='PG-BHP-MOVER')
        fibra = InventarioFibra.objects.create(
            ruta=ruta,
            fibra_numero='F-BHP',
        )
        conectar_puerto(
            puerto_id=puertos[0].pk,
            fibra_id=fibra.pk,
            extremo='A',
        )

        def importar():
            contenido = '\n'.join((
                'Ruta,ID Fibra,Fibra,Codigo Fibra,Site,ODF,Puerto,Extremo',
                f'{ruta.nombre},{fibra.pk},F-BHP,{fibra.codigo_fibra},{site.nombre},{odf.odf},'
                f'{puertos[1].puerto_odf},A',
            ))
            _procesar_terminaciones_fibra(SimpleUploadedFile(
                'pg-bhp-mover.csv',
                contenido.encode('utf-8'),
                content_type='text/csv',
            ))

        errores = self._ejecutar_simultaneas(
            importar,
            lambda: conectar_puerto(
                puerto_id=puertos[2].pk,
                fibra_id=fibra.pk,
                extremo='A',
                permitir_mover=True,
            ),
        )
        self.assertLessEqual(len(errores), 1)
        self.assertEqual(TerminacionFibra.objects.filter(
            fibra=fibra,
            extremo='A',
        ).count(), 1)
        self._assert_puertos_coherentes(puertos[:3])


@_solo_postgresql
class ConstraintsModeloFibraPostgreSQLTests(TransactionTestCase):
    def test_constraints_criticos_rechazan_inconsistencias(self):
        ruta = Ruta.objects.create(nombre='PG-CONSTRAINTS')
        tramo = InventarioTramo.objects.create(
            ruta=ruta,
            tramo_secuencia=1,
            codigo_tramo='PG-CONSTRAINTS-T001',
            capacidad_hilos=2,
        )
        fibra = InventarioFibra.objects.create(ruta=ruta, fibra_numero='F1')

        with self.assertRaises(IntegrityError), transaction.atomic():
            InventarioFibra.objects.filter(pk=fibra.pk).update(
                estado='OCUPADO',
                origen_estado='NO_INFORMADO',
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            InventarioFibra.objects.bulk_create([
                InventarioFibra(ruta=ruta, fibra_numero='f1'),
            ])

        with self.assertRaises(IntegrityError), transaction.atomic():
            FibraTramo.objects.bulk_create([
                FibraTramo(
                    tramo=tramo,
                    fibra=fibra,
                    numero_hilo='F3',
                )
            ])

        site = HubSite.objects.create(nombre='PG-CONSTRAINT-SITE')
        sala = SalaTecnica.objects.create(hub_site=site, nombre='SALA')
        rack = RackFisico.objects.create(sala=sala, nombre='RACK')
        odf = InventarioODF.objects.create(
            rack_obj=rack,
            odf='PG-CONSTRAINT-ODF',
            capacidad_puertos=2,
        )
        puertos = [
            DetallePuertoODF.objects.create(
                odf_obj=odf,
                puerto_odf=str(numero),
                estado_puerto='LIBRE',
            )
            for numero in (1, 2)
        ]
        otra = InventarioFibra.objects.create(fibra_numero='F2')
        TerminacionFibra.objects.create(
            fibra=fibra,
            extremo='A',
            puerto_odf=puertos[0],
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TerminacionFibra.objects.bulk_create([
                TerminacionFibra(
                    fibra=fibra,
                    extremo='A',
                    puerto_odf=puertos[1],
                )
            ])
        with self.assertRaises(IntegrityError), transaction.atomic():
            TerminacionFibra.objects.bulk_create([
                TerminacionFibra(
                    fibra=otra,
                    extremo='A',
                    puerto_odf=puertos[0],
                )
            ])
