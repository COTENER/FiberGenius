from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('mapas', '0024_consolidar_planta_externa_en_reservas'),
    ]

    operations = [
        migrations.AddField(
            model_name='loteimportacion',
            name='fecha_origen',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='loteimportacion',
            name='metadatos_origen',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='loteimportacion',
            name='origen_registro',
            field=models.CharField(
                choices=[
                    ('GUI', 'Importacion ejecutada desde la GUI'),
                    ('BASELINE_RECONSTRUIDO', 'Baseline reconstruido para homologacion'),
                ],
                db_index=True,
                default='GUI',
                max_length=30,
            ),
        ),
        migrations.AddIndex(
            model_name='alarmaveex',
            index=models.Index(
                fields=['route_name', 'port', 'status'],
                name='ix_alarma_ruta_puerto_estado',
            ),
        ),
    ]
