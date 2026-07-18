import json
import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_http_methods, require_POST
from django.db import transaction
from ..models import Ruta, CoordenadaRuta, InventarioTramo

logger = logging.getLogger('mapas')

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
    from django.db.models import Sum, Count
    from ..models import Ruta, InventarioTramo, Reserva, InventarioODF, DetallePuertoODF, InventarioFibra, HubSite
    
    rutas_db = Ruta.objects.prefetch_related('tramos_inventario', 'reservas').filter(tramos_inventario__isnull=False).distinct()
    
    total_rutas = rutas_db.count()
    
    resumen_rutas = []
    stats_trazado = {'AEREO': 0, 'SOTERRADO': 0, 'HIBRIDO': 0}
    distancia_trazado = {'AEREO': 0.0, 'SOTERRADO': 0.0, 'HIBRIDO': 0.0}
    stats_capacidad = {} # { '144': 0, '64': 0, ... }

    total_km = 0.0
    
    for r in rutas_db:
        tramos = r.tramos_inventario.all()
        tipos_en_ruta = set(t.tipo_trazado for t in tramos)
        
        # Ruta.distancia_m contiene el total; cada InventarioTramo conserva solo su segmento.
        distancia_ruta_m = r.distancia_m or sum((t.distancia_m or 0 for t in tramos), 0)
        if not distancia_ruta_m:
            coords = list(r.coordenadas.all().order_by('orden'))
            if len(coords) >= 2:
                distancia_ruta_m = calculate_coordinate_distance(coords)
                
        distancia_ruta_km = round(distancia_ruta_m / 1000, 2)
        
        res_tramos_m = sum((t.reservas_m or 0 for t in tramos), 0)
        res_nodos_m = sum(res.reserva_m or 0 for res in r.reservas.all())
        total_res_ruta_m = round(res_tramos_m + res_nodos_m, 2)

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
            if t.hub_site and t.hub_site != "N/A" and hub_origen == "N/A":
                hub_origen = t.hub_site
            if t.destino and t.destino != "N/A" and destino == "N/A":
                destino = t.destino
            if t.marca_modelo and t.marca_modelo != "N/A" and marca_modelo == "N/A":
                marca_modelo = t.marca_modelo
            if t.capacidad and t.capacidad != "N/A" and cap == "N/A":
                cap = t.capacidad
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
            
        fibras_qs = r.fibras_inventario.all()
        if fibras_qs.exists():
            hilos_ocupados_total = 0
            hilos_reservados_total = 0
            hilos_libres_total = 0
            for f in fibras_qs:
                est = (f.estado or '').lower()
                if est in ['ocupado', 'ocupada']:
                    hilos_ocupados_total += 1
                elif est in ['reservado', 'reservada']:
                    hilos_reservados_total += 1
                elif est == 'libre':
                    hilos_libres_total += 1
        else:
            hilos_reservados_total = 0
        
        stats_capacidad[cap] = stats_capacidad.get(cap, 0) + 1

        # Conteo dinámico de tipos de reservas (nodos/hitos)
        conteo_reservas_nodos = {}
        for res in r.reservas.all():
            tipo_res = res.tipo if res.tipo else "Otro"
            conteo_reservas_nodos[tipo_res] = conteo_reservas_nodos.get(tipo_res, 0) + 1
        
        resumen_rutas.append({
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
            'hilos_libres': hilos_libres_total
        })

    # Totales para los cards superiores
    total_km_global = sum(r['distancia_km'] for r in resumen_rutas)
    total_res_global_km = sum(r['reservas_km'] for r in resumen_rutas)

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
        'total_odfs': InventarioODF.objects.count() if hasattr(InventarioODF, 'objects') else 0,
        'puertos_libres': DetallePuertoODF.objects.filter(estado_puerto__iexact='Libre').count() if hasattr(DetallePuertoODF, 'objects') else 0,
        'puertos_ocupados': DetallePuertoODF.objects.filter(estado_puerto__iexact='Ocupado').count() if hasattr(DetallePuertoODF, 'objects') else 0,
        'fibras_libres': InventarioFibra.objects.filter(estado__iexact='Libre').count() if hasattr(InventarioFibra, 'objects') else 0,
        'fibras_ocupadas': InventarioFibra.objects.filter(estado__iexact='Ocupada').count() + InventarioFibra.objects.filter(estado__iexact='Ocupado').count() if hasattr(InventarioFibra, 'objects') else 0,
        'odf_list': list(InventarioODF.objects.values('odf', 'hub_site').order_by('odf')),
        'hub_sites': list(HubSite.objects.values_list('nombre', flat=True).order_by('nombre')) if hasattr(HubSite, 'objects') else [],
    }

    # Datos para el mapa de sites
    from django.db.models import Q
    sites_map_data = []
    if hasattr(HubSite, 'objects'):
        sites_con_coords = HubSite.objects.filter(latitud__isnull=False, longitud__isnull=False)
        for site in sites_con_coords:
            rutas_qs = Ruta.objects.filter(
                Q(tramos_inventario__hub_site=site.nombre) | Q(tramos_inventario__destino=site.nombre)
            ).distinct()
            rutas_count = rutas_qs.count()
            
            odf_count = 0
            if hasattr(InventarioODF, 'objects'):
                odf_count = InventarioODF.objects.filter(hub_site=site.nombre).count()
                
            hilos_count = 0
            if hasattr(InventarioFibra, 'objects'):
                hilos_count = InventarioFibra.objects.filter(ruta__in=rutas_qs).count()
            
            sites_map_data.append({
                'nombre': site.nombre,
                'lat': float(site.latitud),
                'lng': float(site.longitud),
                'rutas_count': rutas_count,
                'odf_count': odf_count,
                'hilos_count': hilos_count
            })
    import json
    context['sites_map_data'] = json.dumps(sites_map_data)
    return render(request, 'mapa_inventario/dashboard_inventario.html', context)

