import re
from collections import defaultdict

import django.db.models.deletion
from django.db import migrations, models
from django.db.models.functions import Lower


CAPACIDAD_LEGACY_RE = re.compile(
    r'^\s*(\d+)(?:[.,]0+)?(?:\s*(?:hilos?|fibras?))?\s*$',
    re.IGNORECASE,
)


def colapsar_tramos_geograficos_legacy(apps, schema_editor):
    """Retira pseudo-tramos creados por la antigua segmentación del mapa.

    La detección es deliberadamente conservadora: solo colapsa rutas sin
    fibras cuyo conjunto completo de tramos estaba enlazado a coordenadas,
    todos repetían los mismos extremos/metadatos y los tramos posteriores no
    tenían inventario propio. Los cortes visuales permanecen en
    ``CoordenadaRuta.tramo_secuencia``.
    """
    CoordenadaRuta = apps.get_model('mapas', 'CoordenadaRuta')
    InventarioFibra = apps.get_model('mapas', 'InventarioFibra')
    InventarioTramo = apps.get_model('mapas', 'InventarioTramo')
    Reserva = apps.get_model('mapas', 'Reserva')
    db_alias = schema_editor.connection.alias

    campos = (
        'pk', 'ruta_id', 'tramo_secuencia', 'tipo_trazado', 'estado',
        'mufas', 'splitters', 'reservas_m', 'capacidad', 'tipo_fibra',
        'origen', 'destino', 'hub_site', 'marca_modelo', 'serial',
        'odf_nombre', 'hilos_ocupados', 'hilos_libres',
        'lote_importacion_id',
    )
    por_ruta = defaultdict(list)
    for tramo in (
        InventarioTramo.objects.using(db_alias)
        .order_by('ruta_id', 'tramo_secuencia', 'pk')
        .values(*campos)
    ):
        por_ruta[tramo['ruta_id']].append(tramo)

    rutas_multiples = {
        ruta_id
        for ruta_id, tramos in por_ruta.items()
        if len(tramos) > 1
    }
    if not rutas_multiples:
        return

    rutas_con_fibras = set(
        InventarioFibra.objects.using(db_alias)
        .filter(ruta_id__in=rutas_multiples)
        .values_list('ruta_id', flat=True)
        .distinct()
    )
    referencias = defaultdict(set)
    for ruta_id, tramo_id in (
        CoordenadaRuta.objects.using(db_alias)
        .filter(
            ruta_id__in=rutas_multiples,
            tramo_id__isnull=False,
        )
        .values_list('ruta_id', 'tramo_id')
        .distinct()
    ):
        referencias[ruta_id].add(tramo_id)

    campos_repetidos = (
        'tipo_trazado', 'estado', 'capacidad', 'tipo_fibra',
        'origen', 'destino', 'hub_site', 'marca_modelo', 'serial',
        'odf_nombre', 'lote_importacion_id',
    )

    def normalizado(valor):
        if isinstance(valor, str):
            return valor.strip().casefold()
        return valor

    for ruta_id in sorted(rutas_multiples):
        tramos = por_ruta[ruta_id]
        if ruta_id in rutas_con_fibras:
            continue
        secuencias = [tramo['tramo_secuencia'] for tramo in tramos]
        if secuencias != list(range(1, len(tramos) + 1)):
            continue
        ids = {tramo['pk'] for tramo in tramos}
        if referencias[ruta_id] != ids:
            continue

        primero = tramos[0]
        firma = tuple(
            normalizado(primero[campo])
            for campo in campos_repetidos
        )
        if any(
            tuple(
                normalizado(tramo[campo])
                for campo in campos_repetidos
            ) != firma
            for tramo in tramos[1:]
        ):
            continue
        if any(
            any(
                (tramo[campo] or 0) != 0
                for campo in (
                    'mufas', 'splitters', 'reservas_m',
                    'hilos_ocupados', 'hilos_libres',
                )
            )
            for tramo in tramos[1:]
        ):
            continue

        ids_extra = [tramo['pk'] for tramo in tramos[1:]]
        Reserva.objects.using(db_alias).filter(
            tramo_id__in=ids_extra
        ).update(tramo_id=primero['pk'])
        InventarioTramo.objects.using(db_alias).filter(
            pk__in=ids_extra
        ).delete()


