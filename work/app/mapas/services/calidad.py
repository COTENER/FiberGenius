"""Diagnóstico de completitud e integridad de una fibra óptica."""

from __future__ import annotations

from collections import Counter

from .ubicacion import nombre_site


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
    site = nombre_site(nodo)
    return None if str(site or '').upper() == 'SIN ASIGNAR' else site


def _site_de_terminacion(terminacion):
    site = nombre_site(terminacion.puerto_odf.odf_obj)
    return None if str(site or '').upper() == 'SIN ASIGNAR' else site


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
    pendientes_topologia = [_codigo_tramo(t) for t in tramos
                           if t.origen_nodo_id is None or t.destino_nodo_id is None]
    if pendientes_topologia:
        hallazgos.append(_hallazgo(
            'TOPOLOGIA_PENDIENTE', 'ADVERTENCIA',
            'Falta definir origen/destino del recorrido: ' + ', '.join(pendientes_topologia),
            tramos=pendientes_topologia,
        ))
    for anterior, siguiente in zip(tramos, tramos[1:]):
        if (anterior.destino_nodo_id and siguiente.origen_nodo_id
                and anterior.destino_nodo_id != siguiente.origen_nodo_id):
            hallazgos.append(_hallazgo('RECORRIDO_DISCONTINUO', 'ERROR',
                'El destino de un tramo no coincide con el origen del siguiente.'))

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
            "ADVERTENCIA",
            "La troncal no tiene tramos técnicos registrados.",
        ))
    elif not asignados_validos:
        estado = "SIN_RECORRIDO"
        hallazgos.append(_hallazgo(
            "SIN_FIBRA_TRAMO",
            "ADVERTENCIA",
            "La fibra no está vinculada a ningún tramo de su recorrido.",
        ))
    elif faltantes:
        estado = "RECORRIDO_PARCIAL"
        codigos = [_codigo_tramo(tramo) for tramo in faltantes]
        hallazgos.append(_hallazgo(
            "FIBRA_TRAMO_FALTANTE",
            "ADVERTENCIA",
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
        if not _site_de_terminacion(terminacion):
            hallazgos.append(_hallazgo('UBICACION_PENDIENTE', 'ADVERTENCIA',
                f'La ubicación del ODF del extremo {extremo} está pendiente.', extremo=extremo))
        if puerto.estado_puerto != "OCUPADO":
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
    site_incorrecto = False
    if tramos and len(sites_esperados) < 2:
        hallazgos.append(_hallazgo('SITES_RECORRIDO_PENDIENTES', 'ADVERTENCIA',
            'Falta información para verificar los Sites terminales del recorrido.'))
    if len(sites_esperados) == 2:
        if len(sites_reales) == 1:
            site_incorrecto = sites_reales[0] not in sites_esperados
        elif len(sites_reales) == 2:
            site_incorrecto = Counter(sites_reales) != Counter(sites_esperados)
    if site_incorrecto:
        hallazgos.append(_hallazgo(
            "ODF_EN_SITE_INCORRECTO",
            "ERROR",
            "Existe una terminación ODF fuera de los Sites terminales de la troncal.",
            sites_esperados=sorted(sites_esperados),
            sites_reales=sorted(sites_reales),
        ))
    if tramos and len(terminaciones) == 2:
        from itertools import permutations
        esperados = (tramos[0].origen_nodo, tramos[-1].destino_nodo)
        if any(n and n.odf_obj_id for n in esperados):
            def coincide(nodo, terminacion):
                if nodo is None:
                    return True  # Desconocido, no es evidencia de contradicción.
                if nodo.odf_obj_id:
                    return nodo.odf_obj_id == terminacion.puerto_odf.odf_obj_id
                site = _site_de_nodo(nodo)
                real = _site_de_terminacion(terminacion)
                return not site or not real or site.casefold() == real.casefold()
            if not any(all(coincide(n, t) for n, t in zip(esperados, orden))
                       for orden in permutations(terminaciones)):
                hallazgos.append(_hallazgo('ODF_TERMINAL_INCORRECTO', 'ERROR',
                    'Las terminaciones no coinciden con los ODF declarados en el recorrido.'))

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
        item.estado for item in asignaciones if item.estado != "SIN_INFORMACION"
    ]
    if any(item.estado == "SIN_INFORMACION" for item in asignaciones):
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
    if detalle_completo or 'OCUPADO' in estados_conocidos:
        if "OCUPADO" in estados_conocidos:
            estado_desde_tramos = "OCUPADO"
        elif "RESERVADO" in estados_conocidos:
            estado_desde_tramos = "RESERVADO"
        elif set(estados_conocidos) == {"DISPONIBLE"}:
            estado_desde_tramos = "DISPONIBLE"
    if (
        fibra.origen_estado == "INFORMADO"
        and estado_desde_tramos
        and estado_desde_tramos != fibra.estado
    ):
        hallazgos.append(_hallazgo(
            "ESTADO_FIBRA_TRAMO_DIFERENTE",
            "ADVERTENCIA",
            f"La fibra figura como {fibra.estado}, pero el detalle conocido "
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
    if _texto(fibra.nombre_fibra) and fibra.estado in {"DISPONIBLE", "SIN_INFORMACION"}:
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
    pendiente_verificar = any(h['codigo'] in {
        'TOPOLOGIA_PENDIENTE', 'UBICACION_PENDIENTE', 'SITES_RECORRIDO_PENDIENTES',
    } for h in hallazgos)
    return {
        "estado": estado,
        "etiqueta": ('Pendiente de validar recorrido' if estado == 'COMPLETO' and pendiente_verificar
                     else ESTADOS_COMPLETITUD[estado]),
        "completo": estado == "COMPLETO",
        "valido": estado == "COMPLETO" and nivel != "ERROR" and not pendiente_verificar,
        "pendiente_verificar": pendiente_verificar,
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
