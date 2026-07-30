# Consolida la propuesta duplicada de planta externa en inv_reservas_landmarks.

import django.db.models.deletion
from django.db import migrations, models


def consolidar_elementos(apps, schema_editor):
    Elemento = apps.get_model('mapas', 'ElementoPlantaExterna')
    Reserva = apps.get_model('mapas', 'Reserva')
    Empalme = apps.get_model('mapas', 'EmpalmeFibra')

    sin_troncal = [
        elemento.pk
        for elemento in Elemento.objects.filter(ruta__isnull=True, tramo__isnull=True)
    ]
    if sin_troncal:
        raise RuntimeError(
            'No se eliminó inv_elementos_planta_externa: existen elementos sin '
            f'troncal ni tramo ({sin_troncal[:20]}). Asígnelos antes de migrar.'
        )

    for elemento in Elemento.objects.select_related('tramo'):
        ruta_id = elemento.ruta_id or elemento.tramo.ruta_id
        reserva = Reserva.objects.filter(
            ruta_id=ruta_id,
            nombre=elemento.nombre,
            latitud=elemento.latitud,
            longitud=elemento.longitud,
        ).first()
        atributos = dict(elemento.atributos or {})
        atributos['legacy_elemento_planta_id'] = elemento.pk

        if reserva is None:
            reserva = Reserva.objects.create(
                ruta_id=ruta_id,
                tramo_id=elemento.tramo_id,
                nombre=elemento.nombre,
                tipo=elemento.tipo,
                tipo_original_cliente=elemento.tipo_original_cliente,
                codigo=elemento.codigo,
                latitud=elemento.latitud,
                longitud=elemento.longitud,
                orden_en_ruta=elemento.orden_en_ruta,
                progresiva_m=elemento.progresiva_m,
                reserva_m=elemento.reserva_m,
                estado=elemento.estado,
                atributos=atributos,
                lote_importacion_id=elemento.lote_importacion_id,
            )
        else:
            reserva.tramo_id = reserva.tramo_id or elemento.tramo_id
            reserva.tipo = reserva.tipo or elemento.tipo
            reserva.tipo_original_cliente = (
                reserva.tipo_original_cliente or elemento.tipo_original_cliente
            )
            reserva.codigo = reserva.codigo or elemento.codigo
            reserva.orden_en_ruta = reserva.orden_en_ruta or elemento.orden_en_ruta
            reserva.progresiva_m = (
                reserva.progresiva_m
                if reserva.progresiva_m is not None else elemento.progresiva_m
            )
            reserva.reserva_m = (
                reserva.reserva_m if reserva.reserva_m is not None else elemento.reserva_m
            )
            reserva.estado = reserva.estado or elemento.estado
            reserva.atributos = {**(reserva.atributos or {}), **atributos}
            reserva.lote_importacion_id = (
                reserva.lote_importacion_id or elemento.lote_importacion_id
            )
            reserva.save()

        Empalme.objects.filter(elemento_id=elemento.pk).update(
            reserva_destino_id=reserva.pk
        )

    empalmes_sin_destino = list(
        Empalme.objects.filter(reserva_destino__isnull=True)
        .values_list('pk', flat=True)[:20]
    )
    if empalmes_sin_destino:
        raise RuntimeError(
            'No se pudo consolidar la referencia de estos empalmes: '
            f'{empalmes_sin_destino}'
        )


class Migration(migrations.Migration):

    dependencies = [
        ('mapas', '0023_prioridades_integridad_relacional'),
    ]

    operations = [
        migrations.RunPython(consolidar_elementos, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='elementoplantaexterna',
            name='uq_elemento_planta_codigo',
        ),
        migrations.RemoveConstraint(
            model_name='elementoplantaexterna',
            name='ck_elemento_latitud',
        ),
        migrations.RemoveConstraint(
            model_name='elementoplantaexterna',
            name='ck_elemento_longitud',
        ),
        migrations.RemoveConstraint(
            model_name='elementoplantaexterna',
            name='ck_elemento_progresiva',
        ),
        migrations.RemoveConstraint(
            model_name='elementoplantaexterna',
            name='ck_elemento_reserva',
        ),
        migrations.RemoveConstraint(
            model_name='empalmefibra',
            name='uq_empalme_elemento_fibras',
        ),
        migrations.RemoveField(
            model_name='empalmefibra',
            name='elemento',
        ),
        migrations.RenameField(
            model_name='empalmefibra',
            old_name='reserva_destino',
            new_name='elemento',
        ),
        migrations.AlterField(
            model_name='empalmefibra',
            name='elemento',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='empalmes', to='mapas.reserva'),
        ),
        migrations.AddConstraint(
            model_name='empalmefibra',
            constraint=models.UniqueConstraint(fields=('elemento', 'fibra_entrada', 'fibra_salida'), name='uq_empalme_elemento_fibras'),
        ),
        migrations.AddConstraint(
            model_name='empalmefibra',
            constraint=models.UniqueConstraint(condition=~models.Q(bandeja='') & ~models.Q(posicion=''), fields=('elemento', 'bandeja', 'posicion'), name='uq_empalme_elemento_posicion'),
        ),
        migrations.DeleteModel(
            name='ElementoPlantaExterna',
        ),
    ]
