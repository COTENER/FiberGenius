from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0039_auditoria_puertos_odf'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='detallepuertoodf',
            name='fibra',
        ),
    ]
