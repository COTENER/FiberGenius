"""Diagnóstico de completitud e integridad de una fibra óptica."""

from __future__ import annotations

from collections import Counter


ESTADOS_COMPLETITUD = {
    "SIN_RECORRIDO": "Sin recorrido",
    "RECORRIDO_PARCIAL": "Recorrido parcial",
    "SIN_TERMINACIONES": "Sin terminaciones",
    "TERMINACION_PARCIAL": "Terminación parcial",
    "COMPLETO": "Completo",
}

PRIORIDAD_SEVERIDAD = {
    "NINGUNA": 0,
    "INFORMACION": 1,
    "ADVERTENCIA": 2,
    "ERROR": 3,
}


def _texto(valor):
    texto = str(valor or "").strip()
    return texto or None


def _codigo_tramo(tramo):
    return _texto(tramo.codigo_tramo) or f"T{tramo.tramo_secuencia:03d}"


def _hallazgo(codigo, severidad, mensaje, **detalle):
    return {
        "codigo": codigo,
        "severidad": severidad,
        "mensaje": mensaje,
        **detalle,
    }


def _site_de_nodo(nodo):
    """Resuelve un Site esperado solo desde una identidad topológica confiable."""
    if nodo is None:
        return None
    if nodo.hub_site_obj_id:
        return _texto(nodo.hub_site_obj.nombre)
    if nodo.odf_obj_id:
        odf = nodo.odf_obj
        rack = getattr(odf, "rack_obj", None)
        sala = getattr(rack, "sala", None)
        site = getattr(sala, "hub_site", None)
        return _texto(getattr(site, "nombre", None)) or _texto(odf.hub_site)
    if nodo.tipo == "SITE":
        return _texto(nodo.nombre) or _texto(nodo.codigo)
    return None


def _site_de_terminacion(terminacion):
    puerto = terminacion.puerto_odf
    odf = puerto.odf_obj
    rack = odf.rack_obj
    sala = rack.sala
    return _texto(sala.hub_site.nombre)


def _relacion_precargada(objeto, nombre, atributo_precargado=None):
    if atributo_precargado:
        valor = getattr(objeto, atributo_precargado, None)
        if valor is not None:
            return list(valor)
    cache = getattr(objeto, "_prefetched_objects_cache", {})
    if nombre in cache:
        return list(cache[nombre])
    return list(getattr(objeto, nombre).all())


