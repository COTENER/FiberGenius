from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import HubSite, InventarioODF, RackFisico, SalaTecnica


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
