import importlib

from django.db import migrations, models


TRIGGERS_INTEGRIDAD_SQLITE = (
    'trg_fg_fibratramo_insert_integridad',
    'trg_fg_fibratramo_update_integridad',
    'trg_fg_coordenada_insert_integridad',
    'trg_fg_coordenada_update_integridad',
    'trg_fg_reserva_insert_integridad',
    'trg_fg_reserva_update_integridad',
    'trg_fg_tramo_insert_integridad',
    'trg_fg_tramo_update_integridad',
    'trg_fg_fibra_update_integridad',
)


def retirar_triggers_sqlite(apps, schema_editor):
    if schema_editor.connection.vendor != 'sqlite':
        return
    with schema_editor.connection.cursor() as cursor:
        for nombre in TRIGGERS_INTEGRIDAD_SQLITE:
            cursor.execute(f'DROP TRIGGER IF EXISTS {nombre}')


def restaurar_triggers_sqlite(apps, schema_editor):
    if schema_editor.connection.vendor != 'sqlite':
        return
    integridad = importlib.import_module(
        'mapas.migrations.0033_integridad_fuerte_y_normalizacion_canonica'
    )
    with schema_editor.connection.cursor() as cursor:
        for sentencia in integridad.SQLITE_TRIGGERS:
            cursor.execute(sentencia)


def normalizar_estados_sin_procedencia(apps, schema_editor):
    """No inventa procedencia: degrada históricos incoherentes a desconocido."""
    InventarioFibra = apps.get_model('mapas', 'InventarioFibra')
    InventarioFibra.objects.filter(
        origen_estado='NO_INFORMADO',
    ).exclude(estado='Desconocido').update(estado='Desconocido')


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0042_estado_condicion_y_auditoria_fibra'),
    ]

    operations = [
        migrations.RunPython(
            normalizar_estados_sin_procedencia,
            migrations.RunPython.noop,
        ),
        migrations.RunPython(
            retirar_triggers_sqlite,
            restaurar_triggers_sqlite,
        ),
        migrations.AddConstraint(
            model_name='inventariofibra',
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(origen_estado='NO_INFORMADO')
                    | models.Q(estado='Desconocido')
                ),
                name='ck_fibra_estado_origen_coherente',
            ),
        ),
        migrations.AlterField(
            model_name='auditoriafibra',
            name='accion',
            field=models.CharField(
                choices=[
                    ('CAMBIAR_ESTADO', 'Cambiar estado'),
                    ('INFERIR_ESTADO', 'Inferir estado desde tramos'),
                    ('RESTABLECER_ESTADO', 'Restablecer estado'),
                    ('ASIGNAR_RUTA', 'Asignar troncal'),
                ],
                db_index=True,
                max_length=30,
            ),
        ),
        migrations.RunPython(
            restaurar_triggers_sqlite,
            retirar_triggers_sqlite,
        ),
    ]
