from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('mapas', '0033_integridad_fuerte_y_normalizacion_canonica'),
    ]

    operations = [
        migrations.AddField(
            model_name='ruta',
            name='odf_destino_procedencia',
            field=models.CharField(
                blank=True,
                choices=[
                    ('', 'Sin procedencia'),
                    ('DECLARADO', 'Declarado'),
                    ('INFERIDO_ALTA', 'Inferido con confianza alta'),
                ],
                default='',
                max_length=30,
                verbose_name='Procedencia del ODF extremo B',
            ),
        ),
        migrations.AddField(
            model_name='ruta',
            name='odf_origen_procedencia',
            field=models.CharField(
                blank=True,
                choices=[
                    ('', 'Sin procedencia'),
                    ('DECLARADO', 'Declarado'),
                    ('INFERIDO_ALTA', 'Inferido con confianza alta'),
                ],
                default='',
                max_length=30,
                verbose_name='Procedencia del ODF extremo A',
            ),
        ),
        migrations.AddConstraint(
            model_name='ruta',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    odf_origen_procedencia__in=(
                        '',
                        'DECLARADO',
                        'INFERIDO_ALTA',
                    ),
                ),
                name='ck_ruta_odf_a_procedencia',
            ),
        ),
        migrations.AddConstraint(
            model_name='ruta',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    odf_destino_procedencia__in=(
                        '',
                        'DECLARADO',
                        'INFERIDO_ALTA',
                    ),
                ),
                name='ck_ruta_odf_b_procedencia',
            ),
        ),
        migrations.AddConstraint(
            model_name='ruta',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(odf_origen__isnull=False)
                    | models.Q(odf_origen_procedencia='')
                ),
                name='ck_ruta_odf_a_proc_requiere_odf',
            ),
        ),
        migrations.AddConstraint(
            model_name='ruta',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(odf_destino__isnull=False)
                    | models.Q(odf_destino_procedencia='')
                ),
                name='ck_ruta_odf_b_proc_requiere_odf',
            ),
        ),
    ]
