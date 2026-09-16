from django.db import migrations, models
from django.db.models import Count


def recalcular_contadores_odf(apps, schema_editor):
    InventarioODF = apps.get_model('mapas', 'InventarioODF')
    DetallePuertoODF = apps.get_model('mapas', 'DetallePuertoODF')

    DetallePuertoODF.objects.filter(estado_puerto__iexact='reservado').update(
        estado_puerto='Reservado'
    )
    conteos = {
        (fila['odf_obj_id'], fila['estado_puerto']): fila['total']
        for fila in DetallePuertoODF.objects.values('odf_obj_id', 'estado_puerto').annotate(
            total=Count('id')
        )
    }
    totales = {
        fila['odf_obj_id']: fila['total']
        for fila in DetallePuertoODF.objects.values('odf_obj_id').annotate(total=Count('id'))
    }

    actualizar = []
    for odf in InventarioODF.objects.all():
        total_detalle = totales.get(odf.pk, 0)
        if not total_detalle:
            continue
        capacidad = max(odf.capacidad_puertos or 0, total_detalle)
        ocupados = conteos.get((odf.pk, 'Ocupado'), 0)
        reservados = conteos.get((odf.pk, 'Reservado'), 0)
        odf.capacidad_puertos = capacidad
        odf.puertos_ocupados = ocupados
        odf.puertos_reservados = reservados
        odf.puertos_libres = max(capacidad - ocupados - reservados, 0)
        actualizar.append(odf)

    if actualizar:
        InventarioODF.objects.bulk_update(
            actualizar,
            ['capacidad_puertos', 'puertos_ocupados', 'puertos_libres', 'puertos_reservados'],
            batch_size=500,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('mapas', '0025_loteimportacion_baseline_auditoria'),
    ]

    operations = [
        migrations.AddField(
            model_name='inventarioodf',
            name='puertos_reservados',
            field=models.IntegerField(blank=True, default=0, null=True, verbose_name='Reservados'),
        ),
        migrations.AlterField(
            model_name='detallepuertoodf',
            name='estado_puerto',
            field=models.CharField(
                choices=[('Libre', 'Libre'), ('Ocupado', 'Ocupado'), ('Reservado', 'Reservado')],
                default='Libre',
                max_length=50,
                verbose_name='Estado',
            ),
        ),
        migrations.AddConstraint(
            model_name='inventarioodf',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(puertos_reservados__isnull=True)
                    | models.Q(puertos_reservados__gte=0)
                ),
                name='ck_odf_reservados_no_negativos',
            ),
        ),
        migrations.RunPython(recalcular_contadores_odf, migrations.RunPython.noop),
    ]
