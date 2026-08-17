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


ESTADOS_FIBRA = (
    "DISPONIBLE",
    "OCUPADO",
    "RESERVADO",
    "SIN_INFORMACION",
)


def normalizar_estado_fibra(valor: str | None) -> str:
    equivalencias = {
        "disponible": "DISPONIBLE",
        "ocupado": "OCUPADO",
        "ocupada": "OCUPADO",
        "reservado": "RESERVADO",
        "reservada": "RESERVADO",
        "sin informacion": "SIN_INFORMACION",
        "sin información": "SIN_INFORMACION",
        "sin_informacion": "SIN_INFORMACION",
    }
    estado = equivalencias.get(str(valor or "").strip().casefold())
    if not estado:
        raise ValidationError(
            "Estado de fibra no válido. Use Disponible, Ocupado, Reservado o Sin información."
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
        "hilos_ocupados": conteo["OCUPADO"],
        "hilos_libres": conteo["DISPONIBLE"],
        "hilos_reservados": conteo["RESERVADO"],
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


def _sincronizar_estado_tramo_unico(
    fibra: InventarioFibra,
    estado: str,
) -> None:
    """Mantiene la equivalencia 1:1 solo para una troncal de un tramo."""
    if not fibra.ruta_id:
        return
    preparacion = _preparar_tramo_unico(fibra, fibra.ruta)
    asignacion = _materializar_tramo_unico(fibra, preparacion)
    if asignacion is not None and asignacion.estado != estado:
        FibraTramo.objects.filter(pk=asignacion.pk).update(estado=estado)
        asignacion.estado = estado
        recalcular_cache_tramo(asignacion.tramo)


@transaction.atomic
def actualizar_metadatos_fibra(
    *, fibra: InventarioFibra, usuario=None, origen="SISTEMA",
    lote_importacion=None, **cambios,
) -> bool:
    """Actualiza metadatos sin decidir estado, Ruta ni terminaciones."""
    permitidos = {
        "fibra_numero",
        "nombre_fibra",
        "condicion_fisica",
        "tipo_conector",
        "observaciones",
        "lote_importacion",
    }
    desconocidos = set(cambios) - permitidos
    if desconocidos:
        raise ValidationError(
            "Metadatos de fibra no admitidos: " + ", ".join(sorted(desconocidos))
        )
    bloqueada = InventarioFibra.objects.select_for_update().get(pk=fibra.pk)
    anteriores = {}
    campos = []
    for campo, valor in cambios.items():
        if campo == "condicion_fisica" and valor not in (None, ""):
            valor = normalizar_condicion_fisica(valor)
        if campo == "fibra_numero":
            valor, _ = normalizar_numero_hilo(valor)
        if campo in {"nombre_fibra", "tipo_conector", "observaciones"}:
            valor = str(valor or "").strip()
        if getattr(bloqueada, campo) != valor:
            anteriores[campo] = getattr(bloqueada, campo)
            setattr(bloqueada, campo, valor)
            campos.append(campo)
    if lote_importacion is not None and "lote_importacion" not in cambios:
        if bloqueada.lote_importacion_id != lote_importacion.pk:
            anteriores["lote_importacion_id"] = bloqueada.lote_importacion_id
            bloqueada.lote_importacion = lote_importacion
            campos.append("lote_importacion")
    if not campos:
        return False
    bloqueada.save(update_fields=sorted(set(campos)))
    _registrar_auditoria(
        fibra=bloqueada,
        accion="ACTUALIZAR_METADATOS",
        valor_anterior=anteriores,
        valor_nuevo={campo: getattr(bloqueada, campo) for campo in campos},
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
        metadatos={"campos": sorted(set(campos))},
    )
    for campo in campos:
        setattr(fibra, campo, getattr(bloqueada, campo))
    return True


@transaction.atomic
def crear_fibra(
    *, fibra_numero: str, codigo_fibra: str = "", ruta: Ruta | None = None,
    estado: str = "SIN_INFORMACION", condicion_fisica: str = "SIN_VERIFICAR",
    nombre_fibra: str = "", tipo_conector: str = "", observaciones: str = "",
    usuario=None, origen="SISTEMA", lote_importacion=None,
) -> InventarioFibra:
    """Crea la identidad y delega Ruta/estado a sus servicios oficiales."""
    numero, _ = normalizar_numero_hilo(fibra_numero)
    fibra = InventarioFibra.objects.create(
        **({"codigo_fibra": codigo_fibra} if str(codigo_fibra or "").strip() else {}),
        ruta=None,
        fibra_numero=numero,
        estado="SIN_INFORMACION",
        origen_estado="NO_INFORMADO",
        condicion_fisica=normalizar_condicion_fisica(condicion_fisica),
        nombre_fibra=str(nombre_fibra or "").strip(),
        tipo_conector=str(tipo_conector or "").strip(),
        observaciones=str(observaciones or "").strip(),
        lote_importacion=lote_importacion,
    )
    _registrar_auditoria(
        fibra=fibra,
        accion="CREAR_FIBRA",
        valor_anterior="",
        valor_nuevo=fibra.codigo_fibra,
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
    )
    estado_normalizado = normalizar_estado_fibra(estado)
    if estado_normalizado != "SIN_INFORMACION":
        establecer_estado_fibra_informado(
            fibra=fibra,
            estado=estado_normalizado,
            usuario=usuario,
            origen=origen,
            lote_importacion=lote_importacion,
        )
    if ruta is not None:
        fibra, _, _ = asignar_ruta_fibra(
            fibra=fibra,
            ruta=ruta,
            usuario=usuario,
            origen=origen,
            lote_importacion=lote_importacion,
        )
    return fibra


def calcular_estado_desde_tramos(fibra: InventarioFibra) -> tuple[str, str]:
    """Calcula el global sin confundir evidencia de uso con cobertura."""
    if not fibra.ruta_id:
        return "SIN_INFORMACION", "NO_INFORMADO"

    tramos_esperados = set(
        InventarioTramo.objects.filter(ruta_id=fibra.ruta_id).values_list(
            "pk", flat=True
        )
    )
    asignaciones = list(
        FibraTramo.objects.filter(fibra=fibra).values("tramo_id", "estado")
    )
    if not asignaciones:
        return "SIN_INFORMACION", "NO_INFORMADO"

    estados = {item["estado"] for item in asignaciones}
    if "OCUPADO" in estados:
        return "OCUPADO", "INFERIDO_TRAMOS"
    if "RESERVADO" in estados:
        return "RESERVADO", "INFERIDO_TRAMOS"

    cubiertos = {
        item["tramo_id"]
        for item in asignaciones
        if item["tramo_id"] in tramos_esperados
    }
    cobertura_completa = bool(
        tramos_esperados and tramos_esperados.issubset(cubiertos)
    )
    if not cobertura_completa or "SIN_INFORMACION" in estados:
        return "SIN_INFORMACION", "INFERIDO_TRAMOS"
    if estados == {"DISPONIBLE"}:
        return "DISPONIBLE", "INFERIDO_TRAMOS"
    return "SIN_INFORMACION", "INFERIDO_TRAMOS"


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
    if estado_normalizado == "SIN_INFORMACION":
        anterior = (fibra.estado, fibra.origen_estado)
        restablecer_estado_fibra(
            fibra=fibra,
            usuario=usuario,
            origen=origen,
            lote_importacion=lote_importacion,
        )
        return anterior != (fibra.estado, fibra.origen_estado)
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
        _sincronizar_estado_tramo_unico(bloqueada, estado_normalizado)
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
        estado="SIN_INFORMACION",
        origen_estado="NO_INFORMADO",
    )
    bloqueada.estado = "SIN_INFORMACION"
    bloqueada.origen_estado = "NO_INFORMADO"
    _registrar_auditoria(
        fibra=bloqueada,
        accion="RESTABLECER_ESTADO",
        valor_anterior=anterior,
        valor_nuevo="SIN_INFORMACION/NO_INFORMADO",
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
    )
    _sincronizar_estado_tramo_unico(bloqueada, "SIN_INFORMACION")
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
