"""Production regressions; no real PostgreSQL or external services required."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from unittest.mock import Mock, patch
from types import SimpleNamespace
import zipfile

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.http import Http404, HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .services.backups import BackupError, _run, backup_database, restore_database, restore_backup
from .ui_access import OPERATIONS_VIEW_MODULES, operations_required


class ProductionConfigurationTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(settings.BASE_DIR) / '.tmp_pruebas' / f'production-{uuid.uuid4().hex}'
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.env_file = self.root / '.env'
        lines = [
            'SECRET_KEY=isolated-test-key-not-for-deployment',
            'ALLOWED_HOSTS=testserver', 'CSRF_TRUSTED_ORIGINS=https://testserver',
            'DB_PASSWORD=isolated-test-value', 'DEBUG=False',
            'FIBERGENIUS_VERSION=external-file-version',
            'FIBERGENIUS_OPERATIONS_UI_ENABLED=True',
            'FIBERGENIUS_MAX_UPLOAD_BYTES=12345678',
            'FIBERGENIUS_MAP_TILE_LIGHT=https://maps.example.test/{z}/{x}/{y}.png',
            'BACKUP_COMMAND_TIMEOUT_SECONDS=123',
        ]
        for key in ('DATA_ROOT', 'MEDIA_ROOT', 'STATIC_ROOT', 'BACKUP_ROOT', 'LOG_ROOT'):
            lines.append(f'{key}={(self.root / key.lower()).as_posix()}')
        self.env_file.write_text('\n'.join(lines), encoding='utf-8')
        self.environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith(('FIBERGENIUS_', 'DB_', 'MAP_TILE_', 'BACKUP_', 'VEEX_'))
            and key not in {'SECRET_KEY', 'DEBUG', 'ALLOWED_HOSTS', 'CSRF_TRUSTED_ORIGINS',
                            'DATA_ROOT', 'MEDIA_ROOT', 'STATIC_ROOT', 'LOG_ROOT'}
        }
        self.environment.update(
            DJANGO_SETTINGS_MODULE='fibergenius.settings_production',
            FIBERGENIUS_ENV_FILE=str(self.env_file),
        )

    def read_settings(self, extra_env=None):
        result = subprocess.run(
            [sys.executable, '-c',
             'import json; import fibergenius.settings_production as s; '
             'print(json.dumps([s.FIBERGENIUS_VERSION, s.FIBERGENIUS_MAX_UPLOAD_BYTES, '
             's.FIBERGENIUS_OPERATIONS_UI_ENABLED, s.MAP_TILE_URL_LIGHT, '
             's.BACKUP_COMMAND_TIMEOUT_SECONDS, s.DATA_UPLOAD_MAX_MEMORY_SIZE, s.DEBUG]))'],
            cwd=settings.BASE_DIR, env={**self.environment, **(extra_env or {})},
            capture_output=True, text=True, timeout=30, check=True,
        )
        return json.loads(result.stdout)

    def test_external_file_controls_shared_and_production_settings(self):
        self.assertEqual(self.read_settings(), [
            'external-file-version', 12345678, True,
            'https://maps.example.test/{z}/{x}/{y}.png', 123,
            12345678 + 1024 * 1024, False,
        ])

    def test_process_environment_has_explicit_precedence(self):
        self.assertEqual(self.read_settings({'FIBERGENIUS_VERSION': 'process-version'})[0],
                         'process-version')

    def test_missing_external_file_fails_closed(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.read_settings({'FIBERGENIUS_ENV_FILE': str(self.root / 'missing.env')})


class ProductionRoutingTests(TestCase):
    @override_settings(FIBERGENIUS_OPERATIONS_UI_ENABLED=False)
    def test_all_operation_endpoints_have_module_gate(self):
        from .urls import urlpatterns
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        checked = 0
        for pattern in urlpatterns:
            if pattern.callback.__module__ in OPERATIONS_VIEW_MODULES:
                with self.subTest(endpoint=pattern.name), self.assertRaises(Http404):
                    pattern.callback(request)
                checked += 1
        self.assertGreater(checked, 20)

    @override_settings(FIBERGENIUS_OPERATIONS_UI_ENABLED=True)
    def test_enabled_module_preserves_view_behavior(self):
        view = Mock(return_value=HttpResponse('ok'))
        request = RequestFactory().get('/')
        self.assertEqual(operations_required(view)(request, item=1).status_code, 200)
        view.assert_called_once_with(request, item=1)

    def test_admin_login_redirects_to_protected_login(self):
        self.assertRedirects(self.client.get(reverse('admin:login')),
                             '/login/?next=/admin/', fetch_redirect_response=False)

    @override_settings(FIBERGENIUS_OPERATIONS_UI_ENABLED=False,
                       FIBERGENIUS_MAP_TILE_LIGHT='https://maps.example.test/{z}/{x}/{y}.png',
                       FIBERGENIUS_MAP_TILE_DARK='https://maps.example.test/dark/{z}/{x}/{y}.png')
    def test_inventory_remains_available_and_receives_map_configuration(self):
        user = User.objects.create(username='release-reader', is_staff=True, is_superuser=True)
        self.client.force_login(user)
        response = self.client.get(reverse('planta_externa'))
        self.assertContains(response, 'data-map-tile-light="https://maps.example.test/{z}/{x}/{y}.png"')
        self.assertContains(response, 'data-map-tile-dark="https://maps.example.test/dark/{z}/{x}/{y}.png"')


class PostgreSQLRestoreCommandTests(SimpleTestCase):
    def test_postgresql_dump_does_not_prompt_for_password(self):
        database = {'default': {'ENGINE': 'django.db.backends.postgresql',
                               'NAME': 'fg_test_backup', 'USER': 'test_role'}}
        with override_settings(DATABASES=database), patch('mapas.services.backups._run') as run:
            self.assertEqual(backup_database('isolated.dump'), 'postgresql')
        self.assertIn('--no-password', run.call_args.args[0])

    def test_postgresql_restore_is_single_transaction_and_stops_on_error(self):
        database = {'default': {'ENGINE': 'django.db.backends.postgresql',
                               'NAME': 'fg_test_restore', 'USER': 'test_role'}}
        manifest = {'database_engine': 'postgresql',
                    'artifacts': [{'role': 'database', 'name': 'database.dump'}]}
        with override_settings(DATABASES=database), \
             patch('mapas.services.backups.connections.close_all'), \
             patch('mapas.services.backups._run') as run:
            restore_database(Path('isolated-backup'), manifest)
        command = run.call_args.args[0]
        for flag in ('--single-transaction', '--exit-on-error', '--no-password'):
            self.assertIn(flag, command)
        self.assertIn('fg_test_restore', command)

    @override_settings(BACKUP_COMMAND_TIMEOUT_SECONDS=2)
    def test_external_tool_timeout_is_reported(self):
        with patch('mapas.services.backups.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('pg_restore', 2)) as run:
            with self.assertRaisesRegex(BackupError, 'tiempo máximo'):
                _run(['pg_restore'])
        self.assertEqual(run.call_args.kwargs['timeout'], 2)

    def test_database_failure_does_not_start_file_restore(self):
        with patch('mapas.services.backups.load_and_verify_manifest', return_value={}), \
             patch('mapas.services.backups.restore_database', side_effect=BackupError('database failed')), \
             patch('mapas.services.backups.restore_files') as files:
            with self.assertRaises(BackupError):
                restore_backup('isolated-backup')
        files.assert_not_called()


class ReleaseToolTests(SimpleTestCase):
    def test_runner_only_trusts_local_iis_and_uses_upload_limit(self):
        from scripts.run_production import main
        original_path = sys.path[:]
        self.addCleanup(setattr, sys, 'path', original_path)
        app = Mock()
        with patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': 'fibergenius.settings_production'}), \
             patch.dict(sys.modules, {'fibergenius.wsgi': SimpleNamespace(application=app)}), \
             patch('os.chdir'), patch('waitress.serve') as serve:
            main()
        self.assertEqual(serve.call_args.args, (app,))
        config = serve.call_args.kwargs
        self.assertEqual(config['host'], '127.0.0.1')
        self.assertEqual(config['trusted_proxy'], '127.0.0.1')
        self.assertEqual(config['trusted_proxy_count'], 1)
        self.assertTrue(config['clear_untrusted_proxy_headers'])
        self.assertEqual(config['max_request_body_size'], settings.DATA_UPLOAD_MAX_MEMORY_SIZE)
        from waitress.adjustments import Adjustments
        Adjustments(**config)  # Validate against the installed Waitress API.

    def test_runner_rejects_development_profile(self):
        from scripts.run_production import main
        original_path = sys.path[:]
        self.addCleanup(setattr, sys, 'path', original_path)
        with patch.dict(os.environ, {'DJANGO_SETTINGS_MODULE': 'fibergenius.settings_pruebas'}), \
             patch('os.chdir'), self.assertRaisesRegex(RuntimeError, 'requires'):
            main()

    def test_offline_lock_uses_wheel_metadata_and_hash(self):
        import hashlib
        from scripts.prepare_offline import wheel_requirement
        root = Path(settings.BASE_DIR) / '.tmp_pruebas' / f'wheel-{uuid.uuid4().hex}'
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root)
        wheel = root / 'sample-1.0-py3-none-any.whl'
        with zipfile.ZipFile(wheel, 'w') as archive:
            archive.writestr('sample-1.0.dist-info/METADATA', 'Name: sample\nVersion: 1.0\n')
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        self.assertEqual(wheel_requirement(wheel), f'sample==1.0 --hash=sha256:{digest}')
