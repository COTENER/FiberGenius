"""Consultas operativas de inventario para la GUI.

Estas vistas evitan enviar inventarios completos al navegador y entregan una
ficha transversal de los activos sin modificar el modelo ni la carga existente.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, ExpressionWrapper, F, FloatField, Q, Sum
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse

from ..distancias import (
    etiqueta_fuente,
    redondear_km,
    resolver_distancia_ruta,
    resolver_distancia_tramo,
)
from ..models import (
    CoordenadaRuta,
    DetallePuertoODF,
    HubSite,
    InventarioFibra,
    InventarioODF,
    InventarioTramo,
    RackFisico,
    Reserva,
    Ruta,
)


PAGE_SIZES = {10, 25, 48, 50, 100, 200}
PERMISO_POR_TIPO = {
    "troncal": "mapas.view_ruta",
    "tramo": "mapas.view_inventariotramo",
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
    )
    termino = request.GET.get("q", "").strip()[:150]
    odf_id = request.GET.get("odf_id", "").strip()
    estado = request.GET.get("estado", "").strip()
    site = request.GET.get("site", "").strip()[:150]
    sala = request.GET.get("sala", "").strip()[:100]
    rack = request.GET.get("rack", "").strip()[:100]
    if termino:
        queryset = queryset.filter(
            Q(odf__icontains=termino)
            | Q(puerto_odf__icontains=termino)
            | Q(fibra__icontains=termino)
            | Q(destino__icontains=termino)
            | Q(odf_obj__hub_site__icontains=termino)
            | Q(odf_obj__sala__icontains=termino)
            | Q(odf_obj__rack__icontains=termino)
        )
    if odf_id.isdigit():
        queryset = queryset.filter(odf_obj_id=int(odf_id))
    if estado in {"Libre", "Ocupado", "Reservado"}:
        queryset = queryset.filter(estado_puerto=estado)
    if site:
        queryset = queryset.filter(odf_obj__hub_site=site)
    if sala:
        queryset = queryset.filter(odf_obj__sala=sala)
    if rack:
        queryset = queryset.filter(odf_obj__rack=rack)
    return queryset.order_by("odf_obj__hub_site", "odf", "puerto_odf", "id")


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
    return [
        {
            "id": puerto.pk,
            "site": _texto(puerto.odf_obj.hub_site),
            "sala": _texto(puerto.odf_obj.sala),
            "rack": _texto(puerto.odf_obj.rack),
            "odf": _texto(puerto.odf),
            "bandeja": _texto(puerto.bandeja),
            "puerto": _texto(puerto.puerto_odf),
            "fibra": _texto(puerto.fibra),
            "estado": _texto(puerto.estado_puerto),
            "conector": _texto(puerto.tipo_conector),
            "patchcord": _texto(puerto.patchcord, "No"),
            "destino": _texto(puerto.destino),
            "observaciones": _texto(puerto.observaciones, ""),
            "detail_url": reverse("asset_360", args=["puerto", puerto.pk]),
        }
        for puerto in puertos
    ]


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
    estadisticas = {
        ruta_id: {"registros": 0, "ocupados": 0, "reservados": 0, "libres": 0}
        for ruta_id in ids_ruta
    }
    if not ids_ruta:
        return estadisticas

    for ruta_id, estado_fibra in InventarioFibra.objects.filter(
        ruta_id__in=ids_ruta
    ).values_list("ruta_id", "estado"):
        datos = estadisticas[ruta_id]
        datos["registros"] += 1
        normalizado = (estado_fibra or "").lower()
        if normalizado in {"ocupado", "ocupada"}:
            datos["ocupados"] += 1
        elif normalizado in {"reservado", "reservada"}:
            datos["reservados"] += 1
        elif normalizado == "libre":
            datos["libres"] += 1

    ids_sin_fibras = [
        ruta_id
        for ruta_id, datos in estadisticas.items()
        if not datos["registros"]
    ]
    for ruta in Ruta.objects.filter(pk__in=ids_sin_fibras).only(
        'hilos_ocupados_declarados',
        'hilos_libres_declarados',
        'hilos_reservados_declarados',
    ):
        datos = estadisticas[ruta.pk]
        datos["ocupados"] = ruta.hilos_ocupados_declarados or 0
        datos["libres"] = ruta.hilos_libres_declarados or 0
        datos["reservados"] = ruta.hilos_reservados_declarados or 0
    return estadisticas


def _filtro_troncales(request):
    queryset = Ruta.objects.all()
    termino = request.GET.get("q", "").strip()[:150]
    tipo = request.GET.get("tipo", "").strip()[:50]
    estado = request.GET.get("estado", "").strip()[:50]
    site = request.GET.get("site", "").strip()[:150]
    capacidad = request.GET.get("capacidad", "").strip().lower()
    if termino:
        queryset = queryset.filter(
            Q(nombre__icontains=termino)
            | Q(olt__icontains=termino)
            | Q(hub_site__icontains=termino)
            | Q(destino__icontains=termino)
            | Q(odf_nombre__icontains=termino)
            | Q(serial__icontains=termino)
            | Q(tramos_inventario__hub_site__icontains=termino)
            | Q(tramos_inventario__destino__icontains=termino)
            | Q(tramos_inventario__odf_nombre__icontains=termino)
            | Q(tramos_inventario__serial__icontains=termino)
        )
    if tipo:
        queryset = queryset.filter(tramos_inventario__tipo_trazado__iexact=tipo)
    if estado:
        queryset = queryset.filter(
            Q(estado__iexact=estado)
            | Q(tramos_inventario__estado__iexact=estado)
        )
    if site:
        queryset = queryset.filter(
            Q(hub_site__iexact=site)
            | Q(origen__iexact=site)
            | Q(destino__iexact=site)
            | Q(tramos_inventario__hub_site__iexact=site)
            | Q(tramos_inventario__origen__iexact=site)
            | Q(tramos_inventario__destino__iexact=site)
        )
    if capacidad in {"disponible", "atencion"}:
        ids_ruta = set(queryset.values_list("pk", flat=True).distinct())
        estadisticas = _estadisticas_capacidad_rutas(ids_ruta)

        ids_capacidad = []
        for ruta_id, datos in estadisticas.items():
            total = datos["ocupados"] + datos["reservados"] + datos["libres"]
            if not total:
                continue
            utilizacion = round(
                (datos["ocupados"] + datos["reservados"]) / total * 100,
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
        .prefetch_related("tramos_inventario")
        .annotate(
            tramos_total=Count(
                "tramos_inventario",
                filter=Q(tramos_inventario__vigente=True),
                distinct=True,
            ),
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


def _serializar_troncales(troncales):
    resultado = []
    for troncal in troncales:
        tramos = [
            tramo
            for tramo in troncal.tramos_inventario.all()
            if tramo.vigente
        ]
        primero = tramos[0] if tramos else None
        ultimo = tramos[-1] if tramos else None
        distancia = resolver_distancia_ruta(troncal, tramos)
        resultado.append(
            {
                "id": troncal.pk,
                "nombre": troncal.nombre,
                "origen": _texto(
                    troncal.hub_site
                    or troncal.origen
                    or (
                        (primero.hub_site or primero.origen)
                        if primero
                        else troncal.olt
                    )
                ),
                "destino": _texto(
                    troncal.destino
                    or (ultimo.destino if ultimo else None)
                ),
                "tipo": _tipo_troncal(tramos),
                "distancia_km": redondear_km(distancia),
                "distancia_fuente": distancia.fuente,
                "distancia_documentada_km": (
                    round(troncal.distancia_documentada_m / 1000, 3)
                    if troncal.distancia_documentada_m is not None
                    else None
                ),
                "tramos": troncal.tramos_total,
                "capacidad": _texto(
                    troncal.capacidad_hilos_declarada
                    if troncal.capacidad_hilos_declarada is not None
                    else (primero.capacidad if primero else None)
                ),
                "estado": _texto(
                    troncal.estado
                    or (primero.estado if primero else None),
                    "Sin estado",
                ),
                "fibras": troncal.fibras_total,
                "reservas": troncal.reservas_total,
                "odf": _texto(
                    troncal.odf_nombre
                    or (primero.odf_nombre if primero else None)
                ),
                "tipo_fibra": _texto(
                    troncal.tipo_fibra
                    or (primero.tipo_fibra if primero else None)
                ),
                "marca_modelo": _texto(
                    troncal.marca_modelo
                    or (primero.marca_modelo if primero else None)
                ),
                "serial": _texto(
                    troncal.serial
                    or (primero.serial if primero else None)
                ),
                "mufas": troncal.mufas,
                "splitters": troncal.splitters,
                "hilos_ocupados": troncal.hilos_ocupados_declarados or 0,
                "hilos_libres": troncal.hilos_libres_declarados or 0,
                "reserva_km": round((troncal.reservas_m or 0) / 1000, 3),
                "detail_url": reverse("asset_360", args=["troncal", troncal.pk]),
            }
        )
    return resultado


def _resumen_troncales(queryset):
    base = Ruta.objects.filter(
        pk__in=queryset.values("pk")
    ).prefetch_related("tramos_inventario")
    rutas = list(base)
    ids_ruta = [ruta.pk for ruta in rutas]
    distancias = [
        resolver_distancia_ruta(
            ruta,
            list(ruta.tramos_inventario.all()),
        )
        for ruta in rutas
    ]
    conocidas = [
        distancia.valor_m
        for distancia in distancias
        if distancia.valor_m is not None
    ]
    resumen = {
        "total": len(rutas),
        "distancia_km": (
            round(sum(conocidas) / 1000, 2)
            if conocidas
            else None
        ),
        "distancias_sin_dato": len(distancias) - len(conocidas),
        "tramos": InventarioTramo.objects.filter(
            ruta__in=base,
            vigente=True,
        ).count(),
        "fibras": InventarioFibra.objects.filter(ruta__in=base).count(),
        "reservas": Reserva.objects.filter(ruta__in=base).count(),
    }
    nombres = dict(base.values_list("pk", "nombre"))
    ranking = []
    for ruta_id, datos in _estadisticas_capacidad_rutas(ids_ruta).items():
        total = datos["ocupados"] + datos["reservados"] + datos["libres"]
        utilizados = datos["ocupados"] + datos["reservados"]
        if not total:
            continue
        ranking.append({
            "id": ruta_id,
            "nombre": nombres.get(ruta_id, "Ruta"),
            "total": total,
            "utilizados": utilizados,
            "utilizacion": round(utilizados / total * 100, 1),
        })
    ranking.sort(
        key=lambda fila: (fila["utilizacion"], fila["utilizados"], fila["nombre"]),
        reverse=True,
    )
    resumen["top_ocupacion"] = ranking[:5]
    return resumen


def _filtro_tramos(request):
    queryset = InventarioTramo.objects.select_related("ruta").filter(vigente=True)
    termino = request.GET.get("q", "").strip()[:150]
    tipo = request.GET.get("tipo", "").strip()[:50]
    estado = request.GET.get("estado", "").strip()[:50]
    ruta = request.GET.get("ruta", "").strip()[:150]
    if termino:
        queryset = queryset.filter(
            Q(ruta__nombre__icontains=termino)
            | Q(ruta__hub_site__icontains=termino)
            | Q(ruta__origen__icontains=termino)
            | Q(ruta__destino__icontains=termino)
            | Q(ruta__odf_nombre__icontains=termino)
            | Q(ruta__serial__icontains=termino)
            | Q(codigo_tramo__icontains=termino)
            | Q(origen__icontains=termino)
            | Q(destino__icontains=termino)
            | Q(hub_site__icontains=termino)
            | Q(odf_nombre__icontains=termino)
            | Q(serial__icontains=termino)
        )
    if tipo:
        queryset = queryset.filter(tipo_trazado__iexact=tipo)
    if estado:
        queryset = queryset.filter(estado__iexact=estado)
    if ruta:
        queryset = queryset.filter(ruta__nombre=ruta)
    return queryset.order_by("ruta__nombre", "tramo_secuencia", "id")


def _serializar_tramos(tramos):
    tramos = list(tramos)
    estadisticas = _estadisticas_capacidad_rutas(
        {tramo.ruta_id for tramo in tramos}
    )
    reservas_por_tramo = {
        fila["tramo_id"]: fila["total"] or 0
        for fila in Reserva.objects.filter(
            tramo_id__in={tramo.pk for tramo in tramos}
        ).values("tramo_id").annotate(total=Sum("reserva_m"))
    }
    resultado = []
    for tramo in tramos:
        distancia = resolver_distancia_tramo(tramo)
        resultado.append({
            "id": tramo.pk,
            "ruta_id": tramo.ruta_id,
            "troncal": tramo.ruta.nombre,
            "secuencia": tramo.tramo_secuencia,
            "codigo": tramo.codigo_tramo,
            "origen": _texto(tramo.origen or tramo.hub_site),
            "destino": _texto(tramo.destino),
            "tipo": _texto(tramo.tipo_trazado, "Sin clasificar"),
            "distancia_km": redondear_km(distancia),
            "distancia_fuente": distancia.fuente,
            "distancia_documentada_km": (
                round(tramo.distancia_documentada_m / 1000, 2)
                if tramo.distancia_documentada_m is not None
                else None
            ),
            "capacidad": (
                tramo.ruta.capacidad_hilos_declarada
                if tramo.ruta.capacidad_hilos_declarada is not None
                else sum(
                    estadisticas.get(tramo.ruta_id, {}).get(clave, 0)
                    for clave in ("ocupados", "reservados", "libres")
                )
            ),
            "ocupados": estadisticas.get(
                tramo.ruta_id, {}
            ).get("ocupados", 0),
            "reservados": estadisticas.get(
                tramo.ruta_id, {}
            ).get("reservados", 0),
            "libres": estadisticas.get(tramo.ruta_id, {}).get("libres", 0),
            "reservas_m": round(reservas_por_tramo.get(tramo.pk, 0), 2),
            "estado": _texto(tramo.estado, "Sin estado"),
            "detail_url": reverse("asset_360", args=["tramo", tramo.pk]),
        })
    return resultado


def _resumen_tramos_legacy(queryset):
    tramos_distancia = list(
        queryset.select_related(None).only(
            "id",
            "distancia_m",
            "distancia_documentada_m",
        )
    )
    distancias = [
        resolver_distancia_tramo(tramo)
        for tramo in tramos_distancia
    ]
    conocidas = [
        distancia.valor_m
        for distancia in distancias
        if distancia.valor_m is not None
    ]
    resumen = queryset.aggregate(
        total=Count("id"),
        aereos=Count("id", filter=Q(tipo_trazado__iexact="AEREO")),
        soterrados=Count("id", filter=Q(tipo_trazado__iexact="SOTERRADO")),
        reservas_m=Sum("reservas_m"),
    )
    resultado = {
        "total": resumen["total"] or 0,
        "distancia_km": (
            round(sum(conocidas) / 1000, 2)
            if conocidas
            else None
        ),
        "distancias_sin_dato": len(distancias) - len(conocidas),
        "aereos": resumen["aereos"] or 0,
        "soterrados": resumen["soterrados"] or 0,
        "reservas_m": round(resumen["reservas_m"] or 0, 2),
    }
    ranking = []
    for fila in queryset.values(
        "ruta_id",
        "ruta__nombre",
        "tramo_secuencia",
        "hilos_ocupados",
        "hilos_libres",
    ):
        ocupados = fila["hilos_ocupados"] or 0
        libres = fila["hilos_libres"] or 0
        total = ocupados + libres
        if not total:
            continue
        ranking.append({
            "id": fila["ruta_id"],
            "nombre": (
                f'{fila["ruta__nombre"]} · Tramo {fila["tramo_secuencia"]}'
            ),
            "total": total,
            "utilizados": ocupados,
            "utilizacion": round(ocupados / total * 100, 1),
        })
    ranking.sort(
        key=lambda fila: (fila["utilizacion"], fila["utilizados"], fila["nombre"]),
        reverse=True,
    )
    resultado["top_ocupacion"] = ranking[:5]
    return resultado


def _resumen_tramos(queryset):
    tramos_distancia = list(
        queryset.select_related(None).only(
            "id",
            "distancia_m",
            "distancia_documentada_m",
        )
    )
    distancias = [
        resolver_distancia_tramo(tramo)
        for tramo in tramos_distancia
    ]
    conocidas = [
        distancia.valor_m
        for distancia in distancias
        if distancia.valor_m is not None
    ]
    resumen = queryset.aggregate(
        total=Count("id"),
        aereos=Count("id", filter=Q(tipo_trazado__iexact="AEREO")),
        soterrados=Count("id", filter=Q(tipo_trazado__iexact="SOTERRADO")),
        hibridos=Count("id", filter=Q(tipo_trazado__iexact="HIBRIDO")),
        troncales=Count("ruta_id", distinct=True),
    )
    reservas_m = Reserva.objects.filter(
        tramo_id__in=queryset.values("pk")
    ).aggregate(total=Sum("reserva_m"))["total"] or 0
    total = resumen["total"] or 0
    clasificados = sum(
        resumen[clave] or 0
        for clave in ("aereos", "soterrados", "hibridos")
    )
    resultado = {
        "total": total,
        "distancia_km": (
            round(sum(conocidas) / 1000, 2)
            if conocidas
            else None
        ),
        "distancias_sin_dato": len(distancias) - len(conocidas),
        "aereos": resumen["aereos"] or 0,
        "soterrados": resumen["soterrados"] or 0,
        "hibridos": resumen["hibridos"] or 0,
        "sin_clasificar": max(total - clasificados, 0),
        "reservas_m": round(reservas_m, 2),
        "troncales": resumen["troncales"] or 0,
    }
    top_troncales = list(
        queryset.values("ruta_id", "ruta__nombre").annotate(
            tramos=Count("id"),
        ).order_by("-tramos", "ruta__nombre")[:5]
    )
    maximo = max((fila["tramos"] for fila in top_troncales), default=0)
    resultado["top_troncales"] = [
        {
            "id": fila["ruta_id"],
            "nombre": fila["ruta__nombre"],
            "tramos": fila["tramos"],
            "proporcion": (
                round(fila["tramos"] / maximo * 100, 1)
                if maximo else 0
            ),
        }
        for fila in top_troncales
    ]
    return resultado


def _filtro_fibras(request):
    queryset = InventarioFibra.objects.select_related("ruta")
    termino = request.GET.get("q", "").strip()[:150]
    estado = request.GET.get("estado", "").strip()
    ruta = request.GET.get("ruta", "").strip()[:150]
    if termino:
        queryset = queryset.filter(
            Q(ruta__nombre__icontains=termino)
            | Q(fibra_numero__icontains=termino)
            | Q(nombre_fibra__icontains=termino)
            | Q(origen_odf__icontains=termino)
            | Q(destino__icontains=termino)
        )
    if estado in {"Libre", "Ocupado", "Reservado"}:
        queryset = queryset.filter(estado=estado)
    if ruta:
        queryset = queryset.filter(ruta__nombre=ruta)
    return queryset.order_by("ruta__nombre", "fibra_numero", "id")


def _origenes_troncales(ids_ruta):
    origenes = {}
    tramos = InventarioTramo.objects.filter(
        ruta_id__in=ids_ruta, tramo_secuencia=1
    ).values("ruta_id", "odf_nombre", "hub_site", "origen")
    for tramo in tramos:
        origenes[tramo["ruta_id"]] = next(
            (
                valor
                for valor in (
                    tramo["odf_nombre"],
                    tramo["hub_site"],
                    tramo["origen"],
                )
                if valor and valor.strip() and valor != "N/A"
            ),
            "—",
        )
    return origenes


def _serializar_fibras(fibras):
    origenes = _origenes_troncales({fibra.ruta_id for fibra in fibras})
    return [
        {
            "id": fibra.pk,
            "ruta_id": fibra.ruta_id,
            "troncal": fibra.ruta.nombre,
            "numero": _texto(fibra.fibra_numero),
            "estado": _texto(fibra.estado),
            "servicio": _texto(fibra.nombre_fibra),
            "origen": _texto(fibra.origen_odf, origenes.get(fibra.ruta_id, "—")),
            "destino": _texto(fibra.destino),
            "conector": _texto(fibra.tipo_conector),
            "detail_url": reverse("asset_360", args=["fibra", fibra.pk]),
        }
        for fibra in fibras
    ]


def _resumen_fibras(queryset):
    resumen = queryset.aggregate(
        total=Count("id"),
        libres=Count("id", filter=Q(estado__iexact="Libre")),
        ocupadas=Count("id", filter=Q(estado__in=["Ocupado", "Ocupada"])),
        reservadas=Count("id", filter=Q(estado__in=["Reservado", "Reservada"])),
        troncales=Count("ruta_id", distinct=True),
    )
    resumen = {clave: valor or 0 for clave, valor in resumen.items()}
    ranking = queryset.values("ruta_id", "ruta__nombre").annotate(
        total=Count("id"),
        ocupadas=Count("id", filter=Q(estado__in=["Ocupado", "Ocupada"])),
        reservadas=Count("id", filter=Q(estado__in=["Reservado", "Reservada"])),
    ).order_by("-ocupadas", "-reservadas", "ruta__nombre")[:5]
    resumen["top_troncales"] = [
        {
            "id": fila["ruta_id"],
            "nombre": fila["ruta__nombre"],
            "total": fila["total"],
            "ocupadas": fila["ocupadas"],
            "reservadas": fila["reservadas"],
            "utilizacion": round(
                ((fila["ocupadas"] + fila["reservadas"]) / fila["total"]) * 100,
                1,
            ) if fila["total"] else 0,
        }
        for fila in ranking
    ]
    return resumen


def _filtro_elementos(request):
    queryset = Reserva.objects.select_related("ruta", "tramo")
    termino = request.GET.get("q", "").strip()[:150]
    tipo = request.GET.get("tipo", "").strip()[:50]
    ruta = request.GET.get("ruta", "").strip()[:150]
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
def api_panel_tramo(request):
    """Detalle del tramo seleccionado, con capacidad heredada de su troncal."""
    tramo_id = request.GET.get("tramo_id", "").strip()
    if not tramo_id.isdigit():
        return JsonResponse(
            {"status": "error", "message": "Tramo no válido."},
            status=400,
        )
    tramo = get_object_or_404(
        InventarioTramo.objects.select_related("ruta"),
        pk=tramo_id,
        vigente=True,
    )
    ruta = tramo.ruta
    fibras = InventarioFibra.objects.filter(ruta=ruta)
    resumen_fibras = _resumen_fibras(fibras)
    reservas = Reserva.objects.filter(tramo=tramo).order_by(
        "orden_en_ruta", "nombre", "id"
    )
    coordenadas = list(
        CoordenadaRuta.objects.filter(tramo=tramo)
        .order_by("orden")
        .values_list("latitud", "longitud")
    )
    if not coordenadas:
        coordenadas = list(
            CoordenadaRuta.objects.filter(
                ruta=ruta,
                tramo_secuencia=tramo.tramo_secuencia,
            )
            .order_by("orden")
            .values_list("latitud", "longitud")
        )
    if len(coordenadas) > 500:
        ultimo = len(coordenadas) - 1
        coordenadas = [
            coordenadas[round(indice * ultimo / 499)]
            for indice in range(500)
        ]
    reserva_elementos_m = reservas.aggregate(
        total=Sum("reserva_m")
    )["total"] or 0
    capacidad = ruta.capacidad_hilos_declarada
    if capacidad is None:
        capacidad = resumen_fibras["total"]

    distancia = resolver_distancia_tramo(tramo)
    return JsonResponse({
        "status": "success",
        "asset_kind": "tramo",
        "ruta": {
            "id": ruta.pk,
            "tramo_id": tramo.pk,
            "nombre": f"{ruta.nombre} · {tramo.codigo_tramo}",
            "troncal": ruta.nombre,
            "codigo": tramo.codigo_tramo,
            "secuencia": tramo.tramo_secuencia,
            "distancia_km": redondear_km(distancia),
            "distancia_fuente": distancia.fuente,
            "distancia_documentada_km": (
                round(tramo.distancia_documentada_m / 1000, 2)
                if tramo.distancia_documentada_m is not None
                else None
            ),
            "tramos": 1,
            "tipos_trazado": [_texto(
                tramo.tipo_trazado,
                "SIN CLASIFICAR",
            )],
            "origen": _texto(tramo.origen or tramo.hub_site),
            "destino": _texto(tramo.destino),
            "estado": _texto(tramo.estado, "Sin estado"),
            "capacidad": capacidad,
            "mapa_url": (
                f'{reverse("mapa_inventario")}?'
                f'{urlencode({"ruta": ruta.nombre, "tramo": tramo.tramo_secuencia})}'
            ),
        },
        "fibras": {
            "total": resumen_fibras["total"],
            "libres": resumen_fibras["libres"],
            "ocupadas": resumen_fibras["ocupadas"],
            "reservadas": resumen_fibras["reservadas"],
            "utilizacion": round(
                (
                    (
                        resumen_fibras["ocupadas"]
                        + resumen_fibras["reservadas"]
                    )
                    / resumen_fibras["total"]
                ) * 100,
                1,
            ) if resumen_fibras["total"] else 0,
        },
        "reservas": {
            "elementos": reservas.count(),
            "reserva_m": round(
                reserva_elementos_m or tramo.reservas_m or 0,
                2,
            ),
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
    tramos = InventarioTramo.objects.filter(
        ruta=ruta,
        vigente=True,
    ).order_by("tramo_secuencia")
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
    tramos_lista = list(tramos)
    distancia = resolver_distancia_ruta(ruta, tramos_lista)
    reservas_m = reservas.aggregate(total=Sum("reserva_m"))["total"] or 0

    return JsonResponse({
        "status": "success",
        "asset_kind": "troncal",
        "ruta": {
            "id": ruta.pk,
            "nombre": ruta.nombre,
            "distancia_km": redondear_km(distancia),
            "distancia_fuente": distancia.fuente,
            "tramos": len(extremos),
            "tipos_trazado": sorted({
                tramo["tipo_trazado"]
                for tramo in extremos
                if tramo["tipo_trazado"]
            }),
            "origen": _texto(
                ruta.odf_nombre
                or ruta.hub_site
                or ruta.origen
                or (
                    extremos[0]["odf_nombre"]
                    or extremos[0]["hub_site"]
                    or extremos[0]["origen"]
                    if extremos
                    else None
                )
            ),
            "destino": _texto(
                ruta.destino
                or (extremos[-1]["destino"] if extremos else None)
            ),
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
            "reserva_m": round(reservas_m or ruta.reservas_m or 0, 2),
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


@login_required
@permission_required("mapas.add_inventariotramo", raise_exception=True)
def create_tramo_manual(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Método no permitido"}, status=405)
    try:
        data = json.loads(request.body)
        ruta = Ruta.objects.filter(pk=data.get("ruta_id")).first()
        if not ruta:
            return JsonResponse({"status": "error", "message": "Troncal no encontrada"}, status=404)
        secuencia = _numero_no_negativo(data, "tramo_secuencia", entero=True)
        if secuencia < 1:
            raise ValueError("La secuencia debe ser mayor que cero")
        codigo = str(data.get("codigo_tramo", "")).strip().upper()
        if not codigo:
            codigo = f"T{secuencia:03d}"
        distancia_raw = data.get("distancia_km")
        distancia_m = (
            None
            if distancia_raw in (None, "")
            else _numero_no_negativo(data, "distancia_km") * 1000
        )
        capacidad_texto = str(data.get("capacidad", "")).strip()
        capacidad_numero = None
        if capacidad_texto:
            import re
            coincidencia = re.search(r"\d+", capacidad_texto)
            if not coincidencia:
                raise ValueError("La capacidad debe contener un número de hilos")
            capacidad_numero = int(coincidencia.group(0))
            if (
                ruta.capacidad_hilos_declarada is not None
                and ruta.capacidad_hilos_declarada != capacidad_numero
            ):
                raise ValueError(
                    "La capacidad pertenece a la troncal y no puede variar por tramo"
                )
        with transaction.atomic():
            tramo = InventarioTramo.objects.create(
                ruta=ruta,
                tramo_secuencia=secuencia,
                codigo_tramo=codigo,
                estado_calidad=InventarioTramo.CALIDAD_PENDIENTE,
                tipo_trazado=str(data.get("tipo_trazado", "")).strip(),
                estado=str(data.get("estado", "")).strip(),
                distancia_documentada_m=distancia_m,
                origen=str(data.get("origen", "")).strip(),
                destino=str(data.get("destino", "")).strip(),
            )
            if capacidad_numero is not None:
                ruta.capacidad_hilos_declarada = capacidad_numero
            if "estado" in data:
                ruta.estado = str(data.get("estado", "")).strip() or None
            if "tipo_fibra" in data:
                ruta.tipo_fibra = (
                    str(data.get("tipo_fibra", "")).strip() or None
                )
            if "hub_site" in data:
                ruta.hub_site = str(data.get("hub_site", "")).strip() or None
            if "odf_nombre" in data:
                ruta.odf_nombre = (
                    str(data.get("odf_nombre", "")).strip() or None
                )
            if "mufas" in data:
                ruta.mufas = _numero_no_negativo(
                    data,
                    "mufas",
                    entero=True,
                )
            if "splitters" in data:
                ruta.splitters = _numero_no_negativo(
                    data,
                    "splitters",
                    entero=True,
                )
            if "reservas_m" in data:
                ruta.reservas_m = _numero_no_negativo(data, "reservas_m")
            ruta.save()
        return JsonResponse(
            {"status": "success", "message": f"Tramo {tramo.tramo_secuencia} registrado correctamente"}
        )
    except (IntegrityError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=400)


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
        yield (
            puerto.odf_obj.hub_site,
            puerto.odf_obj.sala,
            puerto.odf_obj.rack,
            puerto.odf,
            puerto.bandeja,
            puerto.puerto_odf,
            puerto.fibra,
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
    for item in _serializar_troncales(queryset.iterator(chunk_size=500)):
        yield (
            item["nombre"], item["origen"], item["destino"], item["tipo"],
            item["distancia_km"], item["tramos"], item["capacidad"],
            item["fibras"], item["reservas"], item["estado"],
        )


def _filas_tramos(queryset):
    ids_ruta = set(queryset.values_list("ruta_id", flat=True).distinct())
    estadisticas = _estadisticas_capacidad_rutas(ids_ruta)
    reservas_por_tramo = {
        fila["tramo_id"]: fila["total"] or 0
        for fila in Reserva.objects.filter(
            tramo_id__in=queryset.values("pk")
        ).values("tramo_id").annotate(total=Sum("reserva_m"))
    }
    for tramo in queryset.iterator(chunk_size=1000):
        distancia = resolver_distancia_tramo(tramo)
        capacidad = tramo.ruta.capacidad_hilos_declarada
        datos = estadisticas.get(
            tramo.ruta_id,
            {"ocupados": 0, "reservados": 0, "libres": 0},
        )
        if capacidad is None:
            capacidad = (
                datos["ocupados"] + datos["reservados"] + datos["libres"]
            )
        yield (
            tramo.ruta.nombre,
            tramo.tramo_secuencia,
            tramo.codigo_tramo,
            tramo.origen or tramo.hub_site,
            tramo.destino,
            tramo.tipo_trazado,
            redondear_km(distancia),
            (
                round(tramo.distancia_documentada_m / 1000, 2)
                if tramo.distancia_documentada_m is not None
                else ""
            ),
            capacidad,
            datos["ocupados"],
            datos["reservados"],
            datos["libres"],
            round(reservas_por_tramo.get(tramo.pk, 0), 2),
            tramo.estado,
        )


def _filas_fibras(queryset):
    ids_ruta = set(queryset.values_list("ruta_id", flat=True).distinct())
    origenes = _origenes_troncales(ids_ruta)
    for fibra in queryset.iterator(chunk_size=1000):
        yield (
            fibra.ruta.nombre,
            fibra.fibra_numero,
            fibra.estado,
            fibra.nombre_fibra,
            fibra.origen_odf or origenes.get(fibra.ruta_id),
            fibra.destino,
            fibra.tipo_conector,
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
        )
        filas = _filas_troncales(_filtro_troncales(request))
    elif recurso == "tramos":
        permiso = "mapas.view_ruta"
        titulo = "Tramos"
        encabezados = (
            "Troncal", "Secuencia", "Código tramo", "Origen", "Destino",
            "Trazado", "Distancia calculada (km)",
            "Distancia documentada (km)", "Capacidad de troncal",
            "Ocupados", "Reservados", "Libres", "Reserva (m)",
            "Estado",
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
            "Troncal", "N.º de fibra", "Estado", "Servicio", "Origen ODF", "Destino", "Conector",
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
            Q(odf__icontains=termino)
            | Q(puerto_odf__icontains=termino)
            | Q(fibra__icontains=termino)
            | Q(destino__icontains=termino)
        ).order_by("odf", "puerto_odf")[:6]
        for puerto in puertos:
            resultados.append(
                _resultado(
                    "puerto",
                    puerto.pk,
                    f"{puerto.odf} · Puerto {puerto.puerto_odf}",
                    f"Puerto ODF · {puerto.destino or puerto.estado_puerto}",
                )
            )
    if usuario.has_perm("mapas.view_inventariofibra"):
        fibras = InventarioFibra.objects.select_related("ruta").filter(
            Q(ruta__nombre__icontains=termino)
            | Q(fibra_numero__icontains=termino)
            | Q(nombre_fibra__icontains=termino)
            | Q(destino__icontains=termino)
        ).order_by("ruta__nombre", "fibra_numero")[:6]
        for fibra in fibras:
            resultados.append(
                _resultado(
                    "fibra",
                    fibra.pk,
                    f"{fibra.ruta.nombre} · Fibra {fibra.fibra_numero}",
                    f"Fibra óptica · {fibra.estado}",
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
        Ruta.objects.select_related("odf_origen", "odf_destino", "otu"), pk=pk
    )
    tramos = list(
        troncal.tramos_inventario.filter(vigente=True).order_by(
            "tramo_secuencia"
        )
    )
    primero = tramos[0] if tramos else None
    ultimo = tramos[-1] if tramos else None
    fibras = troncal.fibras_inventario
    distancia = resolver_distancia_ruta(troncal, tramos)
    return {
        "type": "troncal",
        "title": troncal.nombre,
        "subtitle": "Ficha operativa de troncal",
        "status": _texto(
            troncal.estado
            or (primero.estado if primero else None),
            "Sin estado",
        ),
        "chain": [
            _item(
                "Site de origen",
                troncal.hub_site
                or (primero.hub_site if primero else None),
            ),
            _item(
                "ODF extremo A",
                (
                    troncal.odf_origen.odf
                    if troncal.odf_origen
                    else troncal.odf_nombre
                    or (primero.odf_nombre if primero else None)
                ),
            ),
            _item("Troncal", troncal.nombre),
            _item("Tramos", len(tramos)),
            _item("ODF extremo B", troncal.odf_destino.odf if troncal.odf_destino else None),
            _item(
                "Destino",
                troncal.destino
                or (ultimo.destino if ultimo else None),
            ),
        ],
        "summary": [
            _item(
                "Distancia",
                (
                    f"{distancia.valor_km:.2f} km"
                    if distancia.valor_km is not None
                    else None
                ),
            ),
            _item("Fuente de distancia", etiqueta_fuente(distancia)),
            _item("Fibras", fibras.count()),
            _item("Fibras libres", fibras.filter(estado="Libre").count()),
            _item("Reservas y elementos", troncal.reservas.count()),
        ],
        "sections": [
            {
                "title": "Datos técnicos",
                "items": [
                    _item("Capacidad", troncal.capacidad_hilos_declarada),
                    _item(
                        "Tipo de fibra",
                        troncal.tipo_fibra
                        or (primero.tipo_fibra if primero else None),
                    ),
                    _item("Trazado", primero.tipo_trazado if primero else None),
                    _item(
                        "Marca / modelo",
                        troncal.marca_modelo
                        or (primero.marca_modelo if primero else None),
                    ),
                ],
            }
        ],
        "actions": [
            _accion("Localizar en mapa", _url_con_query("mapa_inventario", ruta=troncal.nombre)),
            _accion("Ver troncales y tramos", _url_con_query("inventario_externo", q=troncal.nombre)),
            _accion("Ver sus fibras", _url_con_query("planta_externa", tab="fibras", q=troncal.nombre)),
        ],
    }


def _ficha_tramo(pk):
    tramo = get_object_or_404(
        InventarioTramo.objects.select_related("ruta"),
        pk=pk,
        vigente=True,
    )
    ruta = tramo.ruta
    fibras = ruta.fibras_inventario
    reserva_tramo_m = Reserva.objects.filter(tramo=tramo).aggregate(
        total=Sum("reserva_m")
    )["total"] or 0
    puntos_geometria = CoordenadaRuta.objects.filter(tramo=tramo).count()
    capacidad = ruta.capacidad_hilos_declarada
    if capacidad is None:
        capacidad = fibras.count()
    distancia = resolver_distancia_tramo(tramo)
    return {
        "type": "tramo",
        "title": f"{ruta.nombre} · {tramo.codigo_tramo}",
        "subtitle": "Ficha operativa de tramo",
        "status": _texto(tramo.estado, "Sin estado"),
        "chain": [
            _item("Troncal", ruta.nombre),
            _item("Código", tramo.codigo_tramo),
            _item("Secuencia", tramo.tramo_secuencia),
            _item("Origen", tramo.origen or tramo.hub_site),
            _item("Destino", tramo.destino),
        ],
        "summary": [
            _item(
                "Distancia",
                (
                    f"{distancia.valor_km:.2f} km"
                    if distancia.valor_km is not None
                    else None
                ),
            ),
            _item("Fuente de distancia", etiqueta_fuente(distancia)),
            _item("Capacidad de la troncal", capacidad),
            _item("Fibras inventariadas", fibras.count()),
        ],
        "sections": [
            {
                "title": "Datos del segmento",
                "items": [
                    _item("Tipo de trazado", tramo.tipo_trazado),
                    _item("Reserva vinculada", f"{reserva_tramo_m:.2f} m"),
                    _item("Puntos de geometría", puntos_geometria),
                    _item("Estado", tramo.estado),
                ],
            },
            {
                "title": "Cable continuo de la troncal",
                "items": [
                    _item("Tipo de fibra", ruta.tipo_fibra),
                    _item("Mufas totales", ruta.mufas),
                    _item("Splitters totales", ruta.splitters),
                    _item("Reserva lineal total", f"{ruta.reservas_m:.2f} m"),
                ],
            },
        ],
        "actions": [
            _accion(
                "Localizar tramo",
                _url_con_query(
                    "mapa_inventario",
                    ruta=ruta.nombre,
                    tramo=tramo.tramo_secuencia,
                ),
            ),
            _accion(
                "Ver todos los tramos",
                _url_con_query(
                    "inventario_externo",
                    tab="tramos",
                    ruta=ruta.nombre,
                ),
            ),
        ],
    }


def _ficha_odf(pk):
    odf = get_object_or_404(
        InventarioODF.objects.select_related("rack_obj__sala__hub_site"), pk=pk
    )
    troncales = Ruta.objects.filter(
        Q(odf_origen=odf)
        | Q(odf_destino=odf)
        | Q(tramos_inventario__odf_nombre__iexact=odf.odf)
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
        "title": f"{puerto.odf} · Puerto {puerto.puerto_odf}",
        "subtitle": "Ficha operativa de puerto ODF",
        "status": puerto.estado_puerto,
        "chain": [
            _item("Site", puerto.odf_obj.hub_site),
            _item("Sala", puerto.odf_obj.sala),
            _item("Rack", puerto.odf_obj.rack),
            _item("ODF", puerto.odf),
            _item("Puerto", puerto.puerto_odf),
            _item("Fibra óptica", fibra.fibra_numero if fibra else puerto.fibra),
            _item("Destino", puerto.destino),
        ],
        "summary": [
            _item("Bandeja", puerto.bandeja),
            _item("Conector", puerto.tipo_conector),
            _item("Patchcord", puerto.patchcord),
            _item("Troncal", fibra.ruta.nombre if fibra else None),
        ],
        "sections": [
            {"title": "Observaciones", "items": [_item("Detalle", puerto.observaciones)]}
        ],
        "actions": [
            _accion("Ver puertos del ODF", _url_con_query("planta_interna", q=puerto.odf)),
        ],
    }


def _ficha_fibra(pk):
    fibra = get_object_or_404(InventarioFibra.objects.select_related("ruta"), pk=pk)
    terminaciones = list(
        fibra.terminaciones.select_related("puerto_odf__odf_obj").order_by("extremo")
    )
    extremo_a = next((t for t in terminaciones if t.extremo == "A"), None)
    extremo_b = next((t for t in terminaciones if t.extremo == "B"), None)
    return {
        "type": "fibra",
        "title": f"{fibra.ruta.nombre} · Fibra {fibra.fibra_numero}",
        "subtitle": "Ficha operativa de fibra óptica",
        "status": fibra.estado,
        "chain": [
            _item("Troncal", fibra.ruta.nombre),
            _item("ODF / puerto A", f"{extremo_a.puerto_odf.odf} / {extremo_a.puerto_odf.puerto_odf}" if extremo_a else fibra.origen_odf),
            _item("Fibra óptica", fibra.fibra_numero),
            _item("ODF / puerto B", f"{extremo_b.puerto_odf.odf} / {extremo_b.puerto_odf.puerto_odf}" if extremo_b else fibra.destino),
        ],
        "summary": [
            _item("Servicio", fibra.nombre_fibra),
            _item("Conector", fibra.tipo_conector),
            _item("Terminaciones confirmadas", len(terminaciones)),
        ],
        "sections": [],
        "actions": [
            _accion("Localizar troncal", _url_con_query("mapa_inventario", ruta=fibra.ruta.nombre)),
            _accion("Ver fibras de la troncal", _url_con_query("planta_externa", tab="fibras", q=fibra.ruta.nombre)),
        ],
    }


def _ficha_site(pk):
    site = get_object_or_404(HubSite, pk=pk)
    odfs = InventarioODF.objects.filter(rack_obj__sala__hub_site=site)
    troncales = Ruta.objects.filter(tramos_inventario__hub_site__iexact=site.nombre).distinct()
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
    "tramo": _ficha_tramo,
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
