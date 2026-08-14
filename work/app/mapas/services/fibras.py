from __future__ import annotations

import re
from collections import Counter

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import (
    AuditoriaFibra,
    FibraTramo,
    InventarioFibra,
    InventarioTramo,
    Ruta,
)


ESTADOS_FIBRA = ("Libre", "Ocupado", "Reservado", "Desconocido")


def normalizar_estado_fibra(valor: str | None) -> str:
    equivalencias = {
        "libre": "Libre",
        "disponible": "Libre",
        "ocupado": "Ocupado",
        "ocupada": "Ocupado",
        "reservado": "Reservado",
        "reservada": "Reservado",
        "desconocido": "Desconocido",
        "desconocida": "Desconocido",
        "sin informacion": "Desconocido",
        "sin información": "Desconocido",
        "sin verificar": "Desconocido",
    }
    estado = equivalencias.get(str(valor or "").strip().casefold())
    if not estado:
        raise ValidationError(
            "Estado de fibra no válido. Use Libre, Ocupado, Reservado o Desconocido."
        )
    return estado


def normalizar_condicion_fisica(valor: str | None) -> str:
    equivalencias = {
        "operativa": "OPERATIVA",
        "operativo": "OPERATIVA",
        "con falla": "CON_FALLA",
        "con_falla": "CON_FALLA",
        "malo": "CON_FALLA",
        "mala": "CON_FALLA",
        "sin verificar": "SIN_VERIFICAR",
        "sin_verificar": "SIN_VERIFICAR",
        "sin informacion": "SIN_VERIFICAR",
        "sin información": "SIN_VERIFICAR",
    }
    condicion = equivalencias.get(str(valor or "").strip().casefold())
    if not condicion:
        raise ValidationError(
            "Condición física no válida. Use Operativa, Con falla o Sin verificar."
        )
    return condicion


def normalizar_numero_hilo(valor: str | None) -> tuple[str, int]:
    texto = str(valor or "").strip().upper().replace(" ", "")
    coincidencia = re.fullmatch(r"F(?:IBRA)?[-_]?0*(\d+)", texto)
    if not coincidencia:
        raise ValidationError(
            f"Número de hilo '{valor}' no válido. Use el formato F1, F2, F3..."
        )
    numero = int(coincidencia.group(1))
    if numero <= 0:
        raise ValidationError("El número de hilo debe ser mayor que cero.")
    return f"F{numero}", numero


def capacidad_hilos_tramo(tramo: InventarioTramo) -> int | None:
    if tramo.capacidad_hilos:
        return int(tramo.capacidad_hilos)
    coincidencia = re.search(r"\d+(?:[.,]\d+)?", str(tramo.capacidad or ""))
    if not coincidencia:
        return None
    numero = float(coincidencia.group().replace(",", "."))
    return int(numero) if numero > 0 and numero.is_integer() else None


def validar_hilo_en_tramo(tramo: InventarioTramo, numero_hilo: int) -> None:
    capacidad = capacidad_hilos_tramo(tramo)
    if capacidad is not None and numero_hilo > capacidad:
        raise ValidationError(
            f"El hilo F{numero_hilo} excede la capacidad {capacidad} "
            f"del tramo {tramo.codigo_tramo or tramo.tramo_secuencia}."
        )


def recalcular_cache_tramo(tramo: InventarioTramo) -> dict[str, int]:
    conteo = Counter(
        FibraTramo.objects.filter(tramo=tramo).values_list("estado", flat=True)
    )
    valores = {
        "hilos_ocupados": conteo["Ocupado"],
        "hilos_libres": conteo["Libre"],
        "hilos_reservados": conteo["Reservado"],
    }
    InventarioTramo.objects.filter(pk=tramo.pk).update(**valores)
    return valores


def _referencia_fibra(fibra: InventarioFibra) -> str:
    return f"{fibra.nombre_troncal} / {fibra.fibra_numero}"


