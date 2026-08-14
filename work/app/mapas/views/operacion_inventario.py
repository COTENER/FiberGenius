"""Consultas operativas de inventario para la GUI.

Estas vistas evitan enviar inventarios completos al navegador y entregan una
ficha transversal de los activos sin modificar el modelo ni la carga existente.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import (
    Case,
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce, Lower, Substr
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse

from ..models import (
    CoordenadaRuta,
    DetallePuertoODF,
    FibraTramo,
    HubSite,
    InventarioFibra,
    InventarioODF,
    InventarioTramo,
    RackFisico,
    Reserva,
    Ruta,
    TerminacionFibra,
)
from ..services.capacidad import (
    resumen_ruta,
    resumen_rutas,
    resumen_tramos,
    texto_capacidad,
)
from ..services.calidad import evaluar_completitud_fibra
from ..services.topologia import (
    codigo_tramo_automatico,
    inferir_tipo_nodo,
    resolver_nodo,
)
from ..services.trazabilidad import (
    obtener_extremo_fibra,
    obtener_presentacion_orientada_fibra,
    obtener_trazabilidad_fibra,
)


PAGE_SIZES = {10, 25, 48, 50, 100, 200}
PERMISO_POR_TIPO = {
    "troncal": "mapas.view_ruta",
    "odf": "mapas.view_inventarioodf",
    "puerto": "mapas.view_detallepuertoodf",
    "fibra": "mapas.view_inventariofibra",
    "site": "mapas.view_hubsite",
    "elemento": "mapas.view_reserva",
}


def _texto(valor, defecto="—"):
    if valor is None:
        return defecto
    texto = str(valor).strip()
    if not texto or texto.casefold() in {
        "-", "nan", "none", "null", "n/a", "sin dato", "sin_dato",
    }:
        return defecto
    return texto


def _pagina(request, queryset, serializador):
    try:
        page_size = int(request.GET.get("page_size", 50))
    except (TypeError, ValueError):
        page_size = 50
    if page_size not in PAGE_SIZES:
        page_size = 50

    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(request.GET.get("page", 1))
    return JsonResponse(
        {
            "status": "success",
            "data": serializador(list(page.object_list)),
            "pagination": {
                "page": page.number,
                "page_size": page_size,
                "total": paginator.count,
                "total_pages": paginator.num_pages,
                "has_previous": page.has_previous(),
                "has_next": page.has_next(),
            },
        }
    )


def _filtro_puertos(request):
    queryset = DetallePuertoODF.objects.select_related(
        "odf_obj__rack_obj__sala__hub_site"
    ).prefetch_related(
        Prefetch(
            "terminaciones_fibra",
            queryset=TerminacionFibra.objects.select_related("fibra__ruta").order_by("pk"),
            to_attr="terminaciones_oficiales",
        )
    )
    termino = request.GET.get("q", "").strip()[:150]
    odf_id = request.GET.get("odf_id", "").strip()
    estado = request.GET.get("estado", "").strip()
    site = request.GET.get("site", "").strip()[:150]
    sala = request.GET.get("sala", "").strip()[:100]
    rack = request.GET.get("rack", "").strip()[:100]
    if termino:
        queryset = queryset.filter(
            Q(odf_obj__odf__icontains=termino)
            | Q(puerto_odf__icontains=termino)
            | Q(terminaciones_fibra__fibra__fibra_numero__icontains=termino)
            | Q(terminaciones_fibra__fibra__ruta__nombre__icontains=termino)
            | Q(destino__icontains=termino)
            | Q(odf_obj__hub_site__icontains=termino)
            | Q(odf_obj__sala__icontains=termino)
            | Q(odf_obj__rack__icontains=termino)
        ).distinct()
    if odf_id.isdigit():
        queryset = queryset.filter(odf_obj_id=int(odf_id))
    if estado in {"Libre", "Ocupado", "Reservado"}:
        queryset = queryset.filter(estado_puerto=estado)
    # Una selección explícita de ODF (por ejemplo, desde el mapa) identifica por
    # sí sola el inventario solicitado. El contexto global de Site puede seguir
    # presente en la URL/sessionStorage y no debe anular esa selección.
    if site and not odf_id.isdigit():
        queryset = queryset.filter(odf_obj__hub_site=site)
    if sala:
        queryset = queryset.filter(odf_obj__sala=sala)
    if rack:
        queryset = queryset.filter(odf_obj__rack=rack)
    # ``puerto_odf`` es texto porque también admite identificadores alfanuméricos.
    # Para los puertos puramente numéricos, ordénelos por su valor y no
    # lexicográficamente (1, 2, ... 10 en lugar de 1, 10, ... 2).
    queryset = queryset.annotate(
        _puerto_es_texto=Case(
            When(puerto_odf__regex=r"^\d+$", then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        _puerto_numero=Case(
            When(
                puerto_odf__regex=r"^\d+$",
                then=Cast("puerto_odf", IntegerField()),
            ),
            default=Value(0),
            output_field=IntegerField(),
        ),
    )
    return queryset.order_by(
        "odf_obj__hub_site",
        "odf_obj__odf",
        "_puerto_es_texto",
        "_puerto_numero",
        "puerto_odf",
        "id",
    )


def _resumen_puertos(queryset):
    sin_destino = Q(destino__isnull=True) | Q(destino__exact="")
    con_patchcord = (
        ~Q(patchcord__isnull=True)
        & ~Q(patchcord__exact="")
        & ~Q(patchcord__iexact="No")
    )
    resumen = queryset.aggregate(
        total=Count("id"),
        ocupados=Count("id", filter=Q(estado_puerto="Ocupado")),
        libres=Count("id", filter=Q(estado_puerto="Libre")),
        reservados=Count("id", filter=Q(estado_puerto="Reservado")),
        con_patchcord=Count("id", filter=con_patchcord),
        sin_destino=Count("id", filter=sin_destino),
    )
    ranking = []
    for fila in queryset.order_by().values("odf_obj_id", "odf_obj__odf").annotate(
        total=Count("id"),
        ocupados=Count("id", filter=Q(estado_puerto="Ocupado")),
        reservados=Count("id", filter=Q(estado_puerto="Reservado")),
    ):
        utilizados = fila["ocupados"] + fila["reservados"]
        ranking.append({
            "id": fila["odf_obj_id"],
            "nombre": fila["odf_obj__odf"],
            "total": fila["total"],
            "utilizados": utilizados,
            "utilizacion": round(
                utilizados / fila["total"] * 100,
                1,
            ) if fila["total"] else 0,
        })
    ranking.sort(
        key=lambda fila: (fila["utilizacion"], fila["utilizados"], fila["nombre"]),
        reverse=True,
    )
    resumen["top_odfs"] = ranking[:5]
    return resumen


def _serializar_puertos(puertos):
    resultado = []
    for puerto in puertos:
        terminacion = next(iter(puerto.terminaciones_oficiales), None)
        conexion = None
        if terminacion:
            conexion = {
                "fibra_id": terminacion.fibra_id,
                "ruta": terminacion.fibra.nombre_troncal,
                "ruta_pendiente": terminacion.fibra.es_provisional,
                "fibra": terminacion.fibra.fibra_numero,
                "estado_fibra": terminacion.fibra.estado,
                "extremo": terminacion.extremo,
            }
        resultado.append({
            "id": puerto.pk,
            "site": _texto(puerto.odf_obj.hub_site),
            "sala": _texto(puerto.odf_obj.sala),
            "rack": _texto(puerto.odf_obj.rack),
            "odf": _texto(puerto.odf_obj.odf),
            "bandeja": _texto(puerto.bandeja),
            "puerto": _texto(puerto.puerto_odf),
            "fibra": (
                f"{conexion['ruta']} / {conexion['fibra']} · Extremo {conexion['extremo']}"
                if conexion else _texto(None)
            ),
            "estado": _texto(puerto.estado_puerto),
            "conector": _texto(puerto.tipo_conector),
            "patchcord": _texto(puerto.patchcord, "No"),
            "destino": _texto(puerto.destino),
            "observaciones": _texto(puerto.observaciones, ""),
            "conexion": conexion,
            "requiere_regularizacion": bool(
                (puerto.estado_puerto == "Ocupado" and not conexion)
                or (puerto.estado_puerto != "Ocupado" and conexion)
            ),
            "detail_url": reverse("asset_360", args=["puerto", puerto.pk]),
        })
    return resultado


def _filtro_odfs(request):
    queryset = InventarioODF.objects.all()
    termino = request.GET.get("q", "").strip()[:150]
    site = request.GET.get("site", "").strip()[:150]
    sala = request.GET.get("sala", "").strip()[:100]
    rack = request.GET.get("rack", "").strip()[:100]
    estado = request.GET.get("estado", "").strip()[:50]
    capacidad = request.GET.get("capacidad", "").strip().lower()
    if termino:
        queryset = queryset.filter(
            Q(odf__icontains=termino)
            | Q(hub_site__icontains=termino)
            | Q(sala__icontains=termino)
            | Q(rack__icontains=termino)
            | Q(tipo_conector__icontains=termino)
        )
    if site:
        queryset = queryset.filter(hub_site=site)
    if sala:
        queryset = queryset.filter(sala=sala)
    if rack:
        queryset = queryset.filter(rack=rack)
    if estado:
        queryset = queryset.filter(estado=estado)
    if capacidad in {"disponible", "sin_disponibilidad"}:
        ids = []
        for odf in queryset.values(
            "id",
            "capacidad_puertos",
            "puertos_ocupados",
            "puertos_libres",
            "puertos_reservados",
        ):
            declarada = odf["capacidad_puertos"] or 0
            ocupados = odf["puertos_ocupados"] or 0
            libres_declarados = odf["puertos_libres"] or 0
            reservados = odf["puertos_reservados"] or 0
            total = max(declarada, ocupados + libres_declarados + reservados)
            if not total:
                continue
            libres = max(total - ocupados - reservados, 0)
            if (
                capacidad == "disponible" and libres > 0
                or capacidad == "sin_disponibilidad" and libres == 0
            ):
                ids.append(odf["id"])
        queryset = queryset.filter(pk__in=ids)
    return queryset.order_by("hub_site", "sala", "rack", "odf", "id")


def _resumen_odfs(queryset):
    resumen = queryset.aggregate(
        total=Count("id"),
        capacidad=Sum("capacidad_puertos"),
        ocupados=Sum("puertos_ocupados"),
        libres=Sum("puertos_libres"),
        reservados=Sum("puertos_reservados"),
        sites=Count("hub_site", distinct=True),
    )
    resultado = {clave: valor or 0 for clave, valor in resumen.items()}
    utilizacion = ExpressionWrapper(
        100.0 * F("puertos_ocupados") / F("capacidad_puertos"),
        output_field=FloatField(),
    )
    resultado["top_ocupacion"] = [
        {
            "id": item["id"],
            "odf": item["odf"],
            "site": item["hub_site"],
            "capacidad": item["capacidad_puertos"] or 0,
            "ocupados": item["puertos_ocupados"] or 0,
            "porcentaje": round(item["porcentaje"] or 0, 1),
        }
        for item in (
            queryset.filter(capacidad_puertos__gt=0)
            .annotate(porcentaje=utilizacion)
            .order_by("-porcentaje", "-puertos_ocupados", "odf")
            .values(
                "id",
                "odf",
                "hub_site",
                "capacidad_puertos",
                "puertos_ocupados",
                "porcentaje",
            )[:5]
        )
    ]
    return resultado


def _serializar_odfs(odfs):
    return [
        {
            "id": odf.pk,
            "site": _texto(odf.hub_site),
            "sala": _texto(odf.sala),
            "rack": _texto(odf.rack),
            "odf": _texto(odf.odf),
            "capacidad": odf.capacidad_puertos or 0,
            "ocupados": odf.puertos_ocupados or 0,
            "libres": odf.puertos_libres or 0,
            "reservados": odf.puertos_reservados or 0,
            "conector": _texto(odf.tipo_conector),
            "estado": _texto(odf.estado, "Sin estado"),
            "detail_url": reverse("asset_360", args=["odf", odf.pk]),
            "ports_url": f'{reverse("planta_interna")}?{urlencode({"q": odf.odf})}',
            "fibers_url": f'{reverse("planta_externa")}?{urlencode({"tab": "fibras", "q": odf.odf})}',
        }
        for odf in odfs
    ]


def _estadisticas_capacidad_rutas(ids_ruta):
    ids_ruta = set(ids_ruta)
    if not ids_ruta:
        return {}

    rutas = list(
        Ruta.objects.filter(pk__in=ids_ruta)
        .prefetch_related(
            "fibras_inventario",
            "tramos_inventario",
            "tramos_inventario__fibras_tramo",
        )
    )
    resumenes = resumen_rutas(rutas)
    resultado = {}
    for ruta_id, resumen in resumenes.items():
        resultado[ruta_id] = {
            "registros": resumen["fibras_total"],
            "ocupados": resumen["hilos_ocupados"],
            "reservados": resumen["hilos_reservados"],
            "libres": resumen["hilos_libres"],
            "capacidad_min": resumen["capacidad_min"],
            "capacidad_max": resumen["capacidad_max"],
            "capacidad_efectiva": resumen["capacidad_efectiva"],
            "capacidades_completas": resumen["capacidades_completas"],
            "detalle_fibras_completo": resumen["detalle_fibras_completo"],
            "sin_inventariar": resumen["sin_inventariar"],
            "hilos_sin_estado": resumen["hilos_sin_estado"],
            "fibras_sin_cobertura": resumen["fibras_sin_cobertura"],
            "conteos_consistentes": resumen["conteos_consistentes"],
            "exceso": resumen["exceso"],
            "fuente": resumen["fuente"],
            "fuente_capacidad": resumen["fuente_capacidad"],
            "fuente_estados": resumen["fuente_estados"],
            "fuente_distancia": resumen["fuente_distancia"],
            "fuente_reservas": resumen["fuente_reservas"],
        }
    return resultado


def _filtro_troncales(request):
    queryset = Ruta.objects.filter(tramos_inventario__isnull=False)
    termino = request.GET.get("q", "").strip()[:150]
    tipo = request.GET.get("tipo", "").strip()[:50]
    estado = request.GET.get("estado", "").strip()[:50]
    site = request.GET.get("site", "").strip()[:150]
    capacidad = request.GET.get("capacidad", "").strip().lower()
    if termino:
        queryset = queryset.filter(
            Q(nombre__icontains=termino)
            | Q(olt__icontains=termino)
            | Q(tramos_inventario__hub_site__icontains=termino)
            | Q(tramos_inventario__destino__icontains=termino)
            | Q(tramos_inventario__origen_nodo__codigo__icontains=termino)
            | Q(tramos_inventario__destino_nodo__codigo__icontains=termino)
            | Q(tramos_inventario__odf_nombre__icontains=termino)
            | Q(tramos_inventario__serial__icontains=termino)
        )
    if tipo:
        if tipo.casefold() in {"hibrido", "híbrido"}:
            rutas_mixtas = (
                Ruta.objects.filter(
                    tramos_inventario__tipo_trazado__iexact="AEREO"
                )
                .filter(
                    tramos_inventario__tipo_trazado__iexact="SOTERRADO"
                )
                .values("pk")
            )
            queryset = queryset.filter(
                Q(tramos_inventario__tipo_trazado__iexact="HIBRIDO")
                | Q(tramos_inventario__tipo_trazado__iexact="HÍBRIDO")
                | Q(pk__in=rutas_mixtas)
            )
        else:
            queryset = queryset.filter(
                tramos_inventario__tipo_trazado__iexact=tipo
            )
    if estado:
        queryset = queryset.filter(tramos_inventario__estado__iexact=estado)
    if site:
        queryset = queryset.filter(
            Q(tramos_inventario__hub_site__iexact=site)
            | Q(tramos_inventario__origen__iexact=site)
            | Q(tramos_inventario__destino__iexact=site)
            | Q(tramos_inventario__origen_nodo__codigo__iexact=site)
            | Q(tramos_inventario__destino_nodo__codigo__iexact=site)
            | Q(tramos_inventario__origen_nodo__nombre__iexact=site)
            | Q(tramos_inventario__destino_nodo__nombre__iexact=site)
        )
    if capacidad in {"disponible", "atencion"}:
        ids_ruta = set(queryset.values_list("pk", flat=True).distinct())
        estadisticas = _estadisticas_capacidad_rutas(ids_ruta)

        ids_capacidad = []
        for ruta_id, datos in estadisticas.items():
            total_contado = (
                datos["ocupados"] + datos["reservados"] + datos["libres"]
            )
            total = datos["capacidad_efectiva"] or total_contado
            if not total:
                continue
            utilizacion = round(
                min(
                    datos["ocupados"] + datos["reservados"],
                    total,
                ) / total * 100,
                1,
            )
            if (
                capacidad == "disponible" and datos["libres"] > 0
                or capacidad == "atencion" and 80 <= utilizacion < 100
            ):
                ids_capacidad.append(ruta_id)
        queryset = queryset.filter(pk__in=ids_capacidad)
    return (
        queryset.distinct()
        .prefetch_related(
            "fibras_inventario",
            Prefetch(
                "tramos_inventario",
                queryset=(
                    InventarioTramo.objects
                    .select_related("origen_nodo", "destino_nodo")
                    .prefetch_related("fibras_tramo")
                    .order_by("tramo_secuencia", "pk")
                ),
            ),
        )
        .annotate(
            tramos_total=Count("tramos_inventario", distinct=True),
            fibras_total=Count("fibras_inventario", distinct=True),
            reservas_total=Count("reservas", distinct=True),
        )
        .order_by("nombre", "id")
    )


def _tipo_troncal(tramos):
    tipos = {
        (tramo.tipo_trazado or "").strip().upper()
        for tramo in tramos
        if (tramo.tipo_trazado or "").strip()
    }
    if "HIBRIDO" in tipos or ({"AEREO", "SOTERRADO"} <= tipos):
        return "HÍBRIDO"
    if "AEREO" in tipos:
        return "AÉREO"
    if "SOTERRADO" in tipos:
        return "SOTERRADO"
    return next(iter(tipos), "SIN CLASIFICAR")


def _valor_nodo(tramo, extremo):
    nodo = getattr(tramo, f"{extremo}_nodo", None) if tramo else None
    if nodo:
        return nodo.nombre or nodo.codigo
    return getattr(tramo, extremo, None) if tramo else None


def _serializar_troncales(troncales):
    troncales = list(troncales)
    capacidades = resumen_rutas(troncales)
    resultado = []
    for troncal in troncales:
        tramos = list(troncal.tramos_inventario.all())
        primero = tramos[0] if tramos else None
        ultimo = tramos[-1] if tramos else None
        capacidad_ruta = capacidades[troncal.pk]
        distancia_m = capacidad_ruta["distancia_m"]
        resultado.append(
            {
                "id": troncal.pk,
                "nombre": troncal.nombre,
                "origen": _texto(
                    (
                        primero.hub_site
                        or _valor_nodo(primero, "origen")
                    ) if primero else troncal.olt
                ),
                "destino": _texto(_valor_nodo(ultimo, "destino")),
                "tipo": _tipo_troncal(tramos),
                "distancia_km": round((distancia_m or 0) / 1000, 2),
                "tramos": troncal.tramos_total,
                "capacidad": (
                    capacidad_ruta["capacidad"]
                    if capacidad_ruta["capacidad_efectiva"] is not None
                    else "—"
                ),
                "estado": _texto(primero.estado if primero else None, "Sin estado"),
                "fibras": getattr(
                    troncal,
                    "fibras_total",
                    capacidad_ruta["fibras_total"],
                ),
                "reservas": troncal.reservas_total,
                "odf": _texto(primero.odf_nombre if primero else None),
                "tipo_fibra": _texto(primero.tipo_fibra if primero else None),
                "marca_modelo": _texto(primero.marca_modelo if primero else None),
                "serial": _texto(primero.serial if primero else None),
                "mufas": (primero.mufas or 0) if primero else 0,
                "splitters": (primero.splitters or 0) if primero else 0,
                "hilos_ocupados": capacidad_ruta["hilos_ocupados"],
                "hilos_reservados": capacidad_ruta["hilos_reservados"],
                "hilos_libres": capacidad_ruta["hilos_libres"],
                "reserva_km": round(
                    (capacidad_ruta["reservas_m"] or 0) / 1000,
                    3,
                ),
                "capacidad_min": capacidad_ruta["capacidad_min"],
                "capacidad_max": capacidad_ruta["capacidad_max"],
                "capacidad_efectiva": capacidad_ruta["capacidad_efectiva"],
                "capacidades_completas": capacidad_ruta[
                    "capacidades_completas"
                ],
                "detalle_fibras_completo": capacidad_ruta[
                    "detalle_fibras_completo"
                ],
                "sin_inventariar": capacidad_ruta["sin_inventariar"],
                "hilos_sin_estado": capacidad_ruta["hilos_sin_estado"],
                "fibras_sin_cobertura": capacidad_ruta[
                    "fibras_sin_cobertura"
                ],
                "conteos_consistentes": capacidad_ruta[
                    "conteos_consistentes"
                ],
                "exceso": capacidad_ruta["exceso"],
                "fuente": capacidad_ruta["fuente"],
                "fuente_capacidad": capacidad_ruta["fuente_capacidad"],
                "fuente_estados": capacidad_ruta["fuente_estados"],
                "fuente_distancia": capacidad_ruta["fuente_distancia"],
                "fuente_reservas": capacidad_ruta["fuente_reservas"],
                "detail_url": reverse("asset_360", args=["troncal", troncal.pk]),
            }
        )
    return resultado


def _resumen_troncales(queryset):
    base = Ruta.objects.filter(pk__in=queryset.values("pk"))
    rutas = list(
        base.prefetch_related(
            "fibras_inventario",
            "coordenadas",
            "tramos_inventario",
            "tramos_inventario__fibras_tramo",
        )
    )
    ids_ruta = [ruta.pk for ruta in rutas]
    resumenes_ruta = resumen_rutas(rutas)
    distancia = sum(
        resumenes_ruta[ruta.pk]["distancia_m"] or 0
        for ruta in rutas
    )
    resumen = {
        "total": base.count(),
        "distancia_km": round(distancia / 1000, 2),
        "tramos": InventarioTramo.objects.filter(ruta__in=base).count(),
        "fibras": InventarioFibra.objects.filter(ruta__in=base).count(),
        "reservas": Reserva.objects.filter(ruta__in=base).count(),
    }
    nombres = dict(base.values_list("pk", "nombre"))
    ranking = []
    for ruta_id, resumen_ruta_actual in resumenes_ruta.items():
        datos = {
            "ocupados": resumen_ruta_actual["hilos_ocupados"],
            "reservados": resumen_ruta_actual["hilos_reservados"],
            "libres": resumen_ruta_actual["hilos_libres"],
            "capacidad_efectiva": resumen_ruta_actual[
                "capacidad_efectiva"
            ],
            "sin_inventariar": resumen_ruta_actual["sin_inventariar"],
            "hilos_sin_estado": resumen_ruta_actual["hilos_sin_estado"],
            "fibras_sin_cobertura": resumen_ruta_actual[
                "fibras_sin_cobertura"
            ],
        }
        total_contado = (
            datos["ocupados"] + datos["reservados"] + datos["libres"]
        )
        total = datos["capacidad_efectiva"] or total_contado
        utilizados = datos["ocupados"] + datos["reservados"]
        if not total:
            continue
        ranking.append({
            "id": ruta_id,
            "nombre": nombres.get(ruta_id, "Ruta"),
            "total": total,
            "utilizados": utilizados,
            "utilizacion": round(
                min(utilizados, total) / total * 100,
                1,
            ),
            "sin_inventariar": datos["sin_inventariar"],
            "hilos_sin_estado": datos["hilos_sin_estado"],
            "fibras_sin_cobertura": datos["fibras_sin_cobertura"],
        })
    ranking.sort(
        key=lambda fila: (fila["utilizacion"], fila["utilizados"], fila["nombre"]),
        reverse=True,
    )
    resumen["top_ocupacion"] = ranking[:5]
    return resumen


def _filtro_tramos(request):
    queryset = InventarioTramo.objects.select_related(
        "ruta",
        "origen_nodo",
        "destino_nodo",
    ).prefetch_related("fibras_tramo")
    termino = request.GET.get("q", "").strip()[:150]
    tipo = request.GET.get("tipo", "").strip()[:50]
    estado = request.GET.get("estado", "").strip()[:50]
    ruta = request.GET.get("ruta", "").strip()[:150]
    site = request.GET.get("site", "").strip()[:150]
    if termino:
        queryset = queryset.filter(
            Q(ruta__nombre__icontains=termino)
            | Q(origen__icontains=termino)
            | Q(destino__icontains=termino)
            | Q(origen_nodo__codigo__icontains=termino)
            | Q(destino_nodo__codigo__icontains=termino)
            | Q(hub_site__icontains=termino)
            | Q(odf_nombre__icontains=termino)
            | Q(serial__icontains=termino)
            | Q(codigo_tramo__icontains=termino)
        )
    if tipo:
        queryset = queryset.filter(tipo_trazado__iexact=tipo)
    if estado:
        queryset = queryset.filter(estado__iexact=estado)
    if ruta:
        queryset = queryset.filter(ruta__nombre=ruta)
    if site:
        queryset = queryset.filter(
            Q(hub_site__iexact=site)
            | Q(origen__iexact=site)
            | Q(destino__iexact=site)
            | Q(origen_nodo__codigo__iexact=site)
            | Q(origen_nodo__nombre__iexact=site)
            | Q(destino_nodo__codigo__iexact=site)
            | Q(destino_nodo__nombre__iexact=site)
        )
    return queryset.order_by("ruta__nombre", "tramo_secuencia", "id")


def _serializar_tramos(tramos):
    tramos = list(tramos)
    capacidades = resumen_tramos(tramos)
    resultado = []
    for tramo in tramos:
        resumen = capacidades[tramo.pk]
        origen_nodo = getattr(tramo, "origen_nodo", None)
        destino_nodo = getattr(tramo, "destino_nodo", None)
        resultado.append({
            "id": tramo.pk,
            "ruta_id": tramo.ruta_id,
            "troncal": tramo.ruta.nombre,
            "secuencia": tramo.tramo_secuencia,
            "origen": _texto(
                (
                    origen_nodo.nombre or origen_nodo.codigo
                    if origen_nodo
                    else tramo.origen or tramo.hub_site
                )
            ),
            "destino": _texto(
                (
                    destino_nodo.nombre or destino_nodo.codigo
                    if destino_nodo
                    else tramo.destino
                )
            ),
            "tipo": _texto(tramo.tipo_trazado, "Sin clasificar"),
            "distancia_km": round((tramo.distancia_m or 0) / 1000, 2),
            "capacidad": (
                texto_capacidad(
                    minima=resumen["capacidad_hilos"],
                    maxima=resumen["capacidad_hilos"],
                )
                if resumen["capacidad_hilos"] is not None
                else _texto(tramo.capacidad)
            ),
            "ocupados": resumen["ocupados"],
            "libres": resumen["libres"],
            "reservas_m": tramo.reservas_m or 0,
            "estado": _texto(tramo.estado, "Sin estado"),
            "codigo_tramo": getattr(tramo, "codigo_tramo", None),
            "capacidad_hilos": resumen["capacidad_hilos"],
            "reservados": resumen["reservados"],
            "origen_tipo": origen_nodo.tipo if origen_nodo else None,
            "origen_codigo": origen_nodo.codigo if origen_nodo else None,
            "destino_tipo": destino_nodo.tipo if destino_nodo else None,
            "destino_codigo": destino_nodo.codigo if destino_nodo else None,
            "detalle_fibras_completo": resumen["detalle_fibras_completo"],
            "fuente_conteo": resumen["fuente_conteo"],
            "sin_inventariar": resumen["sin_inventariar"],
            "conteos_consistentes": resumen["conteos_consistentes"],
            "exceso": resumen["exceso"],
            "detail_url": reverse("asset_360", args=["troncal", tramo.ruta_id]),
        })
    return resultado


def _resumen_tramos(queryset):
    tramos = list(queryset)
    capacidades = resumen_tramos(tramos)
    resultado = {
        "total": len(tramos),
        "distancia_km": round(
            sum(tramo.distancia_m or 0 for tramo in tramos) / 1000,
            2,
        ),
        "aereos": sum(
            1
            for tramo in tramos
            if (tramo.tipo_trazado or "").strip().casefold() == "aereo"
        ),
        "soterrados": sum(
            1
            for tramo in tramos
            if (tramo.tipo_trazado or "").strip().casefold() == "soterrado"
        ),
        "reservas_m": round(
            sum(tramo.reservas_m or 0 for tramo in tramos),
            2,
        ),
        "ocupados": sum(item["ocupados"] for item in capacidades.values()),
        "reservados": sum(item["reservados"] for item in capacidades.values()),
        "libres": sum(item["libres"] for item in capacidades.values()),
        "sin_inventariar": sum(
            item["sin_inventariar"] for item in capacidades.values()
        ),
        "conteos_consistentes": all(
            item["conteos_consistentes"] for item in capacidades.values()
        ),
        "exceso": sum(item["exceso"] for item in capacidades.values()),
    }
    ranking = []
    for tramo in tramos:
        resumen = capacidades[tramo.pk]
        ocupados = resumen["ocupados"]
        reservados = resumen["reservados"]
        libres = resumen["libres"]
        total = resumen["capacidad_hilos"]
        if total is None:
            total = ocupados + reservados + libres
        if not total:
            continue
        ranking.append({
            "id": tramo.ruta_id,
            "nombre": "{} · {}".format(
                tramo.ruta.nombre,
                getattr(tramo, "codigo_tramo", None)
                or f"Tramo {tramo.tramo_secuencia}",
            ),
            "total": total,
            "utilizados": ocupados + reservados,
            "utilizacion": round(
                min(ocupados + reservados, total) / total * 100,
                1,
            ),
        })
    ranking.sort(
        key=lambda fila: (fila["utilizacion"], fila["utilizados"], fila["nombre"]),
        reverse=True,
    )
    resultado["top_ocupacion"] = ranking[:5]
    return resultado


def _anotar_cobertura_fibras(queryset):
    tramos_ruta = (
        InventarioTramo.objects.filter(ruta_id=OuterRef("ruta_id"))
        .order_by()
        .values("ruta_id")
        .annotate(total=Count("pk"))
        .values("total")[:1]
    )
    tramos_asignados = (
        FibraTramo.objects.filter(fibra_id=OuterRef("pk"))
        .order_by()
        .values("fibra_id")
        .annotate(total=Count("tramo_id", distinct=True))
        .values("total")[:1]
    )
    detalle_ruta = (
        FibraTramo.objects.filter(tramo__ruta_id=OuterRef("ruta_id"))
        .order_by()
        .values("tramo__ruta_id")
        .annotate(total=Count("pk"))
        .values("total")[:1]
    )
    return queryset.annotate(
        _tramos_ruta=Coalesce(
            Subquery(tramos_ruta, output_field=IntegerField()),
            Value(0),
        ),
        _tramos_asignados=Coalesce(
            Subquery(tramos_asignados, output_field=IntegerField()),
            Value(0),
        ),
        _detalle_ruta=Coalesce(
            Subquery(detalle_ruta, output_field=IntegerField()),
            Value(0),
        ),
    )


def _filtro_cobertura_completa():
    return Q(
        _tramos_ruta__gt=0,
        _tramos_asignados=F("_tramos_ruta"),
    )


def _filtro_fibras(request):
    queryset = _anotar_cobertura_fibras(
        InventarioFibra.objects.select_related("ruta").prefetch_related(
            Prefetch(
                "ruta__tramos_inventario",
                queryset=InventarioTramo.objects.select_related(
                    "origen_nodo__hub_site_obj",
                    "origen_nodo__odf_obj__rack_obj__sala__hub_site",
                    "destino_nodo__hub_site_obj",
                    "destino_nodo__odf_obj__rack_obj__sala__hub_site",
                ).order_by("tramo_secuencia", "id"),
            ),
            "asignaciones_tramo__tramo",
            Prefetch(
                "terminaciones",
                queryset=TerminacionFibra.objects.select_related(
                    "puerto_odf__odf_obj__rack_obj__sala__hub_site"
                ).order_by("extremo"),
                to_attr="terminaciones_oficiales",
            ),
        )
    )
    termino = request.GET.get("q", "").strip()[:150]
    estado = request.GET.get("estado", "").strip()
    ruta = request.GET.get("ruta", "").strip()[:150]
    ruta_pendiente = request.GET.get("ruta_pendiente", "").strip().casefold()
    site = request.GET.get("site", "").strip()[:150]
    if termino:
        queryset = queryset.filter(
            Q(ruta__nombre__icontains=termino)
            | Q(fibra_numero__icontains=termino)
            | Q(nombre_fibra__icontains=termino)
            | Q(terminaciones__puerto_odf__puerto_odf__icontains=termino)
            | Q(terminaciones__puerto_odf__odf_obj__odf__icontains=termino)
            | Q(terminaciones__puerto_odf__odf_obj__rack_obj__sala__hub_site__nombre__icontains=termino)
            | Q(origen_odf__icontains=termino)
            | Q(destino__icontains=termino)
            | Q(ruta__tramos_inventario__hub_site__icontains=termino)
            | Q(ruta__tramos_inventario__origen__icontains=termino)
            | Q(ruta__tramos_inventario__destino__icontains=termino)
            | Q(ruta__tramos_inventario__origen_nodo__nombre__icontains=termino)
            | Q(ruta__tramos_inventario__origen_nodo__codigo__icontains=termino)
            | Q(ruta__tramos_inventario__destino_nodo__nombre__icontains=termino)
            | Q(ruta__tramos_inventario__destino_nodo__codigo__icontains=termino)
        ).distinct()
    if estado in {"Libre", "Ocupado", "Reservado", "Desconocido"}:
        queryset = queryset.filter(estado=estado)
    if ruta:
        queryset = queryset.filter(ruta__nombre=ruta)
    elif ruta_pendiente in {"1", "true", "si", "sí"}:
        queryset = queryset.filter(ruta__isnull=True)
    if site:
        queryset = queryset.filter(
            Q(terminaciones__puerto_odf__odf_obj__rack_obj__sala__hub_site__nombre__iexact=site)
            | Q(ruta__tramos_inventario__hub_site__iexact=site)
            | Q(ruta__tramos_inventario__origen__iexact=site)
            | Q(ruta__tramos_inventario__destino__iexact=site)
            | Q(ruta__tramos_inventario__origen_nodo__codigo__iexact=site)
            | Q(ruta__tramos_inventario__origen_nodo__nombre__iexact=site)
            | Q(ruta__tramos_inventario__destino_nodo__codigo__iexact=site)
            | Q(ruta__tramos_inventario__destino_nodo__nombre__iexact=site)
        ).distinct()
    patron_fibra_numerica = r"^[Ff][0-9]+$"
    return queryset.annotate(
        _orden_fibra_tipo=Case(
            When(
                fibra_numero__regex=patron_fibra_numerica,
                then=Value(0),
            ),
            default=Value(1),
            output_field=IntegerField(),
        ),
        _orden_fibra_numero=Case(
            When(
                fibra_numero__regex=patron_fibra_numerica,
                then=Cast(
                    Substr("fibra_numero", 2),
                    output_field=DecimalField(
                        max_digits=50,
                        decimal_places=0,
                    ),
                ),
            ),
            default=Value(0),
            output_field=DecimalField(max_digits=50, decimal_places=0),
        ),
        _orden_fibra_codigo=Lower("fibra_numero"),
    ).order_by(
        "ruta__nombre",
        "_orden_fibra_tipo",
        "_orden_fibra_numero",
        "_orden_fibra_codigo",
        "id",
    )


def _origenes_troncales(ids_ruta):
    origenes = {}
    tramos = InventarioTramo.objects.filter(
        ruta_id__in=ids_ruta, tramo_secuencia=1
    ).values(
        "ruta_id",
        "odf_nombre",
        "hub_site",
        "origen",
        "origen_nodo__nombre",
        "origen_nodo__codigo",
    )
    for tramo in tramos:
        origenes[tramo["ruta_id"]] = next(
            (
                valor
                for valor in (
                    tramo["odf_nombre"],
                    tramo["hub_site"],
                    tramo["origen_nodo__nombre"],
                    tramo["origen_nodo__codigo"],
                    tramo["origen"],
                )
                if valor and valor.strip() and valor != "N/A"
            ),
            "—",
        )
    return origenes


def _nombre_site_extremo(tramo, extremo):
    if not tramo:
        return "—"
    candidatos = []
    if extremo == "origen":
        candidatos.append(tramo.hub_site)
    nodo = getattr(tramo, f"{extremo}_nodo", None)
    if nodo and nodo.tipo == "SITE":
        candidatos.extend((nodo.nombre, nodo.codigo))
    candidatos.append(getattr(tramo, extremo, None))
    return _texto(next(
        (
            valor for valor in candidatos
            if valor and str(valor).strip() and str(valor).strip() != "N/A"
        ),
        None,
    ))


def _sites_extremos_troncales(fibras):
    extremos = {}
    for fibra in fibras:
        if fibra.ruta_id in extremos:
            continue
        if not fibra.ruta_id:
            extremos[None] = {
                "site_inicial": "—",
                "site_final": "—",
            }
            continue
        tramos = sorted(
            fibra.ruta.tramos_inventario.all(),
            key=lambda tramo: (tramo.tramo_secuencia, tramo.pk),
        )
        primero = tramos[0] if tramos else None
        ultimo = tramos[-1] if tramos else None
        extremos[fibra.ruta_id] = {
            "site_inicial": _nombre_site_extremo(primero, "origen"),
            "site_final": _nombre_site_extremo(ultimo, "destino"),
        }
    return extremos


def _cobertura_completa_fibra(fibra):
    if not fibra.ruta_id:
        return False
    asignaciones = list(fibra.asignaciones_tramo.all())
    tramos_asignados = {item.tramo_id for item in asignaciones}
    tramos_requeridos = {
        tramo.pk for tramo in fibra.ruta.tramos_inventario.all()
    }
    total_tramos = getattr(fibra, "_tramos_ruta", len(tramos_requeridos))
    total_asignados = getattr(
        fibra,
        "_tramos_asignados",
        len(tramos_asignados),
    )
    detalle_ruta = getattr(
        fibra,
        "_detalle_ruta",
        len(asignaciones),
    )
    return bool(total_tramos > 0 and total_asignados == total_tramos)


def _serializar_fibras(fibras):
    fibras = list(fibras)
    resultado = []
    for fibra in fibras:
        asignaciones = list(fibra.asignaciones_tramo.all())
        asignaciones.sort(
            key=lambda item: (
                item.tramo.tramo_secuencia,
                item.tramo_id,
                item.pk,
            )
        )
        tramos_asignados = {item.tramo_id for item in asignaciones}
        cobertura_completa = _cobertura_completa_fibra(fibra)
        codigos = list(dict.fromkeys(
            item.tramo.codigo_tramo
            for item in asignaciones
            if item.tramo.codigo_tramo
        ))
        trazabilidad = obtener_trazabilidad_fibra(fibra)
        calidad = evaluar_completitud_fibra(fibra)
        presentacion = obtener_presentacion_orientada_fibra(fibra)
        origen = presentacion["origen"]
        destino = presentacion["destino"]

        def etiqueta(item):
            if item is None:
                return "Pendiente de orientación"
            return f'{item["odf"]} / Puerto {item["puerto"]}'

        resultado.append({
            "id": fibra.pk,
            "ruta_id": fibra.ruta_id,
            "troncal": fibra.nombre_troncal,
            "troncal_pendiente": fibra.es_provisional,
            "numero": _texto(fibra.fibra_numero),
            "estado": _texto(fibra.estado),
            "origen_estado": _texto(fibra.origen_estado),
            "condicion_fisica": _texto(fibra.condicion_fisica),
            "servicio": _texto(fibra.nombre_fibra),
            "observaciones": _texto(fibra.observaciones),
            "site_inicial": presentacion["site_origen"] or "—",
            "site_final": presentacion["site_destino"] or "—",
            "origen": etiqueta(origen),
            "destino": etiqueta(destino),
            "terminacion_a": presentacion["terminacion_a"],
            "terminacion_b": presentacion["terminacion_b"],
            "orientacion": presentacion,
            "trazabilidad": trazabilidad,
            "calidad": calidad,
            "completitud": calidad["etiqueta"],
            "conector": _texto(fibra.tipo_conector),
            "tramos_total": len(tramos_asignados),
            "codigos_tramo": codigos,
            "cobertura_completa": cobertura_completa,
            "disponibilidad_confirmada": bool(
                (fibra.estado or "").strip().casefold() != "libre"
                or cobertura_completa
            ),
            "detail_url": reverse("asset_360", args=["fibra", fibra.pk]),
        })
    return resultado


def _resumen_fibras(queryset):
    filas = list(
        _anotar_cobertura_fibras(queryset)
        .order_by()
        .values(
            "pk",
            "ruta_id",
            "ruta__nombre",
            "estado",
            "_tramos_ruta",
            "_tramos_asignados",
            "_detalle_ruta",
        )
    )
    resumen = {
        "total": len(filas),
        "libres": 0,
        "ocupadas": 0,
        "reservadas": 0,
        "sin_estado": 0,
        "sin_cobertura": 0,
        "troncales": len({fila["ruta_id"] for fila in filas if fila["ruta_id"]}),
        "pendientes_troncal": sum(fila["ruta_id"] is None for fila in filas),
    }
    por_ruta = {}
    for fila in filas:
        cobertura_completa = bool(
            (
                fila["_tramos_ruta"] > 0
                and fila["_tramos_asignados"] == fila["_tramos_ruta"]
            )
            or (
                fila["_tramos_ruta"] == 1
                and fila["_detalle_ruta"] == 0
            )
        )
        estado = str(fila["estado"] or "").strip().casefold()
        if not cobertura_completa:
            resumen["sin_cobertura"] += 1
        if estado == "libre" and cobertura_completa:
            resumen["libres"] += 1
        elif estado in {"ocupado", "ocupada"}:
            resumen["ocupadas"] += 1
        elif estado in {"reservado", "reservada"}:
            resumen["reservadas"] += 1
        else:
            resumen["sin_estado"] += 1

        if fila["ruta_id"] is None:
            continue
        ruta = por_ruta.setdefault(
            fila["ruta_id"],
            {
                "id": fila["ruta_id"],
                "nombre": fila["ruta__nombre"],
                "total": 0,
                "ocupadas": 0,
                "reservadas": 0,
                "libres": 0,
                "sin_cobertura": 0,
            },
        )
        ruta["total"] += 1
        if not cobertura_completa:
            ruta["sin_cobertura"] += 1
        if estado == "libre" and cobertura_completa:
            ruta["libres"] += 1
        elif estado in {"ocupado", "ocupada"}:
            ruta["ocupadas"] += 1
        elif estado in {"reservado", "reservada"}:
            ruta["reservadas"] += 1

    ranking = list(por_ruta.values())
    for fila in ranking:
        utilizados = fila["ocupadas"] + fila["reservadas"]
        fila["utilizacion"] = (
            round(utilizados / fila["total"] * 100, 1)
            if fila["total"]
            else 0
        )
    ranking.sort(
        key=lambda fila: (
            fila["utilizacion"],
            fila["ocupadas"],
            fila["reservadas"],
            fila["nombre"],
        ),
        reverse=True,
    )
    resumen["top_troncales"] = ranking[:5]
    return resumen


def _filtro_elementos(request):
    queryset = Reserva.objects.select_related("ruta", "tramo")
    termino = request.GET.get("q", "").strip()[:150]
    tipo = request.GET.get("tipo", "").strip()[:50]
    ruta = request.GET.get("ruta", "").strip()[:150]
    site = request.GET.get("site", "").strip()[:150]
    if termino:
        queryset = queryset.filter(
            Q(ruta__nombre__icontains=termino)
            | Q(nombre__icontains=termino)
            | Q(codigo__icontains=termino)
            | Q(tipo__icontains=termino)
        )
    if tipo:
        queryset = queryset.filter(tipo__iexact=tipo)
    if ruta:
        queryset = queryset.filter(ruta__nombre=ruta)
    if site:
        queryset = queryset.filter(
            Q(ruta__tramos_inventario__hub_site__iexact=site)
            | Q(ruta__tramos_inventario__origen__iexact=site)
            | Q(ruta__tramos_inventario__destino__iexact=site)
            | Q(ruta__tramos_inventario__origen_nodo__codigo__iexact=site)
            | Q(ruta__tramos_inventario__origen_nodo__nombre__iexact=site)
            | Q(ruta__tramos_inventario__destino_nodo__codigo__iexact=site)
            | Q(ruta__tramos_inventario__destino_nodo__nombre__iexact=site)
        ).distinct()
    return queryset.order_by("ruta__nombre", "orden_en_ruta", "nombre", "id")


def _serializar_elementos(elementos):
    return [
        {
            "id": elemento.pk,
            "ruta_id": elemento.ruta_id,
            "troncal": elemento.ruta.nombre,
            "nombre": _texto(elemento.nombre),
            "tipo": _texto(elemento.tipo),
            "reserva_m": elemento.reserva_m,
            "latitud": str(elemento.latitud),
            "longitud": str(elemento.longitud),
            "tramo": elemento.tramo.tramo_secuencia if elemento.tramo else None,
            "estado": _texto(elemento.estado),
            "detail_url": reverse("asset_360", args=["elemento", elemento.pk]),
        }
        for elemento in elementos
    ]


def _resumen_elementos(queryset):
    resumen = queryset.aggregate(
        total=Count("id"),
        reserva_m=Sum("reserva_m"),
        tipos=Count("tipo", distinct=True),
        troncales=Count("ruta_id", distinct=True),
        por_confirmar=Count("id", filter=Q(estado__iexact="POR_CONFIRMAR")),
    )
    resumen["reserva_m"] = round(resumen["reserva_m"] or 0, 2)
    resumen = {clave: valor or 0 for clave, valor in resumen.items()}
    resumen["tipos_detalle"] = [
        {
            "tipo": fila["tipo"] or "SIN CLASIFICAR",
            "total": fila["total"],
            "reserva_m": round(fila["reserva_m"] or 0, 2),
        }
        for fila in queryset.values("tipo").annotate(
            total=Count("id"),
            reserva_m=Sum("reserva_m"),
        ).order_by("-total", "tipo")[:6]
    ]
    return resumen


@login_required
@permission_required("mapas.view_detallepuertoodf", raise_exception=True)
def api_puertos_paginados(request):
    queryset = _filtro_puertos(request)
    response = _pagina(request, queryset, _serializar_puertos)
    payload = json.loads(response.content)
    payload["summary"] = _resumen_puertos(queryset)
    return JsonResponse(payload)


@login_required
@permission_required("mapas.view_inventarioodf", raise_exception=True)
def api_odfs_paginados(request):
    queryset = _filtro_odfs(request)
    response = _pagina(request, queryset, _serializar_odfs)
    payload = json.loads(response.content)
    payload["summary"] = _resumen_odfs(queryset)
    return JsonResponse(payload)


@login_required
@permission_required("mapas.view_inventarioodf", raise_exception=True)
def api_mapa_site_navigation(request, pk):
    """Entrega el resumen y los ODF de un Site para la navegación del mapa."""
    site = get_object_or_404(HubSite, pk=pk)
    odfs = InventarioODF.objects.filter(
        rack_obj__sala__hub_site=site
    ).order_by("sala", "rack", "odf", "id")
    resumen = _resumen_odfs(odfs)
    limite = 100
    troncales = Ruta.objects.filter(
        Q(tramos_inventario__hub_site__iexact=site.nombre)
        | Q(tramos_inventario__origen__iexact=site.nombre)
        | Q(tramos_inventario__destino__iexact=site.nombre)
    ).order_by("nombre").distinct()

    return JsonResponse({
        "status": "success",
        "site": {
            "id": site.pk,
            "nombre": site.nombre,
            "direccion": _texto(site.direccion),
            "latitud": float(site.latitud) if site.latitud is not None else None,
            "longitud": float(site.longitud) if site.longitud is not None else None,
            "salas": site.salas.count(),
            "racks": RackFisico.objects.filter(sala__hub_site=site).count(),
            "troncales": troncales.count(),
            "detail_url": reverse("asset_360", args=["site", site.pk]),
            "odfs_url": f'{reverse("inventario_interno")}?{urlencode({"site": site.nombre})}',
            "ports_url": f'{reverse("planta_interna")}?{urlencode({"site": site.nombre})}',
        },
        "summary": resumen,
        "odfs": _serializar_odfs(list(odfs[:limite])),
        "truncated": resumen["total"] > limite,
        "permissions": {
            "view_ports": request.user.has_perm("mapas.view_detallepuertoodf"),
            "view_fibers": request.user.has_perm("mapas.view_inventariofibra"),
        },
    })


@login_required
@permission_required("mapas.view_ruta", raise_exception=True)
def api_troncales_paginadas(request):
    queryset = _filtro_troncales(request)
    response = _pagina(request, queryset, _serializar_troncales)
    payload = json.loads(response.content)
    payload["summary"] = _resumen_troncales(queryset)
    return JsonResponse(payload)


@login_required
@permission_required("mapas.view_ruta", raise_exception=True)
def api_tramos_paginados(request):
    queryset = _filtro_tramos(request)
    response = _pagina(request, queryset, _serializar_tramos)
    payload = json.loads(response.content)
    payload["summary"] = _resumen_tramos(queryset)
    return JsonResponse(payload)


@login_required
@permission_required("mapas.view_inventariofibra", raise_exception=True)
def api_fibras_paginadas(request):
    queryset = _filtro_fibras(request)
    response = _pagina(request, queryset, _serializar_fibras)
    payload = json.loads(response.content)
    payload["summary"] = _resumen_fibras(queryset)
    return JsonResponse(payload)


@login_required
@permission_required("mapas.view_reserva", raise_exception=True)
def api_elementos_paginados(request):
    queryset = _filtro_elementos(request)
    response = _pagina(request, queryset, _serializar_elementos)
    payload = json.loads(response.content)
    payload["summary"] = _resumen_elementos(queryset)
    return JsonResponse(payload)


@login_required
@permission_required("mapas.view_ruta", raise_exception=True)
def api_panel_troncal(request):
    """Detalle contextual de una troncal, solicitado únicamente al seleccionarla."""
    ruta_id = request.GET.get("ruta_id", "").strip()
    if not ruta_id.isdigit():
        return JsonResponse(
            {"status": "error", "message": "Troncal no válida."},
            status=400,
        )
    ruta = get_object_or_404(Ruta, pk=ruta_id)
    fibras = InventarioFibra.objects.filter(ruta=ruta)
    tramos = InventarioTramo.objects.filter(ruta=ruta).order_by("tramo_secuencia")
    reservas = Reserva.objects.filter(ruta=ruta).order_by(
        "orden_en_ruta", "nombre", "id"
    )
    coordenadas = list(
        CoordenadaRuta.objects.filter(ruta=ruta)
        .order_by("orden")
        .values_list("latitud", "longitud")
    )
    if len(coordenadas) > 500:
        ultimo = len(coordenadas) - 1
        coordenadas = [
            coordenadas[round(indice * ultimo / 499)]
            for indice in range(500)
        ]

    resumen_fibras = _resumen_fibras(fibras)
    extremos = list(
        tramos.values(
            "tramo_secuencia", "origen", "destino", "hub_site",
            "odf_nombre", "tipo_trazado",
        )
    )
    distancia_m = tramos.aggregate(total=Sum("distancia_m"))["total"] or 0
    reservas_m = reservas.aggregate(total=Sum("reserva_m"))["total"] or 0

    return JsonResponse({
        "status": "success",
        "ruta": {
            "id": ruta.pk,
            "nombre": ruta.nombre,
            "distancia_km": round(distancia_m / 1000, 2),
            "tramos": len(extremos),
            "tipos_trazado": sorted({
                tramo["tipo_trazado"]
                for tramo in extremos
                if tramo["tipo_trazado"]
            }),
            "origen": _texto(
                extremos[0]["odf_nombre"]
                or extremos[0]["hub_site"]
                or extremos[0]["origen"]
            ) if extremos else "—",
            "destino": _texto(extremos[-1]["destino"]) if extremos else "—",
            "mapa_url": f'{reverse("mapa_inventario")}?{urlencode({"ruta": ruta.nombre})}',
        },
        "fibras": {
            "total": resumen_fibras["total"],
            "libres": resumen_fibras["libres"],
            "ocupadas": resumen_fibras["ocupadas"],
            "reservadas": resumen_fibras["reservadas"],
            "utilizacion": round(
                (
                    (resumen_fibras["ocupadas"] + resumen_fibras["reservadas"])
                    / resumen_fibras["total"]
                ) * 100,
                1,
            ) if resumen_fibras["total"] else 0,
        },
        "reservas": {
            "elementos": reservas.count(),
            "reserva_m": round(reservas_m, 2),
        },
        "coordenadas": [
            [float(latitud), float(longitud)]
            for latitud, longitud in coordenadas
        ],
        "puntos": [
            {
                "nombre": reserva.nombre,
                "tipo": _texto(reserva.tipo, "SIN CLASIFICAR"),
                "latitud": float(reserva.latitud),
                "longitud": float(reserva.longitud),
                "reserva_m": reserva.reserva_m or 0,
            }
            for reserva in reservas[:100]
        ],
    })


def _numero_no_negativo(data, clave, entero=False):
    valor = data.get(clave)
    if valor in (None, ""):
        return 0 if entero else 0.0
    numero = int(valor) if entero else float(valor)
    if numero < 0:
        raise ValueError(f"{clave} no puede ser negativo")
    return numero


def _capacidad_hilos_manual(data):
    valor = data.get("capacidad_hilos")
    if valor not in (None, ""):
        try:
            numero = int(valor)
        except (TypeError, ValueError) as exc:
            raise ValueError("capacidad_hilos debe ser un número entero") from exc
        if numero < 1:
            raise ValueError("capacidad_hilos debe ser mayor que cero")
        return numero

    texto_legacy = str(data.get("capacidad", "") or "").strip()
    coincidencia = re.search(r"\d+(?:[.,]\d+)?", texto_legacy)
    if not coincidencia:
        return None
    numero = float(coincidencia.group().replace(",", "."))
    if not numero.is_integer() or numero < 1:
        raise ValueError("La capacidad debe indicar una cantidad entera mayor que cero")
    return int(numero)


def _entero_opcional_no_negativo(data, clave):
    valor = data.get(clave)
    if valor in (None, ""):
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{clave} debe ser un número entero") from exc
    if numero < 0:
        raise ValueError(f"{clave} no puede ser negativo")
    return numero


def _codigo_extremo(data, extremo, fallback=""):
    codigo = str(data.get(f"{extremo}_codigo", "") or "").strip()
    if not codigo:
        codigo = str(data.get(extremo, "") or fallback or "").strip()
    if codigo.casefold() in {
        "-", "—", "nan", "none", "null", "n/a", "sin dato", "sin_dato",
    }:
        return ""
    return codigo


def _resolver_extremo_manual(data, extremo, fallback=""):
    codigo = _codigo_extremo(data, extremo, fallback)
    if not codigo:
        return None
    tipo = str(data.get(f"{extremo}_tipo", "") or "").strip()
    if not tipo:
        tipo = inferir_tipo_nodo(codigo)
    nombre = str(data.get(f"{extremo}_nombre", "") or "").strip() or None
    return resolver_nodo(tipo=tipo, codigo=codigo, nombre=nombre)


def _nodo_extremo_existente(tramo, extremo):
    if tramo is None:
        return None
    return getattr(tramo, f"{extremo}_nodo", None)


def _validar_continuidad_manual(
    *,
    anterior,
    siguiente,
    origen_nodo,
    destino_nodo,
):
    destino_anterior = _nodo_extremo_existente(anterior, "destino")
    origen_siguiente = _nodo_extremo_existente(siguiente, "origen")
    if (
        destino_anterior is not None
        and origen_nodo is not None
        and destino_anterior.pk != origen_nodo.pk
    ):
        raise ValidationError(
            "El origen del nuevo tramo debe coincidir con el destino "
            f"del tramo anterior ({destino_anterior.codigo})."
        )
    if (
        origen_siguiente is not None
        and destino_nodo is not None
        and origen_siguiente.pk != destino_nodo.pk
    ):
        raise ValidationError(
            "El destino del nuevo tramo debe coincidir con el origen "
            f"del tramo siguiente ({origen_siguiente.codigo})."
        )
    return (
        origen_nodo or destino_anterior,
        destino_nodo or origen_siguiente,
    )


def _mensaje_validacion(exc):
    if hasattr(exc, "message_dict"):
        mensajes = [
            mensaje
            for lista in exc.message_dict.values()
            for mensaje in lista
        ]
        return " ".join(mensajes)
    if hasattr(exc, "messages"):
        return " ".join(exc.messages)
    return str(exc)


@login_required
@permission_required("mapas.add_inventariotramo", raise_exception=True)
def create_tramo_manual(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Método no permitido"}, status=405)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError("El cuerpo de la solicitud debe ser un objeto JSON")
        secuencia = _numero_no_negativo(data, "tramo_secuencia", entero=True)
        if secuencia < 1:
            raise ValueError("La secuencia debe ser mayor que cero")
        capacidad_numerica = _capacidad_hilos_manual(data)
        hilos_reservados = _entero_opcional_no_negativo(
            data,
            "hilos_reservados",
        )
        with transaction.atomic():
            ruta = (
                Ruta.objects.select_for_update()
                .filter(pk=data.get("ruta_id"))
                .first()
            )
            if not ruta:
                return JsonResponse(
                    {"status": "error", "message": "Troncal no encontrada"},
                    status=404,
                )
            tramos = list(
                InventarioTramo.objects.select_for_update()
                .select_related("origen_nodo", "destino_nodo")
                .filter(ruta=ruta)
                .order_by("tramo_secuencia", "pk")
            )
            if any(item.tramo_secuencia == secuencia for item in tramos):
                raise ValidationError(
                    f"Ya existe el tramo {secuencia} en esta troncal."
                )
            secuencias_finales = sorted(
                [item.tramo_secuencia for item in tramos] + [secuencia]
            )
            if secuencias_finales != list(
                range(1, len(secuencias_finales) + 1)
            ):
                raise ValidationError(
                    "Los tramos deben mantener secuencias consecutivas "
                    "desde 1, sin saltos."
                )

            anterior = next(
                (
                    item
                    for item in reversed(tramos)
                    if item.tramo_secuencia < secuencia
                ),
                None,
            )
            siguiente = next(
                (
                    item
                    for item in tramos
                    if item.tramo_secuencia > secuencia
                ),
                None,
            )
            origen_legacy = str(
                data.get("origen", "") or data.get("hub_site", "") or ""
            ).strip()
            destino_legacy = str(data.get("destino", "") or "").strip()
            origen_nodo = _resolver_extremo_manual(
                data,
                "origen",
                origen_legacy,
            )
            destino_nodo = _resolver_extremo_manual(
                data,
                "destino",
                destino_legacy,
            )
            origen_nodo, destino_nodo = _validar_continuidad_manual(
                anterior=anterior,
                siguiente=siguiente,
                origen_nodo=origen_nodo,
                destino_nodo=destino_nodo,
            )

            codigo_tramo = str(
                data.get("codigo_tramo") or codigo_tramo_automatico(secuencia)
            ).strip().upper()
            if not codigo_tramo:
                raise ValidationError("El código del tramo es obligatorio.")

            tramo = InventarioTramo(
                ruta=ruta,
                tramo_secuencia=secuencia,
                codigo_tramo=codigo_tramo,
                origen_nodo=origen_nodo,
                destino_nodo=destino_nodo,
                tipo_trazado=str(data.get("tipo_trazado", "")).strip(),
                estado=str(data.get("estado", "")).strip(),
                distancia_m=_numero_no_negativo(data, "distancia_km") * 1000,
                reservas_m=_numero_no_negativo(data, "reservas_m"),
                mufas=_numero_no_negativo(data, "mufas", entero=True),
                splitters=_numero_no_negativo(data, "splitters", entero=True),
                capacidad=(
                    str(data.get("capacidad", "") or "").strip()
                    or (
                        f"{capacidad_numerica} Hilos"
                        if capacidad_numerica is not None
                        else ""
                    )
                ),
                capacidad_hilos=capacidad_numerica,
                tipo_fibra=str(data.get("tipo_fibra", "")).strip(),
                origen=origen_legacy or (
                    origen_nodo.nombre or origen_nodo.codigo
                    if origen_nodo
                    else ""
                ),
                destino=destino_legacy or (
                    destino_nodo.nombre or destino_nodo.codigo
                    if destino_nodo
                    else ""
                ),
                hub_site=str(data.get("hub_site", "")).strip(),
                odf_nombre=str(data.get("odf_nombre", "")).strip(),
                hilos_reservados=hilos_reservados,
                observaciones=str(data.get("observaciones", "") or "").strip(),
            )
            tramo.full_clean()
            tramo.save()
        return JsonResponse(
            {
                "status": "success",
                "message": (
                    f"Tramo {tramo.tramo_secuencia} registrado correctamente"
                ),
                "id": tramo.pk,
                "codigo_tramo": tramo.codigo_tramo,
            }
        )
    except (
        IntegrityError,
        TypeError,
        ValueError,
        ValidationError,
        json.JSONDecodeError,
    ) as exc:
        return JsonResponse(
            {"status": "error", "message": _mensaje_validacion(exc)},
            status=400,
        )


class _CsvBuffer:
    def write(self, value):
        return value


def _valor_exportable(valor):
    """Evita fórmulas al abrir exportaciones con una hoja de cálculo."""
    if valor is None:
        return ""
    if isinstance(valor, str) and valor.startswith(("=", "+", "-", "@")):
        return "'" + valor
    return valor


def _csv_response(nombre, encabezados, filas: Iterable):
    writer = csv.writer(_CsvBuffer())

    def contenido():
        yield "\ufeff"
        yield writer.writerow(encabezados)
        for fila in filas:
            yield writer.writerow([_valor_exportable(valor) for valor in fila])

    response = StreamingHttpResponse(contenido(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{nombre}.csv"'
    return response


def _xlsx_response(nombre, titulo, encabezados, filas: Iterable):
    from openpyxl import Workbook

    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet(titulo)
    sheet.freeze_panes = "A2"
    sheet.append(encabezados)
    for fila in filas:
        sheet.append([_valor_exportable(valor) for valor in fila])
    buffer = io.BytesIO()
    workbook.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{nombre}.xlsx"'
    return response


def _filas_puertos(queryset):
    for puerto in queryset.iterator(chunk_size=1000):
        terminacion = next(iter(puerto.terminaciones_oficiales), None)
        fibra_oficial = (
            f"{terminacion.fibra.nombre_troncal} / "
            f"{terminacion.fibra.fibra_numero} · Extremo {terminacion.extremo}"
            if terminacion else ""
        )
        yield (
            puerto.odf_obj.hub_site,
            puerto.odf_obj.sala,
            puerto.odf_obj.rack,
            puerto.odf_obj.odf,
            puerto.bandeja,
            puerto.puerto_odf,
            fibra_oficial,
            puerto.estado_puerto,
            puerto.tipo_conector,
            puerto.patchcord,
            puerto.destino,
        )


def _filas_odfs(queryset):
    for odf in queryset.iterator(chunk_size=1000):
        yield (
            odf.hub_site,
            odf.sala,
            odf.rack,
            odf.odf,
            odf.capacidad_puertos,
            odf.puertos_ocupados,
            odf.puertos_libres,
            odf.puertos_reservados,
            odf.tipo_conector,
            odf.estado,
            odf.observaciones,
        )


def _filas_troncales(queryset):
    for lote in _iterar_lotes(queryset, tamano=500):
        for item in _serializar_troncales(lote):
            yield (
                item["nombre"], item["origen"], item["destino"], item["tipo"],
                item["distancia_km"], item["tramos"], item["capacidad"],
                item["fibras"], item["reservas"], item["estado"],
                item["capacidad_min"], item["capacidad_max"],
                item["capacidad_efectiva"], item["hilos_reservados"],
                item["fuente"], item["sin_inventariar"],
                item["hilos_sin_estado"], item["fibras_sin_cobertura"],
                item["conteos_consistentes"], item["exceso"],
            )


def _iterar_lotes(queryset, tamano=1000):
    lote = []
    for objeto in queryset.iterator(chunk_size=tamano):
        lote.append(objeto)
        if len(lote) == tamano:
            yield lote
            lote = []
    if lote:
        yield lote


def _filas_tramos(queryset):
    for lote in _iterar_lotes(queryset):
        for item in _serializar_tramos(lote):
            yield (
                item["troncal"],
                item["secuencia"],
                item["origen"],
                item["destino"],
                item["tipo"],
                item["distancia_km"],
                item["capacidad"],
                item["ocupados"],
                item["libres"],
                item["reservas_m"],
                item["estado"],
                item["codigo_tramo"],
                item["capacidad_hilos"],
                item["reservados"],
                item["origen_tipo"],
                item["origen_codigo"],
                item["destino_tipo"],
                item["destino_codigo"],
                item["detalle_fibras_completo"],
                item["fuente_conteo"],
                item["sin_inventariar"],
                item["conteos_consistentes"],
                item["exceso"],
            )


def _filas_fibras(queryset):
    for lote in _iterar_lotes(queryset):
        for item in _serializar_fibras(lote):
            yield (
                item["troncal"],
                item["numero"],
                item["estado"],
                item["origen_estado"],
                item["condicion_fisica"],
                item["servicio"],
                item["observaciones"],
                item["site_inicial"],
                item["site_final"],
                item["origen"],
                item["destino"],
                item["conector"],
                item["tramos_total"],
                " | ".join(item["codigos_tramo"]),
                "Sí" if item["cobertura_completa"] else "No",
                item["completitud"],
                "Sí" if item["calidad"]["valido"] else "No",
                " | ".join(
                    hallazgo["mensaje"]
                    for hallazgo in item["calidad"]["hallazgos"]
                ),
            )


def _filas_elementos(queryset):
    for elemento in queryset.iterator(chunk_size=1000):
        yield (
            elemento.ruta.nombre,
            elemento.nombre,
            elemento.tipo,
            elemento.reserva_m,
            elemento.latitud,
            elemento.longitud,
            elemento.tramo.tramo_secuencia if elemento.tramo else "",
            elemento.estado,
        )


@login_required
def exportar_inventario(request, recurso, formato):
    formatos = {"csv", "xlsx"}
    if formato not in formatos:
        raise Http404("Formato no disponible")

    if recurso == "troncales":
        permiso = "mapas.view_ruta"
        titulo = "Troncales"
        encabezados = (
            "Troncal", "Origen", "Destino", "Tipo", "Distancia (km)",
            "Tramos", "Capacidad", "Fibras", "Reservas", "Estado",
            "Capacidad mínima", "Capacidad máxima",
            "Capacidad efectiva", "Hilos reservados", "Fuente",
            "Sin inventariar", "Hilos sin estado",
            "Fibras sin cobertura",
            "Conteos consistentes", "Exceso",
        )
        filas = _filas_troncales(_filtro_troncales(request))
    elif recurso == "tramos":
        permiso = "mapas.view_ruta"
        titulo = "Tramos"
        encabezados = (
            "Troncal", "Secuencia", "Origen", "Destino", "Trazado",
            "Distancia (km)", "Capacidad", "Ocupados", "Libres",
            "Reserva (m)", "Estado",
            "Código tramo", "Capacidad hilos", "Reservados",
            "Origen tipo", "Origen código",
            "Destino tipo", "Destino código",
            "Detalle completo", "Fuente conteo", "Sin inventariar",
            "Conteos consistentes", "Exceso",
        )
        filas = _filas_tramos(_filtro_tramos(request))
    elif recurso == "odfs":
        permiso = "mapas.view_inventarioodf"
        titulo = "Inventario ODF"
        encabezados = (
            "Site", "Sala", "Rack", "ODF", "Capacidad", "Ocupados",
            "Libres", "Reservados", "Conector", "Estado", "Observaciones",
        )
        filas = _filas_odfs(_filtro_odfs(request))
    elif recurso == "puertos":
        permiso = "mapas.view_detallepuertoodf"
        titulo = "Puertos ODF"
        encabezados = (
            "Site", "Sala", "Rack", "ODF", "Bandeja", "Puerto", "Fibra óptica",
            "Estado", "Conector", "Patchcord", "Destino",
        )
        filas = _filas_puertos(_filtro_puertos(request))
    elif recurso == "fibras":
        permiso = "mapas.view_inventariofibra"
        titulo = "Fibras ópticas"
        encabezados = (
            "Troncal", "N.º de fibra", "Estado", "Origen estado",
            "Condición física", "Servicio", "Observaciones",
            "Site inicial", "Site final",
            "Origen ODF / puerto", "Destino ODF / puerto", "Conector",
            "Tramos asociados", "Códigos de tramo", "Cobertura completa",
            "Completitud", "Validación correcta", "Observaciones de calidad",
        )
        filas = _filas_fibras(_filtro_fibras(request))
    elif recurso == "elementos":
        permiso = "mapas.view_reserva"
        titulo = "Reservas y elementos"
        encabezados = (
            "Troncal", "Elemento", "Tipo", "Reserva (m)", "Latitud", "Longitud", "Tramo", "Estado",
        )
        filas = _filas_elementos(_filtro_elementos(request))
    else:
        raise Http404("Recurso no disponible")

    if not request.user.has_perm(permiso):
        raise PermissionDenied
    nombre = f"FiberGenius_{recurso}"
    if formato == "csv":
        return _csv_response(nombre, encabezados, filas)
    return _xlsx_response(nombre, titulo, encabezados, filas)


def _resultado(tipo, objeto_id, titulo, subtitulo):
    return {
        "type": tipo,
        "id": objeto_id,
        "title": _texto(titulo),
        "subtitle": _texto(subtitulo),
        "detail_url": reverse("asset_360", args=[tipo, objeto_id]),
    }


@login_required
def busqueda_global(request):
    termino = request.GET.get("q", "").strip()[:100]
    if len(termino) < 2:
        return JsonResponse({"status": "success", "data": []})

    resultados = []
    usuario = request.user
    if usuario.has_perm("mapas.view_ruta"):
        for ruta in Ruta.objects.filter(nombre__icontains=termino).order_by("nombre")[:6]:
            resultados.append(_resultado("troncal", ruta.pk, ruta.nombre, "Troncal"))
    if usuario.has_perm("mapas.view_inventarioodf"):
        odfs = InventarioODF.objects.filter(
            Q(odf__icontains=termino)
            | Q(hub_site__icontains=termino)
            | Q(sala__icontains=termino)
            | Q(rack__icontains=termino)
        ).order_by("odf")[:6]
        for odf in odfs:
            resultados.append(
                _resultado("odf", odf.pk, odf.odf, f"ODF · {odf.hub_site or 'Site sin especificar'}")
            )
    if usuario.has_perm("mapas.view_detallepuertoodf"):
        puertos = DetallePuertoODF.objects.select_related("odf_obj").filter(
            Q(odf_obj__odf__icontains=termino)
            | Q(puerto_odf__icontains=termino)
            | Q(terminaciones_fibra__fibra__fibra_numero__icontains=termino)
            | Q(terminaciones_fibra__fibra__ruta__nombre__icontains=termino)
            | Q(destino__icontains=termino)
        ).distinct().order_by("odf_obj__odf", "puerto_odf")[:6]
        for puerto in puertos:
            resultados.append(
                _resultado(
                    "puerto",
                    puerto.pk,
                    f"{puerto.odf_obj.odf} · Puerto {puerto.puerto_odf}",
                    f"Puerto ODF · {puerto.destino or puerto.estado_puerto}",
                )
            )
    if usuario.has_perm("mapas.view_inventariofibra"):
        fibras = InventarioFibra.objects.select_related("ruta").prefetch_related(
            "terminaciones__puerto_odf__odf_obj"
        ).filter(
            Q(ruta__nombre__icontains=termino)
            | Q(fibra_numero__icontains=termino)
            | Q(nombre_fibra__icontains=termino)
            | Q(destino__icontains=termino)
            | Q(terminaciones__puerto_odf__puerto_odf__icontains=termino)
            | Q(terminaciones__puerto_odf__odf_obj__odf__icontains=termino)
        ).distinct().order_by("ruta__nombre", "fibra_numero")[:6]
        for fibra in fibras:
            ubicaciones = [
                f"{terminacion.puerto_odf.odf_obj.odf}/P{terminacion.puerto_odf.puerto_odf}"
                for terminacion in fibra.terminaciones.all()
            ]
            contexto = " · ".join(ubicaciones) or fibra.estado
            resultados.append(
                _resultado(
                    "fibra",
                    fibra.pk,
                    f"{fibra.nombre_troncal} · Fibra {fibra.fibra_numero}",
                    f"Fibra óptica · {contexto}",
                )
            )
    if usuario.has_perm("mapas.view_hubsite"):
        for site in HubSite.objects.filter(nombre__icontains=termino).order_by("nombre")[:6]:
            resultados.append(_resultado("site", site.pk, site.nombre, "Site"))
    if usuario.has_perm("mapas.view_reserva"):
        elementos = Reserva.objects.select_related("ruta").filter(
            Q(nombre__icontains=termino)
            | Q(codigo__icontains=termino)
            | Q(tipo__icontains=termino)
        ).order_by("nombre")[:6]
        for elemento in elementos:
            resultados.append(
                _resultado(
                    "elemento",
                    elemento.pk,
                    elemento.nombre,
                    f"{elemento.tipo or 'Elemento'} · {elemento.ruta.nombre}",
                )
            )
    return JsonResponse({"status": "success", "data": resultados[:30]})


def _item(etiqueta, valor):
    return {"label": etiqueta, "value": _texto(valor)}


def _accion(etiqueta, url):
    return {"label": etiqueta, "url": url}


def _url_con_query(nombre_ruta, **parametros):
    return reverse(nombre_ruta) + "?" + urlencode(parametros)


def _ficha_troncal(pk):
    troncal = get_object_or_404(
        Ruta.objects.select_related("otu"), pk=pk
    )
    tramos = list(
        troncal.tramos_inventario.select_related(
            "origen_nodo",
            "destino_nodo",
        ).order_by("tramo_secuencia")
    )
    primero = tramos[0] if tramos else None
    ultimo = tramos[-1] if tramos else None
    capacidad = resumen_ruta(troncal)
    distancia_m = (
        capacidad["distancia_m"] or 0
    )
    capacidad_visible = (
        capacidad["capacidad"]
        if capacidad["capacidad_efectiva"] is not None
        else "—"
    )
    odfs_por_extremo = {"A": [], "B": []}
    for extremo, nombre_odf in (
        TerminacionFibra.objects.filter(fibra__ruta=troncal)
        .values_list("extremo", "puerto_odf__odf_obj__odf")
        .distinct()
        .order_by("extremo", "puerto_odf__odf_obj__odf")
    ):
        odfs_por_extremo[extremo].append(nombre_odf)

    def odfs_visibles(extremo):
        valores = odfs_por_extremo[extremo]
        return ", ".join(valores) if valores else None

    return {
        "type": "troncal",
        "title": troncal.nombre,
        "subtitle": "Ficha operativa de troncal",
        "status": _texto(primero.estado if primero else None, "Sin estado"),
        "chain": [
            _item(
                "Site de origen",
                (
                    primero.hub_site
                    or _valor_nodo(primero, "origen")
                ) if primero else None,
            ),
            _item("ODF de fibras (A)", odfs_visibles("A")),
            _item("Troncal", troncal.nombre),
            _item("Tramos", len(tramos)),
            _item("ODF de fibras (B)", odfs_visibles("B")),
            _item("Destino", _valor_nodo(ultimo, "destino")),
        ],
        "summary": [
            _item("Distancia", f"{distancia_m / 1000:.2f} km"),
            _item("Fibras", capacidad["fibras_total"]),
            _item("Fibras libres", capacidad["hilos_libres"]),
            _item("Reservas y elementos", troncal.reservas.count()),
        ],
        "sections": [
            {
                "title": "Datos técnicos",
                "items": [
                    _item("Capacidad", capacidad_visible),
                    _item("Tipo de fibra", primero.tipo_fibra if primero else None),
                    _item("Trazado", primero.tipo_trazado if primero else None),
                    _item("Marca / modelo", primero.marca_modelo if primero else None),
                ],
            }
        ],
        "actions": [
            _accion("Localizar en mapa", _url_con_query("mapa_inventario", ruta=troncal.nombre)),
            _accion("Ver troncales y tramos", _url_con_query("inventario_externo", q=troncal.nombre)),
            _accion("Ver sus fibras", _url_con_query("planta_externa", tab="fibras", q=troncal.nombre)),
        ],
    }


def _ficha_odf(pk):
    odf = get_object_or_404(
        InventarioODF.objects.select_related("rack_obj__sala__hub_site"), pk=pk
    )
    troncales = Ruta.objects.filter(
        Q(
            fibras_inventario__terminaciones__puerto_odf__odf_obj=odf,
        )
        | Q(tramos_inventario__odf_nombre__iexact=odf.odf)
        | Q(
            tramos_inventario__origen_nodo__tipo="ODF",
            tramos_inventario__origen_nodo__codigo__iexact=odf.odf,
        )
        | Q(
            tramos_inventario__origen_nodo__tipo="ODF",
            tramos_inventario__origen_nodo__nombre__iexact=odf.odf,
        )
        | Q(
            tramos_inventario__destino_nodo__tipo="ODF",
            tramos_inventario__destino_nodo__codigo__iexact=odf.odf,
        )
        | Q(
            tramos_inventario__destino_nodo__tipo="ODF",
            tramos_inventario__destino_nodo__nombre__iexact=odf.odf,
        )
    ).distinct()
    return {
        "type": "odf",
        "title": odf.odf,
        "subtitle": "Ficha operativa de ODF",
        "status": _texto(odf.estado, "Sin estado"),
        "chain": [
            _item("Site", odf.hub_site),
            _item("Sala", odf.sala),
            _item("Rack", odf.rack),
            _item("ODF", odf.odf),
        ],
        "summary": [
            _item("Capacidad", odf.capacidad_puertos),
            _item("Puertos ocupados", odf.puertos_ocupados),
            _item("Puertos libres", odf.puertos_libres),
            _item("Puertos reservados", odf.puertos_reservados),
            _item("Troncales relacionadas", troncales.count()),
        ],
        "sections": [
            {
                "title": "Ubicación y conexión",
                "items": [
                    _item("Tipo de conector", odf.tipo_conector),
                    _item("Observaciones", odf.observaciones),
                    _item("Troncales", ", ".join(troncales.values_list("nombre", flat=True)[:8])),
                ],
            }
        ],
        "actions": [
            _accion("Consultar sus puertos", _url_con_query("planta_interna", q=odf.odf)),
            _accion("Abrir inventario de ODF", _url_con_query("inventario_interno", q=odf.odf)),
        ],
    }


def _ficha_puerto(pk):
    puerto = get_object_or_404(
        DetallePuertoODF.objects.select_related("odf_obj__rack_obj__sala__hub_site"), pk=pk
    )
    terminacion = puerto.terminaciones_fibra.select_related("fibra__ruta").first()
    fibra = terminacion.fibra if terminacion else None
    return {
        "type": "puerto",
        "title": f"{puerto.odf_obj.odf} · Puerto {puerto.puerto_odf}",
        "subtitle": "Ficha operativa de puerto ODF",
        "status": puerto.estado_puerto,
        "chain": [
            _item("Site", puerto.odf_obj.hub_site),
            _item("Sala", puerto.odf_obj.sala),
            _item("Rack", puerto.odf_obj.rack),
            _item("ODF", puerto.odf_obj.odf),
            _item("Puerto", puerto.puerto_odf),
            _item("Fibra óptica", fibra.fibra_numero if fibra else None),
            _item("Destino", puerto.destino),
        ],
        "summary": [
            _item("Bandeja", puerto.bandeja),
            _item("Conector", puerto.tipo_conector),
            _item("Patchcord", puerto.patchcord),
            _item("Troncal", fibra.nombre_troncal if fibra else None),
        ],
        "sections": [
            {"title": "Observaciones", "items": [_item("Detalle", puerto.observaciones)]}
        ],
        "actions": [
            _accion(
                "Ver puertos del ODF",
                _url_con_query("planta_interna", q=puerto.odf_obj.odf),
            ),
        ],
    }


def _ficha_fibra(pk):
    fibra = get_object_or_404(
        _anotar_cobertura_fibras(
            InventarioFibra.objects.select_related("ruta").prefetch_related(
                Prefetch(
                    "ruta__tramos_inventario",
                    queryset=InventarioTramo.objects.select_related(
                        "origen_nodo__hub_site_obj",
                        "origen_nodo__odf_obj__rack_obj__sala__hub_site",
                        "destino_nodo__hub_site_obj",
                        "destino_nodo__odf_obj__rack_obj__sala__hub_site",
                    ).order_by("tramo_secuencia", "id"),
                ),
                "asignaciones_tramo__tramo",
                Prefetch(
                    "terminaciones",
                    queryset=TerminacionFibra.objects.select_related(
                        "puerto_odf__odf_obj__rack_obj__sala__hub_site"
                    ).order_by("extremo"),
                    to_attr="terminaciones_oficiales",
                ),
            )
        ),
        pk=pk,
    )
    cobertura_completa = _cobertura_completa_fibra(fibra)
    estado = fibra.estado
    trazabilidad = obtener_trazabilidad_fibra(fibra)
    calidad = evaluar_completitud_fibra(fibra)
    extremo_a = obtener_extremo_fibra(fibra, "A")
    extremo_b = obtener_extremo_fibra(fibra, "B")
    return {
        "type": "fibra",
        "title": f"{fibra.nombre_troncal} · Fibra {fibra.fibra_numero}",
        "subtitle": "Ficha operativa de fibra óptica",
        "status": estado,
        "chain": [
            _item("Troncal", fibra.nombre_troncal),
            _item("ODF / puerto A", extremo_a.etiqueta),
            _item("Fibra óptica", fibra.fibra_numero),
            _item("ODF / puerto B", extremo_b.etiqueta),
        ],
        "summary": [
            _item("Servicio", fibra.nombre_fibra),
            _item("Origen del estado", fibra.get_origen_estado_display()),
            _item("Condición física", fibra.get_condicion_fisica_display()),
            _item("Conector", fibra.tipo_conector),
            _item("Terminaciones confirmadas", trazabilidad["terminaciones_confirmadas"]),
            _item("Completitud", calidad["etiqueta"]),
        ],
        "sections": [
            {
                "title": "Calidad y validaciones",
                "items": [
                    _item(
                        "Recorrido",
                        f'{calidad["recorrido"]["tramos_asignados"]}/'
                        f'{calidad["recorrido"]["tramos_requeridos"]} tramos',
                    ),
                    _item(
                        "Terminación A",
                        "Confirmada" if calidad["terminaciones"]["extremo_a"] else "Faltante",
                    ),
                    _item(
                        "Terminación B",
                        "Confirmada" if calidad["terminaciones"]["extremo_b"] else "Faltante",
                    ),
                    *[
                        _item(hallazgo["severidad"].title(), hallazgo["mensaje"])
                        for hallazgo in calidad["hallazgos"]
                    ],
                ],
            },
            {
                "title": "Observaciones",
                "items": [_item("Detalle", fibra.observaciones)],
            },
        ],
        "actions": (
            [
                _accion("Localizar troncal", _url_con_query("mapa_inventario", ruta=fibra.ruta.nombre)),
                _accion("Ver fibras de la troncal", _url_con_query("planta_externa", tab="fibras", q=fibra.ruta.nombre)),
            ]
            if fibra.ruta_id else []
        ),
    }


def _ficha_site(pk):
    site = get_object_or_404(HubSite, pk=pk)
    odfs = InventarioODF.objects.filter(rack_obj__sala__hub_site=site)
    troncales = Ruta.objects.filter(
        Q(tramos_inventario__hub_site__iexact=site.nombre)
        | Q(tramos_inventario__origen__iexact=site.nombre)
        | Q(tramos_inventario__destino__iexact=site.nombre)
        | Q(
            tramos_inventario__origen_nodo__tipo="SITE",
            tramos_inventario__origen_nodo__codigo__iexact=site.nombre,
        )
        | Q(
            tramos_inventario__origen_nodo__tipo="SITE",
            tramos_inventario__origen_nodo__nombre__iexact=site.nombre,
        )
        | Q(
            tramos_inventario__destino_nodo__tipo="SITE",
            tramos_inventario__destino_nodo__codigo__iexact=site.nombre,
        )
        | Q(
            tramos_inventario__destino_nodo__tipo="SITE",
            tramos_inventario__destino_nodo__nombre__iexact=site.nombre,
        )
    ).order_by("nombre").distinct()
    return {
        "type": "site",
        "title": site.nombre,
        "subtitle": "Ficha operativa de site",
        "status": "Registrado",
        "chain": [
            _item("Site", site.nombre),
            _item("Salas", site.salas.count()),
            _item("Racks", RackFisico.objects.filter(sala__hub_site=site).count()),
            _item("ODF", odfs.count()),
        ],
        "summary": [
            _item("Puertos", DetallePuertoODF.objects.filter(odf_obj__in=odfs).count()),
            _item("Troncales relacionadas", troncales.count()),
            _item("Dirección", site.direccion),
        ],
        "sections": [
            {
                "title": "Troncales",
                "items": [_item("Relacionadas", ", ".join(troncales.values_list("nombre", flat=True)[:10]))],
            }
        ],
        "actions": [
            _accion("Consultar sus ODF", _url_con_query("inventario_interno", q=site.nombre)),
            _accion("Consultar sus puertos", _url_con_query("planta_interna", q=site.nombre)),
        ],
    }


def _ficha_elemento(pk):
    elemento = get_object_or_404(Reserva.objects.select_related("ruta", "tramo"), pk=pk)
    return {
        "type": "elemento",
        "title": elemento.nombre,
        "subtitle": "Ficha de reserva o elemento de planta externa",
        "status": elemento.estado,
        "chain": [
            _item("Troncal", elemento.ruta.nombre),
            _item("Tramo", elemento.tramo.tramo_secuencia if elemento.tramo else None),
            _item("Tipo", elemento.tipo),
            _item("Elemento", elemento.nombre),
        ],
        "summary": [
            _item("Reserva", f"{elemento.reserva_m} m" if elemento.reserva_m is not None else None),
            _item("Latitud", elemento.latitud),
            _item("Longitud", elemento.longitud),
            _item("Código", elemento.codigo),
        ],
        "sections": [],
        "actions": [
            _accion("Localizar troncal", _url_con_query("mapa_inventario", ruta=elemento.ruta.nombre)),
            _accion("Ver elementos relacionados", _url_con_query("planta_externa", tab="elementos", q=elemento.ruta.nombre)),
        ],
    }


FICHAS = {
    "troncal": _ficha_troncal,
    "odf": _ficha_odf,
    "puerto": _ficha_puerto,
    "fibra": _ficha_fibra,
    "site": _ficha_site,
    "elemento": _ficha_elemento,
}


@login_required
def asset_360(request, tipo, pk):
    permiso = PERMISO_POR_TIPO.get(tipo)
    constructor = FICHAS.get(tipo)
    if not permiso or not constructor:
        raise Http404("Tipo de activo no disponible")
    if not request.user.has_perm(permiso):
        raise PermissionDenied
    return JsonResponse({"status": "success", "data": constructor(pk)})
