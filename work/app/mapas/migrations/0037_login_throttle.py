from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0036_sincronizar_nombre_odf_puertos'),
    ]

    operations = [
        migrations.CreateModel(
            name='LoginThrottle',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True,
                    primary_key=True,
                    serialize=False,
                    verbose_name='ID',
                )),
                ('key_hash', models.CharField(max_length=64, unique=True)),
                ('attempts', models.PositiveIntegerField(default=0)),
                ('window_started', models.DateTimeField()),
                ('locked_until', models.DateTimeField(
                    blank=True,
                    db_index=True,
                    null=True,
                )),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Bloqueo temporal de acceso',
                'verbose_name_plural': 'Bloqueos temporales de acceso',
                'db_table': 'sys_login_throttle',
            },
        ),
    ]