def _origen_auditoria(origen: str | None) -> str:
    valor = str(origen or "SISTEMA").strip().upper()
    permitidos = {clave for clave, _ in AuditoriaFibra.ORIGENES}
    return valor if valor in permitidos else "SISTEMA"


def _registrar_auditoria(
    *, fibra, accion, valor_anterior, valor_nuevo, usuario=None,
    origen="SISTEMA", lote_importacion=None, metadatos=None,
):
    if usuario is not None and not getattr(usuario, "is_authenticated", False):
        usuario = None
    AuditoriaFibra.objects.create(
        accion=accion,
        origen=_origen_auditoria(origen),
        usuario=usuario,
        fibra=fibra,
        referencia_fibra=_referencia_fibra(fibra),
        valor_anterior=str(valor_anterior or ""),
        valor_nuevo=str(valor_nuevo or ""),
        lote_importacion=lote_importacion,
        metadatos=metadatos or {},
    )


def calcular_estado_desde_tramos(fibra: InventarioFibra) -> tuple[str, str]:
    """Calcula el global sin confundir evidencia de uso con cobertura."""
    if not fibra.ruta_id:
        return "Desconocido", "NO_INFORMADO"

    tramos_esperados = set(
        InventarioTramo.objects.filter(ruta_id=fibra.ruta_id).values_list(
            "pk", flat=True
        )
    )
    asignaciones = list(
        FibraTramo.objects.filter(fibra=fibra).values("tramo_id", "estado")
    )
    if not asignaciones:
        return "Desconocido", "NO_INFORMADO"

    estados = {item["estado"] for item in asignaciones}
    if "Ocupado" in estados:
        return "Ocupado", "INFERIDO_TRAMOS"
    if "Reservado" in estados:
        return "Reservado", "INFERIDO_TRAMOS"

    cubiertos = {
        item["tramo_id"]
        for item in asignaciones
        if item["tramo_id"] in tramos_esperados
    }
    cobertura_completa = bool(
        tramos_esperados and tramos_esperados.issubset(cubiertos)
    )
    if not cobertura_completa or "Desconocido" in estados:
        return "Desconocido", "INFERIDO_TRAMOS"
    if estados == {"Libre"}:
        return "Libre", "INFERIDO_TRAMOS"
    return "Desconocido", "INFERIDO_TRAMOS"


@transaction.atomic
def sincronizar_estado_fibra(
    fibra: InventarioFibra,
    *,
    usuario=None,
    origen="SISTEMA",
    lote_importacion=None,
    causa="CAMBIO_FIBRA_TRAMO",
) -> str:
    """Infiere bajo bloqueo; un estado INFORMADO siempre tiene prioridad."""
    bloqueada = (
        InventarioFibra.objects.select_for_update()
        .select_related("ruta")
        .get(pk=fibra.pk)
    )
    if bloqueada.origen_estado == "INFORMADO":
        fibra.estado = bloqueada.estado
        fibra.origen_estado = bloqueada.origen_estado
        fibra.ruta = bloqueada.ruta
        return bloqueada.estado

    estado, origen_estado = calcular_estado_desde_tramos(bloqueada)
    anterior = f"{bloqueada.estado}/{bloqueada.origen_estado}"
    if bloqueada.estado != estado or bloqueada.origen_estado != origen_estado:
        InventarioFibra.objects.filter(
            pk=bloqueada.pk,
        ).exclude(origen_estado="INFORMADO").update(
            estado=estado,
            origen_estado=origen_estado,
        )
        bloqueada.refresh_from_db(fields=["estado", "origen_estado"])
        if bloqueada.origen_estado != "INFORMADO":
            _registrar_auditoria(
                fibra=bloqueada,
                accion="INFERIR_ESTADO",
                valor_anterior=anterior,
                valor_nuevo=f"{bloqueada.estado}/{bloqueada.origen_estado}",
                usuario=usuario,
                origen=origen,
                lote_importacion=lote_importacion,
                metadatos={
                    "causa": causa,
                    "origen_estado": bloqueada.origen_estado,
                },
            )
    fibra.estado = bloqueada.estado
    fibra.origen_estado = bloqueada.origen_estado
    fibra.ruta = bloqueada.ruta
    return bloqueada.estado


