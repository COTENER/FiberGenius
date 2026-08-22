import json
import logging
import re
from urllib.parse import urlencode

from django.conf import settings
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.gzip import gzip_page
from django.db import IntegrityError, transaction
from django.urls import reverse
from ..models import (
    Ruta, CoordenadaRuta, InventarioFibra, InventarioTramo, TerminacionFibra,
    normalizar_estado_puerto_odf,
    normalizar_estado_puerto_odf_con_destino,
)
from ..services.trazabilidad import (
    obtener_presentacion_orientada_fibra,
    obtener_trazabilidad_fibra,
)
from ..services.puertos import (
    MovimientoRequiereConfirmacion,
    cancelar_reserva_puerto,
    conectar_puerto,
    desconectar_puerto,
    reservar_puerto,
)
from ..services.ubicacion import nombre_site

logger = logging.getLogger('mapas')
ERRORES_DATOS_ENTRADA = (json.JSONDecodeError, TypeError, ValueError, ValidationError)


def _clave_orden_natural(valor):
    """Ordena identificadores alfanuméricos respetando sus bloques numéricos."""
    texto = str(valor or '').strip()
    bloques = tuple(
        (0, int(parte)) if parte.isdigit() else (1, parte.casefold())
        for parte in re.split(r'(\d+)', texto)
        if parte
    )
    return bloques, texto.casefold()


def _archivo_dentro_del_limite(archivo):
    limite = int(getattr(settings, 'FIBERGENIUS_MAX_UPLOAD_BYTES', 25 * 1024 * 1024))
    return getattr(archivo, 'size', 0) <= limite


def _mensaje_limite_archivo():
    limite = int(getattr(settings, 'FIBERGENIUS_MAX_UPLOAD_BYTES', 25 * 1024 * 1024))
    return f'El archivo supera el limite permitido de {limite // (1024 * 1024)} MB.'

def calculate_coordinate_distance(coordenadas):
    import math
    if not coordenadas or len(coordenadas) < 2:
        return 0.0
    
    total_dist = 0.0
    # Haversine formula
    for i in range(len(coordenadas) - 1):
        lat1, lon1 = float(coordenadas[i].latitud), float(coordenadas[i].longitud)
        lat2, lon2 = float(coordenadas[i+1].latitud), float(coordenadas[i+1].longitud)
        
        dLat = math.radians(lat2 - lat1)
        dLon = math.radians(lon2 - lon1)
        
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        
        a = (math.sin(dLat / 2) ** 2 +
             math.sin(dLon / 2) ** 2 * math.cos(lat1_rad) * math.cos(lat2_rad))
        rad = 6371000 # earth radius in meters
        c = 2 * math.asin(math.sqrt(a))
        total_dist += rad * c
        
    return total_dist

@login_required
@permission_required('mapas.view_ruta', raise_exception=True)
def mapa_inventario(request):
    """Renderiza la página base del Mapa de Inventario de Red."""
    return render(request, 'mapa_inventario/mapa_inventario.html')