@login_required
@permission_required('mapas.view_ruta', raise_exception=True)
def inventario_externo(request):
    """Vista para Listado de Rutas Troncales (Inventario Externo)."""
    from ..models import Ruta
    rutas_db = Ruta.objects.prefetch_related('tramos_inventario', 'reservas').filter(tramos_inventario__isnull=False).distinct()
    resumen_rutas = []
    
    for r in rutas_db:
        tramos = r.tramos_inventario.all()
        tipos_en_ruta = set(t.tipo_trazado for t in tramos)
        
        distancia_ruta_m = r.distancia_m or sum((t.distancia_m or 0 for t in tramos), 0)
        if not distancia_ruta_m:
            coords = list(r.coordenadas.all().order_by('orden'))
            if len(coords) >= 2:
                distancia_ruta_m = calculate_coordinate_distance(coords)
                
        distancia_ruta_km = round(distancia_ruta_m / 1000, 2)
        
        res_tramos_m = sum((t.reservas_m or 0 for t in tramos), 0)
        res_nodos_m = sum(res.reserva_m or 0 for res in r.reservas.all())
        total_res_ruta_m = round(res_tramos_m + res_nodos_m, 2)

        if 'HIBRIDO' in tipos_en_ruta or ('AEREO' in tipos_en_ruta and 'SOTERRADO' in tipos_en_ruta):
            tipo_final = 'HIBRIDO'
        elif 'AEREO' in tipos_en_ruta:
            tipo_final = 'AEREO'
        elif 'SOTERRADO' in tipos_en_ruta:
            tipo_final = 'SOTERRADO'
        else:
            tipo_final = 'SIN CLASIFICAR'
            
        hub_origen, destino, marca_modelo, cap, tipo_fibra, serial, estado_tramo, odf_nombre = "N/A", "N/A", "N/A", "N/A", "", "", "", ""
        mufas_total, splitters_total, hilos_ocupados_total, hilos_libres_total = 0, 0, 0, 0
        
        for t in tramos:
            if t.hub_site and t.hub_site != "N/A" and hub_origen == "N/A": hub_origen = t.hub_site
            if t.destino and t.destino != "N/A" and destino == "N/A": destino = t.destino
            if t.marca_modelo and t.marca_modelo != "N/A" and marca_modelo == "N/A": marca_modelo = t.marca_modelo
            if t.capacidad and t.capacidad != "N/A" and cap == "N/A": cap = t.capacidad
            if t.tipo_fibra and not tipo_fibra: tipo_fibra = t.tipo_fibra
            if t.serial and not serial: serial = t.serial
            if t.estado and not estado_tramo: estado_tramo = t.estado
            mufas_total += (t.mufas or 0)
            splitters_total += (t.splitters or 0)
            if t.odf_nombre and not odf_nombre: odf_nombre = t.odf_nombre
            hilos_ocupados_total += (t.hilos_ocupados or 0)
            hilos_libres_total += (t.hilos_libres or 0)
        fibras_qs = r.fibras_inventario.all()
        if fibras_qs.exists():
            hilos_ocupados_total = 0
            hilos_reservados_total = 0
            hilos_libres_total = 0
            for f in fibras_qs:
                est = (f.estado or '').lower()
                if est in ['ocupado', 'ocupada']:
                    hilos_ocupados_total += 1
                elif est in ['reservado', 'reservada']:
                    hilos_reservados_total += 1
                elif est == 'libre':
                    hilos_libres_total += 1
        else:
            hilos_reservados_total = 0
            
        conteo_reservas_nodos = {}
        for res in r.reservas.all():
            tipo_res = res.tipo if res.tipo else "Otro"
            conteo_reservas_nodos[tipo_res] = conteo_reservas_nodos.get(tipo_res, 0) + 1
            
        resumen_rutas.append({
            'nombre': r.nombre, 'hub_origen': hub_origen, 'destino': destino, 'marca_modelo': marca_modelo,
            'tipo': tipo_final, 'distancia_km': distancia_ruta_km, 'capacidad': cap,
            'reservas_km': round(total_res_ruta_m / 1000, 3), 'conteo_reservas': conteo_reservas_nodos,
            'tipo_fibra': tipo_fibra, 'serial': serial, 'estado': estado_tramo, 'mufas': mufas_total, 
            'splitters': splitters_total, 'odf_nombre': odf_nombre, 'hilos_ocupados': hilos_ocupados_total, 
            'hilos_reservados': hilos_reservados_total, 'hilos_libres': hilos_libres_total
        })

    from ..models import InventarioODF, HubSite
    context = {
        'resumen_rutas': resumen_rutas,
        'hub_sites': list(HubSite.objects.values_list('nombre', flat=True).order_by('nombre')) if hasattr(HubSite, 'objects') else [],
        'odf_list': list(InventarioODF.objects.values('odf', 'hub_site').order_by('odf'))
    }

    return render(request, 'mapa_inventario/inventario_externo.html', context)

@login_required
@permission_required('mapas.view_inventarioodf', raise_exception=True)
def inventario_interno(request):
    """Vista para Inventario de ODFs / Racks (Inventario Interno)."""
    from ..models import InventarioODF, HubSite, Ruta
    context = {
        'odf_list': list(InventarioODF.objects.values('odf', 'hub_site').order_by('odf')),
        'hub_sites': list(HubSite.objects.values_list('nombre', flat=True).order_by('nombre')) if hasattr(HubSite, 'objects') else [],
        'resumen_rutas': list(Ruta.objects.values('nombre').order_by('nombre')),
    }
    return render(request, 'mapa_inventario/inventario_interno.html', context)