@transaction.atomic
def establecer_estado_fibra_informado(
    *, fibra: InventarioFibra, estado: str, usuario=None, origen="SISTEMA",
    lote_importacion=None,
) -> bool:
    estado_normalizado = normalizar_estado_fibra(estado)
    bloqueada = InventarioFibra.objects.select_for_update().get(pk=fibra.pk)
    anterior = f"{bloqueada.estado}/{bloqueada.origen_estado}"
    cambio = (
        bloqueada.estado != estado_normalizado
        or bloqueada.origen_estado != "INFORMADO"
    )
    if cambio:
        InventarioFibra.objects.filter(pk=bloqueada.pk).update(
            estado=estado_normalizado,
            origen_estado="INFORMADO",
        )
        bloqueada.estado = estado_normalizado
        bloqueada.origen_estado = "INFORMADO"
        _registrar_auditoria(
            fibra=bloqueada,
            accion="CAMBIAR_ESTADO",
            valor_anterior=anterior,
            valor_nuevo=f"{estado_normalizado}/INFORMADO",
            usuario=usuario,
            origen=origen,
            lote_importacion=lote_importacion,
        )
    fibra.estado = bloqueada.estado
    fibra.origen_estado = bloqueada.origen_estado
    return cambio


@transaction.atomic
def restablecer_estado_fibra(
    *, fibra: InventarioFibra, usuario=None, origen="SISTEMA",
    lote_importacion=None,
) -> str:
    bloqueada = InventarioFibra.objects.select_for_update().get(pk=fibra.pk)
    anterior = f"{bloqueada.estado}/{bloqueada.origen_estado}"
    InventarioFibra.objects.filter(pk=bloqueada.pk).update(
        estado="Desconocido",
        origen_estado="NO_INFORMADO",
    )
    bloqueada.estado = "Desconocido"
    bloqueada.origen_estado = "NO_INFORMADO"
    _registrar_auditoria(
        fibra=bloqueada,
        accion="RESTABLECER_ESTADO",
        valor_anterior=anterior,
        valor_nuevo="Desconocido/NO_INFORMADO",
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
    )
    fibra.estado = bloqueada.estado
    fibra.origen_estado = bloqueada.origen_estado
    return bloqueada.estado


def _preparar_tramo_unico(fibra: InventarioFibra, ruta: Ruta):
    tramos = list(
        InventarioTramo.objects.select_for_update()
        .filter(ruta=ruta)
        .order_by("tramo_secuencia", "pk")[:2]
    )
    if len(tramos) != 1:
        return None
    tramo = tramos[0]
    numero_hilo, indice = normalizar_numero_hilo(fibra.fibra_numero)
    validar_hilo_en_tramo(tramo, indice)
    existentes = list(
        FibraTramo.objects.select_for_update().filter(fibra=fibra)[:2]
    )
    if existentes:
        if (
            len(existentes) == 1
            and existentes[0].tramo_id == tramo.pk
            and existentes[0].numero_hilo.casefold() == numero_hilo.casefold()
        ):
            return tramo, numero_hilo, existentes[0]
        raise ValidationError(
            "La fibra ya tiene una asignación física contradictoria; "
            "no se asignó la troncal."
        )
    ocupante = (
        FibraTramo.objects.select_for_update()
        .filter(tramo=tramo, numero_hilo__iexact=numero_hilo)
        .exclude(fibra=fibra)
        .first()
    )
    if ocupante:
        raise ValidationError(
            f"{numero_hilo} ya está ocupado en "
            f"{tramo.codigo_tramo or tramo.tramo_secuencia}."
        )
    return tramo, numero_hilo, None


