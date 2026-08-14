from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import (
    DetallePuertoODF,
    HubSite,
    InventarioODF,
    RackFisico,
    SalaTecnica,
)


def limpiar_texto(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none"} else text


@transaction.atomic
def resolver_rack(hub_nombre: str, sala_nombre: str, rack_nombre: str, lote=None) -> RackFisico:
    hub_nombre = limpiar_texto(hub_nombre)
    sala_nombre = limpiar_texto(sala_nombre)
    rack_nombre = limpiar_texto(rack_nombre)
    faltantes = [
        etiqueta
        for etiqueta, valor in (("hub_site", hub_nombre), ("sala", sala_nombre), ("rack", rack_nombre))
        if not valor
    ]
    if faltantes:
        raise ValidationError(f"Faltan datos de ubicación del ODF: {', '.join(faltantes)}")

    hub = HubSite.objects.filter(nombre__iexact=hub_nombre).first()
    if not hub:
        hub = HubSite.objects.create(nombre=hub_nombre, lote_importacion=lote)
    sala, _ = SalaTecnica.objects.get_or_create(
        hub_site=hub,
        nombre=sala_nombre,
        defaults={"lote_importacion": lote},
    )
    rack, _ = RackFisico.objects.get_or_create(
        sala=sala,
        nombre=rack_nombre,
        defaults={"lote_importacion": lote},
    )
    return rack


@transaction.atomic
def ajustar_puertos_a_capacidad(
    odf: InventarioODF,
    capacidad: int,
) -> InventarioODF:
    """Materializa los puertos físicos declarados por la capacidad del ODF."""
    try:
        capacidad = int(capacidad)
    except (TypeError, ValueError) as exc:
        raise ValidationError("La capacidad del ODF debe ser un número entero.") from exc
    if capacidad < 0:
        raise ValidationError("La capacidad del ODF no puede ser negativa.")

    odf = InventarioODF.objects.select_for_update().get(pk=odf.pk)
    puertos = list(
        DetallePuertoODF.objects.select_for_update()
        .filter(odf_obj=odf)
        .prefetch_related("terminaciones_fibra")
    )

    por_numero = {}
    fuera_de_rango = []
    for puerto in puertos:
        texto = (puerto.puerto_odf or "").strip()
        numero = int(texto) if texto.isdigit() else None
        if numero is not None and numero > 0:
            por_numero[numero] = puerto
            if numero > capacidad:
                fuera_de_rango.append(puerto)

    bloqueados = [
        puerto
        for puerto in fuera_de_rango
        if (
            puerto.estado_puerto != "Libre"
            or bool((puerto.destino or "").strip())
            or bool((puerto.patchcord or "").strip())
            or bool((puerto.observaciones or "").strip())
            or bool(puerto.terminaciones_fibra.all())
        )
    ]
    if bloqueados:
        numeros = ", ".join(puerto.puerto_odf for puerto in bloqueados[:10])
        raise ValidationError(
            "No se puede reducir la capacidad porque los siguientes puertos "
            f"tienen uso, vínculo o información registrada: {numeros}."
        )

    if fuera_de_rango:
        DetallePuertoODF.objects.filter(
            pk__in=[puerto.pk for puerto in fuera_de_rango]
        ).delete()

    restantes_no_numericos = sum(
        1
        for puerto in puertos
        if not (puerto.puerto_odf or "").strip().isdigit()
    )
    existentes_en_rango = sum(1 for numero in por_numero if numero <= capacidad)
    if restantes_no_numericos + existentes_en_rango > capacidad:
        raise ValidationError(
            "La capacidad indicada es menor que la cantidad de puertos "
            "existentes que no pueden ajustarse automáticamente."
        )

    faltantes = [
        DetallePuertoODF(
            odf_obj=odf,
            odf=odf.odf,
            puerto_odf=str(numero),
            estado_puerto="Libre",
            tipo_conector=odf.tipo_conector or "",
            destino="",
            patchcord="",
        )
        for numero in range(1, capacidad + 1)
        if numero not in por_numero
    ]
    if faltantes:
        DetallePuertoODF.objects.bulk_create(faltantes)

    InventarioODF.objects.filter(pk=odf.pk).update(capacidad_puertos=capacidad)
    odf.capacidad_puertos = capacidad
    odf.actualizar_contadores()
    odf.refresh_from_db()
    return odf


@transaction.atomic
def guardar_odf_normalizado(
    *,
    odf_nombre: str,
    hub_nombre: str,
    sala_nombre: str,
    rack_nombre: str,
    defaults: dict | None = None,
    lote=None,
) -> tuple[InventarioODF, bool]:
    odf_nombre = limpiar_texto(odf_nombre)
    if not odf_nombre:
        raise ValidationError("El nombre del ODF es obligatorio.")
    rack = resolver_rack(hub_nombre, sala_nombre, rack_nombre, lote=lote)
    values = dict(defaults or {})
    values.update(
        {
            "hub_site": rack.sala.hub_site.nombre,
            "sala": rack.sala.nombre,
            "rack": rack.nombre,
        }
    )
    if lote is not None:
        values["lote_importacion"] = lote
    existente = InventarioODF.objects.filter(odf__iexact=odf_nombre).first()
    if existente:
        if existente.rack_obj_id != rack.pk:
            raise ValidationError(
                f'El ODF {odf_nombre} ya existe en {existente.hub_site} / '
                f'{existente.sala} / {existente.rack}; no puede reasignarse '
                'silenciosamente a otra ubicación.'
            )
        for campo, valor in values.items():
            setattr(existente, campo, valor)
        existente.save()
        return existente, False

    return InventarioODF.objects.create(
        rack_obj=rack,
        odf=odf_nombre,
        **values,
    ), True
