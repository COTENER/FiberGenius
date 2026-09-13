"""Revisión previa, contadores de negocio e informe íntegro de importación."""
import io
import json
from datetime import timedelta
from pathlib import Path
import shutil
import uuid
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.timezone import now

from .models import (HubSite, LoteImportacion, InventarioODF, DetallePuertoODF,
                     InventarioFibra, TerminacionFibra, AuditoriaPuertoODF, Ruta)
from .views import importacion as imp


def csv(texto):
    return SimpleUploadedFile('revision.csv', texto.encode('utf-8'))


class RevisionImportacionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('revision', password='solo-pruebas')
        self.client.force_login(self.user)
        temporal = Path(settings.BASE_DIR) / '.tmp-review' / f'import-tests-{uuid.uuid4()}'
        temporal.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, temporal, True)
        config = override_settings(DATA_ROOT=temporal)
        config.enable()
        self.addCleanup(config.disable)

    def revisar(self, tipo, texto):
        entrada = csv(texto)
        lote = imp._crear_lote_importacion(entrada, tipo, self.user)
        lote.metadatos_origen = {'modo': 'validar'}
        lote.save(update_fields=['metadatos_origen'])
        imp._procesar_lote_atomicamente(lote.pk, imp.obtener_procesadores_importacion()[tipo][0], entrada)
        lote.refresh_from_db()
        return lote

    def test_revision_sites_no_guarda_y_confirma_mismo_archivo(self):
        texto = 'Nombre,Direccion\nSITE-REV,Norte\n'
        lote = self.revisar('sites_inventario', texto)
        self.assertFalse(HubSite.objects.exists())
        datos = self.client.get(reverse('progreso_importacion', args=[lote.codigo])).json()
        self.assertEqual(datos['modo'], 'validar')
        self.assertEqual(datos['creadas'], 1)
        response = self.client.post(reverse('cargar_csv', args=['sites_inventario']), {
            'csv_file': csv(texto), 'validacion_token': datos['validacion_token'],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(HubSite.objects.get().direccion, 'Norte')

    def test_revision_odf_revierte_puertos_generados(self):
        lote = self.revisar('odf_inventario', 'ODF,Capacidad\nODF-REV,48\n')
        self.assertEqual(lote.filas_creadas, 1)
        self.assertFalse(InventarioODF.objects.exists())
        self.assertFalse(DetallePuertoODF.objects.exists())

    def test_revision_terminaciones_revierte_fibra_ocupacion_y_auditoria(self):
        imp._procesar_odfs_inventario(csv('ODF,Capacidad\nODF-A,2\nODF-B,2\n'))
        datos = 'Fibra,ODF A,Puerto A,ODF B,Puerto B\nF1,ODF-A,1,ODF-B,1\n'
        lote = self.revisar('terminaciones_fibra', datos)
        self.assertEqual(lote.filas_creadas, 2)
        self.assertFalse(InventarioFibra.objects.exists())
        self.assertFalse(TerminacionFibra.objects.exists())
        self.assertFalse(AuditoriaPuertoODF.objects.exists())
        self.assertEqual(set(DetallePuertoODF.objects.values_list('estado_puerto', flat=True)), {'LIBRE'})
        self.assertEqual(sum(InventarioODF.objects.values_list('puertos_ocupados', flat=True)), 0)

    def test_token_no_acepta_archivo_distinto_ni_otro_tipo(self):
        lote = self.revisar('sites_inventario', 'Nombre\nSITE-A\n')
        token = imp._resultado_progreso_lote(lote)['validacion_token']
        for tipo, texto in [('sites_inventario', 'Nombre\nSITE-B\n'), ('ruta_otu', 'Nombre\nSITE-A\n')]:
            response = self.client.post(reverse('cargar_csv', args=[tipo]), {
                'csv_file': csv(texto), 'validacion_token': token,
            })
            self.assertEqual(response.status_code, 400)
        self.assertFalse(HubSite.objects.exists())

    def test_revision_revalida_conflictos_al_confirmar(self):
        texto = 'Nombre,Latitud,Longitud\nSITE-A,-23,-70\n'
        lote = self.revisar('sites_inventario', texto)
        token = imp._resultado_progreso_lote(lote)['validacion_token']
        HubSite.objects.create(nombre='SITE-A', direccion='Cambio GUI')
        self.client.post(reverse('cargar_csv', args=['sites_inventario']), {
            'csv_file': csv(texto), 'validacion_token': token,
        })
        self.assertEqual(HubSite.objects.count(), 1)
        self.assertEqual(HubSite.objects.get().direccion, 'Cambio GUI')

    def test_errores_completos_y_descarga_con_permisos(self):
        entrada = csv('Nombre\nA\n')
        lote = imp._crear_lote_importacion(entrada, 'sites_inventario', self.user)
        errores = [f'fila {i}: ' + 'dato inválido ' * 120 for i in range(2, 103)]
        try:
            imp._errores_csv(errores, 'Sites')
        except ValueError as exc:
            imp._cerrar_lote_fallido(lote, exc)
        lote.refresh_from_db()
        self.assertEqual(len(lote.detalle_errores), 101)
        url = reverse('errores_importacion', args=[lote.codigo])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('fila 102:', response.content.decode())
        self.assertGreater(len(response.content), 100000)
        otro = get_user_model().objects.create_user('otro-revisor')
        self.client.force_login(otro)
        self.assertEqual(self.client.get(url).status_code, 404)

    @patch('mapas.views.importacion._IMPORT_EXECUTOR.submit')
    def test_revision_asincrona_encola_sin_escribir_inventario(self, submit):
        response = self.client.post(reverse('cargar_csv', args=['sites_inventario']), {
            'csv_file': csv('Nombre\nSITE-A\n'), 'modo': 'validar',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 202)
        lote = LoteImportacion.objects.get(codigo=response.json()['lote'])
        self.assertEqual(lote.metadatos_origen['modo'], 'validar')
        self.assertFalse(HubSite.objects.exists())
        submit.assert_called_once()

    def test_revision_exige_mismos_permisos_que_aplicar(self):
        self.client.force_login(get_user_model().objects.create_user('sin-permisos'))
        response = self.client.post(reverse('cargar_csv', args=['sites_inventario']), {
            'csv_file': csv('Nombre\nSITE-A\n'), 'modo': 'validar',
        })
        self.assertEqual(response.status_code, 403)

    def test_contadores_recarga_identica_y_cambio_real(self):
        for tipo, texto in [
            ('sites_inventario', 'Nombre,Direccion\nSITE-A,Norte\n'),
            ('ruta_otu', 'Ruta,Capacidad\nRUTA-A,48\n'),
            ('odf_inventario', 'ODF,Capacidad\nODF-A,4\n'),
            ('fibras_globales', 'Codigo Fibra,Fibra\nFG-REV-1,F1\n'),
            ('puertos_odf_inventario', 'ODF,Puerto,Bandeja\nODF-A,1,1\n'),
        ]:
            with self.subTest(tipo=tipo):
                procesador = imp.obtener_procesadores_importacion()[tipo][0]
                procesador(csv(texto))
                resultado = procesador(csv(texto))
                self.assertEqual(resultado['actualizadas'], 0)
                self.assertEqual(resultado['sin_cambios'], 1)
        resultado = imp._procesar_sites_inventario(csv('Nombre,Direccion\nSITE-A,Sur\n'))
        self.assertEqual(resultado['actualizadas'], 1)
        self.assertEqual(resultado['sin_cambios'], 0)

    def test_contadores_ab_distinguen_filas_y_terminaciones(self):
        imp._procesar_odfs_inventario(csv('ODF,Capacidad\nODF-A,2\nODF-B,2\n'))
        texto = 'Fibra,ODF A,Puerto A,ODF B,Puerto B\nF1,ODF-A,1,ODF-B,1\n'
        primera = imp._procesar_terminaciones_fibra(csv(texto))
        segunda = imp._procesar_terminaciones_fibra(csv(texto))
        self.assertEqual((primera['total'], primera['creadas']), (1, 2))
        self.assertEqual((segunda['actualizadas'], segunda['sin_cambios']), (0, 2))
        self.assertEqual(segunda['unidad'], 'terminaciones')

    def test_geografia_identica_conserva_puntos_y_cuenta_sin_cambios(self):
        ruta = Ruta.objects.create(nombre='R-GEO-REV')
        texto = 'Ruta,Latitude,Longitude\nR-GEO-REV,-23,-70\nR-GEO-REV,-23.1,-70.1\n'
        imp._procesar_coordenadas_csv(csv(texto))
        ids = list(ruta.coordenadas.values_list('pk', flat=True))
        resultado = imp._procesar_coordenadas_csv(csv(texto))
        self.assertEqual(resultado['sin_cambios'], 2)
        self.assertEqual(list(ruta.coordenadas.values_list('pk', flat=True)), ids)

    def test_sin_latido_no_inventa_fallo(self):
        lote = imp._crear_lote_importacion(csv('Nombre\nSITE-A\n'), 'sites_inventario', self.user)
        imp._guardar_progreso(lote.codigo, estado='PROCESANDO', porcentaje=45,
                             latido_en=(now() - timedelta(minutes=3)).isoformat())
        datos = self.client.get(reverse('progreso_importacion', args=[lote.codigo])).json()
        self.assertTrue(datos['sin_senal'])
        self.assertEqual(datos['estado'], 'PROCESANDO')

    @override_settings(FIBERGENIUS_MAX_UPLOAD_BYTES=123456)
    def test_gui_recibe_limite_del_servidor_y_no_duplica_cabeceras(self):
        response = self.client.get(reverse('configuracion'))
        self.assertContains(response, '"maxUploadBytes": 123456')
        self.assertNotContains(response, 'requiredFormats:')

    def test_celdas_sobrantes_no_desplazan_y_finales_opcionales_pueden_omitirse(self):
        with self.assertRaisesRegex(ValueError, 'se esperaban 2'):
            imp._procesar_sites_inventario(csv('Nombre,Direccion\nA,Norte,Extra\n'))
        self.assertFalse(HubSite.objects.exists())
        imp._procesar_sites_inventario(csv('Nombre,Direccion\nA\n'))
        self.assertEqual(HubSite.objects.get().nombre, 'A')

    def test_puertos_informa_todas_las_filas_invalidas(self):
        imp._procesar_odfs_inventario(csv('ODF,Capacidad\nODF-A,2\n'))
        with self.assertRaises(ValueError) as fallo:
            imp._procesar_puertos_odf_inventario(csv('ODF,Puerto\nNO-EXISTE,1\nTAMPOCO,2\n'))
        self.assertEqual(len(fallo.exception.errores_importacion), 2)

    def test_revision_no_ejecuta_callbacks_de_commit(self):
        from django.db import transaction
        disparados = []

        def procesador(archivo, lote=None):
            HubSite.objects.create(nombre='REV-ROLLBACK')
            transaction.on_commit(lambda: disparados.append(True))
            return imp._resultado(1, 1)

        lote = imp._crear_lote_importacion(csv('Nombre\nA\n'), 'sites_inventario', self.user)
        lote.metadatos_origen = {'modo': 'validar'}
        lote.save()
        with self.captureOnCommitCallbacks(execute=True):
            imp._procesar_lote_atomicamente(lote.pk, procesador, io.BytesIO())
        self.assertEqual(disparados, [])
        self.assertFalse(HubSite.objects.exists())

    def test_url_de_revision_no_aplica_aunque_cliente_solicite_aplicar(self):
        response = self.client.post(reverse('validar_csv', args=['sites_inventario']), {
            'csv_file': csv('Nombre\nNO-GUARDAR\n'), 'modo': 'aplicar',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['modo'], 'validar')
        self.assertFalse(HubSite.objects.exists())
