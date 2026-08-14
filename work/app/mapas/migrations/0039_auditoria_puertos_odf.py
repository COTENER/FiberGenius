from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('mapas', '0038_terminacion_fuente_oficial'),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditoriaPuertoODF',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('accion', models.CharField(choices=[('CONECTAR', 'Conectar'), ('MOVER', 'Mover terminación'), ('DESCONECTAR', 'Desconectar'), ('RESERVAR', 'Reservar'), ('CANCELAR_RESERVA', 'Cancelar reserva')], db_index=True, max_length=30)),
                ('origen', models.CharField(choices=[('GUI', 'Interfaz web'), ('EXCEL', 'Actualización masiva por Excel'), ('SISTEMA', 'Proceso interno')], db_index=True, default='SISTEMA', max_length=15)),
                ('extremo', models.CharField(blank=True, max_length=1)),
                ('estado_anterior', models.CharField(blank=True, max_length=50)),
                ('estado_nuevo', models.CharField(blank=True, max_length=50)),
                ('referencia_puerto_anterior', models.CharField(blank=True, max_length=500)),
                ('referencia_puerto_nuevo', models.CharField(blank=True, max_length=500)),
                ('referencia_fibra', models.CharField(blank=True, max_length=300)),
                ('sincronizo_fibra', models.BooleanField(default=False)),
                ('metadatos', models.JSONField(blank=True, default=dict)),
                ('creado_en', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('fibra', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='auditorias_puertos_odf', to='mapas.inventariofibra')),
                ('lote_importacion', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='auditorias_puertos_odf', to='mapas.loteimportacion')),
                ('puerto_anterior', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='auditorias_como_puerto_anterior', to='mapas.detallepuertoodf')),
                ('puerto_nuevo', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='auditorias_como_puerto_nuevo', to='mapas.detallepuertoodf')),
                ('usuario', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='auditorias_puertos_odf', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'aud_puertos_odf',
                'ordering': ['-creado_en', '-pk'],
                'indexes': [
                    models.Index(fields=['puerto_anterior', '-creado_en'], name='idx_aud_puerto_ant_fecha'),
                    models.Index(fields=['puerto_nuevo', '-creado_en'], name='idx_aud_puerto_nvo_fecha'),
                    models.Index(fields=['fibra', '-creado_en'], name='idx_aud_fibra_fecha'),
                ],
            },
        ),
    ]
