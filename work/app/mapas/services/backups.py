import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import uuid
import zipfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connections
from django.utils.timezone import localtime, now


MANIFEST_NAME = 'manifest.json'
SUPPORTED_KINDS = {'daily', 'monthly', 'manual'}
logger = logging.getLogger('fibergenius.backup')


class BackupError(RuntimeError):
    pass


def sha256_file(path, chunk_size=1024 * 1024):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _database_engine():
    return settings.DATABASES['default']['ENGINE'].rsplit('.', 1)[-1]


def _run(command, env=None):
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    except OSError as exc:
        raise BackupError(f'No se pudo ejecutar {command[0]}: {exc}') from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip()
        raise BackupError(
            f'{Path(command[0]).name} terminó con código '
            f'{result.returncode}: {detail}'
        )


def _postgres_environment(database):
    environment = os.environ.copy()
    password = str(database.get('PASSWORD') or '')
    if password:
        environment['PGPASSWORD'] = password
    return environment


def backup_database(destination):
    destination = Path(destination)
    database = settings.DATABASES['default']
    engine = _database_engine()
    if engine == 'sqlite3':
        source = Path(database['NAME'])
        if not source.exists():
            raise BackupError(f'No existe la base SQLite: {source}')
        source_connection = sqlite3.connect(str(source))
        target_connection = sqlite3.connect(str(destination))
        try:
            source_connection.backup(target_connection)
            target_connection.create_function(
                'REGEXP',
                2,
                lambda pattern, value: bool(
                    value is not None and re.search(pattern, str(value))
                ),
            )
            integrity = target_connection.execute(
                'PRAGMA integrity_check'
            ).fetchone()[0]
            if integrity != 'ok':
                raise BackupError(
                    f'La copia SQLite no superó integrity_check: {integrity}'
                )
        except sqlite3.Error as exc:
            raise BackupError(f'Falló el backup SQLite: {exc}') from exc
        finally:
            target_connection.close()
            source_connection.close()
        return 'sqlite3'
    if engine not in {'postgresql', 'postgresql_psycopg2'}:
        raise BackupError(f'Motor de base no soportado: {engine}')

    command = [
        str(settings.BACKUP_PG_DUMP_PATH),
        '--format=custom',
        '--no-owner',
        '--no-privileges',
        '--file',
        str(destination),
    ]
    if database.get('HOST'):
        command.extend(['--host', str(database['HOST'])])
    if database.get('PORT'):
        command.extend(['--port', str(database['PORT'])])
    if database.get('USER'):
        command.extend(['--username', str(database['USER'])])
    command.append(str(database['NAME']))
    _run(command, env=_postgres_environment(database))
    return 'postgresql'


def _safe_archive_name(relative_path):
    return str(relative_path).replace('\\', '/')