@login_required
@permission_required('mapas.view_ruta', raise_exception=True)
def dashboard_inventario(request):
    """
    Vista para el Dashboard de Inventario.
    Agrupa estadísticas por RUTA TRONCAL completa.
    """
    from collections import defaultdict
    from django.db.models import Count, Q
    from ..models import (
        Ruta, InventarioTramo, Reserva, InventarioODF, DetallePuertoODF,
        HubSite, NodoRed,
    )
    from ..services.capacidad import resumen_rutas

    site_seleccionado = request.GET.get('site', '').strip()
    trazado_seleccionado = request.GET.get('trazado', '').strip().upper()
    if trazado_seleccionado not in {'AEREO', 'SOTERRADO', 'HIBRIDO'}:
        trazado_seleccionado = ''
    
    rutas_db = Ruta.objects.prefetch_related(
        'tramos_inventario__fibras_tramo',
        'tramos_inventario__origen_nodo',
        'tramos_inventario__destino_nodo',
        'reservas',
        'coordenadas',
        'fibras_inventario',
    ).filter(
        Q(coordenadas__isnull=False)
        | Q(tramos_inventario__isnull=False)
    ).distinct()
    if site_seleccionado:
        rutas_db = rutas_db.filter(
            Q(tramos_inventario__hub_site=site_seleccionado)
            | Q(tramos_inventario__destino=site_seleccionado)
            | Q(
                tramos_inventario__origen_nodo__tipo='SITE',
                tramos_inventario__origen_nodo__codigo__iexact=site_seleccionado,
            )
            | Q(
                tramos_inventario__origen_nodo__tipo='SITE',
                tramos_inventario__origen_nodo__nombre__iexact=site_seleccionado,
            )
            | Q(
                tramos_inventario__destino_nodo__tipo='SITE',
                tramos_inventario__destino_nodo__codigo__iexact=site_seleccionado,
            )
            | Q(
                tramos_inventario__destino_nodo__tipo='SITE',
                tramos_inventario__destino_nodo__nombre__iexact=site_seleccionado,
            )
        ).distinct()
    if trazado_seleccionado:
        if trazado_seleccionado == 'HIBRIDO':
            rutas_mixtas_geograficas = (
                Ruta.objects.filter(
                    coordenadas__tipo_trazado__iexact='AEREO'
                )
                .filter(coordenadas__tipo_trazado__iexact='SOTERRADO')
                .values('pk')
            )
            rutas_mixtas_tecnicas = (
                Ruta.objects.filter(
                    tramos_inventario__tipo_trazado__iexact='AEREO'
                )
                .filter(
                    tramos_inventario__tipo_trazado__iexact='SOTERRADO'
                )
                .values('pk')
            )
            rutas_db = rutas_db.filter(
                Q(coordenadas__tipo_trazado__iexact='HIBRIDO')
                | Q(pk__in=rutas_mixtas_geograficas)
                | Q(
                    coordenadas__isnull=True,
                    tramos_inventario__tipo_trazado__iexact='HIBRIDO',
                )
                | Q(
                    coordenadas__isnull=True,
                    pk__in=rutas_mixtas_tecnicas,
                )
            ).distinct()
        else:
            rutas_db = rutas_db.filter(
                Q(
                    coordenadas__tipo_trazado__iexact=trazado_seleccionado
                )
                | Q(
                    coordenadas__isnull=True,
                    tramos_inventario__tipo_trazado__iexact=trazado_seleccionado,
                )
            ).distinct()
    
    rutas_db = list(rutas_db)
    total_rutas = len(rutas_db)
    capacidad_por_ruta = resumen_rutas(rutas_db)
    
    resumen_rutas = []
    stats_trazado = {'AEREO': 0, 'SOTERRADO': 0, 'HIBRIDO': 0}
    distancia_trazado = {'AEREO': 0.0, 'SOTERRADO': 0.0, 'HIBRIDO': 0.0}
    stats_capacidad = {} # { '144': 0, '64': 0, ... }

    total_km = 0.0
    
    for r in rutas_db:
        resumen_capacidad = capacidad_por_ruta[r.pk]
        tramos = r.tramos_inventario.all()
        tipos_en_ruta = {
            (coordenada.tipo_trazado or '').strip().upper()
            for coordenada in r.coordenadas.all()
            if (coordenada.tipo_trazado or '').strip()
        }
        if not tipos_en_ruta:
            tipos_en_ruta = {
                (tramo.tipo_trazado or '').strip().upper()
                for tramo in tramos
                if (tramo.tipo_trazado or '').strip()
            }
        
        distancia_ruta_m = resumen_capacidad['distancia_m'] or 0
        distancia_ruta_km = round(distancia_ruta_m / 1000, 2)
        total_res_ruta_m = round(
            resumen_capacidad['reservas_m'] or 0,
            2,
        )

        # Determinar tipo de la ruta
        if 'HIBRIDO' in tipos_en_ruta or ('AEREO' in tipos_en_ruta and 'SOTERRADO' in tipos_en_ruta):
            tipo_final = 'HIBRIDO'
        elif 'AEREO' in tipos_en_ruta:
            tipo_final = 'AEREO'
        elif 'SOTERRADO' in tipos_en_ruta:
            tipo_final = 'SOTERRADO'
        else:
            tipo_final = 'SIN CLASIFICAR'
            
        # Acumular para gráficas (basado en rutas únicas)
        stats_trazado[tipo_final] = stats_trazado.get(tipo_final, 0) + 1
        distancia_trazado[tipo_final] = round(distancia_trazado.get(tipo_final, 0.0) + distancia_ruta_km, 2)
        
        hub_origen = "N/A"
        destino = "N/A"
        marca_modelo = "N/A"
        cap = "N/A"
        tipo_fibra = ""
        serial = ""
        estado_tramo = ""
        mufas_total = 0
        splitters_total = 0
        odf_nombre = ""
        hilos_ocupados_total = 0
        hilos_libres_total = 0
        
        for t in tramos:
            if (
                t.origen_nodo
                and t.origen_nodo.tipo == 'SITE'
                and hub_origen == "N/A"
            ):
                hub_origen = t.origen_nodo.nombre or t.origen_nodo.codigo
            elif t.hub_site and t.hub_site != "N/A" and hub_origen == "N/A":
                hub_origen = t.hub_site
            if (
                t.destino_nodo
                and destino == "N/A"
            ):
                destino = t.destino_nodo.nombre or t.destino_nodo.codigo
            elif t.destino and t.destino != "N/A" and destino == "N/A":
                destino = t.destino
            if t.marca_modelo and t.marca_modelo != "N/A" and marca_modelo == "N/A":
                marca_modelo = t.marca_modelo
            # La capacidad de un tramo aislado no representa capacidad de
            # extremo a extremo. El valor visible se completa únicamente con
            # el resumen operativo después de evaluar todos los tramos.
            if t.tipo_fibra and not tipo_fibra:
                tipo_fibra = t.tipo_fibra
            if t.serial and not serial:
                serial = t.serial
            if t.estado and not estado_tramo:
                estado_tramo = t.estado
            mufas_total += (t.mufas or 0)
            splitters_total += (t.splitters or 0)
            if t.odf_nombre and not odf_nombre:
                odf_nombre = t.odf_nombre
            hilos_ocupados_total += (t.hilos_ocupados or 0)
            hilos_libres_total += (t.hilos_libres or 0)
            
        # Una fibra lógica se cuenta una sola vez aunque atraviese varios
        # tramos. FibraTramo determina cobertura física, no multiplica cards.
        hilos_ocupados_total = resumen_capacidad['hilos_ocupados']
        hilos_reservados_total = resumen_capacidad['hilos_reservados']
        hilos_libres_total = resumen_capacidad['hilos_libres']
        if resumen_capacidad['capacidad_efectiva'] is not None:
            cap = resumen_capacidad['capacidad']
        
        stats_capacidad[cap] = stats_capacidad.get(cap, 0) + 1

        # Conteo dinámico de tipos de reservas (nodos/hitos)
        conteo_reservas_nodos = {}
        for res in r.reservas.all():
            tipo_res = res.tipo if res.tipo else "Otro"
            conteo_reservas_nodos[tipo_res] = conteo_reservas_nodos.get(tipo_res, 0) + 1
        
        hilos_contabilizados = (
            hilos_ocupados_total
            + hilos_reservados_total
            + hilos_libres_total
        )
        total_hilos_ruta = (
            resumen_capacidad['capacidad_efectiva']
            if resumen_capacidad['capacidad_efectiva'] is not None
            else hilos_contabilizados
        )
        utilizacion_hilos = round(
            (
                min(
                    hilos_ocupados_total + hilos_reservados_total,
                    total_hilos_ruta,
                )
                / total_hilos_ruta
            ) * 100,
            1,
        ) if total_hilos_ruta else 0

        resumen_rutas.append({
            'id': r.id,
            'nombre': r.nombre,
            'hub_origen': hub_origen,
            'destino': destino,
            'marca_modelo': marca_modelo,
            'tipo': tipo_final,
            'distancia_km': distancia_ruta_km,
            'capacidad': cap,
            'reservas_km': round(total_res_ruta_m / 1000, 3),
            'conteo_reservas': conteo_reservas_nodos,
            'tipo_fibra': tipo_fibra,
            'serial': serial,
            'estado': estado_tramo,
            'mufas': mufas_total,
            'splitters': splitters_total,
            'odf_nombre': odf_nombre,
            'hilos_ocupados': hilos_ocupados_total,
            'hilos_reservados': hilos_reservados_total,
            'hilos_libres': hilos_libres_total,
            'total_hilos': total_hilos_ruta,
            'hilos_contabilizados': hilos_contabilizados,
            'sin_inventariar': resumen_capacidad['sin_inventariar'],
            'hilos_sin_estado': resumen_capacidad['hilos_sin_estado'],
            'fibras_sin_cobertura': resumen_capacidad[
                'fibras_sin_cobertura'
            ],
            'conteos_consistentes': resumen_capacidad[
                'conteos_consistentes'
            ],
            'exceso_conteos': resumen_capacidad['exceso'],
            'utilizacion_hilos': utilizacion_hilos,
            'capacidad_efectiva': resumen_capacidad['capacidad_efectiva'],
            'capacidad_min': resumen_capacidad['capacidad_min'],
            'capacidad_max': resumen_capacidad['capacidad_max'],
            'capacidades_completas': resumen_capacidad[
                'capacidades_completas'
            ],
            'detalle_fibras_completo': resumen_capacidad[
                'detalle_fibras_completo'
            ],
            'fuente_capacidad': resumen_capacidad['fuente'],
            'fuente_capacidad_operativa': resumen_capacidad[
                'fuente_capacidad'
            ],
            'fuente_estados': resumen_capacidad['fuente_estados'],
            'fuente_distancia': resumen_capacidad['fuente_distancia'],
            'fuente_reservas': resumen_capacidad['fuente_reservas'],
            'estado_conciliacion_capacidad': resumen_capacidad[
                'estado_conciliacion_capacidad'
            ],
            'nivel_capacidad': (
                'critico' if utilizacion_hilos >= 85
                else 'atencion' if utilizacion_hilos >= 70
                else 'disponible'
            ),
        })

    # Totales para los cards superiores
    total_km_global = sum(r['distancia_km'] for r in resumen_rutas)
    total_res_global_km = sum(r['reservas_km'] for r in resumen_rutas)

    rutas_ids = [ruta['id'] for ruta in resumen_rutas]
    tramos_filtrados = InventarioTramo.objects.filter(ruta_id__in=rutas_ids)
    reservas_filtradas = Reserva.objects.filter(ruta_id__in=rutas_ids)
    coordenadas_filtradas = CoordenadaRuta.objects.filter(ruta_id__in=rutas_ids)
    odfs_filtrados = InventarioODF.objects.all()
    if site_seleccionado:
        odfs_filtrados = odfs_filtrados.filter(
            rack_obj__sala__hub_site__nombre=site_seleccionado
        )
    puertos_filtrados = DetallePuertoODF.objects.filter(odf_obj__in=odfs_filtrados)

    conteo_puertos = defaultdict(int)
    for fila in puertos_filtrados.values('estado_puerto').annotate(total=Count('id')):
        conteo_puertos[(fila['estado_puerto'] or '').strip().lower()] += fila['total']

    puertos_libres = conteo_puertos['libre']
    puertos_ocupados = conteo_puertos['ocupado']
    puertos_reservados = conteo_puertos['reservado']
    total_puertos = puertos_libres + puertos_ocupados + puertos_reservados
    resumenes_fibras_logicas = [
        capacidad_por_ruta[ruta_id]
        for ruta_id in rutas_ids
        if (
            capacidad_por_ruta[ruta_id]['capacidad_efectiva'] is not None
            or capacidad_por_ruta[ruta_id]['fibras_total']
        )
    ]
    fibras_dashboard = InventarioFibra.objects.all()
    if site_seleccionado:
        fibras_dashboard = fibras_dashboard.filter(
            Q(
                terminaciones__puerto_odf__odf_obj__rack_obj__sala__hub_site__nombre__iexact=site_seleccionado
            )
            | Q(ruta__tramos_inventario__hub_site__iexact=site_seleccionado)
            | Q(ruta__tramos_inventario__origen__iexact=site_seleccionado)
            | Q(ruta__tramos_inventario__destino__iexact=site_seleccionado)
            | Q(ruta__tramos_inventario__origen_nodo__codigo__iexact=site_seleccionado)
            | Q(ruta__tramos_inventario__origen_nodo__nombre__iexact=site_seleccionado)
            | Q(ruta__tramos_inventario__destino_nodo__codigo__iexact=site_seleccionado)
            | Q(ruta__tramos_inventario__destino_nodo__nombre__iexact=site_seleccionado)
        )
    if trazado_seleccionado:
        fibras_dashboard = fibras_dashboard.filter(ruta_id__in=rutas_ids)

    # El estado global oficial vive en InventarioFibra. La capacidad declarada
    # de las rutas se mantiene para los indicadores de troncales, pero no debe
    # ocultar fibras válidas cuya Ruta todavía está pendiente.
    conteo_fibras = fibras_dashboard.aggregate(
        total=Count('id', distinct=True),
        libres=Count(
            'id', filter=Q(estado='DISPONIBLE'), distinct=True,
        ),
        ocupadas=Count(
            'id', filter=Q(estado='OCUPADO'), distinct=True,
        ),
        reservadas=Count(
            'id', filter=Q(estado='RESERVADO'), distinct=True,
        ),
        sin_estado=Count(
            'id', filter=Q(estado='SIN_INFORMACION'), distinct=True,
        ),
        pendientes_troncal=Count(
            'id', filter=Q(ruta__isnull=True), distinct=True,
        ),
    )
    fibras_libres = conteo_fibras['libres']
    fibras_ocupadas = conteo_fibras['ocupadas']
    fibras_reservadas = conteo_fibras['reservadas']
    fibras_sin_estado = conteo_fibras['sin_estado']
    fibras_sin_cobertura = sum(
        resumen['fibras_sin_cobertura']
        for resumen in resumenes_fibras_logicas
    ) + conteo_fibras['pendientes_troncal']
    total_fibras = conteo_fibras['total']

    odfs_data = [
        {
            'id': odf.pk,
            'odf': odf.odf,
            'hub_site': nombre_site(odf),
            'sala': odf.rack_obj.sala.nombre,
            'rack': odf.rack_obj.nombre,
            'capacidad_puertos': odf.capacidad_puertos,
            'puertos_ocupados': odf.puertos_ocupados,
            'puertos_libres': odf.puertos_libres,
            'puertos_reservados': odf.puertos_reservados,
        }
        for odf in odfs_filtrados.select_related(
            'rack_obj__sala__hub_site'
        ).order_by('odf')
    ]
    total_odfs = len(odfs_data)
    total_salas = len({
        (odf['hub_site'], odf['sala']) for odf in odfs_data
        if odf['sala'] and odf['sala'].strip()
    })
    total_racks = len({
        (odf['hub_site'], odf['sala'], odf['rack']) for odf in odfs_data
        if odf['rack'] and odf['rack'].strip()
    })

    top_rutas_capacidad = sorted(
        (ruta for ruta in resumen_rutas if ruta['total_hilos']),
        key=lambda ruta: (ruta['utilizacion_hilos'], ruta['hilos_ocupados']),
        reverse=True,
    )[:5]
    odfs_capacidad_data = []
    for odf in odfs_data:
        capacidad_declarada = odf['capacidad_puertos'] or 0
        ocupados = odf['puertos_ocupados'] or 0
        libres = odf['puertos_libres'] or 0
        reservados = odf['puertos_reservados'] or 0
        capacidad_observada = ocupados + libres + reservados
        capacidad_total = max(capacidad_declarada, capacidad_observada)
        utilizados = ocupados + reservados
        if not capacidad_total:
            continue
        odfs_capacidad_data.append({
            'id': odf['id'],
            'odf': odf['odf'],
            'hub_site': odf['hub_site'],
            'capacidad_total': capacidad_total,
            'ocupados': ocupados,
            'reservados': reservados,
            'libres': max(capacidad_total - utilizados, 0),
            'utilizados': utilizados,
            'utilizacion_pct': round(utilizados / capacidad_total * 100, 1),
        })
    top_odfs_capacidad = sorted(
        odfs_capacidad_data,
        key=lambda odf: (odf['utilizacion_pct'], odf['utilizados']),
        reverse=True,
    )[:5]

    puertos_utilizados = puertos_ocupados + puertos_reservados
    fibras_utilizadas = fibras_ocupadas + fibras_reservadas
    utilizacion_puertos_pct = round(
        puertos_utilizados / total_puertos * 100, 1
    ) if total_puertos else 0
    disponibilidad_puertos_pct = round(
        puertos_libres / total_puertos * 100, 1
    ) if total_puertos else 0
    utilizacion_fibras_pct = round(
        fibras_utilizadas / total_fibras * 100, 1
    ) if total_fibras else 0
    ocupacion_fibras_pct = round(
        fibras_ocupadas / total_fibras * 100, 1
    ) if total_fibras else 0
    disponibilidad_fibras_pct = round(
        fibras_libres / total_fibras * 100, 1
    ) if total_fibras else 0
    rutas_con_fibras = sum(1 for ruta in resumen_rutas if ruta['total_hilos'])
    odfs_con_capacidad = len(odfs_capacidad_data)
    odfs_con_disponibilidad = sum(1 for odf in odfs_capacidad_data if odf['libres'] > 0)
    odfs_sin_disponibilidad = sum(1 for odf in odfs_capacidad_data if odf['libres'] == 0)
    odfs_capacidad_atencion = sum(
        1 for odf in odfs_capacidad_data if 80 <= odf['utilizacion_pct'] < 100
    )
    odfs_saturados = sum(1 for odf in odfs_capacidad_data if odf['utilizacion_pct'] >= 100)
    odfs_disponibilidad_pct = round(
        odfs_con_disponibilidad / odfs_con_capacidad * 100, 1
    ) if odfs_con_capacidad else 0
    rutas_con_disponibilidad = sum(
        1 for ruta in resumen_rutas if ruta['total_hilos'] and ruta['hilos_libres'] > 0
    )
    rutas_sin_disponibilidad = sum(
        1 for ruta in resumen_rutas if ruta['total_hilos'] and ruta['hilos_libres'] == 0
    )
    rutas_capacidad_atencion = sum(
        1 for ruta in resumen_rutas
        if ruta['total_hilos'] and 80 <= ruta['utilizacion_hilos'] < 100
    )
    rutas_saturadas = sum(
        1 for ruta in resumen_rutas
        if ruta['total_hilos'] and ruta['utilizacion_hilos'] >= 100
    )
    rutas_disponibilidad_pct = round(
        rutas_con_disponibilidad / rutas_con_fibras * 100, 1
    ) if rutas_con_fibras else 0
    rutas_alta_ocupacion = sum(
        1 for ruta in resumen_rutas if ruta['total_hilos'] and ruta['utilizacion_hilos'] >= 85
    )
    odfs_alta_ocupacion = sum(
        1 for odf in odfs_data
        if (odf['capacidad_puertos'] or 0) > 0
        and ((odf['puertos_ocupados'] or 0) / odf['capacidad_puertos']) >= .85
    )

    sites_disponibles = list(
        HubSite.objects.values('id', 'nombre', 'latitud', 'longitud').order_by('nombre')
    )
    nombres_sites = {
        site['nombre'].strip().casefold()
        for site in sites_disponibles
        if site['nombre'] and site['nombre'].strip()
    }
    for nodo in NodoRed.objects.filter(tipo='SITE').values(
        'id',
        'codigo',
        'nombre',
    ).order_by('codigo'):
        codigo = str(nodo['codigo'] or '').strip()
        nombre = str(nodo['nombre'] or '').strip()
        if (
            codigo.casefold() in nombres_sites
            or nombre.casefold() in nombres_sites
        ):
            continue
        etiqueta = nombre or codigo
        if not etiqueta:
            continue
        sites_disponibles.append({
            'id': f"nodo-{nodo['id']}",
            'nombre': etiqueta,
            'latitud': None,
            'longitud': None,
        })
        nombres_sites.add(etiqueta.casefold())
    sites_disponibles.sort(key=lambda site: site['nombre'].casefold())
    sites = [
        site for site in sites_disponibles
        if not site_seleccionado or site['nombre'] == site_seleccionado
    ]
    total_sites = len(sites)
    sites_georreferenciados = sum(
        1 for site in sites if site['latitud'] is not None and site['longitud'] is not None
    )
    sites_georreferenciados_pct = round(
        sites_georreferenciados / total_sites * 100, 1
    ) if total_sites else 0
    sites_sin_coordenadas = total_sites - sites_georreferenciados
    rutas_sin_geometria = sum(1 for ruta in rutas_db if len(ruta.coordenadas.all()) < 2)
    odfs_sin_ubicacion = sum(
        1 for odf in odfs_data if not odf['hub_site'] or not odf['sala'] or not odf['rack']
    )

    controles_calidad = total_rutas + total_odfs + total_sites
    incidencias_calidad = min(
        rutas_sin_geometria + odfs_sin_ubicacion + sites_sin_coordenadas,
        controles_calidad,
    )
    calidad_inventario = round(
        (1 - incidencias_calidad / controles_calidad) * 100
    ) if controles_calidad else 0

    inventario_con_datos = bool(
        controles_calidad
        or total_fibras
        or total_puertos
    )

    alertas_inventario = []
    if not inventario_con_datos:
        alertas_inventario.append({
            'nivel': 'datos',
            'titulo': 'Inventario sin registros',
            'detalle': 'Importa o registra activos para habilitar los indicadores operativos.',
        })
    if rutas_alta_ocupacion:
        alertas_inventario.append({
            'nivel': 'critico',
            'titulo': f'{rutas_alta_ocupacion} troncales con alta ocupacion',
            'detalle': 'Superan el 85 % de fibras ocupadas o reservadas.',
        })
    if odfs_alta_ocupacion:
        alertas_inventario.append({
            'nivel': 'atencion',
            'titulo': f'{odfs_alta_ocupacion} ODF con capacidad limitada',
            'detalle': 'Superan el 85 % de puertos ocupados.',
        })
    if rutas_sin_geometria:
        alertas_inventario.append({
            'nivel': 'datos',
            'titulo': f'{rutas_sin_geometria} troncales sin recorrido completo en el mapa',
            'detalle': 'Necesitan al menos dos coordenadas para visualizarse correctamente.',
        })
    if sites_sin_coordenadas:
        alertas_inventario.append({
            'nivel': 'datos',
            'titulo': f'{sites_sin_coordenadas} sites sin georreferenciar',
            'detalle': 'No aparecen en el mapa de cobertura del inventario.',
        })

    total_tramos = sum(len(ruta.tramos_inventario.all()) for ruta in rutas_db)
    total_reservas = sum(len(ruta.reservas.all()) for ruta in rutas_db)
    total_coordenadas = sum(len(ruta.coordenadas.all()) for ruta in rutas_db)

    context = {
        'total_rutas': total_rutas,
        'total_km': round(total_km_global, 2),
        'total_reservas_km': round(total_res_global_km, 3),
        'resumen_rutas': resumen_rutas,
        'trazado_labels': list(distancia_trazado.keys()),
        'trazado_km_values': list(distancia_trazado.values()),
        'trazado_count_values': list(stats_trazado.values()),
        'capacidad_labels': list(stats_capacidad.keys()),
        'capacidad_count_values': list(stats_capacidad.values()),
        
        # Nuevas estadísticas para gráficas
        'total_odfs': total_odfs,
        'total_salas': total_salas,
        'total_racks': total_racks,
        'total_sites': total_sites,
        'total_tramos': total_tramos,
        'total_reservas': total_reservas,
        'total_coordenadas': total_coordenadas,
        'sites_georreferenciados': sites_georreferenciados,
        'sites_georreferenciados_pct': sites_georreferenciados_pct,
        'puertos_libres': puertos_libres,
        'puertos_ocupados': puertos_ocupados,
        'puertos_reservados': puertos_reservados,
        'puertos_utilizados': puertos_utilizados,
        'total_puertos': total_puertos,
        'ocupacion_puertos_pct': round(puertos_ocupados / total_puertos * 100, 1) if total_puertos else 0,
        'utilizacion_puertos_pct': utilizacion_puertos_pct,
        'disponibilidad_puertos_pct': disponibilidad_puertos_pct,
        'fibras_libres': fibras_libres,
        'fibras_ocupadas': fibras_ocupadas,
        'fibras_reservadas': fibras_reservadas,
        'fibras_sin_estado': fibras_sin_estado,
        'fibras_sin_cobertura': fibras_sin_cobertura,
        'fibras_utilizadas': fibras_utilizadas,
        'total_fibras': total_fibras,
        'ocupacion_fibras_pct': ocupacion_fibras_pct,
        'utilizacion_fibras_pct': utilizacion_fibras_pct,
        'disponibilidad_fibras_pct': disponibilidad_fibras_pct,
        'rutas_con_fibras': rutas_con_fibras,
        'rutas_con_disponibilidad': rutas_con_disponibilidad,
        'rutas_sin_disponibilidad': rutas_sin_disponibilidad,
        'rutas_capacidad_atencion': rutas_capacidad_atencion,
        'rutas_saturadas': rutas_saturadas,
        'rutas_disponibilidad_pct': rutas_disponibilidad_pct,
        'odfs_con_capacidad': odfs_con_capacidad,
        'odfs_con_disponibilidad': odfs_con_disponibilidad,
        'odfs_sin_disponibilidad': odfs_sin_disponibilidad,
        'odfs_capacidad_atencion': odfs_capacidad_atencion,
        'odfs_saturados': odfs_saturados,
        'odfs_disponibilidad_pct': odfs_disponibilidad_pct,
        'calidad_inventario': calidad_inventario,
        'inventario_con_datos': inventario_con_datos,
        'alertas_inventario': alertas_inventario[:4],
        'top_rutas_capacidad': top_rutas_capacidad,
        'top_odfs_capacidad': top_odfs_capacidad,
        'rutas_dashboard_nombres': [ruta['nombre'] for ruta in resumen_rutas],
        'site_seleccionado': site_seleccionado,
        'trazado_seleccionado': trazado_seleccionado,
        'sites_disponibles': sites_disponibles,
        'inventario_tipo_labels': ['ODF', 'Puertos', 'Tramos', 'Fibras', 'Reservas', 'Coordenadas'],
        'inventario_tipo_values': [
            total_odfs, total_puertos, total_tramos, total_fibras,
            total_reservas, total_coordenadas,
        ],
        'odf_list': [{'odf': odf['odf'], 'hub_site': odf['hub_site']} for odf in odfs_data],
        'hub_sites': [site['nombre'] for site in sites_disponibles],
        'capacity_links': {
            'odfs_disponibles': (
                f"{reverse('inventario_interno')}?"
                f"{urlencode({
                    'capacidad': 'disponible',
                    **({'site': site_seleccionado} if site_seleccionado else {}),
                })}"
            ),
            'odfs_sin_disponibilidad': (
                f"{reverse('inventario_interno')}?"
                f"{urlencode({
                    'capacidad': 'sin_disponibilidad',
                    **({'site': site_seleccionado} if site_seleccionado else {}),
                })}"
            ),
            'rutas_disponibles': (
                f"{reverse('inventario_externo')}?"
                f"{urlencode({
                    'tab': 'troncales',
                    'capacidad': 'disponible',
                    **({'site': site_seleccionado} if site_seleccionado else {}),
                    **({'tipo': trazado_seleccionado} if trazado_seleccionado else {}),
                })}"
            ),
            'rutas_atencion': (
                f"{reverse('inventario_externo')}?"
                f"{urlencode({
                    'tab': 'troncales',
                    'capacidad': 'atencion',
                    **({'site': site_seleccionado} if site_seleccionado else {}),
                    **({'tipo': trazado_seleccionado} if trazado_seleccionado else {}),
                })}"
            ),
        },
    }
    return render(request, 'mapa_inventario/dashboard_inventario.html', context)

