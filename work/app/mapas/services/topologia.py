from __future__ import annotations

import unicodedata

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from ..models import HubSite, InventarioODF, NodoRed, Reserva


TIPOS_NODO_RED = {
    "SITE",
    "ODF",
    "MUFA",
    "CAMARA",
    "POSTE",
    "CAJA_EMPALME",
    "PUNTO",
    "OTRO",
}


def normalizar_tipo_nodo(valor: str | None, *, default: str = "OTRO") -> str:
    tipo = unicodedata.normalize(
        "NFKD",
        str(valor or default).strip(),
    )
    tipo = "".join(
        caracter
        for caracter in tipo
        if not unicodedata.combining(caracter)
    ).upper().replace(" ", "_")
    equivalencias = {
        "CAMARA_DE_EMPAME": "CAMARA",
        "CAMARA_DE_EMPALME": "CAMARA",
        "CAJA_EMPAME": "CAJA_EMPALME",
        "CAJA_DE_EMPAME": "CAJA_EMPALME",
        "CAJA_DE_EMPALME": "CAJA_EMPALME",
    }
    tipo = equivalencias.get(tipo, tipo)
    if tipo not in TIPOS_NODO_RED:
        raise ValidationError(
            f"Tipo de nodo '{valor}' no válido. Use: "
            f"{', '.join(sorted(TIPOS_NODO_RED))}."
        )
    return tipo


def inferir_tipo_nodo(codigo: str | None) -> str:
    """Clasifica un extremo legado solo cuando existe una coincidencia exacta."""
    codigo = str(codigo or "").strip()
    if not codigo:
        return "OTRO"
    if HubSite.objects.filter(nombre__iexact=codigo).exists():
        return "SITE"
    if InventarioODF.objects.filter(odf__iexact=codigo).exists():
        return "ODF"
    elemento = (
        Reserva.objects.filter(codigo__iexact=codigo).first()
        or Reserva.objects.filter(nombre__iexact=codigo).first()
    )
    if not elemento:
        return "OTRO"
    tipo = str(elemento.tipo or "").strip().upper().replace(" ", "_")
    return tipo if tipo in TIPOS_NODO_RED else "OTRO"


@transaction.atomic
def resolver_nodo(
    *,
    tipo: str | None,
    codigo: str | None,
    nombre: str | None = None,
    lote=None,
) -> NodoRed:
    tipo_normalizado = normalizar_tipo_nodo(tipo)
    codigo_normalizado = str(codigo or "").strip().upper()
    if not codigo_normalizado:
        raise ValidationError("El código del nodo es obligatorio.")

    nodo = NodoRed.objects.select_for_update().filter(
        tipo=tipo_normalizado,
        codigo__iexact=codigo_normalizado,
    ).first()
    if nodo:
        cambios = []
        nombre_normalizado = str(nombre or "").strip()
        if nombre_normalizado and nodo.nombre != nombre_normalizado:
            nodo.nombre = nombre_normalizado
            cambios.append("nombre")
        if lote is not None and nodo.lote_importacion_id != lote.pk:
            nodo.lote_importacion = lote
            cambios.append("lote_importacion")
        if cambios:
            nodo.save(update_fields=cambios)
        return nodo

    try:
        # El savepoint permite recuperar una carrera de creación sin dejar
        # inutilizable la transacción exterior del importador.
        with transaction.atomic():
            return NodoRed.objects.create(
                tipo=tipo_normalizado,
                codigo=codigo_normalizado,
                nombre=str(nombre or "").strip(),
                lote_importacion=lote,
            )
    except IntegrityError:
        # Cubre dos importaciones concurrentes que intenten crear el mismo nodo.
        return NodoRed.objects.get(
            tipo=tipo_normalizado,
            codigo__iexact=codigo_normalizado,
        )


def codigo_tramo_automatico(secuencia: int) -> str:
    return f"TRAMO-{int(secuencia):03d}"


def nodo_identifica_texto(nodo: NodoRed | None, texto: str | None) -> bool:
    """Confirma un extremo solo mediante coincidencia exacta de código o nombre."""
    if nodo is None:
        return False
    buscado = str(texto or "").strip().casefold()
    if not buscado:
        return False
    referencias = {
        str(nodo.codigo or "").strip().casefold(),
        str(nodo.nombre or "").strip().casefold(),
    }
    referencias.discard("")
    return buscado in referencias
