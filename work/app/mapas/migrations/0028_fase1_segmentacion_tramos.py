import re

from django.db import migrations, models
import django.utils.timezone


def backfill_segmentacion(apps, schema_editor):
    Ruta = apps.get_model("mapas", "Ruta")
    InventarioTramo = apps.get_model("mapas", "InventarioTramo")

    rutas = list(Ruta.objects.all())
    primeros = {}
    distancias_calculadas = {}
    for tramo in InventarioTramo.objects.order_by(
        "ruta_id", "tramo_secuencia", "id"
    ):
        primeros.setdefault(tramo.ruta_id, tramo)
        if tramo.distancia_m is not None:
            distancias_calculadas[tramo.ruta_id] = (
                distancias_calculadas.get(tramo.ruta_id, 0)
                + tramo.distancia_m
            )

    rutas_actualizadas = []
    for ruta in rutas:
        ruta.distancia_documentada_m = ruta.distancia_m
        if ruta.pk in distancias_calculadas:
            ruta.distancia_m = distancias_calculadas[ruta.pk]
        capacidad = getattr(primeros.get(ruta.pk), "capacidad", "") or ""
        coincidencia = re.search(r"\d+", str(capacidad))
        ruta.capacidad_hilos_declarada = (
            int(coincidencia.group(0)) if coincidencia else None
        )
        rutas_actualizadas.append(ruta)
    if rutas_actualizadas:
        Ruta.objects.bulk_update(
            rutas_actualizadas,
            [
                "distancia_m",
                "distancia_documentada_m",
                "capacidad_hilos_declarada",
            ],
            batch_size=500,
        )

    tramos = list(InventarioTramo.objects.all())
    for tramo in tramos:
        tramo.codigo_tramo = f"T{tramo.tramo_secuencia:03d}"
        tramo.estado_calidad = "LEGACY_SIN_SEGMENTAR"
    if tramos:
        InventarioTramo.objects.bulk_update(
            tramos,
            ["codigo_tramo", "estado_calidad"],
            batch_size=500,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("mapas", "0027_normalizar_puertos_reservados_por_destino"),
    ]

    operations = [
        migrations.AddField(
            model_name="ruta",
            name="capacidad_hilos_declarada",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Capacidad del cable continuo asociada a toda la troncal.",
                null=True,
                verbose_name="Capacidad declarada (hilos)",
            ),
        ),
        migrations.AddField(
            model_name="ruta",
            name="distancia_documentada_m",
            field=models.FloatField(
                blank=True,
                help_text=(
                    "Valor declarado en el inventario técnico; no reemplaza la "
                    "distancia calculada desde la geometría."
                ),
                null=True,
                verbose_name="Distancia documentada (m)",
            ),
        ),
        migrations.AddField(
            model_name="inventariotramo",
            name="actualizado_en",
            field=models.DateTimeField(
                auto_now=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="inventariotramo",
            name="codigo_tramo",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Identificador estable dentro de la troncal, por ejemplo T001."
                ),
                max_length=50,
                null=True,
                verbose_name="Código estable del tramo",
            ),
        ),
        migrations.AddField(
            model_name="inventariotramo",
            name="creado_en",
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="inventariotramo",
            name="distancia_documentada_m",
            field=models.FloatField(
                blank=True,
                null=True,
                verbose_name="Distancia documentada del tramo (m)",
            ),
        ),
        migrations.AddField(
            model_name="inventariotramo",
            name="estado_calidad",
            field=models.CharField(
                choices=[
                    (
                        "LEGACY_SIN_SEGMENTAR",
                        "Heredado sin segmentación validada",
                    ),
                    ("PENDIENTE", "Pendiente de validación"),
                    ("VALIDADO", "Validado"),
                    ("INFERIDO", "Inferido desde geometría"),
                    ("REQUIERE_REVISION", "Requiere revisión"),
                ],
                db_index=True,
                default="PENDIENTE",
                max_length=30,
                verbose_name="Calidad de segmentación",
            ),
        ),
        migrations.AddField(
            model_name="inventariotramo",
            name="vigente",
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text=(
                    "Se desactiva, sin borrar, cuando una recarga ya no contiene "
                    "el tramo."
                ),
                verbose_name="Tramo vigente",
            ),
        ),
        migrations.RunPython(
            backfill_segmentacion,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="inventariotramo",
            name="codigo_tramo",
            field=models.CharField(
                help_text=(
                    "Identificador estable dentro de la troncal, por ejemplo T001."
                ),
                max_length=50,
                verbose_name="Código estable del tramo",
            ),
        ),
        migrations.AddConstraint(
            model_name="ruta",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("distancia_m__isnull", True))
                    | models.Q(("distancia_m__gte", 0))
                ),
                name="ck_ruta_distancia_no_negativa",
            ),
        ),
        migrations.AddConstraint(
            model_name="ruta",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("distancia_documentada_m__isnull", True))
                    | models.Q(("distancia_documentada_m__gte", 0))
                ),
                name="ck_ruta_dist_doc_no_negativa",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventariotramo",
            constraint=models.UniqueConstraint(
                fields=("ruta", "codigo_tramo"),
                name="uq_tramo_codigo_por_ruta",
            ),
        ),
        migrations.AddConstraint(
            model_name="inventariotramo",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("distancia_documentada_m__isnull", True))
                    | models.Q(("distancia_documentada_m__gte", 0))
                ),
                name="ck_tramo_dist_doc_no_negativa",
            ),
        ),
    ]
