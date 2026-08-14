from django.db import migrations, models


def migrar_geografia_existente(apps, schema_editor):
    CoordenadaRuta = apps.get_model('mapas', 'CoordenadaRuta')
    InventarioTramo = apps.get_model('mapas', 'InventarioTramo')

    tramos = {
        tramo['id']: tramo
        for tramo in InventarioTramo.objects.values(
            'id', 'ruta_id', 'tramo_secuencia', 'tipo_trazado'
        )
    }
    tramos_por_ruta_secuencia = {
        (tramo['ruta_id'], tramo['tramo_secuencia']): tramo
        for tramo in tramos.values()
    }

    coordenadas = list(
        CoordenadaRuta.objects.order_by('ruta_id', 'orden', 'pk')
    )

    # No intentamos reparar silenciosamente asociaciones contradictorias:
    # una migración así podría asignar un tipo geográfico incorrecto.
    for coordenada in coordenadas:
        if not coordenada.tramo_id:
            continue
        tramo = tramos.get(coordenada.tramo_id)
        if tramo is None or tramo['ruta_id'] != coordenada.ruta_id:
            raise RuntimeError(
                f'La coordenada {coordenada.pk} referencia un tramo de otra ruta.'
            )
        if (
            coordenada.tramo_secuencia is not None
            and coordenada.tramo_secuencia != tramo['tramo_secuencia']
        ):
            raise RuntimeError(
                f'La coordenada {coordenada.pk} tiene una secuencia '
                'incompatible con su tramo.'
            )

    ruta_anterior = None
    secuencia_anterior = None
    for coordenada in coordenadas:
        tramo = tramos.get(coordenada.tramo_id)
        secuencia = (
            coordenada.tramo_secuencia
            or (tramo['tramo_secuencia'] if tramo else None)
            or 1
        )
        if tramo is None:
            tramo = tramos_por_ruta_secuencia.get(
                (coordenada.ruta_id, secuencia)
            )

        tipo_trazado = (tramo or {}).get('tipo_trazado')
        tipo_trazado = (
            str(tipo_trazado).strip()
            if tipo_trazado is not None and str(tipo_trazado).strip()
            else 'DESCONOCIDO'
        )

        coordenada.tipo_trazado = tipo_trazado
        coordenada.inicio_segmento = (
            coordenada.ruta_id != ruta_anterior
            or secuencia != secuencia_anterior
        )
        ruta_anterior = coordenada.ruta_id
        secuencia_anterior = secuencia

    if coordenadas:
        CoordenadaRuta.objects.bulk_update(
            coordenadas,
            ['tipo_trazado', 'inicio_segmento'],
            batch_size=2000,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('mapas', '0027_normalizar_puertos_reservados_por_destino'),
    ]

    operations = [
        migrations.AddField(
            model_name='coordenadaruta',
            name='tipo_trazado',
            field=models.CharField(
                blank=True,
                max_length=50,
                null=True,
                verbose_name='Tipo de trazado geográfico',
            ),
        ),
        migrations.AddField(
            model_name='coordenadaruta',
            name='inicio_segmento',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'Indica que este punto inicia un nuevo segmento visual '
                    'del trazado.'
                ),
                verbose_name='Inicio de segmento geográfico',
            ),
        ),
        migrations.RunPython(
            migrar_geografia_existente,
            migrations.RunPython.noop,
        ),
    ]
