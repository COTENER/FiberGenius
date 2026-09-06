import django.db.models.deletion
import importlib
from django.conf import settings
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


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0041_fibras_provisionales_sin_troncal'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            retirar_triggers_sqlite,
            restaurar_triggers_sqlite,
        ),
        migrations.AlterField(
            model_name='inventariofibra',
            name='estado',
            field=models.CharField(
                choices=[
                    ('Libre', 'Libre'),
                    ('Ocupado', 'Ocupado'),
                    ('Reservado', 'Reservado'),
                    ('Desconocido', 'Desconocido'),
                ],
                default='Desconocido',
                max_length=50,
                verbose_name='Estado',
            ),
        ),
        migrations.AddField(
            model_name='inventariofibra',
            name='origen_estado',
            field=models.CharField(
                choices=[
                    ('INFORMADO', 'Informado'),
                    ('INFERIDO_TRAMOS', 'Inferido desde tramos'),
                    ('NO_INFORMADO', 'No informado'),
                ],
                db_index=True,
                default='NO_INFORMADO',
                max_length=20,
                verbose_name='Origen del estado',
            ),
        ),
        migrations.AddField(
            model_name='inventariofibra',
            name='condicion_fisica',
            field=models.CharField(
                choices=[
                    ('OPERATIVA', 'Operativa'),
                    ('CON_FALLA', 'Con falla'),
                    ('SIN_VERIFICAR', 'Sin verificar'),
                ],
                db_index=True,
                default='SIN_VERIFICAR',
                max_length=20,
                verbose_name='Condición física',
            ),
        ),
        migrations.AddField(
            model_name='inventariofibra',
            name='observaciones',
            field=models.TextField(blank=True, default='', verbose_name='Observaciones'),
        ),
        migrations.AddConstraint(
            model_name='inventariofibra',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    origen_estado__in=(
                        'INFORMADO',
                        'INFERIDO_TRAMOS',
                        'NO_INFORMADO',
                    )
                ),
                name='ck_fibra_origen_estado_valido',
            ),
        ),
        migrations.AddConstraint(
            model_name='inventariofibra',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    condicion_fisica__in=(
                        'OPERATIVA',
                        'CON_FALLA',
                        'SIN_VERIFICAR',
                    )
                ),
                name='ck_fibra_condicion_fisica_valida',
            ),
        ),
        migrations.CreateModel(
            name='AuditoriaFibra',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('accion', models.CharField(choices=[('CAMBIAR_ESTADO', 'Cambiar estado'), ('RESTABLECER_ESTADO', 'Restablecer estado'), ('ASIGNAR_RUTA', 'Asignar troncal')], db_index=True, max_length=30)),
                ('origen', models.CharField(choices=[('GUI', 'Interfaz web'), ('EXCEL', 'Importación Excel/CSV'), ('API', 'API'), ('ADMIN', 'Administrador'), ('SISTEMA', 'Proceso interno')], db_index=True, default='SISTEMA', max_length=15)),
                ('referencia_fibra', models.CharField(blank=True, max_length=300)),
                ('valor_anterior', models.CharField(blank=True, max_length=300)),
                ('valor_nuevo', models.CharField(blank=True, max_length=300)),
                ('metadatos', models.JSONField(blank=True, default=dict)),
                ('creado_en', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('fibra', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='auditorias', to='mapas.inventariofibra')),
                ('lote_importacion', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='auditorias_fibras', to='mapas.loteimportacion')),
                ('usuario', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='auditorias_fibras', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'aud_fibras',
                'ordering': ['-creado_en', '-pk'],
            },
        ),
        migrations.AddIndex(
            model_name='auditoriafibra',
            index=models.Index(fields=['fibra', '-creado_en'], name='idx_aud_fibra_evento'),
        ),
        migrations.AddIndex(
            model_name='auditoriafibra',
            index=models.Index(fields=['accion', '-creado_en'], name='idx_aud_fibra_accion'),
        ),
        migrations.RunPython(
            restaurar_triggers_sqlite,
            retirar_triggers_sqlite,
        ),
    ]
