"""Lectura canónica de las terminaciones físicas de una fibra.

La única fuente confirmada es TerminacionFibra. Los textos heredados de
InventarioFibra se conservan únicamente como referencia histórica.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ExtremoFibra:
    extremo: str
    confirmado: bool
    fuente: str
    site: str | None = None
    sala: str | None = None
    rack: str | None = None
    odf: str | None = None
    puerto: str | None = None
    conector: str | None = None
    referencia_legacy: str | None = None

    def como_dict(self):
        return asdict(self)

    @property
    def etiqueta(self):
        if self.confirmado:
            return f"{self.odf} / Puerto {self.puerto}"
        if self.referencia_legacy:
            return f"{self.referencia_legacy} (referencia histórica)"
        return "Sin terminación confirmada"


def _texto(valor):
    valor = str(valor or "").strip()
    return valor or None


def _terminaciones_indexadas(fibra):
    precargadas = getattr(fibra, "terminaciones_oficiales", None)
    if precargadas is None:
        precargadas = getattr(fibra, "_prefetched_objects_cache", {}).get(
            "terminaciones"
        )
    if precargadas is None:
        precargadas = fibra.terminaciones.select_related(
            "puerto_odf__odf_obj__rack_obj__sala__hub_site"
        ).all()
    return {terminacion.extremo: terminacion for terminacion in precargadas}


def obtener_extremo_fibra(fibra, extremo, terminaciones=None):
    """Devuelve un extremo confirmado o una referencia legacy identificada."""
    if extremo not in {"A", "B"}:
        raise ValueError("El extremo debe ser A o B.")
    terminaciones = terminaciones or _terminaciones_indexadas(fibra)
    terminacion = terminaciones.get(extremo)
    legado = _texto(fibra.origen_odf if extremo == "A" else fibra.destino)
    if terminacion is None:
        return ExtremoFibra(
            extremo=extremo,
            confirmado=False,
            fuente="LEGACY" if legado else "SIN_DATO",
            referencia_legacy=legado,
        )

    puerto = terminacion.puerto_odf
    odf = puerto.odf_obj
    rack = odf.rack_obj
    sala = rack.sala
    site = sala.hub_site
    return ExtremoFibra(
        extremo=extremo,
        confirmado=True,
        fuente="TERMINACION_FIBRA",
        site=site.nombre,
        sala=sala.nombre,
        rack=rack.nombre,
        odf=odf.odf,
        puerto=puerto.puerto_odf,
        conector=_texto(terminacion.tipo_conector),
        referencia_legacy=legado,
    )


def obtener_trazabilidad_fibra(fibra):
    """Construye la respuesta oficial A/B sin mezclar datos confirmados y legacy."""
    terminaciones = _terminaciones_indexadas(fibra)
    extremo_a = obtener_extremo_fibra(fibra, "A", terminaciones)
    extremo_b = obtener_extremo_fibra(fibra, "B", terminaciones)
    return {
        "fibra_id": fibra.pk,
        "fibra": fibra.fibra_numero,
        "ruta": fibra.nombre_troncal,
        "ruta_pendiente": fibra.es_provisional,
        "extremo_a": extremo_a.como_dict(),
        "extremo_b": extremo_b.como_dict(),
        "completa": extremo_a.confirmado and extremo_b.confirmado,
        "terminaciones_confirmadas": sum(
            (extremo_a.confirmado, extremo_b.confirmado)
        ),
    }


def _nombre_site_terminal(tramo, lado):
    if tramo is None:
        return None
    nodo = getattr(tramo, f"{lado}_nodo", None)
    if nodo is not None and getattr(nodo, "tipo", None) == "SITE":
        site = getattr(nodo, "hub_site_obj", None)
        return _texto(getattr(site, "nombre", None) or nodo.nombre or nodo.codigo)
    if lado == "origen":
        valor = _texto(getattr(tramo, "hub_site", None))
        if valor and valor != "N/A":
            return valor
    valor = _texto(getattr(tramo, lado, None))
    return None if valor == "N/A" else valor


def obtener_sites_terminales_ruta(fibra):
    """Devuelve la orientación declarada por la ruta, sin usar A/B."""
    if not fibra.ruta_id:
        return None, None
    tramos = sorted(
        fibra.ruta.tramos_inventario.all(),
        key=lambda item: (item.tramo_secuencia, item.pk),
    )
    if not tramos:
        return None, None
    return (
        _nombre_site_terminal(tramos[0], "origen"),
        _nombre_site_terminal(tramos[-1], "destino"),
    )


def obtener_presentacion_orientada_fibra(fibra):
    """Separa extremos técnicos A/B de origen/destino funcional.

    Una terminación solo se presenta como origen o destino cuando su Site
    coincide inequívocamente con el Site terminal correspondiente de la ruta.
    """
    terminaciones = _terminaciones_indexadas(fibra)
    tecnicos = {
        "A": obtener_extremo_fibra(fibra, "A", terminaciones),
        "B": obtener_extremo_fibra(fibra, "B", terminaciones),
    }
    site_origen, site_destino = obtener_sites_terminales_ruta(fibra)
    normalizar = lambda valor: (valor or "").strip().casefold()
    orientacion_valida = bool(
        site_origen
        and site_destino
        and normalizar(site_origen) != normalizar(site_destino)
    )

    asignados = set()

    def resolver(site_objetivo):
        if not orientacion_valida:
            return None
        candidatos = [
            extremo for extremo in tecnicos.values()
            if extremo.confirmado
            and extremo.extremo not in asignados
            and normalizar(extremo.site) == normalizar(site_objetivo)
        ]
        if len(candidatos) != 1:
            return None
        asignados.add(candidatos[0].extremo)
        return candidatos[0]

    origen = resolver(site_origen)
    destino = resolver(site_destino)
    pendientes = [
        extremo.como_dict() for extremo in tecnicos.values()
        if extremo.confirmado and extremo.extremo not in asignados
    ]
    return {
        "site_origen": site_origen,
        "site_destino": site_destino,
        "origen": origen.como_dict() if origen else None,
        "destino": destino.como_dict() if destino else None,
        "terminacion_a": tecnicos["A"].como_dict(),
        "terminacion_b": tecnicos["B"].como_dict(),
        "extremos_sin_orientar": pendientes,
        "orientacion_ruta_disponible": orientacion_valida,
        "orientacion_completa": bool(origen and destino),
    }
