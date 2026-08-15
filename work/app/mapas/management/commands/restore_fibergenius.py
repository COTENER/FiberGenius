from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from mapas.services.backups import (
    BackupError,
    load_and_verify_manifest,
    restore_backup,
)


CONFIRMATION = 'RESTORE-FIBERGENIUS'


class Command(BaseCommand):
    help = 'Valida o restaura un backup de Fiber Genius.'

    def add_arguments(self, parser):
        parser.add_argument('backup_dir')
        parser.add_argument('--verify-only', action='store_true')
        parser.add_argument('--database-only', action='store_true')
        parser.add_argument('--execute', action='store_true')
        parser.add_argument('--confirm', default='')

    def handle(self, *args, **options):
        backup_dir = Path(options['backup_dir'])
        try:
            manifest = load_and_verify_manifest(backup_dir)
            if options['verify_only']:
                self.stdout.write(self.style.SUCCESS(
                    f'Backup válido: {manifest["created_at"]}'
                ))
                return
            if not options['execute'] or options['confirm'] != CONFIRMATION:
                raise CommandError(
                    'La restauración no se ejecutó. Use --execute '
                    f'--confirm {CONFIRMATION} durante una ventana de '
                    'mantenimiento y con Fiber Genius detenido.'
                )
            restore_backup(
                backup_dir,
                include_files=not options['database_only'],
            )
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS('Restauración completada.'))
