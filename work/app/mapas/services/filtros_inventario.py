"""Reglas compartidas de consulta; no modifican el inventario."""

import unicodedata

from django.db.models import Count, Exists, OuterRef, Q

from ..models import CoordenadaRuta, InventarioTramo


def anotar_puertos_odf(queryset):
    """Disponibilidad observada: la capacidad pendiente nunca implica libre."""
    return queryset.annotate(
        _puertos_total=Count('puertos_detalle', distinct=True),
        **{
            f'_puertos_{nombre}': Count(
                'puertos_detalle', distinct=True,
                filter=Q(puertos_detalle__estado_puerto=estado),
            )
            for nombre, estado in [('libres', 'LIBRE'), ('ocupados', 'OCUPADO'), ('reservados', 'RESERVADO')]
        },
    )


def filtro_site_tramos(site, prefijo=""):
    """Resuelve extremos canónicos; los textos son fallback, no otra ubicación."""
    filtro = Q()
    for extremo in ("origen_nodo", "destino_nodo"):
        nodo = f"{prefijo}{extremo}__"
        filtro |= Q(**{f"{nodo}hub_site_obj__nombre__iexact": site})
        filtro |= Q(**{
            f"{nodo}odf_obj__rack_obj__sala__hub_site__nombre__iexact": site,
        })
        sin_vinculo = Q(**{
            f"{nodo}hub_site_obj__isnull": True,
            f"{nodo}odf_obj__isnull": True,
        })
        fallback = Q()
        for campo in ("codigo", "nombre"):
            fallback |= Q(**{f"{nodo}tipo": "SITE", f"{nodo}{campo}__iexact": site})
        for campo in (("hub_site", "origen") if extremo == "origen_nodo" else ("destino",)):
            fallback |= Q(**{f"{prefijo}{campo}__iexact": site})
        filtro |= sin_vinculo & fallback
    return filtro


def filtro_site_fibras(site):
    return (
        Q(terminaciones__puerto_odf__odf_obj__rack_obj__sala__hub_site__nombre__iexact=site)
        | filtro_site_tramos(site, "ruta__tramos_inventario__")
    )


def filtrar_rutas_site(queryset, site):
    """Selecciona rutas, sin recortar sus tramos en los contadores agregados."""
    if not site:
        return queryset
    return queryset.filter(pk__in=InventarioTramo.objects.filter(
        filtro_site_tramos(site)
    ).values('ruta_id'))


def normalizar_trazado(valor):
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(valor or ""))
        if not unicodedata.combining(c)
    ).strip().upper()


def tipos_trazado_ruta(ruta):
    tipos = {
        normalizar_trazado(c.tipo_trazado) for c in ruta.coordenadas.all()
        if c.tipo_trazado
    }
    if not tipos:
        tipos = {
            normalizar_trazado(t.tipo_trazado) for t in ruta.tramos_inventario.all()
            if t.tipo_trazado
        }
    return tipos


def tipo_trazado_ruta(ruta):
    tipos = tipos_trazado_ruta(ruta)
    if "HIBRIDO" in tipos or {"AEREO", "SOTERRADO"} <= tipos:
        return "HIBRIDO"
    if "AEREO" in tipos:
        return "AEREO"
    if "SOTERRADO" in tipos:
        return "SOTERRADO"
    return "SIN CLASIFICAR"


def filtrar_rutas_trazado(queryset, tipo):
    """La geografía clasifica la ruta; sin clasificación geográfica usa tramos.

    Aéreo/soterrado incluyen rutas con ese componente; híbrido incluye mezclas.
    Exists evita multiplicar filas al combinar filtros de varios tramos.
    """
    tipo = normalizar_trazado(tipo)
    if not tipo:
        return queryset
    geo = CoordenadaRuta.objects.filter(ruta_id=OuterRef("pk"))
    tecnico = InventarioTramo.objects.filter(ruta_id=OuterRef("pk"))
    queryset = queryset.alias(
        _fg_geo=Exists(geo.exclude(tipo_trazado__isnull=True).exclude(tipo_trazado=""))
    )
    variantes = {"AEREO": ("AEREO", "AÉREO"), "HIBRIDO": ("HIBRIDO", "HÍBRIDO")}

    def coincide(base, valor):
        q = Q()
        for variante in variantes.get(valor, (valor,)):
            q |= Q(tipo_trazado__iexact=variante)
        return base.filter(q)

    queryset = queryset.alias(
        _fg_g_tipo=Exists(coincide(geo, tipo)),
        _fg_t_tipo=Exists(coincide(tecnico, tipo)),
    )
    geo_q = Q(_fg_g_tipo=True)
    tecnico_q = Q(_fg_t_tipo=True)
    if tipo == "HIBRIDO":
        queryset = queryset.alias(
            _fg_g_a=Exists(coincide(geo, "AEREO")),
            _fg_g_s=Exists(coincide(geo, "SOTERRADO")),
            _fg_t_a=Exists(coincide(tecnico, "AEREO")),
            _fg_t_s=Exists(coincide(tecnico, "SOTERRADO")),
        )
        geo_q |= Q(_fg_g_a=True, _fg_g_s=True)
        tecnico_q |= Q(_fg_t_a=True, _fg_t_s=True)
    return queryset.filter((Q(_fg_geo=True) & geo_q) | (Q(_fg_geo=False) & tecnico_q))
