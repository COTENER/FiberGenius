from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('mapas', '0037_login_throttle'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='terminacionfibra',
            constraint=models.CheckConstraint(
                condition=models.Q(extremo__in=('A', 'B')),
                name='ck_terminacion_extremo_valido',
            ),
        ),
    ]