def archive_directory(source, destination):
    source = Path(source)
    destination = Path(destination)
    with zipfile.ZipFile(
        destination,
        'w',
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        if not source.exists():
            return False
        for path in sorted(source.rglob('*')):
            if path.is_symlink() or not path.is_file():
                continue
            archive.write(path, _safe_archive_name(path.relative_to(source)))
    return True


@contextmanager
def backup_lock(root):
    lock_path = Path(root) / '.backup.lock'
    try:
        descriptor = os.open(
            str(lock_path),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError as exc:
        raise BackupError('Ya existe otro backup en ejecución.') from exc
    try:
        os.write(descriptor, str(os.getpid()).encode('ascii'))
        os.close(descriptor)
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _artifact(path, role):
    path = Path(path)
    return {
        'name': path.name,
        'role': role,
        'size': path.stat().st_size,
        'sha256': sha256_file(path),
    }


def apply_retention(root, retention_days):
    cutoff = now() - timedelta(days=max(1, int(retention_days)))
    removed = []
    for kind in ('daily', 'monthly'):
        folder = Path(root) / kind
        if not folder.exists():
            continue
        for candidate in folder.iterdir():
            if not candidate.is_dir() or candidate.name.startswith('.'):
                continue
            modified = candidate.stat().st_mtime
            if modified < cutoff.timestamp():
                shutil.rmtree(candidate)
                removed.append(str(candidate))
    return removed


def create_backup(kind='manual', root=None, retention_days=None):
    if kind not in SUPPORTED_KINDS:
        raise BackupError(f'Tipo de backup inválido: {kind}')
    root = Path(root or settings.BACKUP_ROOT).resolve()
    for setting_name in ('DATA_ROOT', 'MEDIA_ROOT'):
        source = getattr(settings, setting_name, None)
        if not source:
            continue
        source = Path(source).resolve()
        if root == source or source in root.parents:
            raise BackupError(
                f'BACKUP_ROOT no puede estar dentro de {setting_name}.'
            )
    root.mkdir(parents=True, exist_ok=True)
    retention_days = (
        settings.BACKUP_RETENTION_DAYS
        if retention_days is None
        else retention_days
    )
    timestamp = localtime().strftime('%Y-%m-%d_%H%M%S')
    final_dir = root / kind / f'FiberGenius_{timestamp}'
    staging = root / kind / f'.partial-{uuid.uuid4().hex}'
    final_dir.parent.mkdir(parents=True, exist_ok=True)

    with backup_lock(root):
        staging.mkdir(parents=True)
        try:
            db_extension = 'sqlite3' if _database_engine() == 'sqlite3' else 'dump'
            database_path = staging / f'database.{db_extension}'
            database_engine = backup_database(database_path)
            artifacts = [_artifact(database_path, 'database')]

            directories = (
                ('media.zip', getattr(settings, 'MEDIA_ROOT', None), 'media'),
                ('data.zip', getattr(settings, 'DATA_ROOT', None), 'data'),
            )
            for filename, source, role in directories:
                if source:
                    archive_path = staging / filename
                    archive_directory(source, archive_path)
                    artifacts.append(_artifact(archive_path, role))

            manifest = {
                'schema_version': 1,
                'application': 'Fiber Genius',
                'created_at': now().isoformat(),
                'kind': kind,
                'database_engine': database_engine,
                'artifacts': artifacts,
                'configuration': {
                    'included': False,
                    'reason': (
                        'El archivo .env contiene secretos y se respalda '
                        'mediante el procedimiento seguro de infraestructura.'
                    ),
                },
            }
            (staging / MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            if final_dir.exists():
                final_dir = final_dir.with_name(
                    f'{final_dir.name}_{uuid.uuid4().hex[:6]}'
                )
            staging.replace(final_dir)
            removed = apply_retention(root, retention_days)
            logger.info(
                'backup_success kind=%s path=%s artifacts=%s retention_removed=%s',
                kind,
                final_dir,
                len(artifacts),
                len(removed),
            )
            return final_dir
        except Exception:
            logger.exception('backup_failed kind=%s root=%s', kind, root)
            shutil.rmtree(staging, ignore_errors=True)
            raise


def load_and_verify_manifest(backup_dir):
    backup_dir = Path(backup_dir).resolve()
    manifest_path = backup_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BackupError(f'No existe {MANIFEST_NAME} en {backup_dir}')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f'Manifiesto inválido: {exc}') from exc
    if manifest.get('application') != 'Fiber Genius':
        raise BackupError('El paquete no corresponde a Fiber Genius.')
    for artifact in manifest.get('artifacts', []):
        name = artifact.get('name', '')
        path = (backup_dir / name).resolve()
        if path.parent != backup_dir or not path.is_file():
            raise BackupError(f'Artefacto ausente o inseguro: {name}')
        if path.stat().st_size != artifact.get('size'):
            raise BackupError(f'Tamaño inválido para {name}.')
        if sha256_file(path) != artifact.get('sha256'):
            raise BackupError(f'Hash SHA-256 inválido para {name}.')
    return manifest


def _artifact_by_role(backup_dir, manifest, role):
    item = next(
        (item for item in manifest['artifacts'] if item.get('role') == role),
        None,
    )
    return Path(backup_dir) / item['name'] if item else None


def restore_database(backup_dir, manifest):
    database = settings.DATABASES['default']
    backup_path = _artifact_by_role(backup_dir, manifest, 'database')
    if backup_path is None:
        raise BackupError('El backup no contiene base de datos.')
    engine = _database_engine()
    expected_engine = (
        'sqlite3' if engine == 'sqlite3' else 'postgresql'
        if engine in {'postgresql', 'postgresql_psycopg2'}
        else engine
    )
    if manifest.get('database_engine') != expected_engine:
        raise BackupError(
            'El motor del backup no coincide con la instalación activa: '
            f'{manifest.get("database_engine")} != {expected_engine}.'
        )
    if engine == 'sqlite3':
        target = Path(database['NAME'])
        suffix = (
            f'{localtime().strftime("%Y%m%d-%H%M%S")}-'
            f'{uuid.uuid4().hex[:6]}'
        )
        safety = target.with_name(
            f'{target.name}.before-restore-{suffix}'
        )
        incoming = target.with_name(f'.restore-{uuid.uuid4().hex}.sqlite3')
        connections.close_all()
        shutil.copy2(backup_path, incoming)
        moved_original = False
        try:
            if target.exists():
                target.replace(safety)
                moved_original = True
            incoming.replace(target)
        except Exception:
            if moved_original and safety.exists() and not target.exists():
                safety.replace(target)
            raise
        finally:
            incoming.unlink(missing_ok=True)
        return
    if engine not in {'postgresql', 'postgresql_psycopg2'}:
        raise BackupError(f'Motor de base no soportado: {engine}')
    command = [
        str(settings.BACKUP_PG_RESTORE_PATH),
        '--clean',
        '--if-exists',
        '--no-owner',
        '--no-privileges',
    ]
    if database.get('HOST'):
        command.extend(['--host', str(database['HOST'])])
    if database.get('PORT'):
        command.extend(['--port', str(database['PORT'])])
    if database.get('USER'):
        command.extend(['--username', str(database['USER'])])
    command.extend(['--dbname', str(database['NAME']), str(backup_path)])
    connections.close_all()
    _run(command, env=_postgres_environment(database))


def _safe_extract_zip(archive_path, destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            if destination not in target.parents and target != destination:
                raise BackupError(f'Ruta insegura en ZIP: {info.filename}')
        archive.extractall(destination)


def restore_files(backup_dir, manifest):
    for role, setting_name in (('media', 'MEDIA_ROOT'), ('data', 'DATA_ROOT')):
        archive_path = _artifact_by_role(backup_dir, manifest, role)
        destination = getattr(settings, setting_name, None)
        if archive_path is None or not destination:
            continue
        destination = Path(destination)
        suffix = (
            f'{localtime().strftime("%Y%m%d-%H%M%S")}-'
            f'{uuid.uuid4().hex[:6]}'
        )
        safety = destination.with_name(
            f'{destination.name}.before-restore-{suffix}'
        )
        incoming = destination.with_name(f'.restore-{uuid.uuid4().hex}')
        _safe_extract_zip(archive_path, incoming)
        moved_original = False
        try:
            if destination.exists():
                destination.replace(safety)
                moved_original = True
            incoming.replace(destination)
        except Exception:
            if (
                moved_original
                and safety.exists()
                and not destination.exists()
            ):
                safety.replace(destination)
            raise
        finally:
            shutil.rmtree(incoming, ignore_errors=True)


def restore_backup(backup_dir, include_files=True):
    backup_dir = Path(backup_dir).resolve()
    manifest = load_and_verify_manifest(backup_dir)
    restore_database(backup_dir, manifest)
    if include_files:
        restore_files(backup_dir, manifest)
    logger.warning(
        'restore_completed backup=%s include_files=%s',
        backup_dir,
        include_files,
    )
    return manifest