@login_required
@permission_required('mapas.view_ruta', raise_exception=True)
def get_datos_inventario(request):
    """
    Retorna JSON con la estructura de rutas segmentadas y su metadata.
    """
    rutas_db = Ruta.objects.prefetch_related('coordenadas', 'tramos_inventario', 'reservas').filter(tramos_inventario__isnull=False).distinct()
    resultado = []

    for r in rutas_db:
        # Agrupar coordenadas por tramo_secuencia
        tramos_coords = {}
        for c in r.coordenadas.all():
            sec = c.tramo_secuencia or 1
            if sec not in tramos_coords:
                tramos_coords[sec] = []
            tramos_coords[sec].append([float(c.latitud), float(c.longitud)])
            
        if not tramos_coords:
            continue # Si no tiene coordenadas, la saltamos
            
        # Generar metadata mapping
        tramos_metadata = {t.tramo_secuencia: t for t in r.tramos_inventario.all()}
        tramo_principal = tramos_metadata.get(1)
        
        tramos_list = []
        for sec, coords in tramos_coords.items():
            meta = tramos_metadata.get(sec)
            
            def get_val(field):
                val = getattr(meta, field, None) if meta else None
                if val in [None, '', 'N/A'] and tramo_principal:
                    val = getattr(tramo_principal, field, None)
                return val
            
            hub_site_name = get_val('hub_site')
            hub_site_lat = None
            hub_site_lon = None
            if hub_site_name:
                from ..models import HubSite
                hs = HubSite.objects.filter(nombre__iexact=hub_site_name).first()
                if hs and hs.latitud and hs.longitud:
                    hub_site_lat = float(hs.latitud)
                    hub_site_lon = float(hs.longitud)

            tramo_obj = {
                "tramo_secuencia": sec,
                "tipo_trazado": (meta.tipo_trazado if meta else "SIN CLASIFICAR") or "SIN CLASIFICAR",
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
                "hub_site_lat": hub_site_lat,
                "hub_site_lon": hub_site_lon,
                "marca_modelo": get_val('marca_modelo'),
                "serial": get_val('serial'),
                "odf_nombre": get_val('odf_nombre'),
                "coordenadas": coords
            }
            tramos_list.append(tramo_obj)
            
        reservas_list = []
        for res in r.reservas.all():
            reservas_list.append({
                "nombre": res.nombre,
                "tipo": res.tipo if res.tipo else "Desconocido",
                "reserva_m": res.reserva_m if res.reserva_m else 0.0,
                "lat": float(res.latitud),
                "lon": float(res.longitud)
            })
            
        resultado.append({
            "nombre": r.nombre,
            "otu": r.otu.nombre if r.otu else "N/A",
            "olt": r.olt if r.olt else "N/A",
            "pon": r.pon if r.pon else "N/A",
            "tramos": tramos_list,
            "reservas_nodos": reservas_list
        })

    return JsonResponse({"status": "success", "data": resultado})

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
    
    fibras = InventarioFibra.objects.filter(ruta__nombre=ruta_nombre).values(
        'id', 'fibra_numero', 'estado', 'nombre_fibra', 
        'destino', 'tipo_conector'
    )
    
    data = []
    for f in fibras:
        data.append({
            'id': f['id'],
            'fibra_numero': f['fibra_numero'],
            'estado': f['estado'],
            'nombre_fibra': f['nombre_fibra'],
            'origen_odf': origen_ruta,
            'destino': f['destino'],
            'tipo_conector': f['tipo_conector']
        })
        
    return JsonResponse({"status": "success", "data": data})

