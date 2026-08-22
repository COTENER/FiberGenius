from pathlib import Path
import shutil
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from .models import LoteImportacion, Ruta
from .views.importacion import _ejecutar_importacion_en_segundo_plano


class ProgresoImportacionTests(TransactionTestCase):
    reset_sequences = True

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
        self.assertEqual(lote.estado, 'PROCESANDO')
        submit.assert_called_once()

        estado = self.client.get(payload['progreso_url'])
        self.assertEqual(estado.status_code, 200)
        self.assertEqual(estado.json()['estado'], 'PENDIENTE')
        self.assertEqual(estado.json()['porcentaje'], 2)

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
