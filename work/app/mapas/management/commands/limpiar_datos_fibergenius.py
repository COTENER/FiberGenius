from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connection, transaction


CONFIRMACION = "BORRAR DATOS FIBER GENIUS"


class Command(BaseCommand):
    help = (
        "Elimina todos los datos funcionales de Fiber Genius y conserva "
        "usuarios, grupos, permisos, sesiones y el esquema de la base de datos."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirmar",
            default="",
            help=f'Frase obligatoria: "{CONFIRMACION}".',
        )

    def handle(self, *args, **options):
        if options["confirmar"] != CONFIRMACION:
            raise CommandError(
                f'Operación cancelada. Use --confirmar "{CONFIRMACION}".'
            )

        tablas_existentes = set(connection.introspection.table_names())
        tablas_mapas = {
            modelo._meta.db_table
            for modelo in apps.get_app_config("mapas").get_models(
                include_auto_created=True,
            )
            if modelo._meta.managed
        }
        tablas = sorted(tablas_mapas & tablas_existentes)
        if not tablas:
            self.stdout.write(self.style.WARNING(
                "No existen tablas funcionales de Fiber Genius para limpiar."
            ))
            return

        sentencias = connection.ops.sql_flush(
            no_style(),
            tablas,
            reset_sequences=True,
            allow_cascade=True,
        )
        with transaction.atomic(), connection.constraint_checks_disabled():
            with connection.cursor() as cursor:
                for sentencia in sentencias:
                    cursor.execute(sentencia)

        self.stdout.write(self.style.SUCCESS(
            f"Datos funcionales eliminados de {len(tablas)} tablas. "
            "Usuarios, grupos, permisos y sesiones fueron conservados."
        ))
