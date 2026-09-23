from importlib import import_module

from django.db import migrations, models

import mapas.models


def completar_codigos(apps, schema_editor):
    InventarioFibra = apps.get_model('mapas', 'InventarioFibra')
    for fibra in InventarioFibra.objects.filter(codigo_fibra__isnull=True).iterator():
        fibra.codigo_fibra = f'FGF-LEGACY-{fibra.pk:010d}'
        fibra.save(update_fields=['codigo_fibra'])


def vaciar_codigos(apps, schema_editor):
    InventarioFibra = apps.get_model('mapas', 'InventarioFibra')
    InventarioFibra.objects.update(codigo_fibra=None)


def eliminar_triggers_integridad(apps, schema_editor):
    modulo = import_module(
        'mapas.migrations.0033_integridad_fuerte_y_normalizacion_canonica'
    )
    modulo.eliminar_triggers(apps, schema_editor)


def recrear_triggers_integridad(apps, schema_editor):
    modulo = import_module(
        'mapas.migrations.0033_integridad_fuerte_y_normalizacion_canonica'
    )
    modulo.crear_triggers(apps, schema_editor)


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0043_coherencia_estado_fibra'),
    ]

    operations = [
        migrations.AddField(
            model_name='inventariofibra',
            name='codigo_fibra',
            field=models.CharField(
                blank=True,
                max_length=64,
                null=True,
                verbose_name='Código estable de fibra',
            ),
        ),
        migrations.RunPython(completar_codigos, vaciar_codigos),
        migrations.RunPython(
            eliminar_triggers_integridad,
            recrear_triggers_integridad,
        ),
        migrations.AlterField(
            model_name='inventariofibra',
            name='codigo_fibra',
            field=models.CharField(
                default=mapas.models.generar_codigo_fibra,
                help_text=(
                    'Identificador global e inmutable; no corresponde al '
                    'número físico F1/F2 de un tramo.'
                ),
                max_length=64,
                unique=True,
                verbose_name='Código estable de fibra',
            ),
        ),
        migrations.RunPython(
            recrear_triggers_integridad,
            eliminar_triggers_integridad,
        ),
    ]