@login_required
@permission_required('mapas.view_ruta', raise_exception=True)
def inventario_externo(request):
    """Inventario paginado de troncales y tramos."""
    from collections import defaultdict
    from ..models import CoordenadaRuta, InventarioTramo, Ruta

    tramos = InventarioTramo.objects.all()

    def opciones(campo):
        return list(
            tramos.exclude(**{f'{campo}__isnull': True})
            .exclude(**{campo: ''})
            .order_by(campo)
            .values_list(campo, flat=True)
            .distinct()
        )

    sites_troncal = set(opciones('hub_site'))
    for prefijo in ('origen_nodo', 'destino_nodo'):
        for codigo, nombre in (
            tramos.filter(**{f'{prefijo}__tipo': 'SITE'})
            .values_list(f'{prefijo}__codigo', f'{prefijo}__nombre')
            .distinct()
        ):
            if codigo:
                sites_troncal.add(codigo)
            if nombre:
                sites_troncal.add(nombre)

    tipos_trazado = set(opciones('tipo_trazado'))
    tipos_geograficos_por_ruta = defaultdict(set)
    for ruta_id, tipo in (
        CoordenadaRuta.objects.exclude(tipo_trazado__isnull=True)
        .exclude(tipo_trazado='')
        .values_list('ruta_id', 'tipo_trazado')
        .distinct()
    ):
        tipos_geograficos_por_ruta[ruta_id].add(tipo.strip().upper())
    if any(
        'HIBRIDO' in tipos
        or {'AEREO', 'SOTERRADO'}.issubset(tipos)
        for tipos in tipos_geograficos_por_ruta.values()
    ):
        tipos_trazado.add('HIBRIDO')

    context = {
        'rutas_inventario': list(
            Ruta.objects.order_by('nombre').values('id', 'nombre')
        ),
        'tipos_trazado': sorted(tipos_trazado, key=str.casefold),
        'estados_tramo': opciones('estado'),
        'sites_troncal': sorted(sites_troncal, key=str.casefold),
    }
    return render(request, 'mapa_inventario/inventario_externo.html', context)

@login_required
@permission_required('mapas.view_inventarioodf', raise_exception=True)
def inventario_interno(request):
    """Inventario de ODF; los registros se solicitan por página."""
    from ..models import InventarioODF, Ruta

    queryset = InventarioODF.objects.all()
    def opciones(campo):
        ruta_campo = {
            'hub_site': 'rack_obj__sala__hub_site__nombre',
            'sala': 'rack_obj__sala__nombre',
            'rack': 'rack_obj__nombre',
            'estado': 'estado',
        }[campo]
        return list(
            queryset.exclude(**{f'{ruta_campo}__isnull': True})
            .exclude(**{ruta_campo: ''})
            .order_by(ruta_campo)
            .values_list(ruta_campo, flat=True)
            .distinct()
        )

    context = {
        'sites_odf': opciones('hub_site'),
        'salas_odf': opciones('sala'),
        'racks_odf': opciones('rack'),
        'estados_odf': opciones('estado'),
    }
    return render(request, 'mapa_inventario/inventario_interno.html', context)
@gzip_page
@login_required
@permission_required('mapas.view_ruta', raise_exception=True)
def get_datos_inventario(request):
    """
    Retorna JSON con la estructura de rutas segmentadas y su metadata.
    """
    from collections import defaultdict
    from ..models import HubSite, Reserva
    from ..services.capacidad import capacidad_hilos

    # El mapa necesita miles de coordenadas, pero no las instancias completas de
    # Django. Consultar solo las columnas utilizadas evita construir más de
    # 10 000 modelos en memoria y mantiene exactamente el mismo contrato JSON.
    rutas = list(
        Ruta.objects.filter(coordenadas__isnull=False)
        .order_by('nombre')
        .values('id', 'nombre', 'otu__nombre', 'olt', 'pon', 'distancia_m')
        .distinct()
    )
    rutas_ids = [ruta['id'] for ruta in rutas]

    coordenadas_por_ruta = defaultdict(list)
    for coordenada in (
        CoordenadaRuta.objects.filter(ruta_id__in=rutas_ids)
        .order_by('ruta_id', 'orden')
        .values(
            'ruta_id', 'tramo_secuencia', 'tipo_trazado',
            'inicio_segmento', 'latitud', 'longitud',
        )
    ):
        coordenadas_por_ruta[coordenada['ruta_id']].append(coordenada)

    campos_tramo = (
        'ruta_id', 'tramo_secuencia', 'tipo_trazado', 'estado', 'distancia_m',
        'mufas', 'splitters', 'reservas_m', 'capacidad', 'tipo_fibra',
        'origen', 'destino', 'hub_site', 'marca_modelo', 'serial', 'odf_nombre',
        'capacidad_hilos',
        'origen_nodo__tipo', 'origen_nodo__codigo', 'origen_nodo__nombre',
        'destino_nodo__tipo', 'destino_nodo__codigo', 'destino_nodo__nombre',
    )
    tramos_por_ruta = defaultdict(list)
    for tramo in (
        InventarioTramo.objects.filter(ruta_id__in=rutas_ids)
        .order_by('ruta_id', 'tramo_secuencia')
        .values(*campos_tramo)
    ):
        tramos_por_ruta[tramo['ruta_id']].append(tramo)

    reservas_por_ruta = defaultdict(list)
    for reserva in (
        Reserva.objects.filter(ruta_id__in=rutas_ids)
        .order_by('ruta_id', 'pk')
        .values('ruta_id', 'nombre', 'tipo', 'reserva_m', 'latitud', 'longitud')
    ):
        reservas_por_ruta[reserva['ruta_id']].append(reserva)

    sites = list(
        HubSite.objects.filter(latitud__isnull=False, longitud__isnull=False)
        .order_by('nombre')
        .values('id', 'nombre', 'latitud', 'longitud')
    )
    sites_por_nombre = {
        site['nombre'].casefold(): site
        for site in sites
    }
    resultado = []

    for ruta in rutas:
        # `tramos` se conserva como alias JSON para no cambiar la GUI. Desde
        # esta fase representa segmentos visuales derivados de coordenadas, no
        # filas de InventarioTramo.
        segmentos_geograficos = []
        segmento_actual = None
        tipo_anterior = None
        secuencia_legacy_anterior = None
        for coordenada in coordenadas_por_ruta[ruta['id']]:
            tipo_geografico_raw = (
                str(coordenada['tipo_trazado']).strip().upper()
                if coordenada['tipo_trazado']
                else ''
            )
            tipo_geografico = (
                'SIN CLASIFICAR'
                if tipo_geografico_raw in {'', 'DESCONOCIDO'}
                else tipo_geografico_raw
            )
            secuencia_legacy = coordenada['tramo_secuencia'] or 1
            nuevo_segmento = (
                segmento_actual is None
                or coordenada['inicio_segmento']
                or tipo_geografico != tipo_anterior
                # Compatibilidad con coordenadas creadas por código anterior a
                # la migración o fixtures que solo completan la secuencia.
                or secuencia_legacy != secuencia_legacy_anterior
            )
            if nuevo_segmento:
                ancla_continuidad = (
                    segmento_actual['coordenadas'][-1]
                    if segmento_actual
                    and segmento_actual['coordenadas']
                    else None
                )
                segmento_actual = {
                    'secuencia': len(segmentos_geograficos) + 1,
                    'tipo_trazado': tipo_geografico,
                    # El punto anterior es el origen de la arista que llega al
                    # nuevo corte. Duplicarlo evita huecos entre polilíneas.
                    'coordenadas': (
                        [ancla_continuidad]
                        if ancla_continuidad
                        else []
                    ),
                }
                segmentos_geograficos.append(segmento_actual)

            segmento_actual['coordenadas'].append([
                float(coordenada['latitud']),
                float(coordenada['longitud']),
            ])
            tipo_anterior = tipo_geografico
            secuencia_legacy_anterior = secuencia_legacy

        if not segmentos_geograficos:
            continue

        # La metadata técnica se mantiene como fallback de compatibilidad para
        # el popup actual, pero nunca determina cortes ni colores del mapa.
        tramos_tecnicos = tramos_por_ruta[ruta['id']]
        tramo_principal = dict(tramos_tecnicos[0]) if tramos_tecnicos else None
        if tramo_principal:
            capacidades = [
                capacidad_hilos(tramo)
                for tramo in tramos_tecnicos
                if capacidad_hilos(tramo) is not None
            ]
            if capacidades and len(capacidades) == len(tramos_tecnicos):
                minima = min(capacidades)
                maxima = max(capacidades)
                tramo_principal['capacidad'] = (
                    f'{minima} Hilos'
                    if minima == maxima
                    else f'{minima} Hilos efectivos ({minima}–{maxima})'
                )
            elif tramos_tecnicos:
                tramo_principal['capacidad'] = None
            tramo_principal['distancia_m'] = sum(
                tramo['distancia_m'] or 0 for tramo in tramos_tecnicos
            ) or tramo_principal.get('distancia_m')
            tramo_principal['mufas'] = sum(
                tramo['mufas'] or 0 for tramo in tramos_tecnicos
            )
            tramo_principal['splitters'] = sum(
                tramo['splitters'] or 0 for tramo in tramos_tecnicos
            )
            tramo_principal['reservas_m'] = sum(
                tramo['reservas_m'] or 0 for tramo in tramos_tecnicos
            )
            ultimo_tramo = tramos_tecnicos[-1]
            tramo_principal['destino'] = (
                ultimo_tramo.get('destino')
                or tramo_principal.get('destino')
            )

        # El contrato JSON histórico expone un único `hub_site`. Para tramos
        # creados con la topología F2, donde ese texto legacy puede estar vacío,
        # se deriva del primer NodoRed tipo SITE sin cambiar las claves que
        # consume la GUI del mapa.
        candidatos_site_nodo = []
        for tramo_tecnico in tramos_tecnicos:
            for extremo in ('origen_nodo', 'destino_nodo'):
                if tramo_tecnico.get(f'{extremo}__tipo') != 'SITE':
                    continue
                for campo in ('nombre', 'codigo'):
                    candidato = str(
                        tramo_tecnico.get(f'{extremo}__{campo}') or ''
                    ).strip()
                    if candidato and candidato not in candidatos_site_nodo:
                        candidatos_site_nodo.append(candidato)
        
        tramos_list = []
        for segmento in segmentos_geograficos:
            sec = segmento['secuencia']
            coords = segmento['coordenadas']
            # No existe una correspondencia ordinal garantizada entre el
            # segmento visual N y el tramo técnico N. Mientras el frontend
            # mantenga su contrato legado, se muestra el resumen principal de
            # la ruta en todos los segmentos.
            meta = tramo_principal
            
            def get_val(field):
                val = meta.get(field) if meta else None
                if val in [None, '', 'N/A'] and tramo_principal:
                    val = tramo_principal.get(field)
                return val
            
            hub_site_name = get_val('hub_site')
            if not hub_site_name and candidatos_site_nodo:
                hub_site_name = next(
                    (
                        candidato
                        for candidato in candidatos_site_nodo
                        if candidato.casefold() in sites_por_nombre
                    ),
                    candidatos_site_nodo[0],
                )
            hub_site_lat = None
            hub_site_lon = None
            hub_site_id = None
            if hub_site_name:
                hs = sites_por_nombre.get(hub_site_name.casefold())
                if hs and hs['latitud'] and hs['longitud']:
                    hub_site_id = hs['id']
                    hub_site_lat = float(hs['latitud'])
                    hub_site_lon = float(hs['longitud'])

            tramo_obj = {
                "tramo_secuencia": sec,
                "tipo_trazado": segmento['tipo_trazado'],
                "estado": get_val('estado') or "Desconocido",
                "distancia_m": get_val('distancia_m'),
                "mufas": get_val('mufas') or 0,
                "splitters": get_val('splitters') or 0,
                "reservas_m": get_val('reservas_m') or 0.0,
                "capacidad": get_val('capacidad') or "N/A",
                "tipo_fibra": get_val('tipo_fibra') or "N/A",
                "origen": get_val('origen'),
                "destino": get_val('destino'),
                "hub_site": hub_site_name,
                "hub_site_id": hub_site_id,
                "hub_site_lat": hub_site_lat,
                "hub_site_lon": hub_site_lon,
                "marca_modelo": get_val('marca_modelo'),
                "serial": get_val('serial'),
                "odf_nombre": get_val('odf_nombre'),
                "coordenadas": coords
            }
            tramos_list.append(tramo_obj)
            
        reservas_list = []
        for reserva in reservas_por_ruta[ruta['id']]:
            reservas_list.append({
                "nombre": reserva['nombre'],
                "tipo": reserva['tipo'] if reserva['tipo'] else "Desconocido",
                "reserva_m": reserva['reserva_m'] if reserva['reserva_m'] else 0.0,
                "lat": float(reserva['latitud']),
                "lon": float(reserva['longitud'])
            })
            
        resultado.append({
            "nombre": ruta['nombre'],
            "otu": ruta['otu__nombre'] if ruta['otu__nombre'] else "N/A",
            "olt": ruta['olt'] if ruta['olt'] else "N/A",
            "pon": ruta['pon'] if ruta['pon'] else "N/A",
            "distancia_m": (
                ruta['distancia_m']
                if ruta['distancia_m'] is not None
                else (
                    tramo_principal.get('distancia_m')
                    if tramo_principal
                    else None
                )
            ),
            "tramos": tramos_list,
            "reservas_nodos": reservas_list
        })

    sites_data = [
        {
            "id": site['id'],
            "nombre": site['nombre'],
            "lat": float(site['latitud']),
            "lon": float(site['longitud']),
        }
        for site in sites_por_nombre.values()
    ]
    return JsonResponse({"status": "success", "data": resultado, "sites": sites_data})