def _materializar_tramo_unico(fibra: InventarioFibra, preparacion):
    if preparacion is None:
        return None
    tramo, numero_hilo, existente = preparacion
    if existente is not None:
        return existente
    asignacion = FibraTramo.objects.create(
        tramo=tramo,
        numero_hilo=numero_hilo,
        estado=fibra.estado,
        fibra=fibra,
        observaciones="Creado por regla 1:1 de troncal con un solo tramo.",
    )
    recalcular_cache_tramo(tramo)
    return asignacion


@transaction.atomic
def asignar_ruta_fibra(
    *, fibra: InventarioFibra, ruta: Ruta, usuario=None, origen="SISTEMA",
    lote_importacion=None,
):
    """Asigna la troncal conservando identidad, estado, servicio y terminaciones."""
    bloqueada = (
        InventarioFibra.objects.select_for_update()
        .select_related("ruta")
        .get(pk=fibra.pk)
    )
    ruta_bloqueada = Ruta.objects.select_for_update().get(pk=ruta.pk)
    if bloqueada.ruta_id:
        if bloqueada.ruta_id == ruta_bloqueada.pk:
            preparacion = _preparar_tramo_unico(
                bloqueada,
                ruta_bloqueada,
            )
            _materializar_tramo_unico(bloqueada, preparacion)
            fibra.ruta = ruta_bloqueada
            return bloqueada, False, ""
        raise ValidationError("La fibra ya pertenece a otra troncal.")
    if InventarioFibra.objects.filter(
        ruta=ruta_bloqueada,
        fibra_numero__iexact=bloqueada.fibra_numero,
    ).exclude(pk=bloqueada.pk).exists():
        raise ValidationError(
            f"Ya existe {ruta_bloqueada.nombre} / {bloqueada.fibra_numero}."
        )

    preparacion_tramo_unico = _preparar_tramo_unico(
        bloqueada,
        ruta_bloqueada,
    )
    InventarioFibra.objects.filter(pk=bloqueada.pk).update(ruta=ruta_bloqueada)
    bloqueada.ruta = ruta_bloqueada
    _materializar_tramo_unico(bloqueada, preparacion_tramo_unico)
    _registrar_auditoria(
        fibra=bloqueada,
        accion="ASIGNAR_RUTA",
        valor_anterior="Troncal pendiente",
        valor_nuevo=ruta_bloqueada.nombre,
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
    )
    fibra.ruta = ruta_bloqueada
    return bloqueada, True, ""


@transaction.atomic
def asignar_fibra_a_tramos(
    *,
    fibra: InventarioFibra,
    tramos,
    numero_hilo: str,
    estado: str,
    observaciones: str = "",
    lote=None,
) -> list[FibraTramo]:
    numero_normalizado, indice = normalizar_numero_hilo(numero_hilo)
    estado_normalizado = normalizar_estado_fibra(estado)
    tramos = list(tramos)
    if not tramos:
        raise ValidationError("La troncal no tiene tramos técnicos registrados.")

    asignaciones = []
    for tramo in tramos:
        if tramo.ruta_id != fibra.ruta_id:
            raise ValidationError("La fibra y el tramo deben pertenecer a la misma troncal.")
        validar_hilo_en_tramo(tramo, indice)
        ocupante = FibraTramo.objects.filter(
            tramo=tramo,
            numero_hilo__iexact=numero_normalizado,
        ).exclude(fibra=fibra).first()
        if ocupante:
            raise ValidationError(
                f"{numero_normalizado} ya está asignado en el tramo "
                f"{tramo.codigo_tramo or tramo.tramo_secuencia}."
            )
        asignacion, _ = FibraTramo.objects.update_or_create(
            tramo=tramo,
            fibra=fibra,
            defaults={
                "numero_hilo": numero_normalizado,
                "estado": estado_normalizado,
                "observaciones": observaciones,
                "lote_importacion": lote,
            },
        )
        asignaciones.append(asignacion)

    sincronizar_estado_fibra(fibra)
    for tramo in tramos:
        recalcular_cache_tramo(tramo)
    return asignaciones
