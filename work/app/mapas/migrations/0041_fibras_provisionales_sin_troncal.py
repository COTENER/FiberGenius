import django.db.models.deletion
import importlib
from django.db import migrations, models
from django.db.models.functions import Lower


TRIGGERS_INTEGRIDAD_SQLITE = (
    "trg_fg_fibratramo_insert_integridad",
    "trg_fg_fibratramo_update_integridad",
    "trg_fg_coordenada_insert_integridad",
    "trg_fg_coordenada_update_integridad",
    "trg_fg_reserva_insert_integridad",
    "trg_fg_reserva_update_integridad",
    "trg_fg_tramo_insert_integridad",
    "trg_fg_tramo_update_integridad",
    "trg_fg_fibra_update_integridad",
)


def retirar_triggers_fibra_sqlite(apps, schema_editor):
    if schema_editor.connection.vendor != "sqlite":
        return
    with schema_editor.connection.cursor() as cursor:
        for nombre in TRIGGERS_INTEGRIDAD_SQLITE:
            cursor.execute(f"DROP TRIGGER IF EXISTS {nombre}")


def restaurar_triggers_fibra_sqlite(apps, schema_editor):
    if schema_editor.connection.vendor != "sqlite":
        return
    integridad = importlib.import_module(
        "mapas.migrations.0033_integridad_fuerte_y_normalizacion_canonica"
    )
    with schema_editor.connection.cursor() as cursor:
        for sentencia in integridad.SQLITE_TRIGGERS:
            cursor.execute(sentencia)


class Migration(migrations.Migration):

    dependencies = [
        ("mapas", "0040_remove_detallepuertoodf_fibra"),
    ]

    operations = [
        migrations.RunPython(
            retirar_triggers_fibra_sqlite,
            restaurar_triggers_fibra_sqlite,
        ),
        migrations.RemoveConstraint(
            model_name="inventariofibra",
            name="uq_fibra_ruta_numero_ci",
        ),
        migrations.AlterField(
            model_name="inventariofibra",
            name="ruta",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Puede quedar pendiente mientras solo se conoce la "
                    "terminación física ODF/puerto del hilo."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="fibras_inventario",
                to="mapas.ruta",
                verbose_name="Ruta Asociada",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventariofibra",
            constraint=models.UniqueConstraint(
                models.F("ruta"),
                Lower("fibra_numero"),
                condition=models.Q(ruta__isnull=False),
                name="uq_fibra_ruta_numero_ci",
            ),
        ),
        migrations.RunPython(
            restaurar_triggers_fibra_sqlite,
            retirar_triggers_fibra_sqlite,
        ),
    ]