@login_required
@permission_required('mapas.view_inventariofibra', raise_exception=True)
def get_detalle_fibras(request, ruta_nombre):
    """Retorna el detalle de hilos/fibras para una ruta específica."""
    from ..models import InventarioFibra, InventarioTramo
    
    tramo = InventarioTramo.objects.filter(ruta__nombre=ruta_nombre).order_by('tramo_secuencia').first()
    origen_ruta = "-"
    if tramo:
        if tramo.odf_nombre and tramo.odf_nombre.strip() and tramo.odf_nombre != "N/A":
            origen_ruta = tramo.odf_nombre
        elif tramo.hub_site and tramo.hub_site.strip() and tramo.hub_site != "N/A":
            origen_ruta = tramo.hub_site
        elif tramo.origen and tramo.origen.strip() and tramo.origen != "N/A":
            origen_ruta = tramo.origen
    
    fibras = list(
        InventarioFibra.objects.filter(ruta__nombre=ruta_nombre)
        .select_related('ruta')
        .prefetch_related(
            'asignaciones_tramo__tramo',
            'terminaciones__puerto_odf__odf_obj__rack_obj__sala__hub_site',
            'ruta__tramos_inventario__origen_nodo__hub_site_obj',
            'ruta__tramos_inventario__destino_nodo__hub_site_obj',
        )
        .order_by('fibra_numero')
    )
    fibras.sort(key=lambda fibra: _clave_orden_natural(fibra.fibra_numero))
    
    data = []
    for f in fibras:
        asignaciones = list(f.asignaciones_tramo.all())
        presentacion = obtener_presentacion_orientada_fibra(f)
        origen = presentacion['origen']
        destino = presentacion['destino']
        etiqueta = lambda item: (
            f"{item['odf']} / Puerto {item['puerto']}"
            if item else 'Pendiente de orientación'
        )
        data.append({
            'id': f.id,
            'fibra_numero': f.fibra_numero,
            'estado': f.estado,
            'origen_estado': f.origen_estado,
            'condicion_fisica': f.condicion_fisica,
            'nombre_fibra': f.nombre_fibra,
            'observaciones': f.observaciones,
            'origen_odf': etiqueta(origen),
            'destino': etiqueta(destino),
            'site_inicial': presentacion['site_origen'],
            'site_final': presentacion['site_destino'],
            'terminacion_a': presentacion['terminacion_a'],
            'terminacion_b': presentacion['terminacion_b'],
            'orientacion': presentacion,
            'trazabilidad': obtener_trazabilidad_fibra(f),
            'tipo_conector': f.tipo_conector,
            'tramo_ids': [asignacion.tramo_id for asignacion in asignaciones],
            'codigos_tramo': [
                asignacion.tramo.codigo_tramo
                or f'TRAMO-{asignacion.tramo.tramo_secuencia:03d}'
                for asignacion in asignaciones
            ],
            'tramos': [
                {
                    'id': asignacion.tramo_id,
                    'codigo': (
                        asignacion.tramo.codigo_tramo
                        or f'TRAMO-{asignacion.tramo.tramo_secuencia:03d}'
                    ),
                    'numero_hilo': asignacion.numero_hilo,
                    'estado': asignacion.estado,
                }
                for asignacion in asignaciones
            ],
        })
        
    return JsonResponse({"status": "success", "data": data})


def _lista_seleccion_fibra(valor, nombre):
    if isinstance(valor, str):
        valor = [item.strip() for item in valor.split(',') if item.strip()]
    if not isinstance(valor, (list, tuple)):
        raise ValidationError(f"{nombre} debe ser una lista.")
    if not valor:
        raise ValidationError("Selecciona al menos un tramo técnico.")
    return list(valor)


def _seleccionar_tramos_fibra(data, tramos):
    usa_ids = 'tramo_ids' in data and data.get('tramo_ids') is not None
    usa_codigos = (
        'codigos_tramo' in data
        and data.get('codigos_tramo') is not None
    )
    if usa_ids and usa_codigos:
        raise ValidationError(
            "Envía tramo_ids o codigos_tramo, pero no ambos."
        )
    if not usa_ids and not usa_codigos:
        return tramos

    if usa_ids:
        valores = _lista_seleccion_fibra(data.get('tramo_ids'), 'tramo_ids')
        try:
            solicitados = {int(valor) for valor in valores}
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "Todos los tramo_ids deben ser números enteros."
            ) from exc
        if any(valor < 1 for valor in solicitados):
            raise ValidationError("Los tramo_ids deben ser mayores que cero.")
        encontrados = {tramo.pk for tramo in tramos}
        faltantes = solicitados - encontrados
        if faltantes:
            raise ValidationError(
                "Hay tramos que no pertenecen a la troncal seleccionada: "
                + ", ".join(str(valor) for valor in sorted(faltantes))
            )
        return [tramo for tramo in tramos if tramo.pk in solicitados]

    valores = _lista_seleccion_fibra(
        data.get('codigos_tramo'),
        'codigos_tramo',
    )
    solicitados = {
        str(valor or '').strip().upper()
        for valor in valores
        if str(valor or '').strip()
    }
    if not solicitados:
        raise ValidationError("Selecciona al menos un tramo técnico.")
    disponibles = {
        (
            tramo.codigo_tramo
            or f'TRAMO-{tramo.tramo_secuencia:03d}'
        ).strip().upper(): tramo
        for tramo in tramos
    }
    faltantes = solicitados - set(disponibles)
    if faltantes:
        raise ValidationError(
            "No existen estos códigos de tramo en la troncal: "
            + ", ".join(sorted(faltantes))
        )
    return [
        tramo
        for tramo in tramos
        if (
            tramo.codigo_tramo
            or f'TRAMO-{tramo.tramo_secuencia:03d}'
        ).strip().upper() in solicitados
    ]


@login_required
@permission_required('mapas.add_inventariofibra', raise_exception=True)
@require_POST
@transaction.atomic
def create_detalle_fibra(request):
    """Crea una fibra; la troncal puede quedar pendiente."""
    import json
    try:
        data = json.loads(request.body)
        from ..models import InventarioFibra
        from ..services.fibras import (
            asignar_fibra_a_tramos,
            crear_fibra,
            normalizar_condicion_fisica,
            normalizar_estado_fibra,
            normalizar_numero_hilo,
        )

        ruta_nombre = str(data.get('ruta_nombre', '')).strip()
        fibra_numero, _ = normalizar_numero_hilo(data.get('fibra_numero'))
        ruta = None
        if ruta_nombre:
            ruta = Ruta.objects.select_for_update().filter(
                nombre__iexact=ruta_nombre
            ).first()
        if ruta_nombre and not ruta:
            return JsonResponse(
                {"status": "error", "message": "Ruta no encontrada"},
                status=404,
            )
        if ruta and InventarioFibra.objects.filter(
            ruta=ruta,
            fibra_numero__iexact=fibra_numero,
        ).exists():
            return JsonResponse(
                {
                    "status": "error",
                    "message": (
                        f"La fibra lógica '{fibra_numero}' ya existe para "
                        "esta ruta."
                    ),
                },
                status=409,
            )

        estado = normalizar_estado_fibra(
            data.get('estado', 'SIN_INFORMACION')
        )
        condicion = normalizar_condicion_fisica(
            data.get('condicion_fisica', 'SIN_VERIFICAR')
        )
        fibra = crear_fibra(
            fibra_numero=fibra_numero,
            ruta=ruta,
            estado=estado,
            condicion_fisica=condicion,
            nombre_fibra=str(data.get('nombre_fibra', '')).strip(),
            tipo_conector=str(data.get('tipo_conector', '')).strip(),
            observaciones=str(data.get('observaciones', '')).strip(),
            usuario=request.user,
            origen='GUI',
        )
        advertencia = ''
        if ruta:
            seleccion_explicita = (
                ('tramo_ids' in data and data.get('tramo_ids') is not None)
                or (
                    'codigos_tramo' in data
                    and data.get('codigos_tramo') is not None
                )
            )
            if seleccion_explicita:
                tramos_ruta = list(
                    InventarioTramo.objects.select_for_update()
                    .filter(ruta=ruta)
                    .order_by('tramo_secuencia', 'pk')
                )
                tramos_seleccionados = _seleccionar_tramos_fibra(
                    data,
                    tramos_ruta,
                )
                asignar_fibra_a_tramos(
                    fibra=fibra,
                    tramos=tramos_seleccionados,
                    numero_hilo=fibra_numero,
                    estado=estado,
                    observaciones=(
                        'Asignación física informada explícitamente desde la GUI.'
                    ),
                )
        mensaje = (
            f"Hilo creado en {ruta.nombre}." if ruta
            else "Hilo creado con troncal pendiente."
        )
        if advertencia:
            mensaje += f" Ruta asignada; detalle de tramo pendiente: {advertencia}"
        return JsonResponse({
            "status": "success",
            "message": mensaje,
        })
    except ValidationError as exc:
        transaction.set_rollback(True)
        return JsonResponse(
            {"status": "error", "message": "; ".join(exc.messages)},
            status=400,
        )
    except IntegrityError:
        transaction.set_rollback(True)
        return JsonResponse(
            {
                "status": "error",
                "message": (
                    "La fibra o su posición física ya existe en uno de los "
                    "tramos seleccionados."
                ),
            },
            status=409,
        )
    except Exception:
        transaction.set_rollback(True)
        logger.exception("Error inesperado al crear una fibra de inventario")
        return JsonResponse(
            {
                "status": "error",
                "message": (
                    "No se pudo crear la fibra. Revisa los datos e intenta "
                    "nuevamente."
                ),
            },
            status=500,
        )