@login_required
@permission_required('mapas.add_inventariofibra', raise_exception=True)
@require_POST
@transaction.atomic
def create_detalle_fibra(request):
    """Crea un hilo/fibra manualmente para una ruta específica."""
    import json
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            from ..models import InventarioFibra, Ruta
            
            ruta_nombre = data.get('ruta_nombre', '').strip()
            fibra_numero = str(data.get('fibra_numero', '')).strip()
            
            if not ruta_nombre or not fibra_numero:
                return JsonResponse({"status": "error", "message": "Faltan datos obligatorios"}, status=400)
                
            ruta = Ruta.objects.filter(nombre=ruta_nombre).first()
            if not ruta:
                return JsonResponse({"status": "error", "message": "Ruta no encontrada"}, status=404)
                
            # Validar si ya existe
            if InventarioFibra.objects.filter(ruta=ruta, fibra_numero=fibra_numero).exists():
                return JsonResponse({"status": "error", "message": f"La fibra/hilo '{fibra_numero}' ya existe para esta ruta."}, status=400)
            
            # Validar capacidad de hilos
            import re
            from ..models import InventarioTramo
            tramo = InventarioTramo.objects.filter(ruta=ruta).order_by('tramo_secuencia').first()
            if tramo and tramo.capacidad:
                match = re.search(r'\d+', tramo.capacidad)
                if match:
                    capacidad_max = int(match.group())
                    if capacidad_max > 0:
                        actual_hilos = InventarioFibra.objects.filter(ruta=ruta).count()
                        if actual_hilos >= capacidad_max:
                            return JsonResponse({"status": "error", "message": f"Límite excedido. La ruta tiene una capacidad máxima de {capacidad_max} hilos."}, status=400)
            
            nuevo_estado = str(data.get('estado', 'Libre')).strip()
            
            InventarioFibra.objects.create(
                ruta=ruta,
                fibra_numero=fibra_numero,
                estado=nuevo_estado,
                nombre_fibra=str(data.get('nombre_fibra', '')).strip(),
                origen_odf=str(data.get('origen_odf', '')).strip(),
                destino=str(data.get('destino', '')).strip(),
                tipo_conector=str(data.get('tipo_conector', '')).strip()
            )
            return JsonResponse({"status": "success", "message": "Hilo creado correctamente"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)
    return JsonResponse({"status": "error", "message": "Método no permitido"}, status=405)

@login_required
@permission_required('mapas.add_inventariofibra', raise_exception=True)
@require_POST
@transaction.atomic
def import_fibras_csv(request):
    """Importa hilos/fibras desde un CSV para una ruta específica, actualizando o agregando."""
    if request.method == 'POST':
        try:
            import pandas as pd
            import io
            from ..models import InventarioFibra, Ruta
            
            csv_file = request.FILES.get('csv_fibras')
            ruta_nombre = request.POST.get('ruta_nombre', '').strip()
            
            if not csv_file or not ruta_nombre:
                return JsonResponse({"status": "error", "message": "Archivo o ruta faltante."}, status=400)
                
            ruta = Ruta.objects.filter(nombre=ruta_nombre).first()
            if not ruta:
                return JsonResponse({"status": "error", "message": "Ruta no encontrada."}, status=404)
                
            decoded_file = csv_file.read().decode('utf-8')
            df = pd.read_csv(io.StringIO(decoded_file))
            df.columns = [c.strip().lower() for c in df.columns]
            
            # Mapeo de nombres de columnas a la base de datos
            # Expected columns in CSV: fibra_numero, estado, nombre_fibra, origen_odf, destino, tipo_conector
            col_map = {
                'fibra_numero': ['hilo/fibra', 'fibra', 'hilo', 'fibra_numero'],
                'estado': ['estado'],
                'nombre_fibra': ['nombre/uso', 'nombre', 'uso', 'nombre_fibra'],
                'origen_odf': ['origen_odf', 'origen odf', 'odf'],
                'destino': ['destino'],
                'tipo_conector': ['tipo_conector', 'conector', 'tipo conector']
            }
            
            def get_val(row, field):
                for possible_name in col_map[field]:
                    if possible_name in df.columns:
                        val = row[possible_name]
                        return str(val).strip() if pd.notna(val) else ''
                return ''
                
            # Necesitamos encontrar cuál es la columna que tiene el número de fibra
            fibra_col = next((c for c in col_map['fibra_numero'] if c in df.columns), None)
            
            if not fibra_col:
                return JsonResponse({"status": "error", "message": "El CSV debe contener una columna para el Hilo/Fibra (ej: 'hilo/fibra' o 'fibra')."}, status=400)
                
            # Validar capacidad de hilos
            import re
            from ..models import InventarioTramo
            tramo = InventarioTramo.objects.filter(ruta=ruta).order_by('tramo_secuencia').first()
            if tramo and tramo.capacidad:
                match = re.search(r'\d+', tramo.capacidad)
                if match:
                    capacidad_max = int(match.group())
                    if capacidad_max > 0:
                        hilos_en_csv = set(str(row[fibra_col]).strip() for index, row in df.iterrows() if str(row[fibra_col]).strip() not in ['nan', 'None', ''])
                        hilos_existentes = set(InventarioFibra.objects.filter(ruta=ruta).values_list('fibra_numero', flat=True))
                        nuevos_hilos = hilos_en_csv - hilos_existentes
                        if len(hilos_existentes) + len(nuevos_hilos) > capacidad_max:
                            return JsonResponse({"status": "error", "message": f"Límite excedido. La ruta tiene capacidad para {capacidad_max} hilos, pero estás intentando registrar {len(hilos_existentes) + len(nuevos_hilos)} hilos totales."}, status=400)
                
            for _, row in df.iterrows():
                fibra_num = str(row[fibra_col]).strip()
                if not fibra_num or fibra_num.lower() == 'nan':
                    continue
                    
                InventarioFibra.objects.update_or_create(
                    ruta=ruta,
                    fibra_numero=fibra_num,
                    defaults={
                        'estado': get_val(row, 'estado') or 'Libre',
                        'nombre_fibra': get_val(row, 'nombre_fibra'),
                        'origen_odf': get_val(row, 'origen_odf'),
                        'destino': get_val(row, 'destino'),
                        'tipo_conector': get_val(row, 'tipo_conector')
                    }
                )
                
            return JsonResponse({"status": "success", "message": "Hilos importados correctamente."})
        except Exception as e:
            return JsonResponse({"status": "error", "message": f"Error procesando CSV: {str(e)}"}, status=500)
    return JsonResponse({"status": "error", "message": "Método no permitido"}, status=405)

@login_required
@permission_required('mapas.change_inventariofibra', raise_exception=True)
@require_POST
@transaction.atomic
def update_detalle_fibra(request):
    """Actualiza los datos de un hilo/fibra específico."""
    import json
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            from ..models import InventarioFibra
            
            fibra_id = data.get('id')
            if not fibra_id:
                return JsonResponse({"status": "error", "message": "ID de fibra no proporcionado"}, status=400)
            
            # Limpiar datos entrantes
            nuevo_estado = str(data.get('estado', '')).strip()
            
            fibra = InventarioFibra.objects.select_for_update().filter(id=fibra_id).first()
            if not fibra:
                return JsonResponse({"status": "error", "message": "No se encontró la fibra o no hubo cambios"}, status=400)

            fibra.fibra_numero = str(data.get('fibra_numero', '')).strip()
            fibra.estado = nuevo_estado
            fibra.nombre_fibra = str(data.get('nombre_fibra', '')).strip()
            fibra.origen_odf = str(data.get('origen_odf', '')).strip()
            fibra.destino = str(data.get('destino', '')).strip()
            fibra.tipo_conector = str(data.get('tipo_conector', '')).strip()
            fibra.full_clean()
            fibra.save()

            if fibra.ruta_id:
                from ..models import InventarioTramo
                ocupados = InventarioFibra.objects.filter(ruta_id=fibra.ruta_id, estado__in=['Ocupada', 'Ocupado', 'Active', 'Activo']).count()
                libres = InventarioFibra.objects.filter(ruta_id=fibra.ruta_id, estado='Libre').count()
                tramos = InventarioTramo.objects.filter(
                    ruta_id=fibra.ruta_id
                ).order_by('tramo_secuencia')
                primer_tramo = tramos.first()
                tramos.update(hilos_ocupados=0, hilos_libres=0)
                if primer_tramo:
                    InventarioTramo.objects.filter(pk=primer_tramo.pk).update(
                        hilos_ocupados=ocupados,
                        hilos_libres=libres,
                    )
            
            return JsonResponse({"status": "success", "message": "Fibra actualizada correctamente"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
    return JsonResponse({"status": "error", "message": "Método no permitido"}, status=405)

@login_required
@permission_required('mapas.view_inventarioodf', raise_exception=True)
def get_odfs(request):
    """Retorna la lista de todos los ODFs para la tabla del dashboard."""
    from ..models import InventarioODF
    odfs = InventarioODF.objects.all().values(
        'id', 'hub_site', 'sala', 'rack', 'odf', 
        'capacidad_puertos', 'puertos_ocupados', 'puertos_libres', 
        'tipo_conector', 'estado', 'observaciones'
    )
    return JsonResponse({"status": "success", "data": list(odfs)})

@login_required
@permission_required('mapas.view_detallepuertoodf', raise_exception=True)
def get_detalle_puertos(request, odf_nombre):
    """Retorna el detalle de puertos para un ODF específico."""
    from ..models import DetallePuertoODF
    
    hub_site = request.GET.get('hub_site', '').strip()
    query = DetallePuertoODF.objects.filter(odf_obj__odf__iexact=odf_nombre)
    if hub_site:
        query = query.filter(odf_obj__hub_site=hub_site)
        
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
        data.append({
            'id': p.id,
            'odf': p.odf,
            'bandeja': p.bandeja,
            'puerto_odf': p.puerto_odf,
            'fibra': p.fibra,
            'estado_puerto': p.estado_puerto,
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
            
            # Limpiar datos entrantes
            nuevo_estado = str(data.get('estado_puerto', '')).strip()
            nuevo_puerto = str(data.get('puerto_odf', '')).strip()
            
            puerto_actualizado = DetallePuertoODF.objects.select_for_update().filter(id=puerto_id).first()
            if not puerto_actualizado:
                return JsonResponse({"status": "error", "message": "No se encontró el puerto o no hubo cambios"}, status=400)

            puerto_actualizado.bandeja = str(data.get('bandeja', '')).strip()
            puerto_actualizado.puerto_odf = nuevo_puerto
            puerto_actualizado.fibra = str(data.get('fibra', '')).strip()
            puerto_actualizado.estado_puerto = nuevo_estado
            puerto_actualizado.tipo_conector = str(data.get('tipo_conector', '')).strip()
            puerto_actualizado.patchcord = str(data.get('patchcord', '')).strip()
            puerto_actualizado.destino = str(data.get('destino', '')).strip()
            puerto_actualizado.observaciones = str(data.get('observaciones', '')).strip()
            puerto_actualizado.save()
            puerto_actualizado.odf_obj.actualizar_contadores()
            
            return JsonResponse({
                "status": "success", 
                "message": "Puerto actualizado correctamente"
            })
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
    return JsonResponse({"status": "error", "message": "Método no permitido"}, status=405)

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
            
        # Crear Tramo Técnico inicial (para que aparezca en el dashboard)
        tramo_inicial = InventarioTramo.objects.create(
            ruta=nueva_ruta,
            tramo_secuencia=1,
            tipo_trazado=data.get('tipo_trazado', ''),
            estado=data.get('estado', ''),
            capacidad=capacidad_raw,
            hub_site=data.get('hub_origen', ''),
            destino=data.get('destino', ''),
            marca_modelo=data.get('marca_modelo', ''),
            tipo_fibra=data.get('tipo_fibra', ''),
            serial=data.get('serial', ''),
            mufas=int(data.get('mufas', 0) or 0),
            splitters=int(data.get('splitters', 0) or 0),
            odf_nombre=data.get('odf_nombre', ''),
            hilos_ocupados=int(data.get('hilos_ocupados', 0) or 0),
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
                CoordenadaRuta.objects.filter(ruta=nueva_ruta).delete()
                
                coords_a_crear = []
                distancia_calculada = 0.0
                prev_lat = None
                prev_lon = None
                from .importacion import calcular_distancia_haversine
                
                for i, row in df.iterrows():
                    lat = float(row['latitude'])
                    lon = float(row['longitude'])
                    
                    if prev_lat is not None and prev_lon is not None:
                        distancia_calculada += calcular_distancia_haversine(prev_lat, prev_lon, lat, lon)
                    prev_lat = lat
                    prev_lon = lon
                    
                    coords_a_crear.append(
                        CoordenadaRuta(
                            ruta=nueva_ruta,
                            tramo=tramo_inicial,
                            latitud=lat,
                            longitud=lon,
                            orden=i + 1,
                            tramo_secuencia=1
                        )
                    )
                if coords_a_crear:
                    CoordenadaRuta.objects.bulk_create(coords_a_crear)
                    
                distancia_ingresada = float(data.get('distancia_km', 0) or 0) * 1000
                if distancia_ingresada == 0 and distancia_calculada > 0:
                    nueva_ruta.distancia_m = distancia_calculada
                    nueva_ruta.save(update_fields=['distancia_m'])
                    tramo = InventarioTramo.objects.filter(ruta=nueva_ruta).first()
                    if tramo:
                        tramo.distancia_m = distancia_calculada
                        tramo.save(update_fields=['distancia_m'])

        return JsonResponse({'status': 'success', 'message': 'Ruta creada correctamente'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

@login_required
@permission_required('mapas.add_inventarioodf', raise_exception=True)
@require_POST
def create_odf_manual(request):
    """Crea un nuevo ODF con validación de ubicación única."""
    import json
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)
    
    try:
        from ..models import InventarioODF
        from ..services.inventario import resolver_rack
        data = json.loads(request.body)
        
        hub = data.get('hub_site', '').strip()
        sala = data.get('sala', '').strip()
        rack = data.get('rack', '').strip()
        odf_nombre = data.get('odf', '').strip()
        
        if not odf_nombre:
            return JsonResponse({'status': 'error', 'message': 'El nombre del ODF es obligatorio'})

        rack_obj = resolver_rack(hub, sala, rack)
        exists = InventarioODF.objects.filter(rack_obj=rack_obj, odf=odf_nombre).exists()
        
        if exists:
            return JsonResponse({
                'status': 'error', 
                'message': f'El ODF "{odf_nombre}" ya existe en esta ubicación (Hub: {hub}, Sala: {sala}, Rack: {rack}).'
            })

        # Crear ODF
        InventarioODF.objects.create(
            rack_obj=rack_obj,
            hub_site=hub,
            sala=sala,
            rack=rack,
            odf=odf_nombre,
            capacidad_puertos=int(data.get('capacidad_puertos', 0) or 0),
            puertos_libres=int(data.get('capacidad_puertos', 0) or 0),
            tipo_conector=data.get('tipo_conector', ''),
            estado=data.get('estado', 'Activo')
        )
        
        return JsonResponse({'status': 'success', 'message': 'ODF creado correctamente'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

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
            return JsonResponse({'status': 'error', 'message': 'El ODF y el número de puerto son obligatorios'})

        # Find the ODF
        odf_obj = InventarioODF.objects.filter(odf__iexact=odf_nombre).first()
        if not odf_obj:
            return JsonResponse({'status': 'error', 'message': 'ODF no encontrado'})

        # Validar si el puerto ya existe
        exists = DetallePuertoODF.objects.filter(odf_obj=odf_obj, puerto_odf=puerto_num).exists()
        if exists:
            return JsonResponse({'status': 'error', 'message': f'El puerto {puerto_num} ya existe en este ODF.'})

        # Validar capacidad
        capacidad_max = odf_obj.capacidad_puertos or 0
        if capacidad_max > 0:
            actual_ports = DetallePuertoODF.objects.filter(odf_obj=odf_obj).count()
            if actual_ports >= capacidad_max:
                return JsonResponse({'status': 'error', 'message': f'Límite excedido. El ODF "{odf_nombre}" ya alcanzó su capacidad máxima de {capacidad_max} puertos.'})

        estado_puerto = data.get('estado_puerto', 'Libre').capitalize()
        if estado_puerto not in ['Libre', 'Ocupado']:
            estado_puerto = 'Libre'

        # Crear Puerto
        DetallePuertoODF.objects.create(
            odf_obj=odf_obj,
            odf=odf_nombre,
            puerto_odf=puerto_num,
            bandeja=data.get('bandeja', ''),
            fibra=data.get('fibra', ''),
            estado_puerto=estado_puerto,
            tipo_conector=data.get('tipo_conector', ''),
            patchcord=data.get('patchcord', ''),
            destino=data.get('destino', ''),
            observaciones=data.get('observaciones', '')
        )
        
        odf_obj.actualizar_contadores()
        
        return JsonResponse({'status': 'success', 'message': 'Puerto creado correctamente'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

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
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)

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
            
        ruta = Ruta.objects.filter(nombre=nombre_original).first()
        if not ruta:
            return JsonResponse({'status': 'error', 'message': 'Ruta no encontrada'})
            
        # Si cambia el nombre, verificar que el nuevo no exista
        if nombre_original != nuevo_nombre and Ruta.objects.filter(nombre=nuevo_nombre).exists():
            return JsonResponse({'status': 'error', 'message': f'La ruta "{nuevo_nombre}" ya existe.'})
            
        # Actualizar Ruta
        ruta.nombre = nuevo_nombre
        ruta.olt = data.get('hub_origen', '')
        ruta.distancia_m = float(data.get('distancia_km', 0) or 0) * 1000
        ruta.save()
        
        capacidad_raw = data.get('capacidad', '').strip()
        if capacidad_raw.isdigit():
            capacidad_raw = f"{capacidad_raw} Hilos"

        # Actualizar el primer tramo técnico
        tramo = InventarioTramo.objects.filter(ruta=ruta).order_by('tramo_secuencia').first()
        if tramo:
            # Solo permitimos sobreescribir el tipo_trazado si no hay coordenadas.
            # Si hay coordenadas, los tramos ya tienen su trazado específico (AEREO/SOTERRADO)
            if not ruta.coordenadas.exists():
                tramo.tipo_trazado = data.get('tipo_trazado', '')
                
            tramo.estado = data.get('estado', '')
            tramo.capacidad = capacidad_raw
            tramo.hub_site = data.get('hub_origen', '')
            tramo.destino = data.get('destino', '')
            tramo.marca_modelo = data.get('marca_modelo', '')
            tramo.tipo_fibra = data.get('tipo_fibra', '')
            tramo.serial = data.get('serial', '')
            tramo.mufas = int(data.get('mufas', 0) or 0)
            tramo.splitters = int(data.get('splitters', 0) or 0)
            tramo.odf_nombre = data.get('odf_nombre', '')
            tramo.hilos_ocupados = int(data.get('hilos_ocupados', 0) or 0)
            tramo.hilos_libres = int(data.get('hilos_libres', 0) or 0)
            tramo.distancia_m = float(data.get('distancia_km', 0) or 0) * 1000
            tramo.reservas_m = float(data.get('reserva_km', 0) or 0) * 1000
            tramo.save()
            
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
                CoordenadaRuta.objects.filter(ruta=ruta).delete()
                
                coords_a_crear = []
                distancia_calculada = 0.0
                prev_lat = None
                prev_lon = None
                from .importacion import calcular_distancia_haversine
                
                for i, row in df.iterrows():
                    lat = float(row['latitude'])
                    lon = float(row['longitude'])
                    
                    if prev_lat is not None and prev_lon is not None:
                        distancia_calculada += calcular_distancia_haversine(prev_lat, prev_lon, lat, lon)
                    prev_lat = lat
                    prev_lon = lon
                    
                    coords_a_crear.append(
                        CoordenadaRuta(
                            ruta=ruta,
                            tramo=tramo,
                            latitud=lat,
                            longitud=lon,
                            orden=i + 1,
                            tramo_secuencia=1
                        )
                    )
                if coords_a_crear:
                    CoordenadaRuta.objects.bulk_create(coords_a_crear)
                    
                distancia_ingresada = float(data.get('distancia_km', 0) or 0) * 1000
                if distancia_ingresada == 0 and distancia_calculada > 0:
                    ruta.distancia_m = distancia_calculada
                    ruta.save(update_fields=['distancia_m'])
                    tramo = InventarioTramo.objects.filter(ruta=ruta).first()
                    if tramo:
                        tramo.distancia_m = distancia_calculada
                        tramo.save(update_fields=['distancia_m'])

        return JsonResponse({'status': 'success', 'message': 'Ruta actualizada correctamente'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

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
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

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
        from ..services.inventario import resolver_rack
        data = json.loads(request.body)
        id_odf = data.get('id')
        
        if not id_odf:
            return JsonResponse({'status': 'error', 'message': 'ID de ODF requerido'})
            
        odf = InventarioODF.objects.filter(id=id_odf).first()
        if not odf:
            return JsonResponse({'status': 'error', 'message': 'ODF no encontrado'})
            
        hub = data.get('hub_site', '').strip()
        sala = data.get('sala', '').strip()
        rack = data.get('rack', '').strip()
        odf_nombre = data.get('odf', '').strip()
        
        rack_obj = resolver_rack(hub, sala, rack)
        # Validar duplicados si cambian los campos clave
        if odf.rack_obj_id != rack_obj.id or odf.odf != odf_nombre:
            exists = InventarioODF.objects.filter(rack_obj=rack_obj, odf=odf_nombre).exclude(id=id_odf).exists()
            if exists:
                return JsonResponse({'status': 'error', 'message': 'Ya existe otro ODF con esta misma ubicación/nombre'})
                
        # Actualizar campos
        odf.hub_site = hub
        odf.sala = sala
        odf.rack = rack
        odf.rack_obj = rack_obj
        odf.odf = odf_nombre
        # Solo actualizamos capacidad si no hay lógicas complejas de puertos ocupados, o se ajusta puertos libres
        nueva_capacidad = int(data.get('capacidad_puertos', 0) or 0)
        diff = nueva_capacidad - odf.capacidad_puertos
        odf.capacidad_puertos = nueva_capacidad
        odf.puertos_libres = max(0, odf.puertos_libres + diff)
        
        odf.tipo_conector = data.get('tipo_conector', '')
        odf.estado = data.get('estado', 'Activo')
        odf.save()
        
        return JsonResponse({'status': 'success', 'message': 'ODF actualizado correctamente'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

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
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

@login_required
@permission_required('mapas.view_detallepuertoodf', raise_exception=True)
def get_puertos_odf_api(request):
    """
    Retorna la lista de puertos para un ODF específico.
    Se usa en el mapa de inventario para el panel lateral flotante.
    """
    from ..models import InventarioODF, DetallePuertoODF
    
    hub_site = request.GET.get('hub_site', '').strip()
    odf_nombre = request.GET.get('odf_nombre', '').strip()
    
    if not odf_nombre:
        return JsonResponse({'status': 'error', 'message': 'El nombre del ODF es requerido'})
        
    # Buscar el ODF. Si hay hub_site, lo usamos para ser precisos.
    # Si no hay hub_site o no coincide exactamente, buscamos solo por odf_nombre y tomamos el primero
    odf = None
    if hub_site:
        odf = InventarioODF.objects.filter(hub_site=hub_site, odf=odf_nombre).first()
        
    if not odf:
        odf = InventarioODF.objects.filter(odf__iexact=odf_nombre).first()
        
    if not odf:
        return JsonResponse({'status': 'error', 'message': f'No se encontró el ODF {odf_nombre}'})
        
    puertos = list(DetallePuertoODF.objects.filter(odf_obj=odf))
    
    # Ordenar puertos de forma numérica ascendente
    def sort_key(p):
        try:
            return int(p.puerto_odf)
        except (ValueError, TypeError):
            return p.puerto_odf
            
    puertos.sort(key=sort_key)
    
    from django.db.models import Q
    from ..models import Ruta

    # Buscar la ruta troncal asociada a este ODF basándose en los tramos de las rutas
    ruta_asociada = Ruta.objects.filter(
        Q(odf_origen=odf)
        | Q(odf_destino=odf)
        | Q(tramos_inventario__odf_nombre__iexact=odf.odf)
    ).distinct().first()
    nombre_ruta_global = ruta_asociada.nombre if ruta_asociada else 'N/A'

    data = []
    for p in puertos:
        ruta_nombre = 'N/A'
        if p.estado_puerto.lower() == 'ocupado':
            ruta_nombre = nombre_ruta_global
            # Fallback en caso de que Destino contenga el nombre de la ruta explícitamente
            if ruta_nombre == 'N/A' and p.destino and "Puerto" in p.destino:
                ruta_nombre = p.destino
                
        data.append({
            'puerto': p.puerto_odf,
            'estado': p.estado_puerto,
            'bandeja': p.bandeja,
            'fibra': p.fibra,
            'destino': p.destino,
            'tipo_conector': p.tipo_conector,
            'ruta_troncal': ruta_nombre
        })
        
    return JsonResponse({
        'status': 'success',
        'odf': odf.odf,
        'hub_site': odf.hub_site,
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
        
        if not ruta_nombre:
            return JsonResponse({'status': 'error', 'message': 'Ruta no especificada.'})
            
        ruta = Ruta.objects.filter(nombre=ruta_nombre).first()
        if not ruta:
            return JsonResponse({'status': 'error', 'message': 'Ruta no encontrada.'})
            
        nueva_reserva = Reserva.objects.create(
            ruta=ruta,
            nombre=data.get('nombre', ''),
            tipo=data.get('tipo', ''),
            latitud=data.get('latitud', 0.0),
            longitud=data.get('longitud', 0.0),
            reserva_m=data.get('reserva_m') or 0.0
        )
        return JsonResponse({'status': 'success', 'message': f'Reserva {nueva_reserva.nombre} agregada exitosamente.'})
        
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

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
            
        ruta = Ruta.objects.filter(nombre=ruta_nombre).first()
        if not ruta:
            return JsonResponse({'status': 'error', 'message': 'Ruta no encontrada.'})

        if archivo.name.endswith('.csv'):
            df = pd.read_csv(archivo)
        elif archivo.name.endswith('.xlsx') or archivo.name.endswith('.xls'):
            df = pd.read_excel(archivo)
        else:
            return JsonResponse({'status': 'error', 'message': 'Formato no soportado. Use .csv o .xlsx'})

        nuevas_reservas = []
        for index, row in df.iterrows():
            nombre = str(row.get('Landmark name', row.get('Nombre', f'Reserva-{index}')))
            tipo = str(row.get('Connection type', row.get('Tipo', '')))
            reserva_m = row.get('Reserva (m)', row.get('Reserva_m', 0))
            lat = row.get('Latitud', 0)
            lon = row.get('Longitud', 0)
            
            # Limpiar NaNs
            if pd.isna(reserva_m) or str(reserva_m).strip() == '-': reserva_m = 0
            if pd.isna(lat) or str(lat).strip() == '-': lat = 0
            if pd.isna(lon) or str(lon).strip() == '-': lon = 0
            if pd.isna(tipo) or tipo == 'nan': tipo = ''

            from decimal import Decimal
            nuevas_reservas.append(Reserva(
                ruta=ruta,
                nombre=nombre,
                tipo=tipo,
                reserva_m=float(reserva_m),
                latitud=Decimal(str(lat)),
                longitud=Decimal(str(lon))
            ))

        Reserva.objects.bulk_create(nuevas_reservas)
        return JsonResponse({'status': 'success', 'message': f'{len(nuevas_reservas)} reservas importadas exitosamente.'})

    except Exception as e:
        transaction.set_rollback(True)
        return JsonResponse({'status': 'error', 'message': f'Error al procesar el archivo: {str(e)}'})

@login_required
@permission_required('mapas.add_detallepuertoodf', raise_exception=True)
@require_POST
@transaction.atomic
def import_puertos_archivo(request):
    try:
        import pandas as pd
        from ..models import InventarioODF, DetallePuertoODF
        
        archivo = request.FILES.get('archivo')
        odf_nombre = request.POST.get('odf_nombre')
        
        if not archivo or not odf_nombre:
            return JsonResponse({'status': 'error', 'message': 'Archivo u ODF no proporcionados.'})
            
        odf_obj = InventarioODF.objects.filter(odf__iexact=odf_nombre).first()
        if not odf_obj:
            return JsonResponse({'status': 'error', 'message': 'ODF no encontrado.'})

        if archivo.name.endswith('.csv'):
            df = pd.read_csv(archivo)
        elif archivo.name.endswith('.xlsx') or archivo.name.endswith('.xls'):
            df = pd.read_excel(archivo)
        else:
            return JsonResponse({'status': 'error', 'message': 'Formato no soportado. Use .csv o .xlsx'})

        creados = 0
        actualizados = 0

        # Mapeo flexible de columnas para soportar diferentes variantes
        def get_col(df, *nombres, default=''):
            for n in nombres:
                for c in df.columns:
                    if str(c).strip().lower() == str(n).strip().lower():
                        return df[c]
            return pd.Series([default] * len(df))

        df_puerto = get_col(df, 'Puerto ODF', 'Puerto')
        df_bandeja = get_col(df, 'Bandeja')
        df_fibra = get_col(df, 'Fibra', 'Hilo', 'Fibra/Hilo')
        df_estado = get_col(df, 'Estado', 'Estado Puerto')
        df_conector = get_col(df, 'Conector', 'Tipo Conector')
        df_patch = get_col(df, 'Patchcord')
        df_destino = get_col(df, 'Destino', 'Destino / Cliente')

        # Validar capacidad
        capacidad_max = odf_obj.capacidad_puertos or 0
        if capacidad_max > 0:
            puertos_en_csv = set(str(p).strip() for p in df_puerto if str(p).strip() not in ['nan', 'None', ''])
            puertos_existentes = set(DetallePuertoODF.objects.filter(odf_obj=odf_obj).values_list('puerto_odf', flat=True))
            nuevos_puertos = puertos_en_csv - puertos_existentes
            if len(puertos_existentes) + len(nuevos_puertos) > capacidad_max:
                return JsonResponse({'status': 'error', 'message': f'Límite excedido. El ODF "{odf_nombre}" tiene capacidad para {capacidad_max} puertos, pero estás intentando registrar un total de {len(puertos_existentes) + len(nuevos_puertos)} puertos.'})

        for i in range(len(df)):
            puerto_val = str(df_puerto.iloc[i]).strip()
            if not puerto_val or puerto_val == 'nan' or puerto_val == 'None':
                continue

            estado_val = str(df_estado.iloc[i]).strip().capitalize()
            if estado_val not in ['Libre', 'Ocupado']:
                estado_val = 'Libre'

            datos = {
                'bandeja': str(df_bandeja.iloc[i]).strip() if str(df_bandeja.iloc[i]).strip() != 'nan' else '',
                'fibra': str(df_fibra.iloc[i]).strip() if str(df_fibra.iloc[i]).strip() != 'nan' else '',
                'estado_puerto': estado_val,
                'tipo_conector': str(df_conector.iloc[i]).strip() if str(df_conector.iloc[i]).strip() != 'nan' else '',
                'patchcord': str(df_patch.iloc[i]).strip() if str(df_patch.iloc[i]).strip() != 'nan' else '',
                'destino': str(df_destino.iloc[i]).strip() if str(df_destino.iloc[i]).strip() != 'nan' else '',
            }

            obj, created = DetallePuertoODF.objects.update_or_create(
                odf_obj=odf_obj,
                puerto_odf=puerto_val,
                defaults=datos
            )
            
            if created:
                obj.odf = odf_nombre
                obj.save()
                creados += 1
            else:
                actualizados += 1

        # Actualizar contadores del ODF (pero no la capacidad máxima)
        odf_obj.puertos_libres = DetallePuertoODF.objects.filter(odf_obj=odf_obj, estado_puerto='Libre').count()
        odf_obj.puertos_ocupados = DetallePuertoODF.objects.filter(odf_obj=odf_obj, estado_puerto='Ocupado').count()
        odf_obj.save()

        return JsonResponse({
            'status': 'success', 
            'creados': creados,
            'actualizados': actualizados
        })

    except Exception as e:
        transaction.set_rollback(True)
        return JsonResponse({'status': 'error', 'message': f'Error al procesar el archivo: {str(e)}'})

@login_required
@permission_required('mapas.view_detallepuertoodf', raise_exception=True)
def planta_interna_view(request):
    """Consulta global de puertos; los registros se solicitan por página."""
    return render(request, 'mapa_inventario/planta_interna.html')

@login_required
def planta_externa_view(request):
    """Consulta global de fibras y elementos; los datos se solicitan por página."""
    if not (
        request.user.has_perm('mapas.view_inventariofibra')
        or request.user.has_perm('mapas.view_reserva')
    ):
        raise PermissionDenied
    return render(request, 'mapa_inventario/planta_externa.html')
