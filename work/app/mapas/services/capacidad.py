"""Cálculos de capacidad para tramos y troncales.

El inventario mantiene dos niveles deliberadamente distintos:

* ``InventarioFibra`` identifica un hilo lógico dentro de una troncal.
* ``FibraTramo`` documenta en qué tramos físicos está presente ese hilo.

Este módulo concentra la precedencia entre el detalle nuevo y los contadores
legados. En particular, una posición de cable no inventariada nunca se
considera libre de forma implícita.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping

from django.apps import apps


ESTADOS_OCUPADOS = {"ocupado", "ocupada", "activo", "activa", "active"}
ESTADOS_RESERVADOS = {"reservado", "reservada"}
ESTADOS_LIBRES = {"libre", "disponible"}


def _valor(objeto, campo, default=None):
    if isinstance(objeto, Mapping):
        return objeto.get(campo, default)
    return getattr(objeto, campo, default)


def _porcentaje_diferencia(declarado, calculado):
    if declarado is None or calculado is None:
        return None
    if calculado == 0:
        return 0.0 if declarado == 0 else None
    return round(abs(declarado - calculado) / abs(calculado) * 100, 2)


def _estado_conciliacion(declarado, calculado, *, critico=False):
    diferencia = _porcentaje_diferencia(declarado, calculado)
    if declarado is None or calculado is None:
        return "NO_COMPARABLE", diferencia
    if critico:
        return "INCONSISTENCIA_CRITICA", diferencia
    if diferencia is None:
        return "INCONSISTENCIA", diferencia
    if diferencia < 2:
        return "COINCIDENTE", diferencia
    if diferencia <= 5:
        return "ADVERTENCIA", diferencia
    return "INCONSISTENCIA", diferencia


def _distancia_geometrica(coordenadas):
    coordenadas = sorted(
        coordenadas,
        key=lambda coordenada: (
            _valor(coordenada, "orden", 0),
            _valor(coordenada, "pk", 0),
        ),
    )
    if len(coordenadas) < 2:
        return None

    total = 0.0
    segmentos = 0
    anterior = coordenadas[0]
    for actual in coordenadas[1:]:
        if _valor(actual, "inicio_segmento", False):
            anterior = actual
            continue
        lat1 = math.radians(float(_valor(anterior, "latitud")))
        lon1 = math.radians(float(_valor(anterior, "longitud")))
        lat2 = math.radians(float(_valor(actual, "latitud")))
        lon2 = math.radians(float(_valor(actual, "longitud")))
        delta_lat = lat2 - lat1
        delta_lon = lon2 - lon1
        haversine = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1)
            * math.cos(lat2)
            * math.sin(delta_lon / 2) ** 2
        )
        total += 6371000 * 2 * math.atan2(
            math.sqrt(haversine),
            math.sqrt(max(1 - haversine, 0)),
        )
        segmentos += 1
        anterior = actual
    return total if segmentos else None


def _magnitudes_operativas(ruta, tramos, coordenadas):
    distancia_declarada = _valor(ruta, "distancia_declarada_m")
    distancias = [_valor(tramo, "distancia_m") for tramo in tramos]
    distancia_tramos_completa = bool(
        tramos and all(valor is not None for valor in distancias)
    )
    distancia_tramos = (
        sum(distancias)
        if distancia_tramos_completa
        else None
    )
    distancia_geometria = _distancia_geometrica(coordenadas)
    if distancia_declarada is not None:
        distancia_operativa = distancia_declarada
        fuente_distancia = "declarado_ruta"
    elif distancia_tramos is not None:
        distancia_operativa = distancia_tramos
        fuente_distancia = "calculado_tramos"
    elif distancia_geometria is not None:
        distancia_operativa = distancia_geometria
        fuente_distancia = "calculado_geometria"
    else:
        distancia_operativa = None
        fuente_distancia = "sin_informacion"
    estado_distancia, diferencia_distancia_pct = _estado_conciliacion(
        distancia_declarada,
        distancia_tramos,
    )

    reservas_declaradas = _valor(ruta, "reservas_declaradas_m")
    reservas = [_valor(tramo, "reservas_m") for tramo in tramos]
    reservas_tramos_completas = bool(
        tramos and all(valor is not None for valor in reservas)
    )
    reservas_tramos = (
        sum(reservas)
        if reservas_tramos_completas
        else None
    )
    if reservas_declaradas is not None:
        reservas_operativas = reservas_declaradas
        fuente_reservas = "declarado_ruta"
    elif reservas_tramos is not None:
        reservas_operativas = reservas_tramos
        fuente_reservas = "calculado_tramos"
    else:
        reservas_operativas = None
        fuente_reservas = "sin_informacion"
    estado_reservas, diferencia_reservas_pct = _estado_conciliacion(
        reservas_declaradas,
        reservas_tramos,
    )

    return {
        "distancia_m": distancia_operativa,
        "fuente_distancia": fuente_distancia,
        "distancia_declarada_m": distancia_declarada,
        "distancia_tramos_m": distancia_tramos,
        "distancia_geometria_m": distancia_geometria,
        "distancia_tramos_completa": distancia_tramos_completa,
        "estado_conciliacion_distancia": estado_distancia,
        "diferencia_distancia_pct": diferencia_distancia_pct,
        "reservas_m": reservas_operativas,
        "fuente_reservas": fuente_reservas,
        "reservas_declaradas_m": reservas_declaradas,
        "reservas_tramos_m": reservas_tramos,
        "reservas_tramos_completas": reservas_tramos_completas,
        "estado_conciliacion_reservas": estado_reservas,
        "diferencia_reservas_pct": diferencia_reservas_pct,
    }


def _modelo_fibra_tramo():
    """Obtiene el modelo sin acoplar la migración de transición al import."""
    try:
        return apps.get_model("mapas", "FibraTramo")
    except LookupError:
        return None


def _normalizar_estado(valor) -> str:
    texto = str(valor or "").strip().casefold()
    if texto in ESTADOS_OCUPADOS:
        return "ocupados"
    if texto in ESTADOS_RESERVADOS:
        return "reservados"
    if texto in ESTADOS_LIBRES:
        return "libres"
    return "sin_estado"


def _entero_no_negativo(valor, default=0):
    if valor in (None, ""):
        return default
    try:
        return max(int(float(valor)), 0)
    except (TypeError, ValueError):
        return default


def capacidad_hilos(tramo):
    """Devuelve la capacidad numérica nueva o interpreta el texto legado."""
    def valor(campo):
        if isinstance(tramo, Mapping):
            return tramo.get(campo)
        return getattr(tramo, campo, None)

    valor_nuevo = valor("capacidad_hilos")
    if valor_nuevo not in (None, ""):
        return _entero_no_negativo(valor_nuevo, None)

    texto = str(valor("capacidad") or "").strip()
    coincidencia = re.search(r"\d+(?:[.,]\d+)?", texto)
    if not coincidencia:
        return None
    return _entero_no_negativo(coincidencia.group().replace(",", "."), None)


def texto_capacidad(*, minima=None, maxima=None, defecto="—"):
    """Formatea una capacidad sin cambiar el contrato textual de las APIs."""
    if minima is None and maxima is None:
        return defecto
    if minima is None:
        minima = maxima
    if maxima is None:
        maxima = minima
    if minima == maxima:
        return f"{minima} Hilos"
    return f"{minima}–{maxima} Hilos"


def _links_por_tramo(tramos):
    tramos = list(tramos)
    resultado = {tramo.pk: [] for tramo in tramos if tramo.pk is not None}
    if not resultado:
        return resultado

    todos_prefetch = all(
        "fibras_tramo" in getattr(tramo, "_prefetched_objects_cache", {})
        for tramo in tramos
    )
    if todos_prefetch:
        for tramo in tramos:
            resultado[tramo.pk] = list(tramo.fibras_tramo.all())
        return resultado

    modelo = _modelo_fibra_tramo()
    if modelo is None:
        return resultado
    for relacion in (
        modelo.objects.filter(tramo_id__in=resultado)
        .select_related("fibra", "tramo")
        .order_by("tramo_id", "numero_hilo", "pk")
    ):
        resultado[relacion.tramo_id].append(relacion)
    return resultado


def _resumen_tramo_desde_links(tramo, relaciones):
    capacidad = capacidad_hilos(tramo)
    relaciones = list(relaciones)
    estados = defaultdict(int)

    if relaciones:
        for relacion in relaciones:
            estados[_normalizar_estado(getattr(relacion, "estado", None))] += 1
        detalladas = len(relaciones)
        fuente = "detalle"
    else:
        estados["ocupados"] = _entero_no_negativo(
            getattr(tramo, "hilos_ocupados", 0)
        )
        estados["reservados"] = _entero_no_negativo(
            getattr(tramo, "hilos_reservados", 0)
        )
        estados["libres"] = _entero_no_negativo(
            getattr(tramo, "hilos_libres", 0)
        )
        detalladas = (
            estados["ocupados"] + estados["reservados"] + estados["libres"]
        )
        fuente = "legacy"

    ocupados_declarados = estados["ocupados"]
    reservados_declarados = estados["reservados"]
    libres_declarados = estados["libres"]
    exceso = (
        max(detalladas - capacidad, 0)
        if capacidad is not None
        else 0
    )
    conteos_consistentes = not exceso
    contadas_para_capacidad = detalladas
    if fuente == "legacy" and not conteos_consistentes:
        # Los contadores originales permanecen intactos en el modelo para
        # auditoría, pero un total imposible no puede probar disponibilidad.
        if capacidad is not None:
            estados["ocupados"] = min(ocupados_declarados, capacidad)
            restante = max(capacidad - estados["ocupados"], 0)
            estados["reservados"] = min(
                reservados_declarados,
                restante,
            )
        estados["libres"] = 0
        contadas_para_capacidad = (
            estados["ocupados"]
            + estados["reservados"]
            + estados["sin_estado"]
        )

    sin_inventariar = (
        max(capacidad - contadas_para_capacidad, 0)
        if capacidad is not None
        else 0
    )
    return {
        "tramo_id": tramo.pk,
        "codigo_tramo": getattr(tramo, "codigo_tramo", None),
        "capacidad_hilos": capacidad,
        "ocupados": estados["ocupados"],
        "reservados": estados["reservados"],
        "libres": estados["libres"],
        "ocupados_declarados": ocupados_declarados,
        "reservados_declarados": reservados_declarados,
        "libres_declarados": libres_declarados,
        "sin_estado": estados["sin_estado"],
        "detalladas": detalladas,
        "sin_inventariar": sin_inventariar,
        "conteos_consistentes": conteos_consistentes,
        "exceso": exceso,
        "detalle_fibras_completo": bool(
            relaciones
            and conteos_consistentes
            and capacidad is not None
            and capacidad > 0
            and detalladas >= capacidad
        ),
        "fuente_conteo": fuente,
    }


def resumen_tramos(tramos: Iterable) -> dict[int, dict]:
    """Resume varios tramos con una sola consulta de ``FibraTramo``.

    El resultado es un diccionario indexado por la PK del tramo. Esta forma
    permite serializar páginas y exportaciones sin consultas N+1.
    """
    tramos = list(tramos)
    relaciones = _links_por_tramo(tramos)
    return {
        tramo.pk: _resumen_tramo_desde_links(
            tramo,
            relaciones.get(tramo.pk, ()),
        )
        for tramo in tramos
    }


def resumen_tramo(tramo) -> dict:
    """Resume un tramo aislado usando detalle nuevo o cache legado."""
    if tramo.pk is None:
        return _resumen_tramo_desde_links(tramo, ())
    return resumen_tramos([tramo])[tramo.pk]


def _relaciones_rutas(rutas, tramos):
    """Agrupa tramos y fibras lógicas reutilizando prefetch cuando existe."""
    rutas = list(rutas)
    rutas_ids = [ruta.pk for ruta in rutas if ruta.pk is not None]

    tramos_por_ruta = defaultdict(list)
    cache_tramos_completa = all(
        "tramos_inventario" in getattr(ruta, "_prefetched_objects_cache", {})
        for ruta in rutas
    )
    if cache_tramos_completa:
        for ruta in rutas:
            tramos_por_ruta[ruta.pk].extend(ruta.tramos_inventario.all())
    else:
        for tramo in tramos:
            tramos_por_ruta[tramo.ruta_id].append(tramo)

    fibras_por_ruta = defaultdict(list)
    cache_fibras_completa = all(
        "fibras_inventario" in getattr(ruta, "_prefetched_objects_cache", {})
        for ruta in rutas
    )
    if cache_fibras_completa:
        for ruta in rutas:
            fibras_por_ruta[ruta.pk].extend(ruta.fibras_inventario.all())
    elif rutas_ids:
        modelo_fibra = apps.get_model("mapas", "InventarioFibra")
        for fibra in (
            modelo_fibra.objects.filter(ruta_id__in=rutas_ids)
            .select_related("ruta")
            .order_by("ruta_id", "fibra_numero", "pk")
        ):
            fibras_por_ruta[fibra.ruta_id].append(fibra)

    return tramos_por_ruta, fibras_por_ruta


def resumen_rutas(rutas: Iterable) -> dict[int, dict]:
    """Resume varias rutas en bloque, preservando fibras lógicas distintas."""
    rutas = list(rutas)
    if not rutas:
        return {}

    rutas_ids = [ruta.pk for ruta in rutas if ruta.pk is not None]
    cache_tramos_completa = all(
        "tramos_inventario" in getattr(ruta, "_prefetched_objects_cache", {})
        for ruta in rutas
    )
    if cache_tramos_completa:
        tramos = [
            tramo
            for ruta in rutas
            for tramo in ruta.tramos_inventario.all()
        ]
    else:
        modelo_tramo = apps.get_model("mapas", "InventarioTramo")
        tramos = list(
            modelo_tramo.objects.filter(ruta_id__in=rutas_ids)
            .select_related("ruta", "origen_nodo", "destino_nodo")
            .prefetch_related("fibras_tramo")
            .order_by("ruta_id", "tramo_secuencia", "pk")
        )

    tramos_por_ruta, fibras_por_ruta = _relaciones_rutas(rutas, tramos)
    relaciones = _links_por_tramo(tramos)
    coordenadas_por_ruta = defaultdict(list)
    cache_coordenadas_completa = all(
        "coordenadas" in getattr(ruta, "_prefetched_objects_cache", {})
        for ruta in rutas
    )
    if cache_coordenadas_completa:
        for ruta in rutas:
            coordenadas_por_ruta[ruta.pk].extend(ruta.coordenadas.all())
    elif rutas_ids:
        modelo_coordenada = apps.get_model("mapas", "CoordenadaRuta")
        for coordenada in (
            modelo_coordenada.objects.filter(ruta_id__in=rutas_ids)
            .order_by("ruta_id", "orden", "pk")
        ):
            coordenadas_por_ruta[coordenada.ruta_id].append(coordenada)
    resumenes_tramo = {
        tramo.pk: _resumen_tramo_desde_links(
            tramo,
            relaciones.get(tramo.pk, ()),
        )
        for tramo in tramos
    }

    cobertura_por_fibra = defaultdict(set)
    estados_fisicos_por_fibra = defaultdict(list)
    for tramo_id, asignaciones in relaciones.items():
        for asignacion in asignaciones:
            fibra_id = getattr(asignacion, "fibra_id", None)
            if fibra_id:
                cobertura_por_fibra[fibra_id].add(tramo_id)
                estado_fisico = _normalizar_estado(
                    getattr(asignacion, "estado", None)
                )
                estados_fisicos_por_fibra[fibra_id].append(
                    estado_fisico
                )

    resultado = {}
    for ruta in rutas:
        tramos_ruta = sorted(
            tramos_por_ruta[ruta.pk],
            key=lambda tramo: (tramo.tramo_secuencia, tramo.pk),
        )
        resumenes = [resumenes_tramo[tramo.pk] for tramo in tramos_ruta]
        capacidades = [
            item["capacidad_hilos"]
            for item in resumenes
            if item["capacidad_hilos"] is not None
        ]
        capacidad_min = min(capacidades) if capacidades else None
        capacidad_max = max(capacidades) if capacidades else None
        capacidades_completas = bool(
            resumenes and len(capacidades) == len(resumenes)
        )
        capacidad_calculada = (
            capacidad_min if capacidades_completas else None
        )
        capacidad_declarada = _valor(
            ruta,
            "capacidad_declarada_hilos",
        )
        if capacidad_declarada is not None:
            if (
                capacidad_calculada is not None
                and capacidad_declarada > capacidad_calculada
            ):
                capacidad_efectiva = capacidad_calculada
                fuente_capacidad = "calculado_tramos_seguro"
                estado_capacidad = "INCONSISTENCIA_CRITICA"
                diferencia_capacidad_pct = _porcentaje_diferencia(
                    capacidad_declarada,
                    capacidad_calculada,
                )
            else:
                capacidad_efectiva = capacidad_declarada
                fuente_capacidad = "declarado_ruta"
                estado_capacidad, diferencia_capacidad_pct = (
                    _estado_conciliacion(
                        capacidad_declarada,
                        capacidad_calculada,
                    )
                )
        else:
            capacidad_efectiva = capacidad_calculada
            fuente_capacidad = (
                "calculado_tramos"
                if capacidad_calculada is not None
                else "sin_informacion"
            )
            estado_capacidad = (
                "NO_COMPARABLE"
                if capacidad_calculada is not None
                else "NO_CALCULABLE"
            )
            diferencia_capacidad_pct = None

        fibras_unicas = {
            fibra.pk: fibra
            for fibra in fibras_por_ruta[ruta.pk]
            if fibra.pk is not None
        }
        tramos_requeridos = {tramo.pk for tramo in tramos_ruta}
        compatibilidad_tramo_unico_legacy = bool(
            len(resumenes) == 1
            and resumenes[0]["fuente_conteo"] == "legacy"
        )
        estados = defaultdict(int)
        fibras_cobertura_completa = 0
        fibras_sin_cobertura = 0
        for fibra_id, fibra in fibras_unicas.items():
            estados_fisicos = estados_fisicos_por_fibra.get(fibra_id, ())
            if "ocupados" in estados_fisicos:
                estado = "ocupados"
            elif "reservados" in estados_fisicos:
                estado = "reservados"
            elif "sin_estado" in estados_fisicos:
                estado = "sin_estado"
            elif "libres" in estados_fisicos:
                estado = "libres"
            else:
                estado = _normalizar_estado(
                    getattr(fibra, "estado", None)
                )
            cobertura_completa = bool(
                compatibilidad_tramo_unico_legacy
                or (
                    tramos_requeridos
                    and tramos_requeridos.issubset(
                        cobertura_por_fibra[fibra_id]
                    )
                )
            )
            if cobertura_completa:
                fibras_cobertura_completa += 1
            else:
                fibras_sin_cobertura += 1
            # Una fibra libre solo representa capacidad disponible de extremo
            # a extremo cuando está inventariada en todos los tramos.
            if estado == "libres" and not cobertura_completa:
                estados["sin_estado"] += 1
            else:
                estados[estado] += 1

        # Sin fibras lógicas solo un tramo permite trasladar exactamente sus
        # contadores físicos/legados al nivel ruta. Sumar varios tramos
        # sobrecontaría los mismos hilos y no prueba disponibilidad de extremo
        # a extremo.
        if not fibras_unicas and len(resumenes) == 1:
            resumen_unico = resumenes[0]
            estados["ocupados"] = resumen_unico["ocupados"]
            estados["reservados"] = resumen_unico["reservados"]
            estados["libres"] = resumen_unico["libres"]
            estados["sin_estado"] = resumen_unico["sin_estado"]

        estados_calculados = {
            "ocupados": estados["ocupados"],
            "reservados": estados["reservados"],
            "libres": estados["libres"],
            "sin_estado": estados["sin_estado"],
        }
        declarados_estado = {
            "ocupados": _valor(ruta, "hilos_ocupados_declarados"),
            "reservados": _valor(ruta, "hilos_reservados_declarados"),
            "libres": _valor(ruta, "hilos_libres_declarados"),
        }
        declaracion_estados_completa = all(
            valor is not None
            for valor in declarados_estado.values()
        )
        exceso_declarado = 0
        if declaracion_estados_completa:
            estados["ocupados"] = declarados_estado["ocupados"]
            estados["reservados"] = declarados_estado["reservados"]
            estados["libres"] = declarados_estado["libres"]
            estados["sin_estado"] = 0
            fuente_estados = "declarado_ruta"
            if capacidad_efectiva is not None:
                total_declarado = sum(declarados_estado.values())
                exceso_declarado = max(
                    total_declarado - capacidad_efectiva,
                    0,
                )
                estados["ocupados"] = min(
                    estados["ocupados"],
                    capacidad_efectiva,
                )
                restante = max(
                    capacidad_efectiva - estados["ocupados"],
                    0,
                )
                estados["reservados"] = min(
                    estados["reservados"],
                    restante,
                )
                restante = max(restante - estados["reservados"], 0)
                estados["libres"] = min(estados["libres"], restante)
        else:
            fuente_estados = "calculado_fibras"
        if declaracion_estados_completa and (
            fibras_unicas or len(resumenes) == 1
        ):
            estado_conciliacion_estados = (
                "COINCIDENTE"
                if all(
                    declarados_estado[estado]
                    == estados_calculados[estado]
                    for estado in ("ocupados", "reservados", "libres")
                )
                else "INCONSISTENCIA"
            )
        else:
            estado_conciliacion_estados = "NO_COMPARABLE"

        inventario_fisico_completo = bool(
            resumenes
            and all(
                item["detalle_fibras_completo"]
                for item in resumenes
            )
        )
        conteos_consistentes = all(
            item["conteos_consistentes"]
            for item in resumenes
        )
        exceso = (
            sum(item["exceso"] for item in resumenes)
            + exceso_declarado
        )
        fuentes = {item["fuente_conteo"] for item in resumenes}
        if not fuentes:
            fuente = "sin_datos"
        elif fuentes == {"detalle"}:
            fuente = "detalle"
        elif fuentes == {"legacy"}:
            fuente = "legacy"
        else:
            fuente = "mixta"

        total_logico = len(fibras_unicas)
        conocidos_operativos = (
            estados["ocupados"]
            + estados["reservados"]
            + estados["libres"]
        )
        hilos_sin_estado_logicos = estados["sin_estado"]
        if capacidad_efectiva is not None:
            hilos_sin_estado = max(
                capacidad_efectiva - conocidos_operativos,
                0,
            )
        else:
            hilos_sin_estado = hilos_sin_estado_logicos
        sin_inventariar = max(
            hilos_sin_estado - hilos_sin_estado_logicos,
            0,
        )
        magnitudes = _magnitudes_operativas(
            ruta,
            tramos_ruta,
            coordenadas_por_ruta[ruta.pk],
        )
        resultado[ruta.pk] = {
            "ruta_id": ruta.pk,
            "capacidad_min": capacidad_min,
            "capacidad_max": capacidad_max,
            "capacidad_calculada": capacidad_calculada,
            "capacidad_declarada": capacidad_declarada,
            "capacidad_efectiva": capacidad_efectiva,
            "capacidades_completas": capacidades_completas,
            "capacidad": texto_capacidad(
                minima=capacidad_efectiva,
                maxima=capacidad_efectiva,
            ),
            "capacidad_instalada": texto_capacidad(
                minima=capacidad_min,
                maxima=capacidad_max,
            ),
            "fuente_capacidad": fuente_capacidad,
            "estado_conciliacion_capacidad": estado_capacidad,
            "diferencia_capacidad_pct": diferencia_capacidad_pct,
            "fibras_total": total_logico,
            "hilos_ocupados": estados["ocupados"],
            "hilos_reservados": estados["reservados"],
            "hilos_libres": estados["libres"],
            "hilos_sin_estado": hilos_sin_estado,
            "hilos_sin_estado_logicos": hilos_sin_estado_logicos,
            "hilos_ocupados_calculados": estados_calculados["ocupados"],
            "hilos_reservados_calculados": estados_calculados["reservados"],
            "hilos_libres_calculados": estados_calculados["libres"],
            "declaracion_estados_completa": declaracion_estados_completa,
            "fuente_estados": fuente_estados,
            "estado_conciliacion_estados": (
                estado_conciliacion_estados
            ),
            "sin_inventariar": sin_inventariar,
            "fibras_cobertura_completa": fibras_cobertura_completa,
            "fibras_cobertura_incompleta": fibras_sin_cobertura,
            "fibras_sin_cobertura": fibras_sin_cobertura,
            "inventario_fisico_completo": inventario_fisico_completo,
            "conteos_consistentes": bool(
                conteos_consistentes and not exceso_declarado
            ),
            "exceso": exceso,
            "detalle_fibras_completo": bool(
                inventario_fisico_completo
                and (
                    not total_logico
                    or fibras_cobertura_completa == total_logico
                )
            ),
            "fuente": fuente,
            "tramos_total": len(tramos_ruta),
            "codigos_tramo": [
                tramo.codigo_tramo
                for tramo in tramos_ruta
                if getattr(tramo, "codigo_tramo", None)
            ],
            "resumenes_tramo": {
                tramo.pk: resumenes_tramo[tramo.pk]
                for tramo in tramos_ruta
            },
            **magnitudes,
        }
    return resultado


def resumen_ruta(ruta) -> dict:
    """Resume una ruta aislada con la misma semántica de los cálculos en lote."""
    if ruta.pk is None:
        return {
            "ruta_id": None,
            "capacidad_min": None,
            "capacidad_max": None,
            "capacidad_calculada": None,
            "capacidad_declarada": None,
            "capacidad_efectiva": None,
            "capacidades_completas": False,
            "capacidad_instalada": "—",
            "fuente_capacidad": "sin_informacion",
            "estado_conciliacion_capacidad": "NO_CALCULABLE",
            "diferencia_capacidad_pct": None,
            "capacidad": "—",
            "fibras_total": 0,
            "hilos_ocupados": 0,
            "hilos_reservados": 0,
            "hilos_libres": 0,
            "hilos_sin_estado": 0,
            "hilos_sin_estado_logicos": 0,
            "hilos_ocupados_calculados": 0,
            "hilos_reservados_calculados": 0,
            "hilos_libres_calculados": 0,
            "declaracion_estados_completa": False,
            "fuente_estados": "sin_informacion",
            "estado_conciliacion_estados": "NO_COMPARABLE",
            "sin_inventariar": 0,
            "fibras_cobertura_completa": 0,
            "fibras_cobertura_incompleta": 0,
            "fibras_sin_cobertura": 0,
            "inventario_fisico_completo": False,
            "conteos_consistentes": True,
            "exceso": 0,
            "detalle_fibras_completo": False,
            "fuente": "sin_datos",
            "tramos_total": 0,
            "codigos_tramo": [],
            "resumenes_tramo": {},
            "distancia_m": None,
            "fuente_distancia": "sin_informacion",
            "distancia_declarada_m": None,
            "distancia_tramos_m": None,
            "distancia_geometria_m": None,
            "distancia_tramos_completa": False,
            "estado_conciliacion_distancia": "NO_COMPARABLE",
            "diferencia_distancia_pct": None,
            "reservas_m": None,
            "fuente_reservas": "sin_informacion",
            "reservas_declaradas_m": None,
            "reservas_tramos_m": None,
            "reservas_tramos_completas": False,
            "estado_conciliacion_reservas": "NO_COMPARABLE",
            "diferencia_reservas_pct": None,
        }
    return resumen_rutas([ruta])[ruta.pk]