@login_required
@permission_required('mapas.add_inventariofibra', raise_exception=True)
@permission_required('mapas.change_inventariofibra', raise_exception=True)
@require_POST
@transaction.atomic
def import_fibras_csv(request):
    """Importa detalle por tramo usando el mismo validador de la carga global."""
    try:
        import io
        import pandas as pd
        from .importacion import _procesar_fibras_inventario

        csv_file = request.FILES.get('csv_fibras')
        ruta_nombre = request.POST.get('ruta_nombre', '').strip()
        if not csv_file or not ruta_nombre:
            return JsonResponse(
                {"status": "error", "message": "Archivo o ruta faltante."},
                status=400,
            )
        if not _archivo_dentro_del_limite(csv_file):
            return JsonResponse(
                {'status': 'error', 'message': _mensaje_limite_archivo()},
                status=413,
            )
        ruta = Ruta.objects.select_for_update().filter(
            nombre__iexact=ruta_nombre
        ).first()
        if not ruta:
            return JsonResponse(
                {"status": "error", "message": "Ruta no encontrada."},
                status=404,
            )

        df = pd.read_csv(io.StringIO(csv_file.read().decode('utf-8-sig')))
        columnas_normalizadas = {
            str(columna).strip().casefold(): columna for columna in df.columns
        }
        columna_ruta = next(
            (
                columnas_normalizadas[clave]
                for clave in ('ruta', 'troncal')
                if clave in columnas_normalizadas
            ),
            None,
        )
        if columna_ruta:
            rutas_archivo = {
                str(valor).strip().casefold()
                for valor in df[columna_ruta]
                if pd.notna(valor) and str(valor).strip()
            }
            if rutas_archivo and rutas_archivo != {ruta.nombre.casefold()}:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": (
                            "El archivo contiene una troncal distinta de la "
                            "seleccionada."
                        ),
                    },
                    status=400,
                )
            df[columna_ruta] = ruta.nombre
        else:
            df.insert(0, 'Ruta', ruta.nombre)

        contenido = io.BytesIO(df.to_csv(index=False).encode('utf-8-sig'))
        resultado = _procesar_fibras_inventario(contenido)
        advertencias = resultado.get('advertencias') or []
        mensaje = (
            f"Procesadas {resultado.get('total', 0)} filas; "
            f"{resultado.get('creadas', 0)} creadas y "
            f"{resultado.get('actualizadas', 0)} actualizadas."
        )
        if advertencias:
            mensaje += " " + " ".join(advertencias)
        return JsonResponse({
            "status": "success",
            "message": mensaje,
            "result": resultado,
        })
    except (UnicodeDecodeError, pd.errors.ParserError, ValidationError, ValueError) as exc:
        logger.info("CSV de fibras inválido", exc_info=True)
        mensaje = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        return JsonResponse(
            {
                "status": "error",
                "message": mensaje or "El archivo CSV contiene datos inválidos.",
            },
            status=400,
        )
    except Exception:
        logger.exception("Error inesperado al importar fibras")
        return JsonResponse(
            {"status": "error", "message": "No se pudo procesar el archivo CSV."},
            status=500,
        )

