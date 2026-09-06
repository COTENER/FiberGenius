from django.db import migrations
from django.db.models import F, OuterRef, Subquery


def sincronizar_nombre_odf(apps, schema_editor):
    DetallePuertoODF = apps.get_model("mapas", "DetallePuertoODF")
    InventarioODF = apps.get_model("mapas", "InventarioODF")
    nombre_oficial = InventarioODF.objects.filter(
        pk=OuterRef("odf_obj_id")
    ).values("odf")[:1]
    DetallePuertoODF.objects.exclude(
        odf=F("odf_obj__odf")
    ).update(odf=Subquery(nombre_oficial))


class Migration(migrations.Migration):
    dependencies = [
        ("mapas", "0035_odf_por_fibra_no_por_ruta"),
    ]

    operations = [
        migrations.RunPython(
            sincronizar_nombre_odf,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
