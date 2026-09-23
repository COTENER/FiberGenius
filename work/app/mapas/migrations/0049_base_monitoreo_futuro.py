"""Base pasiva de monitoreo: solo tablas nuevas, sin backfill ni activación."""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('mapas', '0048_reglas_aprobadas_inventario'),
    ]

    operations = [
        migrations.CreateModel(
            name='FuenteMonitoreo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('nombre', models.CharField(max_length=150)),
                ('proveedor', models.CharField(blank=True, default='', max_length=100)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Fuente de monitoreo',
                'verbose_name_plural': 'Fuentes de monitoreo',
                'db_table': 'mon_fuentes',
                'default_permissions': (),
                'constraints': [
                    models.CheckConstraint(condition=~models.Q(nombre=''), name='ck_mon_fuente_nombre'),
                ],
            },
        ),
        migrations.CreateModel(
            name='CanalMonitoreo',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('identificador_externo', models.CharField(help_text='ID completo y único dentro de la fuente; no solo el número de puerto.', max_length=255)),
                ('extremo_origen', models.CharField(blank=True, choices=[('A', 'Extremo A'), ('B', 'Extremo B')], default='', help_text='Extremo desde el que se medirá. Vacío significa pendiente, no extremo A.', max_length=1)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('retirado_en', models.DateTimeField(blank=True, editable=False, null=True)),
                ('fibra', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='canales_monitoreo', to='mapas.inventariofibra')),
                ('fuente', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='canales', to='mapas.fuentemonitoreo')),
            ],
            options={
                'verbose_name': 'Puerto de monitoreo',
                'verbose_name_plural': 'Puertos de monitoreo',
                'db_table': 'mon_canales',
                'default_permissions': (),
                'constraints': [
                    models.UniqueConstraint(fields=('fuente', 'identificador_externo'), condition=models.Q(retirado_en__isnull=True), name='uq_mon_canal_vigente'),
                    models.CheckConstraint(condition=~models.Q(identificador_externo=''), name='ck_mon_canal_identificador'),
                    models.CheckConstraint(condition=models.Q(extremo_origen__in=('', 'A', 'B')), name='ck_mon_canal_extremo'),
                    models.CheckConstraint(condition=models.Q(retirado_en__isnull=True) | models.Q(retirado_en__gte=models.F('creado_en')), name='ck_mon_canal_fechas'),
                ],
            },
        ),
    ]
