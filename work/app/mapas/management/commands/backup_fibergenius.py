from django.core.management.base import BaseCommand, CommandError

from mapas.services.backups import BackupError, create_backup


class Command(BaseCommand):
    help = 'Genera un backup verificable de Fiber Genius.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--kind',
            choices=('daily', 'monthly', 'manual'),
            default='manual',
        )
        parser.add_argument('--root')
        parser.add_argument('--retention-days', type=int)

    def handle(self, *args, **options):
        try:
            backup_dir = create_backup(
                kind=options['kind'],
                root=options.get('root'),
                retention_days=options.get('retention_days'),
            )
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(str(backup_dir)))
