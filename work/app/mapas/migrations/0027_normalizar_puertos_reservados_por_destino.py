from django.db import migrations
from django.db.models import Count


def normalizar_reservas(apps, schema_editor):
    DetallePuertoODF = apps.get_model('mapas', 'DetallePuertoODF')
    InventarioODF = apps.get_model('mapas', 'InventarioODF')

    candidatos = DetallePuertoODF.objects.filter(
        destino__icontains='reservad',
    ).exclude(estado_puerto='Reservado')
    odf_ids = list(candidatos.values_list('odf_obj_id', flat=True).distinct())
    candidatos.update(estado_puerto='Reservado')

    conteos = {
        (fila['odf_obj_id'], fila['estado_puerto']): fila['total']
        for fila in DetallePuertoODF.objects.filter(odf_obj_id__in=odf_ids)
        .values('odf_obj_id', 'estado_puerto')
        .annotate(total=Count('id'))
    }
    odfs = list(InventarioODF.objects.filter(pk__in=odf_ids))
    for odf in odfs:
        ocupados = conteos.get((odf.pk, 'Ocupado'), 0)
        reservados = conteos.get((odf.pk, 'Reservado'), 0)
        total = sum(
            conteos.get((odf.pk, estado), 0)
            for estado in ('Libre', 'Ocupado', 'Reservado')
        )
        capacidad = max(odf.capacidad_puertos or 0, total)
        odf.capacidad_puertos = capacidad
        odf.puertos_ocupados = ocupados
        odf.puertos_reservados = reservados
        odf.puertos_libres = max(capacidad - ocupados - reservados, 0)

    if odfs:
        InventarioODF.objects.bulk_update(
            odfs,
            [
                'capacidad_puertos',
                'puertos_ocupados',
                'puertos_libres',
                'puertos_reservados',
            ],
            batch_size=200,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('mapas', '0026_estado_reservado_puerto_odf'),
    ]

    operations = [
        migrations.RunPython(normalizar_reservas, migrations.RunPython.noop),
    ]
