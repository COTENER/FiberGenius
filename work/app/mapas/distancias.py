"""Reglas únicas para resolver distancias de rutas y tramos.

El inventario técnico es la fuente oficial. La geometría es opcional y se usa
únicamente como respaldo cuando no existe una distancia documentada completa.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


FUENTE_INVENTARIO_TRONCAL = "inventario_troncal"
FUENTE_INVENTARIO_TRAMOS = "inventario_tramos"
FUENTE_GEOMETRIA = "geometria"
ETIQUETAS_FUENTE = {
    FUENTE_INVENTARIO_TRONCAL: "Inventario de la troncal",
    FUENTE_INVENTARIO_TRAMOS: "Inventario de tramos",
    FUENTE_GEOMETRIA: "Geometría",
}


@dataclass(frozen=True)
class DistanciaResuelta:
    valor_m: float | None
    fuente: str | None

    @property
    def valor_km(self) -> float | None:
        if self.valor_m is None:
            return None
        return self.valor_m / 1000


def resolver_distancia_tramo(tramo) -> DistanciaResuelta:
    """Prioriza inventario del tramo y usa geometría solo como respaldo."""
    if tramo.distancia_documentada_m is not None:
        return DistanciaResuelta(
            float(tramo.distancia_documentada_m),
            FUENTE_INVENTARIO_TRAMOS,
        )
    if tramo.distancia_m is not None:
        return DistanciaResuelta(float(tramo.distancia_m), FUENTE_GEOMETRIA)
    return DistanciaResuelta(None, None)


def resolver_distancia_ruta(ruta, tramos: Iterable | None = None) -> DistanciaResuelta:
    """Resuelve la distancia total sin presentar sumas parciales como totales.

    Orden:
    1. Distancia documentada directamente en la troncal.
    2. Suma de distancias documentadas si todos sus tramos vigentes las tienen.
    3. Geometría completa de la ruta.
    4. Sin dato.
    """
    if ruta.distancia_documentada_m is not None:
        return DistanciaResuelta(
            float(ruta.distancia_documentada_m),
            FUENTE_INVENTARIO_TRONCAL,
        )

    if tramos is None:
        tramos = ruta.tramos_inventario.filter(vigente=True).order_by(
            "tramo_secuencia",
            "id",
        )
    tramos = [tramo for tramo in tramos if tramo.vigente]
    if tramos and all(
        tramo.distancia_documentada_m is not None
        for tramo in tramos
    ):
        return DistanciaResuelta(
            sum(float(tramo.distancia_documentada_m) for tramo in tramos),
            FUENTE_INVENTARIO_TRAMOS,
        )

    if ruta.distancia_m is not None:
        return DistanciaResuelta(float(ruta.distancia_m), FUENTE_GEOMETRIA)
    return DistanciaResuelta(None, None)


def redondear_km(distancia: DistanciaResuelta, decimales: int = 2) -> float | None:
    if distancia.valor_km is None:
        return None
    return round(distancia.valor_km, decimales)


def etiqueta_fuente(distancia: DistanciaResuelta) -> str | None:
    return ETIQUETAS_FUENTE.get(distancia.fuente)
