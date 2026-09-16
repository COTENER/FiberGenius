from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("mapas", "0046_alter_detallepuertoodf_options_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="inventariofibra",
            name="uq_fibra_ruta_numero_ci",
        ),
    ]