@login_required
@permission_required('mapas.change_inventariofibra', raise_exception=True)
@require_POST
@transaction.atomic
def update_detalle_fibra(request):
    """Actualiza los datos de un hilo/fibra específico."""
    import json
    try:
        data = json.loads(request.body)
        from ..models import InventarioFibra
        from ..services.fibras import (
            asignar_ruta_fibra,
            actualizar_metadatos_fibra,
            establecer_estado_fibra_informado,
            normalizar_condicion_fisica,
            normalizar_estado_fibra,
            normalizar_numero_hilo,
            restablecer_estado_fibra,
        )

        fibra_id = data.get('id')
        if not fibra_id:
            return JsonResponse(
                {"status": "error", "message": "ID de fibra no proporcionado"},
                status=400,
            )

        fibra = InventarioFibra.objects.select_for_update().filter(
            id=fibra_id
        ).select_related('ruta').first()
        if not fibra:
            return JsonResponse(
                {"status": "error", "message": "No se encontró la fibra"},
                status=404,
            )

        if 'fibra_numero' in data:
            numero_logico, _ = normalizar_numero_hilo(data.get('fibra_numero'))
        else:
            numero_logico = fibra.fibra_numero
        ruta_objetivo = None
        if fibra.ruta_id is None:
            ruta_nombre = str(data.get('ruta_nombre', '')).strip()
            if ruta_nombre:
                ruta_objetivo = Ruta.objects.select_for_update().filter(
                    nombre__iexact=ruta_nombre
                ).first()
                if not ruta_objetivo:
                    return JsonResponse(
                        {"status": "error", "message": "Ruta no encontrada"},
                        status=404,
                    )
        ruta_para_unicidad = fibra.ruta or ruta_objetivo
        if (
            ruta_para_unicidad
            and InventarioFibra.objects.filter(
                ruta=ruta_para_unicidad,
                fibra_numero__iexact=numero_logico,
            ).exclude(pk=fibra.pk).exists()
        ):
            return JsonResponse(
                {
                    "status": "error",
                    "message": (
                        f"La fibra lógica {numero_logico} ya existe en "
                        "esta troncal."
                    ),
                },
                status=409,
            )
        cambios_metadatos = {}
        if fibra.fibra_numero != numero_logico:
            cambios_metadatos['fibra_numero'] = numero_logico
        campos_texto = {
            'nombre_fibra': ('nombre_fibra', 'servicio'),
            'tipo_conector': ('tipo_conector',),
            'observaciones': ('observaciones',),
        }
        for campo_modelo, claves_payload in campos_texto.items():
            clave_payload = next(
                (clave for clave in claves_payload if clave in data),
                None,
            )
            if clave_payload is None:
                continue
            valor = str(data.get(clave_payload) or '').strip()
            if getattr(fibra, campo_modelo) != valor:
                cambios_metadatos[campo_modelo] = valor
        if 'condicion_fisica' in data:
            condicion_raw = str(data.get('condicion_fisica') or '').strip()
            condicion = (
                normalizar_condicion_fisica(condicion_raw)
                if condicion_raw
                else 'SIN_VERIFICAR'
            )
            if fibra.condicion_fisica != condicion:
                cambios_metadatos['condicion_fisica'] = condicion
        if cambios_metadatos:
            actualizar_metadatos_fibra(
                fibra=fibra,
                usuario=request.user,
                origen='GUI',
                **cambios_metadatos,
            )

        if 'estado' in data:
            nuevo_estado = normalizar_estado_fibra(data.get('estado'))
            if nuevo_estado == 'SIN_INFORMACION':
                restablecer_estado_fibra(
                    fibra=fibra,
                    usuario=request.user,
                    origen='GUI',
                )
            else:
                establecer_estado_fibra_informado(
                    fibra=fibra,
                    estado=nuevo_estado,
                    usuario=request.user,
                    origen='GUI',
                )

        advertencia = ''
        ruta_asignada = False
        if ruta_objetivo:
            fibra, ruta_asignada, advertencia = asignar_ruta_fibra(
                fibra=fibra,
                ruta=ruta_objetivo,
                usuario=request.user,
                origen='GUI',
            )

        mensaje = (
            "Fibra asociada a la troncal; sus terminaciones ODF se conservaron."
            if ruta_asignada
            else "Datos globales de la fibra actualizados."
        )
        if advertencia:
            mensaje += f" Detalle de tramo pendiente: {advertencia}"
        return JsonResponse({
            "status": "success",
            "message": mensaje,
        })
    except ValidationError as exc:
        transaction.set_rollback(True)
        return JsonResponse(
            {"status": "error", "message": "; ".join(exc.messages)},
            status=400,
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        transaction.set_rollback(True)
        return JsonResponse(
            {"status": "error", "message": "Los datos de la fibra no son válidos."},
            status=400,
        )
    except IntegrityError:
        transaction.set_rollback(True)
        return JsonResponse(
            {"status": "error", "message": "La fibra entra en conflicto con otro registro."},
            status=409,
        )
    except Exception:
        transaction.set_rollback(True)
        logger.exception("Error inesperado al actualizar una fibra")
        return JsonResponse(
            {"status": "error", "message": "No se pudo actualizar la fibra."},
            status=500,
        )

@login_required
@permission_required('mapas.view_inventarioodf', raise_exception=True)
def get_odfs(request):
    """Retorna la lista de todos los ODFs para la tabla del dashboard."""
    from ..models import InventarioODF
    odfs = InventarioODF.objects.select_related('rack_obj__sala__hub_site')
    data = [{
        'id': odf.pk,
        'hub_site': nombre_site(odf),
        'sala': odf.rack_obj.sala.nombre,
        'rack': odf.rack_obj.nombre,
        'odf': odf.odf,
        'capacidad_puertos': odf.capacidad_puertos,
        'puertos_ocupados': odf.puertos_ocupados,
        'puertos_libres': odf.puertos_libres,
        'puertos_reservados': odf.puertos_reservados,
        'tipo_conector': odf.tipo_conector,
        'estado': odf.estado,
        'observaciones': odf.observaciones,
    } for odf in odfs]
    return JsonResponse({"status": "success", "data": data})

@login_required
@permission_required('mapas.view_detallepuertoodf', raise_exception=True)
def get_detalle_puertos(request, odf_nombre):
    """Retorna el detalle de puertos para un ODF específico."""
    from django.db.models import Prefetch
    from ..models import DetallePuertoODF, TerminacionFibra
    
    hub_site = request.GET.get('hub_site', '').strip()
    query = (
        DetallePuertoODF.objects.select_related(
            'odf_obj__rack_obj__sala__hub_site'
        )
        .prefetch_related(Prefetch(
            'terminaciones_fibra',
            queryset=TerminacionFibra.objects.select_related('fibra__ruta'),
            to_attr='terminaciones_oficiales',
        ))
        .filter(odf_obj__odf__iexact=odf_nombre)
    )
    if hub_site:
        query = query.filter(
            odf_obj__rack_obj__sala__hub_site__nombre=hub_site
        )
        
    puertos = list(query)
    
    # Ordenar puertos de forma numérica ascendente
    def sort_key(p):
        try:
            return int(p.puerto_odf)
        except (ValueError, TypeError):
            return p.puerto_odf
            
    puertos.sort(key=sort_key)
    
    data = []
    for p in puertos:
        terminacion = next(iter(p.terminaciones_oficiales), None)
        data.append({
            'id': p.id,
            'odf': p.odf_obj.odf,
            'bandeja': p.bandeja,
            'puerto_odf': p.puerto_odf,
            'fibra': terminacion.fibra.fibra_numero if terminacion else '',
            'estado_puerto': p.estado_puerto,
            'estado_puerto_label': p.get_estado_puerto_display(),
            'tipo_conector': p.tipo_conector,
            'patchcord': p.patchcord,
            'destino': p.destino,
            'observaciones': p.observaciones
        })
        
    return JsonResponse({"status": "success", "data": data})

@login_required
@permission_required('mapas.change_detallepuertoodf', raise_exception=True)
@require_POST
@transaction.atomic
def update_detalle_puerto(request):
    """Actualiza los datos de un puerto de ODF específico."""
    import json
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            from ..models import DetallePuertoODF
            
            puerto_id = data.get('id')
            if not puerto_id:
                return JsonResponse({"status": "error", "message": "ID de puerto no proporcionado"}, status=400)
            
            puerto_actualizado = DetallePuertoODF.objects.select_for_update().filter(id=puerto_id).first()
            if not puerto_actualizado:
                return JsonResponse({"status": "error", "message": "No se encontró el puerto o no hubo cambios"}, status=400)

            identidad_solicitada = {
                'puerto_odf': puerto_actualizado.puerto_odf,
                'odf_nombre': puerto_actualizado.odf_obj.odf,
            }
            for clave, valor_actual in identidad_solicitada.items():
                if clave not in data:
                    continue
                valor_solicitado = str(data.get(clave) or '').strip().upper()
                if valor_solicitado != str(valor_actual or '').strip().upper():
                    return JsonResponse(
                        {
                            "status": "error",
                            "message": (
                                "El ODF y el número de puerto forman una identidad fija. "
                                "No pueden modificarse desde la edición normal."
                            ),
                        },
                        status=400,
                    )

            if 'estado_puerto' in data:
                nuevo_estado = normalizar_estado_puerto_odf(
                    data.get('estado_puerto'), default=None
                )
                if not nuevo_estado:
                    return JsonResponse(
                        {"status": "error", "message": "Estado de puerto no válido"},
                        status=400,
                    )
                if nuevo_estado != puerto_actualizado.estado_puerto:
                    return JsonResponse(
                        {
                            "status": "error",
                            "message": (
                                "El estado se cambia desde Gestionar conexión: "
                                "conectar, desconectar, reservar o cancelar reserva."
                            ),
                        },
                        status=409,
                    )

            campos_actualizados = []
            campos_texto = {
                'bandeja': ('bandeja',),
                'tipo_conector': ('tipo_conector', 'conector'),
                'patchcord': ('patchcord',),
                'destino': ('destino', 'destino_externo'),
                'observaciones': ('observaciones',),
            }
            for campo_modelo, claves_payload in campos_texto.items():
                clave = next(
                    (item for item in claves_payload if item in data),
                    None,
                )
                if clave is None:
                    continue
                valor = str(data.get(clave) or '').strip()
                if getattr(puerto_actualizado, campo_modelo) != valor:
                    setattr(puerto_actualizado, campo_modelo, valor)
                    campos_actualizados.append(campo_modelo)
            if campos_actualizados:
                puerto_actualizado.save(update_fields=campos_actualizados)
            
            return JsonResponse({
                "status": "success", 
                "message": (
                    "Puerto actualizado correctamente"
                    if campos_actualizados else "No se solicitaron cambios en el puerto"
                )
            })
        except ERRORES_DATOS_ENTRADA:
            return JsonResponse({"status": "error", "message": "Los datos del puerto no son válidos."}, status=400)
        except Exception:
            logger.exception("Error inesperado al actualizar un puerto ODF")
            return JsonResponse({"status": "error", "message": "No se pudo actualizar el puerto."}, status=500)
    return JsonResponse({"status": "error", "message": "Método no permitido"}, status=405)

@login_required
@permission_required('mapas.change_detallepuertoodf', raise_exception=True)
@require_POST
def gestionar_conexion_puerto(request):
    """Ejecuta las transiciones válidas del estado de un puerto ODF."""
    try:
        data = json.loads(request.body)
        accion = str(data.get('accion', '')).strip().lower()
        puerto_id = data.get('puerto_id')
        if not puerto_id:
            raise ValidationError('Seleccione un puerto ODF.')

        if accion == 'conectar':
            sincronizar_fibra = data.get('sincronizar_fibra') is True
            fibra_id = data.get('fibra_id')
            creando_provisional = not bool(fibra_id)
            terminacion_existente = bool(
                fibra_id
                and TerminacionFibra.objects.filter(
                    fibra_id=fibra_id,
                    extremo=str(data.get('extremo', '')).strip().upper(),
                ).exists()
            )
            permiso = (
                'mapas.change_terminacionfibra'
                if terminacion_existente
                else 'mapas.add_terminacionfibra'
            )
            if not request.user.is_superuser and not request.user.has_perm(permiso):
                raise PermissionDenied
            if (
                creando_provisional
                and not request.user.is_superuser
                and not request.user.has_perm('mapas.add_inventariofibra')
            ):
                raise PermissionDenied
            if (
                sincronizar_fibra
                and not request.user.is_superuser
                and not request.user.has_perm('mapas.change_inventariofibra')
            ):
                raise PermissionDenied
            terminacion, creada = conectar_puerto(
                puerto_id=puerto_id,
                fibra_id=fibra_id,
                fibra_numero=data.get('fibra_numero'),
                extremo=data.get('extremo'),
                permitir_mover=bool(data.get('permitir_mover')),
                ocupar_fibra=sincronizar_fibra,
                usuario=request.user,
                origen='GUI',
            )
            verbo = 'conectado' if creada else 'movido'
            return JsonResponse({
                'status': 'success',
                'fibra_sincronizada': sincronizar_fibra,
                'message': (
                    f'Extremo {terminacion.extremo} de '
                    f'{terminacion.fibra.nombre_troncal} / '
                    f'{terminacion.fibra.fibra_numero} {verbo} correctamente.'
                ),
                'fibra_provisional': terminacion.fibra.es_provisional,
            })
        if accion == 'desconectar':
            sincronizar_fibra = (
                data.get('sincronizar_fibra') is True
                or data.get('liberar_fibra') is True
            )
            if (
                not request.user.is_superuser
                and not request.user.has_perm('mapas.delete_terminacionfibra')
            ):
                raise PermissionDenied
            if (
                sincronizar_fibra
                and not request.user.is_superuser
                and not request.user.has_perm('mapas.change_inventariofibra')
            ):
                raise PermissionDenied
            fibra = desconectar_puerto(
                puerto_id=puerto_id,
                liberar_fibra=sincronizar_fibra,
                usuario=request.user,
                origen='GUI',
            )
            return JsonResponse({
                'status': 'success',
                'fibra_sincronizada': sincronizar_fibra,
                'message': (
                    f'{fibra.nombre_troncal} / {fibra.fibra_numero} '
                    'fue desconectada.'
                ),
            })
        if accion == 'reservar':
            reservar_puerto(puerto_id=puerto_id, usuario=request.user, origen='GUI')
            return JsonResponse({'status': 'success', 'message': 'Puerto reservado correctamente.'})
        if accion == 'cancelar_reserva':
            cancelar_reserva_puerto(
                puerto_id=puerto_id,
                usuario=request.user,
                origen='GUI',
            )
            return JsonResponse({'status': 'success', 'message': 'Reserva cancelada; el puerto quedó libre.'})
        raise ValidationError('La acción solicitada no es válida.')
    except MovimientoRequiereConfirmacion as exc:
        terminacion = exc.terminacion
        return JsonResponse({
            'status': 'confirmation_required',
            'code': 'MOVER_TERMINACION',
            'message': exc.message,
            'current': {
                'odf': terminacion.puerto_odf.odf_obj.odf,
                'puerto': terminacion.puerto_odf.puerto_odf,
            },
        }, status=409)
    except (json.JSONDecodeError, TypeError, ValueError, ValidationError) as exc:
        mensaje = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        return JsonResponse({'status': 'error', 'message': mensaje}, status=400)
    except IntegrityError:
        return JsonResponse(
            {'status': 'error', 'message': 'El puerto o el extremo fue utilizado por otra operación.'},
            status=409,
        )
    except PermissionDenied:
        raise
    except Exception:
        logger.exception('Error inesperado al gestionar la conexión del puerto ODF')
        return JsonResponse({'status': 'error', 'message': 'No se pudo gestionar la conexión.'}, status=500)


@login_required
@permission_required('mapas.add_ruta', raise_exception=True)
@require_POST
@transaction.atomic
def create_ruta_manual(request):
    """Crea una nueva ruta y su tramo técnico inicial."""
    import json
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)
    
    try:
        from ..models import Ruta, InventarioTramo
        
        if request.content_type == 'application/json':
            data = json.loads(request.body)
        else:
            data = request.POST

        nombre = data.get('nombre', '').strip()
        
        if not nombre:
            return JsonResponse({'status': 'error', 'message': 'El nombre de la ruta es obligatorio'})

        # Validación de duplicado
        ruta_existente = Ruta.objects.filter(nombre=nombre).first()
        
        if ruta_existente:
            if InventarioTramo.objects.filter(ruta=ruta_existente).exists():
                return JsonResponse({'status': 'error', 'message': f'La ruta troncal "{nombre}" ya existe y tiene datos registrados.'})
            else:
                nueva_ruta = ruta_existente
                if not nueva_ruta.olt:
                    nueva_ruta.olt = data.get('hub_origen', '')
                if nueva_ruta.distancia_m is None or nueva_ruta.distancia_m == 0:
                    nueva_ruta.distancia_m = float(data.get('distancia_km', 0) or 0) * 1000
                nueva_ruta.save()
        else:
            # Crear Ruta
            nueva_ruta = Ruta.objects.create(
                nombre=nombre,
                olt=data.get('hub_origen', ''),
                distancia_m=float(data.get('distancia_km', 0) or 0) * 1000
            )
        
        capacidad_raw = data.get('capacidad', '').strip()
        if capacidad_raw.isdigit():
            capacidad_raw = f"{capacidad_raw} Hilos"
        import re
        capacidad_match = re.search(r'\d+(?:[.,]\d+)?', capacidad_raw)
        capacidad_hilos = None
        if capacidad_match:
            capacidad_numero = float(
                capacidad_match.group().replace(',', '.')
            )
            if capacidad_numero > 0 and capacidad_numero.is_integer():
                capacidad_hilos = int(capacidad_numero)

        from ..services.topologia import (
            codigo_tramo_automatico,
            inferir_tipo_nodo,
            resolver_nodo,
        )
        origen_texto = str(data.get('hub_origen', '')).strip()
        destino_texto = str(data.get('destino', '')).strip()
        origen_nodo = (
            resolver_nodo(
                tipo=inferir_tipo_nodo(origen_texto),
                codigo=origen_texto,
                nombre=origen_texto,
            )
            if origen_texto
            else None
        )
        destino_nodo = (
            resolver_nodo(
                tipo=inferir_tipo_nodo(destino_texto),
                codigo=destino_texto,
                nombre=destino_texto,
            )
            if destino_texto
            else None
        )
            
        # Crear Tramo Técnico inicial (para que aparezca en el dashboard)
        tramo_inicial = InventarioTramo.objects.create(
            ruta=nueva_ruta,
            tramo_secuencia=1,
            codigo_tramo=codigo_tramo_automatico(1),
            origen_nodo=origen_nodo,
            destino_nodo=destino_nodo,
            tipo_trazado=data.get('tipo_trazado', ''),
            estado=data.get('estado', ''),
            capacidad=capacidad_raw,
            capacidad_hilos=capacidad_hilos,
            hub_site=data.get('hub_origen', ''),
            destino=data.get('destino', ''),
            marca_modelo=data.get('marca_modelo', ''),
            tipo_fibra=data.get('tipo_fibra', ''),
            serial=data.get('serial', ''),
            mufas=int(data.get('mufas', 0) or 0),
            splitters=int(data.get('splitters', 0) or 0),
            odf_nombre=data.get('odf_nombre', ''),
            hilos_ocupados=int(data.get('hilos_ocupados', 0) or 0),
            hilos_reservados=int(data.get('hilos_reservados', 0) or 0),
            hilos_libres=int(data.get('hilos_libres', 0) or 0),
            distancia_m=float(data.get('distancia_km', 0) or 0) * 1000,
            reservas_m=float(data.get('reserva_km', 0) or 0) * 1000
        )
        # Procesar archivo CSV si se adjuntó
        csv_file = request.FILES.get('csv_coordenadas')
        if csv_file:
            import pandas as pd
            import io
            from ..models import CoordenadaRuta
            
            decoded_file = csv_file.read().decode('utf-8')
            df = pd.read_csv(io.StringIO(decoded_file))
            df.columns = [c.strip().lower() for c in df.columns]
            
            if 'latitude' in df.columns and 'longitude' in df.columns:
                from .importacion import _reemplazar_geografia_ruta

                if 'tipo_trazado' not in df.columns:
                    df['tipo_trazado'] = data.get(
                        'tipo_trazado',
                        'DESCONOCIDO',
                    )
                _reemplazar_geografia_ruta(nueva_ruta, df)

                distancia_ingresada = float(data.get('distancia_km', 0) or 0) * 1000
                if distancia_ingresada > 0:
                    nueva_ruta.distancia_m = distancia_ingresada
                    nueva_ruta.save(update_fields=['distancia_m'])

        return JsonResponse({'status': 'success', 'message': 'Ruta creada correctamente'})
    except ERRORES_DATOS_ENTRADA:
        return JsonResponse({'status': 'error', 'message': 'Los datos de la ruta no son válidos.'}, status=400)
    except Exception:
        logger.exception("Error inesperado al crear una ruta")
        return JsonResponse({'status': 'error', 'message': 'No se pudo crear la ruta.'}, status=500)

@login_required
@permission_required('mapas.add_inventarioodf', raise_exception=True)
@require_POST
@transaction.atomic
def create_odf_manual(request):
    """Crea un nuevo ODF con validación de ubicación única."""
    import json
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)
    
    try:
        from ..models import InventarioODF
        from ..services.inventario import ajustar_puertos_a_capacidad, resolver_rack
        data = json.loads(request.body)
        
        hub = data.get('hub_site', '').strip()
        sala = data.get('sala', '').strip()
        rack = data.get('rack', '').strip()
        odf_nombre = data.get('odf', '').strip()
        
        if not odf_nombre:
            return JsonResponse({'status': 'error', 'message': 'El nombre del ODF es obligatorio'})

        existente = InventarioODF.objects.select_related(
            'rack_obj__sala__hub_site'
        ).filter(odf__iexact=odf_nombre).first()

        if existente:
            return JsonResponse({
                'status': 'error',
                'message': (
                    f'Ya existe {existente.odf} en '
                    f'{nombre_site(existente)} / '
                    f'{existente.rack_obj.sala.nombre} / '
                    f'{existente.rack_obj.nombre}.'
                ),
            }, status=409)

        rack_obj = resolver_rack(hub, sala, rack)
        capacidad = int(data.get('capacidad_puertos', 0) or 0)
        # Crear el ODF y materializar todas sus posiciones físicas.
        odf = InventarioODF.objects.create(
            rack_obj=rack_obj,
            odf=odf_nombre,
            capacidad_puertos=capacidad,
            puertos_libres=0,
            tipo_conector=data.get('tipo_conector', ''),
            estado=data.get('estado', 'Activo')
        )
        ajustar_puertos_a_capacidad(odf, capacidad)
        
        return JsonResponse({
            'status': 'success',
            'message': f'ODF creado correctamente con {capacidad} puertos libres.',
        })
    except ERRORES_DATOS_ENTRADA:
        return JsonResponse({'status': 'error', 'message': 'Los datos del ODF no son válidos.'}, status=400)
    except Exception:
        logger.exception("Error inesperado al crear un ODF")
        return JsonResponse({'status': 'error', 'message': 'No se pudo crear el ODF.'}, status=500)

