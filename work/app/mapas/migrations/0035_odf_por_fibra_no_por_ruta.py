from django.db import migrations


def validar_rutas_sin_odf_directo(apps, schema_editor):
    Ruta = apps.get_model('mapas', 'Ruta')
    db_alias = schema_editor.connection.alias
    ejemplos = list(
        Ruta.objects.using(db_alias)
        .exclude(odf_origen_id=None, odf_destino_id=None)
        .values_list('pk', 'nombre')[:20]
    )
    if ejemplos:
        raise RuntimeError(
            'No se eliminaron los ODF directos de Ruta porque todavía '
            f'existen relaciones que deben revisarse: {ejemplos}'
        )


class Migration(migrations.Migration):
    dependencies = [
        ('mapas', '0034_procedencia_extremos_odf'),
    ]

    operations = [
        migrations.RunPython(
            validar_rutas_sin_odf_directo,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name='ruta',
            name='ck_ruta_odf_a_procedencia',
        ),
        migrations.RemoveConstraint(
            model_name='ruta',
            name='ck_ruta_odf_b_procedencia',
        ),
        migrations.RemoveConstraint(
            model_name='ruta',
            name='ck_ruta_odf_a_proc_requiere_odf',
        ),
        migrations.RemoveConstraint(
            model_name='ruta',
            name='ck_ruta_odf_b_proc_requiere_odf',
        ),
        migrations.RemoveField(
            model_name='ruta',
            name='odf_origen_procedencia',
        ),
        migrations.RemoveField(
            model_name='ruta',
            name='odf_destino_procedencia',
        ),
        migrations.RemoveField(
            model_name='ruta',
            name='odf_origen',
        ),
        migrations.RemoveField(
            model_name='ruta',
            name='odf_destino',
        ),
    ]