def evaluar_completitud_fibra(fibra):
    """Devuelve estado estructural y hallazgos sin modificar la base de datos."""
    tramos = (
        _relacion_precargada(fibra.ruta, "tramos_inventario")
        if fibra.ruta_id
        else []
    )
    tramos.sort(key=lambda item: (item.tramo_secuencia, item.pk))
    asignaciones = _relacion_precargada(fibra, "asignaciones_tramo")
    terminaciones = _relacion_precargada(
        fibra,
        "terminaciones",
        "terminaciones_oficiales",
    )
    terminaciones.sort(key=lambda item: (item.extremo, item.pk))

    tramos_por_id = {tramo.pk: tramo for tramo in tramos}
    asignados_validos = {
        item.tramo_id for item in asignaciones if item.tramo_id in tramos_por_id
    }
    faltantes = [
        tramo for tramo in tramos if tramo.pk not in asignados_validos
    ]
    ajenos = [
        item for item in asignaciones if item.tramo_id not in tramos_por_id
    ]
    por_extremo = {item.extremo: item for item in terminaciones}
    hallazgos = []

    if not fibra.ruta_id:
        estado = "SIN_RECORRIDO"
        hallazgos.append(_hallazgo(
            "TRONCAL_PENDIENTE",
            "ADVERTENCIA",
            (
                "La terminación física está registrada, pero falta asociar "
                "el hilo a una troncal."
                if terminaciones
                else "La fibra todavía no tiene una troncal asociada."
            ),
        ))
    elif not tramos:
        estado = "SIN_RECORRIDO"
        hallazgos.append(_hallazgo(
            "RUTA_SIN_TRAMOS",
            "ERROR",
            "La troncal no tiene tramos técnicos registrados.",
        ))
    elif not asignados_validos:
        estado = "SIN_RECORRIDO"
        hallazgos.append(_hallazgo(
            "SIN_FIBRA_TRAMO",
            "ERROR",
            "La fibra no está vinculada a ningún tramo de su recorrido.",
        ))
    elif faltantes:
        estado = "RECORRIDO_PARCIAL"
        codigos = [_codigo_tramo(tramo) for tramo in faltantes]
        hallazgos.append(_hallazgo(
            "FIBRA_TRAMO_FALTANTE",
            "ERROR",
            "Falta FibraTramo en: " + ", ".join(codigos),
            tramos=codigos,
        ))
    elif not terminaciones:
        estado = "SIN_TERMINACIONES"
    elif len(por_extremo) == 1:
        estado = "TERMINACION_PARCIAL"
    else:
        estado = "COMPLETO"

    if ajenos:
        hallazgos.append(_hallazgo(
            "TRAMO_AJENO_A_RUTA",
            "ERROR",
            "Existen asignaciones de fibra en tramos que no pertenecen a su troncal.",
        ))

    for extremo in ("A", "B"):
        terminacion = por_extremo.get(extremo)
        if terminacion is None:
            hallazgos.append(_hallazgo(
                f"TERMINACION_{extremo}_FALTANTE",
                "ADVERTENCIA",
                f"Falta registrar la terminación del extremo {extremo}.",
                extremo=extremo,
            ))
            continue

        puerto = terminacion.puerto_odf
        if puerto.estado_puerto != "Ocupado":
            hallazgos.append(_hallazgo(
                "PUERTO_FIBRA_INCONSISTENTE",
                "ERROR",
                f"El extremo {extremo} está conectado al puerto "
                f"{puerto.odf_obj.odf} / {puerto.puerto_odf}, pero el puerto "
                f"figura como {puerto.estado_puerto}.",
                extremo=extremo,
                puerto_id=puerto.pk,
            ))

    # A/B son etiquetas técnicas estables, no orientación geográfica.
    # Se valida el conjunto de Sites terminales sin imponer A=inicio/B=final.
    sites_esperados = []
    if tramos:
        for nodo in (tramos[0].origen_nodo, tramos[-1].destino_nodo):
            site = _site_de_nodo(nodo)
            if site:
                sites_esperados.append(site.casefold())
    sites_reales = [
        _site_de_terminacion(por_extremo[extremo]).casefold()
        for extremo in ('A', 'B')
        if extremo in por_extremo and _site_de_terminacion(por_extremo[extremo])
    ]
    if (
        len(sites_esperados) == 2
        and len(sites_reales) == 2
        and Counter(sites_reales) != Counter(sites_esperados)
    ):
        hallazgos.append(_hallazgo(
            "ODF_EN_SITE_INCORRECTO",
            "ERROR",
            "Existe una terminación ODF fuera de los Sites terminales de la troncal.",
            sites_esperados=sorted(sites_esperados),
            sites_reales=sorted(sites_reales),
        ))

    posiciones = sorted({
        _texto(item.numero_hilo) for item in asignaciones
        if _texto(item.numero_hilo)
    })
    if len(posiciones) > 1:
        hallazgos.append(_hallazgo(
            "CAMBIO_NUMERO_HILO",
            "ADVERTENCIA",
            "La posición física cambia entre tramos: " + ", ".join(posiciones) + ".",
            posiciones=posiciones,
        ))

    estados_conocidos = [
        item.estado for item in asignaciones if item.estado != "Desconocido"
    ]
    if any(item.estado == "Desconocido" for item in asignaciones):
        hallazgos.append(_hallazgo(
            "DETALLE_TRAMO_PENDIENTE",
            "INFORMACION",
            "Existe detalle por Tramo pendiente de verificar.",
        ))
    cobertura_completa = bool(tramos) and not faltantes and not ajenos
    detalle_completo = bool(asignaciones) and cobertura_completa and (
        len(estados_conocidos) == len(asignaciones)
    )
    estado_desde_tramos = None
    if detalle_completo:
        if "Ocupado" in estados_conocidos:
            estado_desde_tramos = "Ocupado"
        elif "Reservado" in estados_conocidos:
            estado_desde_tramos = "Reservado"
        elif set(estados_conocidos) == {"Libre"}:
            estado_desde_tramos = "Libre"
    if (
        fibra.origen_estado == "INFORMADO"
        and estado_desde_tramos
        and estado_desde_tramos != fibra.estado
    ):
        hallazgos.append(_hallazgo(
            "ESTADO_FIBRA_TRAMO_DIFERENTE",
            "ADVERTENCIA",
            f"La fibra figura como {fibra.estado}, pero el detalle completo "
            f"por Tramos indica {estado_desde_tramos}.",
            estado_fibra=fibra.estado,
            estado_tramos=estado_desde_tramos,
        ))

    if fibra.condicion_fisica == "CON_FALLA":
        hallazgos.append(_hallazgo(
            "FIBRA_CON_FALLA",
            "ADVERTENCIA",
            "La fibra tiene una falla física informada.",
        ))
    if _texto(fibra.nombre_fibra) and fibra.estado in {"Libre", "Desconocido"}:
        hallazgos.append(_hallazgo(
            "SERVICIO_ESTADO_POR_REVISAR",
            "ADVERTENCIA",
            "La fibra tiene servicio informado y su estado de uso requiere revisión.",
        ))

    nivel = max(
        (item["severidad"] for item in hallazgos),
        key=lambda item: PRIORIDAD_SEVERIDAD[item],
        default="NINGUNA",
    )
    return {
        "estado": estado,
        "etiqueta": ESTADOS_COMPLETITUD[estado],
        "completo": estado == "COMPLETO",
        "valido": estado == "COMPLETO" and nivel != "ERROR",
        "con_observaciones": bool(hallazgos),
        "nivel": nivel,
        "recorrido": {
            "tramos_requeridos": len(tramos),
            "tramos_asignados": len(asignados_validos),
            "tramos_faltantes": [_codigo_tramo(item) for item in faltantes],
        },
        "terminaciones": {
            "confirmadas": len(por_extremo),
            "extremo_a": "A" in por_extremo,
            "extremo_b": "B" in por_extremo,
        },
        "hallazgos": hallazgos,
    }