@login_required
@permission_required('mapas.add_detallepuertoodf', raise_exception=True)
@require_POST
@transaction.atomic
def create_puerto_manual(request):
    """Crea un nuevo puerto asociado a un ODF."""
    import json
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)
    
    try:
        from ..models import InventarioODF, DetallePuertoODF
        data = json.loads(request.body)
        
        odf_nombre = data.get('odf_nombre', '').strip()
        puerto_num = data.get('puerto_odf', '').strip()
        
        if not odf_nombre or not puerto_num:
            return JsonResponse(
                {'status': 'error', 'message': 'El ODF y el número de puerto son obligatorios'},
                status=400,
            )

        # La capacidad se valida y consume bajo el mismo bloqueo para evitar
        # sobreasignaciones cuando varios usuarios crean puertos a la vez.
        odf_obj = InventarioODF.objects.select_for_update().filter(odf__iexact=odf_nombre).first()
        if not odf_obj:
            return JsonResponse({'status': 'error', 'message': 'ODF no encontrado'}, status=404)

        # Validar si el puerto ya existe
        exists = DetallePuertoODF.objects.filter(odf_obj=odf_obj, puerto_odf=puerto_num).exists()
        if exists:
            return JsonResponse(
                {'status': 'error', 'message': f'El puerto {puerto_num} ya existe en este ODF.'},
                status=409,
            )

        # Validar capacidad
        capacidad_max = odf_obj.capacidad_puertos or 0
        if capacidad_max > 0:
            actual_ports = DetallePuertoODF.objects.filter(odf_obj=odf_obj).count()
            if actual_ports >= capacidad_max:
                return JsonResponse(
                    {'status': 'error', 'message': f'Límite excedido. El ODF "{odf_nombre}" ya alcanzó su capacidad máxima de {capacidad_max} puertos.'},
                    status=409,
                )

        estado_puerto = normalizar_estado_puerto_odf(
            data.get('estado_puerto', 'LIBRE')
        )
        if estado_puerto == 'OCUPADO':
            return JsonResponse(
                {
                    'status': 'error',
                    'message': 'Cree el puerto como Libre o Reservado y luego conecte una fibra.',
                },
                status=409,
            )

        # Crear Puerto
        DetallePuertoODF.objects.create(
            odf_obj=odf_obj,
            puerto_odf=puerto_num,
            bandeja=data.get('bandeja', ''),
            estado_puerto=estado_puerto,
            tipo_conector=data.get('tipo_conector', ''),
            patchcord=data.get('patchcord', ''),
            destino=data.get('destino', ''),
            observaciones=data.get('observaciones', '')
        )
        
        odf_obj.actualizar_contadores()
        
        return JsonResponse({'status': 'success', 'message': 'Puerto creado correctamente'})
    except ValidationError as exc:
        return JsonResponse({'status': 'error', 'message': '; '.join(exc.messages)}, status=400)
    except IntegrityError:
        return JsonResponse(
            {'status': 'error', 'message': 'El puerto ya existe o entra en conflicto con otro registro.'},
            status=409,
        )
    except Exception:
        logger.exception('Error inesperado al crear un puerto ODF')
        return JsonResponse(
            {'status': 'error', 'message': 'No se pudo crear el puerto. Revisa los datos e intenta nuevamente.'},
            status=500,
        )

@login_required
@permission_required('mapas.delete_inventarioodf', raise_exception=True)
@require_POST
@transaction.atomic
def vaciar_inventario_odf(request):
    """Elimina todos los registros de ODFs y sus puertos."""
    from ..models import InventarioODF, DetallePuertoODF
    try:
        # Al eliminar InventarioODF, por CASCADE se eliminan los DetallePuertoODF asociados
        DetallePuertoODF.objects.all().delete()
        InventarioODF.objects.all().delete()
        return JsonResponse({"status": "success", "message": "Todo el inventario de ODFs ha sido eliminado correctamente."})
    except Exception:
        logger.exception("Error inesperado al vaciar el inventario ODF")
        return JsonResponse(
            {"status": "error", "message": "No se pudo vaciar el inventario ODF."},
            status=500,
        )

@login_required
@permission_required('mapas.change_ruta', raise_exception=True)
@require_POST
@transaction.atomic
def update_ruta_manual(request):
    import json
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)
    
    try:
        from ..models import Ruta, InventarioTramo
        
        if request.content_type == 'application/json':
            data = json.loads(request.body)
        else:
            data = request.POST

        nombre_original = data.get('nombre_original', '').strip()
        nuevo_nombre = data.get('nombre', '').strip()
        
        if not nombre_original or not nuevo_nombre:
            return JsonResponse({'status': 'error', 'message': 'El nombre de la ruta es obligatorio'})
            
        ruta = (
            Ruta.objects.select_for_update()
            .filter(nombre=nombre_original)
            .first()
        )
        if not ruta:
            return JsonResponse({'status': 'error', 'message': 'Ruta no encontrada'})
        tramos_ruta = list(
            InventarioTramo.objects.select_for_update()
            .select_related('origen_nodo', 'destino_nodo')
            .filter(ruta=ruta)
            .order_by('tramo_secuencia', 'pk')
        )
            
        # Si cambia el nombre, verificar que el nuevo no exista
        if (
            nombre_original.casefold() != nuevo_nombre.casefold()
            and Ruta.objects.filter(nombre__iexact=nuevo_nombre).exists()
        ):
            return JsonResponse({'status': 'error', 'message': f'La ruta "{nuevo_nombre}" ya existe.'})
            
        # Actualizar Ruta
        ruta.nombre = nuevo_nombre
        ruta.olt = data.get('hub_origen', '')
        ruta.distancia_m = float(data.get('distancia_km', 0) or 0) * 1000
        ruta.save()
        
        capacidad_raw = data.get('capacidad', '').strip()
        if capacidad_raw.isdigit():
            capacidad_raw = f"{capacidad_raw} Hilos"
        import re
        capacidad_match = re.search(r'\d+(?:[.,]\d+)?', capacidad_raw)
        capacidad_hilos = None
        if capacidad_match:
            capacidad_numero = float(
                capacidad_match.group().replace(',', '.')
            )
            if capacidad_numero > 0 and capacidad_numero.is_integer():
                capacidad_hilos = int(capacidad_numero)

        # El editor histórico representa una troncal consolidada. En rutas
        # multitramos solo actualiza los datos generales para no convertir
        # accidentalmente el primer tramo en el resumen de toda la ruta.
        tramo = tramos_ruta[0] if len(tramos_ruta) == 1 else None
        if tramo:
            from ..services.topologia import (
                codigo_tramo_automatico,
                inferir_tipo_nodo,
                resolver_nodo,
            )
            # Una ruta ya segmentada no se reclasifica en bloque desde este
            # formulario: su tipo visual pertenece ahora a cada coordenada.
            if not ruta.coordenadas.exists():
                tramo.tipo_trazado = data.get('tipo_trazado', '')
                
            tramo.estado = data.get('estado', '')
            tramo.capacidad = capacidad_raw
            tramo.capacidad_hilos = capacidad_hilos
            tramo.codigo_tramo = (
                tramo.codigo_tramo
                or codigo_tramo_automatico(tramo.tramo_secuencia)
            )
            tramo.hub_site = data.get('hub_origen', '')
            tramo.destino = data.get('destino', '')
            origen_texto = str(data.get('hub_origen', '')).strip()
            destino_texto = str(data.get('destino', '')).strip()
            tramo.origen_nodo = (
                resolver_nodo(
                    tipo=inferir_tipo_nodo(origen_texto),
                    codigo=origen_texto,
                    nombre=origen_texto,
                )
                if origen_texto
                else None
            )
            tramo.destino_nodo = (
                resolver_nodo(
                    tipo=inferir_tipo_nodo(destino_texto),
                    codigo=destino_texto,
                    nombre=destino_texto,
                )
                if destino_texto
                else None
            )
            tramo.marca_modelo = data.get('marca_modelo', '')
            tramo.tipo_fibra = data.get('tipo_fibra', '')
            tramo.serial = data.get('serial', '')
            tramo.mufas = int(data.get('mufas', 0) or 0)
            tramo.splitters = int(data.get('splitters', 0) or 0)
            tramo.odf_nombre = data.get('odf_nombre', '')
            tiene_detalle_fisico = tramo.fibras_tramo.exists()
            if not tiene_detalle_fisico:
                tramo.hilos_ocupados = int(
                    data.get('hilos_ocupados', 0) or 0
                )
                tramo.hilos_reservados = int(
                    data.get('hilos_reservados', 0) or 0
                )
                tramo.hilos_libres = int(
                    data.get('hilos_libres', 0) or 0
                )
            tramo.distancia_m = float(data.get('distancia_km', 0) or 0) * 1000
            tramo.reservas_m = float(data.get('reserva_km', 0) or 0) * 1000
            tramo.save()
            if tiene_detalle_fisico:
                from ..services.fibras import recalcular_cache_tramo
                recalcular_cache_tramo(tramo)
            
        # Procesar archivo CSV si se adjuntó
        csv_file = request.FILES.get('csv_coordenadas')
        if csv_file:
            import pandas as pd
            import io
            from ..models import CoordenadaRuta
            
            decoded_file = csv_file.read().decode('utf-8')
            df = pd.read_csv(io.StringIO(decoded_file))
            df.columns = [c.strip().lower() for c in df.columns]
            
            if 'latitude' in df.columns and 'longitude' in df.columns:
                from .importacion import _reemplazar_geografia_ruta

                if 'tipo_trazado' not in df.columns:
                    df['tipo_trazado'] = data.get(
                        'tipo_trazado',
                        'DESCONOCIDO',
                    )
                _reemplazar_geografia_ruta(ruta, df)

                distancia_ingresada = float(data.get('distancia_km', 0) or 0) * 1000
                if distancia_ingresada > 0:
                    ruta.distancia_m = distancia_ingresada
                    ruta.save(update_fields=['distancia_m'])

        mensaje = 'Ruta actualizada correctamente'
        if len(tramos_ruta) > 1:
            mensaje += (
                '. Los tramos técnicos se conservaron sin cambios y se '
                'administran mediante Inventario Técnico de Tramos.'
            )
        return JsonResponse({'status': 'success', 'message': mensaje})
    except ERRORES_DATOS_ENTRADA:
        return JsonResponse({'status': 'error', 'message': 'Los datos de la ruta no son válidos.'}, status=400)
    except Exception:
        logger.exception("Error inesperado al actualizar una ruta")
        return JsonResponse({'status': 'error', 'message': 'No se pudo actualizar la ruta.'}, status=500)

@login_required
@permission_required('mapas.delete_ruta', raise_exception=True)
@require_POST
@transaction.atomic
def delete_ruta_manual(request):
    import json
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)
        
    try:
        from ..models import Ruta
        data = json.loads(request.body)
        nombre = data.get('nombre', '').strip()
        
        ruta = Ruta.objects.filter(nombre=nombre).first()
        if not ruta:
            return JsonResponse({'status': 'error', 'message': 'Ruta no encontrada'})
            
        # Lógica de protección:
        # Si la ruta tiene coordenadas o reservas, es una ruta con valor operativo/geográfico.
        # El "borrar" desde el dashboard de inventario solo debería limpiar su ficha técnica (Inventario),
        # permitiendo que la ruta siga existiendo en el mapa operativo y conserve sus hitos.
        
        has_geo = ruta.coordenadas.exists() or ruta.reservas.exists()
        
        if has_geo:
            # Solo limpiamos el inventario técnico y fibras
            ruta.tramos_inventario.all().delete()
            # También podríamos querer limpiar las fibras asociadas
            from ..models import InventarioFibra
            InventarioFibra.objects.filter(ruta=ruta).delete()
            
            return JsonResponse({
                'status': 'success', 
                'message': 'Se ha limpiado el inventario técnico. La ruta y sus hitos permanecen en el mapa operativo.'
            })
        else:
            # Si no tiene nada de geo, es una ruta creada 100% manual sin mapa. Borrado total.
            ruta.delete()
            return JsonResponse({'status': 'success', 'message': 'Ruta eliminada correctamente'})
    except ERRORES_DATOS_ENTRADA:
        return JsonResponse({'status': 'error', 'message': 'La solicitud para eliminar la ruta no es válida.'}, status=400)
    except Exception:
        logger.exception("Error inesperado al eliminar una ruta")
        return JsonResponse({'status': 'error', 'message': 'No se pudo eliminar la ruta.'}, status=500)

@login_required
@permission_required('mapas.change_inventarioodf', raise_exception=True)
@require_POST
@transaction.atomic
def update_odf_manual(request):
    import json
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)
    
    try:
        from ..models import InventarioODF
        from ..services.inventario import ajustar_puertos_a_capacidad, resolver_rack
        data = json.loads(request.body)
        id_odf = data.get('id')
        
        if not id_odf:
            return JsonResponse({'status': 'error', 'message': 'ID de ODF requerido'})
            
        odf = InventarioODF.objects.select_for_update().filter(id=id_odf).first()
        if not odf:
            return JsonResponse({'status': 'error', 'message': 'ODF no encontrado'})
            
        odf = InventarioODF.objects.select_related(
            'rack_obj__sala__hub_site'
        ).get(pk=odf.pk)
        hub = str(data.get('hub_site', nombre_site(odf)) or '').strip()
        sala = str(data.get('sala', odf.rack_obj.sala.nombre) or '').strip()
        rack = str(data.get('rack', odf.rack_obj.nombre) or '').strip()
        odf_nombre = str(data.get('odf', odf.odf) or '').strip()
        if not odf_nombre:
            return JsonResponse({
                'status': 'error',
                'message': 'El nombre del ODF es obligatorio.',
            }, status=400)

        duplicado = InventarioODF.objects.select_related(
            'rack_obj__sala__hub_site'
        ).filter(odf__iexact=odf_nombre).exclude(id=id_odf).first()
        if duplicado:
            return JsonResponse({
                'status': 'error',
                'message': (
                    f'Ya existe {duplicado.odf} en '
                    f'{nombre_site(duplicado)} / '
                    f'{duplicado.rack_obj.sala.nombre} / '
                    f'{duplicado.rack_obj.nombre}.'
                ),
            }, status=409)

        rack_obj = resolver_rack(hub, sala, rack)
        # Actualizar campos
        odf.rack_obj = rack_obj
        odf.odf = odf_nombre
        nueva_capacidad = int(
            data.get('capacidad_puertos', odf.capacidad_puertos) or 0
        )
        
        if 'tipo_conector' in data:
            odf.tipo_conector = data.get('tipo_conector') or ''
        if 'estado' in data:
            odf.estado = data.get('estado') or ''
        if 'observaciones' in data:
            odf.observaciones = data.get('observaciones') or ''
        odf.save()
        odf = ajustar_puertos_a_capacidad(odf, nueva_capacidad)
        
        return JsonResponse({
            'status': 'success',
            'message': (
                f'ODF actualizado correctamente con {odf.capacidad_puertos} '
                'puertos físicos.'
            ),
        })
    except ERRORES_DATOS_ENTRADA:
        return JsonResponse({'status': 'error', 'message': 'Los datos del ODF no son válidos.'}, status=400)
    except Exception:
        logger.exception("Error inesperado al actualizar un ODF")
        return JsonResponse({'status': 'error', 'message': 'No se pudo actualizar el ODF.'}, status=500)

