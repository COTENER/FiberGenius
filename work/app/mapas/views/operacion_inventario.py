"""Consultas operativas de inventario para la GUI.

Estas vistas evitan enviar inventarios completos al navegador y entregan una
ficha transversal de los activos sin modificar el modelo ni la carga existente.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse

from ..models import (
    DetallePuertoODF,
    HubSite,
    InventarioFibra,
    InventarioODF,
    InventarioTramo,
    RackFisico,
    Reserva,
    Ruta,
)


PAGE_SIZES = {25, 50, 100, 200}
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
    return texto or defecto


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
    estado = request.GET.get("estado", "").strip()
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
    if estado in {"Libre", "Ocupado"}:
        queryset = queryset.filter(estado_puerto=estado)
    return queryset.order_by("odf_obj__hub_site", "odf", "puerto_odf", "id")


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
            "detail_url": reverse("asset_360", args=["puerto", puerto.pk]),
        }
        for puerto in puertos
    ]


def _filtro_fibras(request):
    queryset = InventarioFibra.objects.select_related("ruta")
    termino = request.GET.get("q", "").strip()[:150]
    estado = request.GET.get("estado", "").strip()
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


def _filtro_elementos(request):
    queryset = Reserva.objects.select_related("ruta", "tramo")
    termino = request.GET.get("q", "").strip()[:150]
    tipo = request.GET.get("tipo", "").strip()[:50]
    if termino:
        queryset = queryset.filter(
            Q(ruta__nombre__icontains=termino)
            | Q(nombre__icontains=termino)
            | Q(codigo__icontains=termino)
            | Q(tipo__icontains=termino)
        )
    if tipo:
        queryset = queryset.filter(tipo__iexact=tipo)
    return queryset.order_by("ruta__nombre", "orden_en_ruta", "nombre", "id")


def _serializar_elementos(elementos):
    return [
        {
            "id": elemento.pk,
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


@login_required
@permission_required("mapas.view_detallepuertoodf", raise_exception=True)
def api_puertos_paginados(request):
    return _pagina(request, _filtro_puertos(request), _serializar_puertos)


@login_required
@permission_required("mapas.view_inventariofibra", raise_exception=True)
def api_fibras_paginadas(request):
    return _pagina(request, _filtro_fibras(request), _serializar_fibras)


@login_required
@permission_required("mapas.view_reserva", raise_exception=True)
def api_elementos_paginados(request):
    return _pagina(request, _filtro_elementos(request), _serializar_elementos)


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

    if recurso == "puertos":
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
    tramos = list(troncal.tramos_inventario.order_by("tramo_secuencia"))
    primero = tramos[0] if tramos else None
    ultimo = tramos[-1] if tramos else None
    fibras = troncal.fibras_inventario
    return {
        "type": "troncal",
        "title": troncal.nombre,
        "subtitle": "Ficha operativa de troncal",
        "status": _texto(primero.estado if primero else None, "Sin estado"),
        "chain": [
            _item("Site de origen", primero.hub_site if primero else None),
            _item("ODF extremo A", troncal.odf_origen.odf if troncal.odf_origen else (primero.odf_nombre if primero else None)),
            _item("Troncal", troncal.nombre),
            _item("Tramos", len(tramos)),
            _item("ODF extremo B", troncal.odf_destino.odf if troncal.odf_destino else None),
            _item("Destino", ultimo.destino if ultimo else None),
        ],
        "summary": [
            _item("Distancia", f"{(troncal.distancia_m or 0) / 1000:.2f} km"),
            _item("Fibras", fibras.count()),
            _item("Fibras libres", fibras.filter(estado="Libre").count()),
            _item("Reservas y elementos", troncal.reservas.count()),
        ],
        "sections": [
            {
                "title": "Datos técnicos",
                "items": [
                    _item("Capacidad", primero.capacidad if primero else None),
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