def migrar_inventario_tecnico_existente(apps, schema_editor):
    CoordenadaRuta = apps.get_model('mapas', 'CoordenadaRuta')
    FibraTramo = apps.get_model('mapas', 'FibraTramo')
    InventarioFibra = apps.get_model('mapas', 'InventarioFibra')
    InventarioTramo = apps.get_model('mapas', 'InventarioTramo')
    db_alias = schema_editor.connection.alias

    colapsar_tramos_geograficos_legacy(apps, schema_editor)

    tramos = list(
        InventarioTramo.objects.using(db_alias)
        .all()
        .only('pk', 'ruta_id', 'capacidad', 'capacidad_hilos')
    )
    capacidades_invalidas = []
    for tramo in tramos:
        capacidad_raw = str(tramo.capacidad or '').strip()
        if not capacidad_raw:
            tramo.capacidad_hilos = None
            continue

        coincidencia = CAPACIDAD_LEGACY_RE.fullmatch(capacidad_raw)
        if coincidencia is None:
            capacidades_invalidas.append(
                f'tramo={tramo.pk}, capacidad={capacidad_raw!r}'
            )
            continue

        capacidad = int(coincidencia.group(1))
        tramo.capacidad_hilos = capacidad if capacidad > 0 else None

    if capacidades_invalidas:
        muestra = '; '.join(capacidades_invalidas[:20])
        raise RuntimeError(
            'No se pudo convertir la capacidad legacy de InventarioTramo. '
            f'Corrija estos valores antes de migrar: {muestra}'
        )

    if tramos:
        InventarioTramo.objects.using(db_alias).bulk_update(
            tramos,
            ['capacidad_hilos'],
            batch_size=1000,
        )

    # Fase 1 separó la geometría de los tramos técnicos. Las FK históricas
    # restantes ya no representan una relación válida con el nuevo inventario.
    CoordenadaRuta.objects.using(db_alias).filter(
        tramo_id__isnull=False
    ).update(tramo_id=None)

    tramos_por_ruta = defaultdict(list)
    for tramo_id, ruta_id in (
        InventarioTramo.objects.using(db_alias)
        .order_by('ruta_id', 'tramo_secuencia', 'pk')
        .values_list('pk', 'ruta_id')
    ):
        tramos_por_ruta[ruta_id].append(tramo_id)

    tramo_unico_por_ruta = {
        ruta_id: tramo_ids[0]
        for ruta_id, tramo_ids in tramos_por_ruta.items()
        if len(tramo_ids) == 1
    }
    capacidad_por_tramo = {
        tramo.pk: tramo.capacidad_hilos
        for tramo in tramos
    }
    if not tramo_unico_por_ruta:
        return

    fibras_tramo = []
    fibras_invalidas = []
    for fibra in (
        InventarioFibra.objects.using(db_alias)
        .filter(ruta_id__in=tramo_unico_por_ruta)
        .only(
            'pk', 'ruta_id', 'fibra_numero', 'estado',
            'lote_importacion_id',
        )
        .iterator(chunk_size=2000)
    ):
        numero_hilo = str(fibra.fibra_numero or '').strip().upper()
        coincidencia = re.fullmatch(r'F([1-9]\d*)', numero_hilo)
        tramo_id = tramo_unico_por_ruta[fibra.ruta_id]
        capacidad = capacidad_por_tramo.get(tramo_id)
        if coincidencia is None:
            fibras_invalidas.append(
                f'fibra={fibra.pk}, numero={numero_hilo!r}'
            )
            continue
        if (
            capacidad is not None
            and int(coincidencia.group(1)) > capacidad
        ):
            fibras_invalidas.append(
                f'fibra={fibra.pk}, numero={numero_hilo!r}, '
                f'capacidad={capacidad}'
            )
            continue
        fibras_tramo.append(
            FibraTramo(
                tramo_id=tramo_id,
                numero_hilo=numero_hilo,
                estado=fibra.estado,
                fibra_id=fibra.pk,
                lote_importacion_id=fibra.lote_importacion_id,
            )
        )

    if fibras_invalidas:
        muestra = ', '.join(fibras_invalidas[:20])
        raise RuntimeError(
            'No se pudo asignar el número físico de estas fibras: '
            f'{muestra}'
        )

    if fibras_tramo:
        FibraTramo.objects.using(db_alias).bulk_create(
            fibras_tramo,
            batch_size=2000,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('mapas', '0028_geografia_independiente_de_tramos'),
    ]

    operations = [
        migrations.CreateModel(
            name='NodoRed',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'tipo',
                    models.CharField(
                        choices=[
                            ('SITE', 'Site'),
                            ('ODF', 'ODF'),
                            ('MUFA', 'Mufa'),
                            ('CAMARA', 'Cámara'),
                            ('POSTE', 'Poste'),
                            ('CAJA_EMPALME', 'Caja de empalme'),
                            ('PUNTO', 'Punto intermedio'),
                            ('OTRO', 'Otro'),
                        ],
                        max_length=30,
                    ),
                ),
                ('codigo', models.CharField(max_length=150)),
                ('nombre', models.CharField(blank=True, max_length=180)),
                (
                    'lote_importacion',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='nodos_red',
                        to='mapas.loteimportacion',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Nodo de red',
                'verbose_name_plural': 'Nodos de red',
                'db_table': 'inv_nodos_red',
                'ordering': ['tipo', 'codigo'],
            },
        ),
        migrations.AddField(
            model_name='inventariotramo',
            name='capacidad_hilos',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name='Capacidad de hilos',
            ),
        ),
        migrations.AddField(
            model_name='inventariotramo',
            name='codigo_tramo',
            field=models.CharField(
                blank=True,
                max_length=100,
                null=True,
                verbose_name='Código del tramo',
            ),
        ),
        migrations.AddField(
            model_name='inventariotramo',
            name='destino_nodo',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='tramos_como_destino',
                to='mapas.nodored',
                verbose_name='Nodo de destino',
            ),
        ),
        migrations.AddField(
            model_name='inventariotramo',
            name='hilos_reservados',
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name='Hilos reservados',
            ),
        ),
        migrations.AddField(
            model_name='inventariotramo',
            name='observaciones',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='inventariotramo',
            name='origen_nodo',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='tramos_como_origen',
                to='mapas.nodored',
                verbose_name='Nodo de origen',
            ),
        ),
        migrations.CreateModel(
            name='FibraTramo',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('numero_hilo', models.CharField(max_length=50)),
                (
                    'estado',
                    models.CharField(
                        choices=[
                            ('Libre', 'Libre'),
                            ('Ocupado', 'Ocupado'),
                            ('Reservado', 'Reservado'),
                            ('Desconocido', 'Desconocido'),
                        ],
                        default='Desconocido',
                        max_length=20,
                    ),
                ),
                ('observaciones', models.TextField(blank=True, default='')),
                (
                    'fibra',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='asignaciones_tramo',
                        to='mapas.inventariofibra',
                    ),
                ),
                (
                    'lote_importacion',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='fibras_tramo',
                        to='mapas.loteimportacion',
                    ),
                ),
                (
                    'tramo',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='fibras_tramo',
                        to='mapas.inventariotramo',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Fibra por tramo',
                'verbose_name_plural': 'Fibras por tramo',
                'db_table': 'inv_fibras_tramos',
                'ordering': ['tramo', 'numero_hilo'],
            },
        ),
        migrations.AddConstraint(
            model_name='nodored',
            constraint=models.UniqueConstraint(
                models.F('tipo'),
                Lower('codigo'),
                name='uq_nodo_tipo_codigo_ci',
            ),
        ),
        migrations.AddConstraint(
            model_name='nodored',
            constraint=models.CheckConstraint(
                condition=~models.Q(codigo=''),
                name='ck_nodo_codigo_no_vacio',
            ),
        ),
        migrations.AddConstraint(
            model_name='inventariotramo',
            constraint=models.UniqueConstraint(
                models.F('ruta'),
                Lower('codigo_tramo'),
                condition=(
                    models.Q(codigo_tramo__isnull=False)
                    & ~models.Q(codigo_tramo='')
                ),
                name='uq_tramo_codigo_ci',
            ),
        ),
        migrations.AddConstraint(
            model_name='inventariotramo',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(capacidad_hilos__isnull=True)
                    | models.Q(capacidad_hilos__gt=0)
                ),
                name='ck_tramo_capacidad_positiva',
            ),
        ),
        migrations.AddConstraint(
            model_name='inventariotramo',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(origen_nodo__isnull=True)
                    | models.Q(destino_nodo__isnull=True)
                    | ~models.Q(origen_nodo=models.F('destino_nodo'))
                ),
                name='ck_tramo_nodos_distintos',
            ),
        ),
        migrations.AddConstraint(
            model_name='inventariotramo',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(hilos_reservados__isnull=True)
                    | models.Q(hilos_reservados__gte=0)
                ),
                name='ck_tramo_hilos_reservados',
            ),
        ),
        migrations.AddIndex(
            model_name='fibratramo',
            index=models.Index(
                fields=['tramo', 'estado'],
                name='ix_fibra_tramo_estado',
            ),
        ),
        migrations.AddConstraint(
            model_name='fibratramo',
            constraint=models.UniqueConstraint(
                models.F('tramo'),
                Lower('numero_hilo'),
                name='uq_fibra_tramo_numero_ci',
            ),
        ),
        migrations.AddConstraint(
            model_name='fibratramo',
            constraint=models.UniqueConstraint(
                condition=models.Q(fibra__isnull=False),
                fields=('tramo', 'fibra'),
                name='uq_fibra_tramo_fibra',
            ),
        ),
        migrations.AddConstraint(
            model_name='fibratramo',
            constraint=models.CheckConstraint(
                condition=~models.Q(numero_hilo=''),
                name='ck_fibra_tramo_numero',
            ),
        ),
        migrations.RunPython(
            migrar_inventario_tecnico_existente,
            migrations.RunPython.noop,
        ),
    ]