@login_required
@permission_required('mapas.delete_inventarioodf', raise_exception=True)
@require_POST
@transaction.atomic
def delete_odf_manual(request):
    import json
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)
        
    try:
        from ..models import InventarioODF
        data = json.loads(request.body)
        id_odf = data.get('id')
        
        odf = InventarioODF.objects.filter(id=id_odf).first()
        if not odf:
            return JsonResponse({'status': 'error', 'message': 'ODF no encontrado'})
            
        odf.delete() # Elimina en cascada DetallePuertoODF
        return JsonResponse({'status': 'success', 'message': 'ODF eliminado correctamente'})
    except ERRORES_DATOS_ENTRADA:
        return JsonResponse({'status': 'error', 'message': 'La solicitud para eliminar el ODF no es válida.'}, status=400)
    except Exception:
        logger.exception("Error inesperado al eliminar un ODF")
        return JsonResponse({'status': 'error', 'message': 'No se pudo eliminar el ODF.'}, status=500)

@login_required
@permission_required('mapas.view_detallepuertoodf', raise_exception=True)
def get_puertos_odf_api(request):
    """
    Retorna la lista de puertos para un ODF específico.
    Se usa en el mapa de inventario para el panel lateral flotante.
    """
    from django.db.models import Prefetch
    from ..models import (
        InventarioODF, DetallePuertoODF, TerminacionFibra,
    )
    
    hub_site = request.GET.get('hub_site', '').strip()
    odf_nombre = request.GET.get('odf_nombre', '').strip()
    
    if not odf_nombre:
        return JsonResponse({'status': 'error', 'message': 'El nombre del ODF es requerido'})
        
    # Buscar el ODF. Si hay hub_site, lo usamos para ser precisos.
    # Si no hay hub_site o no coincide exactamente, buscamos solo por odf_nombre y tomamos el primero
    odf = None
    if hub_site:
        odf = InventarioODF.objects.filter(
            rack_obj__sala__hub_site__nombre=hub_site,
            odf__iexact=odf_nombre,
        ).first()
        
    if not odf:
        odf = InventarioODF.objects.filter(odf__iexact=odf_nombre).first()
        
    if not odf:
        return JsonResponse({'status': 'error', 'message': f'No se encontró el ODF {odf_nombre}'})
        
    puertos = list(
        DetallePuertoODF.objects.filter(odf_obj=odf).prefetch_related(
            Prefetch(
                'terminaciones_fibra',
                queryset=(
                    TerminacionFibra.objects.select_related('fibra__ruta')
                    .order_by('pk')
                ),
                to_attr='terminaciones_normalizadas',
            )
        )
    )
    
    # Ordenar puertos de forma numérica ascendente
    def sort_key(p):
        try:
            return int(p.puerto_odf)
        except (ValueError, TypeError):
            return p.puerto_odf
            
    puertos.sort(key=sort_key)
    
    data = []
    for p in puertos:
        terminacion = next(
            (
                item for item in p.terminaciones_normalizadas
                if item.fibra_id
            ),
            None,
        )
        if terminacion is not None:
            ruta_nombre = terminacion.fibra.nombre_troncal
        else:
            ruta_nombre = 'No determinada'
                
        data.append({
            'puerto': p.puerto_odf,
            'estado': p.estado_puerto,
            'estado_label': p.get_estado_puerto_display(),
            'bandeja': p.bandeja,
            'fibra': terminacion.fibra.fibra_numero if terminacion else '',
            'destino': p.destino,
            'tipo_conector': p.tipo_conector,
            'ruta_troncal': ruta_nombre
        })
        
    return JsonResponse({
        'status': 'success',
        'odf': odf.odf,
        'hub_site': nombre_site(odf),
        'capacidad': odf.capacidad_puertos,
        'puertos': data
    })

@login_required
@permission_required('mapas.view_reserva', raise_exception=True)
@require_http_methods(["GET"])
def get_detalle_reservas(request):
    """
    Devuelve la lista de reservas asociadas a una ruta específica.
    """
    ruta_nombre = request.GET.get('ruta', '').strip()
    
    if not ruta_nombre:
        return JsonResponse({'status': 'error', 'message': 'El nombre de la ruta es requerido'})
        
    # Buscar la ruta usando import local o usando la que ya tenemos
    from ..models import Ruta, Reserva
    ruta = Ruta.objects.filter(nombre__iexact=ruta_nombre).first()
    
    if not ruta:
        return JsonResponse({'status': 'error', 'message': f'No se encontró la ruta {ruta_nombre}'})
        
    reservas = list(Reserva.objects.filter(ruta=ruta))
    
    data = []
    for r in reservas:
        data.append({
            'nombre': r.nombre,
            'reserva_m': r.reserva_m,
            'latitud': r.latitud,
            'longitud': r.longitud,
            'tipo': r.tipo
        })
        
    return JsonResponse({
        'status': 'success',
        'ruta': ruta.nombre,
        'reservas': data
    })

@login_required
@permission_required('mapas.add_reserva', raise_exception=True)
@require_POST
def add_reserva_manual(request):
    try:
        import json
        from ..models import Ruta, Reserva
        data = json.loads(request.body)
        ruta_nombre = data.get('ruta')
        nombre = str(data.get('nombre', '')).strip()
        latitud = data.get('latitud')
        longitud = data.get('longitud')
        
        if not ruta_nombre:
            return JsonResponse({'status': 'error', 'message': 'Ruta no especificada.'}, status=400)
        if not nombre or latitud is None or longitud is None:
            return JsonResponse(
                {'status': 'error', 'message': 'El nombre, la latitud y la longitud son obligatorios.'},
                status=400,
            )
            
        ruta = Ruta.objects.filter(nombre=ruta_nombre).first()
        if not ruta:
            return JsonResponse({'status': 'error', 'message': 'Ruta no encontrada.'}, status=404)
            
        nueva_reserva = Reserva.objects.create(
            ruta=ruta,
            nombre=nombre,
            tipo=data.get('tipo', ''),
            latitud=latitud,
            longitud=longitud,
            reserva_m=data.get('reserva_m') or 0.0
        )
        return JsonResponse({'status': 'success', 'message': f'Reserva {nueva_reserva.nombre} agregada exitosamente.'})
        
    except ValidationError as exc:
        return JsonResponse({'status': 'error', 'message': '; '.join(exc.messages)}, status=400)
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({'status': 'error', 'message': 'Los datos de la reserva no son válidos.'}, status=400)
    except Exception:
        logger.exception('Error inesperado al crear una reserva')
        return JsonResponse({'status': 'error', 'message': 'No se pudo crear la reserva.'}, status=500)

@login_required
@permission_required('mapas.add_reserva', raise_exception=True)
@require_POST
@transaction.atomic
def import_reservas_archivo(request):
    try:
        import pandas as pd
        from ..models import Ruta, Reserva
        
        archivo = request.FILES.get('archivo')
        ruta_nombre = request.POST.get('ruta_nombre')
        
        if not archivo or not ruta_nombre:
            return JsonResponse({'status': 'error', 'message': 'Archivo o ruta no proporcionados.'})
        if not _archivo_dentro_del_limite(archivo):
            return JsonResponse(
                {'status': 'error', 'message': _mensaje_limite_archivo()},
                status=413,
            )
            
        ruta = Ruta.objects.filter(nombre=ruta_nombre).first()
        if not ruta:
            return JsonResponse({'status': 'error', 'message': 'Ruta no encontrada.'})

        if archivo.name.endswith('.csv'):
            df = pd.read_csv(archivo)
        elif archivo.name.endswith('.xlsx') or archivo.name.endswith('.xls'):
            df = pd.read_excel(archivo)
        else:
            return JsonResponse({'status': 'error', 'message': 'Formato no soportado. Use .csv o .xlsx'})

        from decimal import Decimal, InvalidOperation

        nuevas_reservas = []
        rechazadas = 0
        for index, row in df.iterrows():
            nombre_valor = row.get('Landmark name', row.get('Nombre', f'Reserva-{index + 1}'))
            nombre = '' if pd.isna(nombre_valor) else str(nombre_valor).strip()
            tipo_valor = row.get('Connection type', row.get('Tipo', ''))
            tipo = '' if pd.isna(tipo_valor) else str(tipo_valor).strip()
            reserva_m = row.get('Reserva (m)', row.get('Reserva_m', 0))
            lat = row.get('Latitud')
            lon = row.get('Longitud')

            try:
                if not nombre or pd.isna(lat) or pd.isna(lon):
                    raise ValueError
                latitud = Decimal(str(lat).strip().replace(',', '.'))
                longitud = Decimal(str(lon).strip().replace(',', '.'))
                if not (Decimal('-90') <= latitud <= Decimal('90')):
                    raise ValueError
                if not (Decimal('-180') <= longitud <= Decimal('180')):
                    raise ValueError
                if pd.isna(reserva_m) or str(reserva_m).strip() == '-':
                    reserva_m = 0
                reserva_m = float(str(reserva_m).strip().replace(',', '.'))
                if reserva_m < 0:
                    raise ValueError
            except (InvalidOperation, TypeError, ValueError):
                rechazadas += 1
                continue

            nuevas_reservas.append(Reserva(
                ruta=ruta,
                nombre=nombre,
                tipo=tipo,
                reserva_m=reserva_m,
                latitud=latitud,
                longitud=longitud,
            ))

        if not nuevas_reservas and rechazadas:
            return JsonResponse(
                {
                    'status': 'error',
                    'message': 'No se importaron reservas: todas las filas tienen nombre o coordenadas inválidas.',
                },
                status=400,
            )

        Reserva.objects.bulk_create(nuevas_reservas, batch_size=1000)
        mensaje = f'{len(nuevas_reservas)} reservas importadas exitosamente.'
        if rechazadas:
            mensaje += f' {rechazadas} filas fueron omitidas por datos inválidos.'
        return JsonResponse({
            'status': 'success',
            'warning': bool(rechazadas),
            'message': mensaje,
            'rechazadas': rechazadas,
        })

    except ERRORES_DATOS_ENTRADA:
        transaction.set_rollback(True)
        logger.info("Archivo de reservas inválido", exc_info=True)
        return JsonResponse(
            {'status': 'error', 'message': 'El archivo de reservas contiene datos inválidos.'},
            status=400,
        )
    except Exception:
        transaction.set_rollback(True)
        logger.exception("Error inesperado al importar reservas")
        return JsonResponse(
            {'status': 'error', 'message': 'No se pudo procesar el archivo de reservas.'},
            status=500,
        )

@login_required
@permission_required('mapas.add_detallepuertoodf', raise_exception=True)
@permission_required('mapas.change_detallepuertoodf', raise_exception=True)
@require_POST
@transaction.atomic
def import_puertos_archivo(request):
    return JsonResponse(
        {
            'status': 'error',
            'message': (
                'Este importador fue retirado. Utilice la carga oficial '
                '“Detalle de Puertos ODF” desde Importación.'
            ),
        },
        status=410,
    )


@login_required
@permission_required('mapas.view_detallepuertoodf', raise_exception=True)
def planta_interna_view(request):
    """Consulta global de puertos; los registros se solicitan por página."""
    from ..models import InventarioODF

    odfs = [
        {
            'id': odf.pk,
            'odf': odf.odf,
            'hub_site': nombre_site(odf),
            'sala': odf.rack_obj.sala.nombre,
            'rack': odf.rack_obj.nombre,
        }
        for odf in InventarioODF.objects.select_related(
            'rack_obj__sala__hub_site'
        ).order_by(
            'rack_obj__sala__hub_site__nombre',
            'rack_obj__sala__nombre',
            'rack_obj__nombre',
            'odf',
        )
    ]
    context = {
        'odfs_puertos': odfs,
        'sites_puertos': sorted({odf['hub_site'] for odf in odfs if odf['hub_site']}),
        'salas_puertos': sorted({odf['sala'] for odf in odfs if odf['sala']}),
        'racks_puertos': sorted({odf['rack'] for odf in odfs if odf['rack']}),
        'rutas_puertos': list(Ruta.objects.order_by('nombre').values('id', 'nombre')),
    }
    return render(request, 'mapa_inventario/planta_interna.html', context)

@login_required
def planta_externa_view(request):
    """Consulta global de fibras y elementos; los datos se solicitan por página."""
    if not (
        request.user.has_perm('mapas.view_inventariofibra')
        or request.user.has_perm('mapas.view_reserva')
    ):
        raise PermissionDenied
    from ..models import Reserva, Ruta

    context = {
        'rutas_planta_externa': list(
            Ruta.objects.filter(tramos_inventario__isnull=False)
            .order_by('nombre').values('id', 'nombre').distinct()
        ),
        'tipos_elemento': list(
            Reserva.objects.exclude(tipo__isnull=True).exclude(tipo='')
            .order_by('tipo').values_list('tipo', flat=True).distinct()
        ),
    }
    return render(request, 'mapa_inventario/planta_externa.html', context)
