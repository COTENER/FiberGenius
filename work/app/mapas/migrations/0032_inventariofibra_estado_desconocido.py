from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0031_declaraciones_ruta_y_recorridos'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='inventariofibra',
            name='ck_inventario_fibra_estado',
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
                default='Libre',
                max_length=50,
                verbose_name='Estado',
            ),
        ),
        migrations.AddConstraint(
            model_name='inventariofibra',
            constraint=models.CheckConstraint(
                condition=Q(estado__in=(
                    'Libre',
                    'Ocupado',
                    'Reservado',
                    'Desconocido',
                )),
                name='ck_inventario_fibra_estado',
            ),
        ),
    ]
