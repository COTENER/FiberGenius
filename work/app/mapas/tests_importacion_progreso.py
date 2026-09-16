from pathlib import Path
import json
import shutil
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import OperationalError
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from .models import LoteImportacion, Ruta
from .views.importacion import _ejecutar_importacion_en_segundo_plano
from .views import importacion


class ProgresoImportacionTests(TransactionTestCase):
    def setUp(self):
        self.directorio = Path(settings.BASE_DIR) / '.tmp-tests-progress-data'
        self.directorio.mkdir(parents=True, exist_ok=True)
        self.ajustes = override_settings(DATA_ROOT=self.directorio)
        self.ajustes.enable()
        self.addCleanup(self.ajustes.disable)
        self.addCleanup(shutil.rmtree, self.directorio, True)

        self.usuario = get_user_model().objects.create_user(
            username='importador-progreso',
            password='clave-segura',
        )
        self.usuario.user_permissions.add(*Permission.objects.filter(
            codename__in=['add_ruta', 'change_ruta', 'add_otu'],
        ))
        self.client.force_login(self.usuario)

    def _archivo(self):
        return SimpleUploadedFile(
            'rutas-progreso.csv',
            (
                'Ruta,OTU,Distancia (m),OLT,SLOT OLT,PUERTO OLT,PON,Enlace\n'
                'RUTA-PROGRESO,,1000,,,,,NO_APLICA\n'
            ).encode(),
            content_type='text/csv',
        )

    @patch('mapas.views.importacion._IMPORT_EXECUTOR.submit')
    def test_gui_encola_y_publica_progreso_sin_esperar_resultado(self, submit):
        respuesta = self.client.post(
            reverse('cargar_csv', args=['ruta_otu']),
            {'csv_file': self._archivo()},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(respuesta.status_code, 202)
        payload = respuesta.json()
        lote = LoteImportacion.objects.get(codigo=payload['lote'])
        self.assertEqual(lote.estado, 'PENDIENTE')
        submit.assert_called_once()

        estado = self.client.get(payload['progreso_url'])
        self.assertEqual(estado.status_code, 200)
        self.assertEqual(estado.json()['estado'], 'PENDIENTE')
        self.assertEqual(estado.json()['porcentaje'], 2)
        self.assertEqual(estado.json()['usuario_id'], self.usuario.pk)

    @patch('mapas.views.importacion._IMPORT_EXECUTOR.submit')
    def test_progreso_exige_sesion_y_respeta_el_propietario(self, submit):
        respuesta = self.client.post(
            reverse('cargar_csv', args=['ruta_otu']),
            {'csv_file': self._archivo()},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        progreso_url = respuesta.json()['progreso_url']

        self.client.logout()
        anonimo = self.client.get(progreso_url)
        self.assertEqual(anonimo.status_code, 302)

        otro_usuario = get_user_model().objects.create_user(
            username='otro-importador',
            password='clave-segura',
        )
        self.client.force_login(otro_usuario)
        ajeno = self.client.get(progreso_url)
        self.assertEqual(ajeno.status_code, 404)

    @patch('mapas.views.importacion._IMPORT_EXECUTOR.submit')
    def test_trabajo_en_segundo_plano_cierra_lote_y_llega_a_cien(self, submit):
        respuesta = self.client.post(
            reverse('cargar_csv', args=['ruta_otu']),
            {'csv_file': self._archivo()},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        argumentos = submit.call_args.args
        self.assertIs(argumentos[0], _ejecutar_importacion_en_segundo_plano)

        argumentos[0](*argumentos[1:])

        lote = LoteImportacion.objects.get(codigo=respuesta.json()['lote'])
        self.assertEqual(lote.estado, 'COMPLETADO')
        self.assertEqual(lote.total_filas, 1)
        self.assertTrue(Ruta.objects.filter(nombre='RUTA-PROGRESO').exists())
        estado = self.client.get(respuesta.json()['progreso_url']).json()
        self.assertEqual(estado['estado'], 'COMPLETADO')
        self.assertEqual(estado['porcentaje'], 100)
        self.assertEqual(estado['procesadas'], 1)

    def test_configuracion_incluye_barra_y_sondeo_de_progreso(self):
        respuesta = self.client.get(reverse('configuracion'))
        self.assertContains(respuesta, 'id="importExecutionBar"')
        self.assertContains(respuesta, 'pollImportProgress')

    def _encolar(self, contenido=None):
        archivo = self._archivo() if contenido is None else SimpleUploadedFile(
            'rutas.csv', contenido, content_type='text/csv',
        )
        with patch('mapas.views.importacion._IMPORT_EXECUTOR.submit') as submit:
            respuesta = self.client.post(
                reverse('cargar_csv', args=['ruta_otu']),
                {'csv_file': archivo}, HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            )
        self.assertEqual(respuesta.status_code, 202)
        return respuesta.json(), submit.call_args.args

    def test_fallo_publicando_resultado_no_cambia_exito_ni_archiva_como_fallido(self):
        payload, argumentos = self._encolar()
        guardar_original = importacion._guardar_progreso

        def fallar_final(codigo, **datos):
            if datos.get('estado') == 'COMPLETADO':
                raise OSError('Fallo simulado de disco al publicar el resultado')
            return guardar_original(codigo, **datos)

        with patch.object(importacion, '_guardar_progreso', side_effect=fallar_final):
            argumentos[0](*argumentos[1:])

        lote = LoteImportacion.objects.get(codigo=payload['lote'])
        self.assertEqual(lote.estado, 'COMPLETADO')
        self.assertTrue(Ruta.objects.filter(nombre='RUTA-PROGRESO').exists())
        self.assertFalse(Path(argumentos[-1]).exists())
        self.assertFalse(list((self.directorio / 'importaciones' / 'fallidos').iterdir()))
        # El JSON quedó atrasado, pero el sondeo recupera el commit de la BD.
        cache = json.loads(importacion._ruta_progreso(lote.codigo).read_text())
        self.assertEqual(cache['estado'], 'PROCESANDO')
        estado = self.client.get(payload['progreso_url'])
        self.assertEqual(estado.json()['estado'], 'COMPLETADO')
        self.assertEqual(estado.json()['porcentaje'], 100)
        self.assertEqual(estado.json()['creadas'], 1)

    def test_error_de_archivo_progreso_durante_carga_no_aborta_los_datos(self):
        payload, argumentos = self._encolar()
        with patch.object(
            importacion, '_guardar_progreso', side_effect=OSError('Disco no disponible'),
        ):
            argumentos[0](*argumentos[1:])
        self.assertEqual(self.client.get(payload['progreso_url']).json()['estado'], 'COMPLETADO')
        self.assertTrue(Ruta.objects.filter(nombre='RUTA-PROGRESO').exists())

    def test_resultado_final_no_depende_de_json_ausente_corrupto_o_contradictorio(self):
        payload, argumentos = self._encolar()
        argumentos[0](*argumentos[1:])
        ruta = importacion._ruta_progreso(payload['lote'])
        for contenido in (None, '{incompleto', '[]', '{"estado":"FALLIDO"}'):
            with self.subTest(contenido=contenido):
                if contenido is None:
                    ruta.unlink(missing_ok=True)
                else:
                    ruta.write_text(contenido, encoding='utf-8')
                respuesta = self.client.get(payload['progreso_url'])
                self.assertEqual(respuesta.status_code, 200)
                self.assertEqual(respuesta.json()['estado'], 'COMPLETADO')
                self.assertIn('no-store', respuesta['Cache-Control'])

    def test_error_real_revierte_y_se_informa_aunque_falle_el_json_final(self):
        payload, argumentos = self._encolar(b'Ruta\nRUTA-NUEVA\nRUTA-NUEVA\n')
        with patch.object(
            importacion, '_guardar_progreso', side_effect=OSError('Disco no disponible'),
        ):
            argumentos[0](*argumentos[1:])
        lote = LoteImportacion.objects.get(codigo=payload['lote'])
        self.assertEqual(lote.estado, 'FALLIDO')
        self.assertFalse(Ruta.objects.filter(nombre='RUTA-NUEVA').exists())
        self.assertEqual(self.client.get(payload['progreso_url']).json()['estado'], 'FALLIDO')
        self.assertFalse(Path(argumentos[-1]).exists())
        self.assertTrue(list((self.directorio / 'importaciones' / 'fallidos').iterdir()))

    def test_sin_confirmacion_de_bd_no_publica_fallo_definitivo_y_conserva_archivo(self):
        payload, argumentos = self._encolar(b'Ruta\nRUTA-NUEVA\nRUTA-NUEVA\n')
        with patch.object(
            importacion, '_cerrar_lote_fallido', side_effect=OperationalError('BD temporalmente inaccesible'),
        ):
            argumentos[0](*argumentos[1:])
        self.assertEqual(LoteImportacion.objects.get().estado, 'PENDIENTE')
        self.assertEqual(self.client.get(payload['progreso_url']).json()['estado'], 'PROCESANDO')
        self.assertTrue(Path(argumentos[-1]).exists())

    def test_sondeo_sin_bd_no_afirma_fallo_ni_filtra_datos(self):
        payload, _ = self._encolar()
        with patch(
            'mapas.views.importacion.LoteImportacion.objects.filter',
            side_effect=OperationalError('BD temporalmente inaccesible'),
        ):
            respuesta = self.client.get(payload['progreso_url'])
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(respuesta.json()['estado'], 'PROCESANDO')
        self.assertNotIn('archivo', respuesta.json())
        self.assertIn('no-store', respuesta['Cache-Control'])

    def test_propietario_se_verifica_en_bd_aunque_no_exista_json(self):
        payload, argumentos = self._encolar()
        argumentos[0](*argumentos[1:])
        importacion._ruta_progreso(payload['lote']).unlink()
        otro = get_user_model().objects.create_user(username='otro-sin-progreso')
        self.client.force_login(otro)
        self.assertEqual(self.client.get(payload['progreso_url']).status_code, 404)
        self.client.force_login(self.usuario)
        self.assertEqual(self.client.get(payload['progreso_url']).status_code, 200)

    def test_lote_pendiente_sin_json_no_se_presenta_como_inexistente(self):
        payload, _ = self._encolar()
        importacion._ruta_progreso(payload['lote']).unlink()
        respuesta = self.client.get(payload['progreso_url'])
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()['estado'], 'PENDIENTE')

    def test_lote_ya_completado_republica_resultado_sin_reprocesar(self):
        payload, argumentos = self._encolar()
        argumentos[0](*argumentos[1:])
        Path(argumentos[-1]).write_bytes(b'Ruta\nNO-REPROCESAR\n')
        nuevos_argumentos = list(argumentos[1:])
        with patch.object(importacion, '_procesar_ruta_otu') as procesador:
            nuevos_argumentos[4] = procesador
            argumentos[0](*nuevos_argumentos)
        procesador.assert_not_called()
        self.assertFalse(Ruta.objects.filter(nombre='NO-REPROCESAR').exists())
        self.assertEqual(self.client.get(payload['progreso_url']).json()['estado'], 'COMPLETADO')
