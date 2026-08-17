"""ResoluciÃ³n canÃ³nica de la ubicaciÃ³n fÃ­sica del inventario."""

from __future__ import annotations

from ..models import HubSite, InventarioODF, NodoRed, RackFisico, SalaTecnica


def resolver_site(elemento) -> HubSite | None:
    """Devuelve el Site oficial sin recurrir a textos duplicados."""
    if elemento is None:
        return None
    if isinstance(elemento, HubSite):
        return elemento
    if isinstance(elemento, SalaTecnica):
        return elemento.hub_site
    if isinstance(elemento, RackFisico):
        return elemento.sala.hub_site
    if isinstance(elemento, InventarioODF):
        return elemento.rack_obj.sala.hub_site
    if isinstance(elemento, NodoRed):
        if elemento.hub_site_obj_id:
            return elemento.hub_site_obj
        if elemento.odf_obj_id:
            return elemento.odf_obj.rack_obj.sala.hub_site
        return None

    odf = getattr(elemento, "odf_obj", None)
    if odf is not None:
        return resolver_site(odf)
    rack = getattr(elemento, "rack_obj", None)
    if rack is not None:
        return resolver_site(rack)
    sala = getattr(elemento, "sala", None)
    if isinstance(sala, SalaTecnica):
        return resolver_site(sala)
    site = getattr(elemento, "hub_site", None)
    return site if isinstance(site, HubSite) else None


def nombre_site(elemento, default=None):
    site = resolver_site(elemento)
    if site is None:
        return default
    nombre = str(site.nombre or "").strip()
    return nombre or default


def jerarquia_odf(odf: InventarioODF) -> dict[str, str]:
    rack = odf.rack_obj
    sala = rack.sala
    site = sala.hub_site
    return {
        "site": site.nombre,
        "sala": sala.nombre,
        "rack": rack.nombre,
        "odf": odf.odf,
    }
