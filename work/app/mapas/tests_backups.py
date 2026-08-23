import json
import shutil
import sqlite3
import uuid
import zipfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from mapas.services.backups import (
    BackupError,
    create_backup,
    load_and_verify_manifest,
    restore_backup,
)


class BackupRestoreTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(settings.BASE_DIR) / f'backup-test-{uuid.uuid4().hex}'
        self.root.mkdir()
        self.database = self.root / 'active.sqlite3'
        self.media = self.root / 'media'
        self.data = self.root / 'data'
        self.backups = self.root / 'backups'
        self.media.mkdir()
        self.data.mkdir()
        (self.media / 'trace.sor').write_bytes(b'SOR-original')
        (self.data / 'inventory.csv').write_text(
            'site,odf\nSITE-A,ODF-A\n',
            encoding='utf-8',
        )
        connection = sqlite3.connect(self.database)
        connection.execute('CREATE TABLE sample (value TEXT)')
        connection.execute('INSERT INTO sample VALUES (?)', ('original',))
        connection.commit()
        connection.close()
        self.overrides = override_settings(
            BACKUP_ROOT=self.backups,
            BACKUP_RETENTION_DAYS=30,
            MEDIA_ROOT=self.media,
            DATA_ROOT=self.data,
            DATABASES={
                'default': {
                    'ENGINE': 'django.db.backends.sqlite3',
                    'NAME': self.database,
                },
            },
        )
        self.overrides.enable()

    def tearDown(self):
        self.overrides.disable()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_backup_genera_base_archivos_y_manifiesto_verificable(self):
        backup_dir = create_backup(kind='manual')

        manifest = load_and_verify_manifest(backup_dir)
        self.assertEqual(manifest['application'], 'Fiber Genius')
        self.assertEqual(manifest['database_engine'], 'sqlite3')
        self.assertFalse(manifest['configuration']['included'])
        self.assertTrue((backup_dir / 'database.sqlite3').is_file())
        with zipfile.ZipFile(backup_dir / 'media.zip') as archive:
            self.assertEqual(archive.namelist(), ['trace.sor'])
        with zipfile.ZipFile(backup_dir / 'data.zip') as archive:
            self.assertEqual(archive.namelist(), ['inventory.csv'])

    def test_verificacion_detecta_archivo_modificado(self):
        backup_dir = create_backup(kind='manual')
        (backup_dir / 'database.sqlite3').write_bytes(b'alterado')

        with self.assertRaisesRegex(BackupError, 'Tamaño inválido|Hash'):
            load_and_verify_manifest(backup_dir)

    def test_restore_recupera_base_y_archivos(self):
        backup_dir = create_backup(kind='manual')
        connection = sqlite3.connect(self.database)
        connection.execute('UPDATE sample SET value = ?', ('modificado',))
        connection.commit()
        connection.close()
        (self.media / 'trace.sor').write_bytes(b'SOR-modificado')

        restore_backup(backup_dir)

        connection = sqlite3.connect(self.database)
        value = connection.execute('SELECT value FROM sample').fetchone()[0]
        connection.close()
        self.assertEqual(value, 'original')
        self.assertEqual(
            (self.media / 'trace.sor').read_bytes(),
            b'SOR-original',
        )

    def test_rechaza_backup_dentro_de_data(self):
        with self.assertRaisesRegex(BackupError, 'BACKUP_ROOT'):
            create_backup(root=self.data / 'backups')

    def test_manifest_es_json_legible(self):
        backup_dir = create_backup(kind='manual')
        payload = json.loads(
            (backup_dir / 'manifest.json').read_text(encoding='utf-8')
        )
        self.assertEqual(payload['schema_version'], 1)
