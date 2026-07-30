"""
Vistas del mapa interactivo principal.
"""
import json
import logging
from datetime import datetime
from collections import defaultdict

from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.staticfiles import finders
from django.db import connection
from django.templatetags.static import static
import csv

from ..distancias import redondear_km, resolver_distancia_ruta
from ..models import OTU, Ruta, Reserva, CoordenadaRuta
from .utils import clasificar_tipo_evento, get_geo_bounds, get_color_evento

logger = logging.getLogger('mapas')





def obtener_equipos_desde_db():
    equipos_queryset = OTU.objects.prefetch_related(
        'puertos', 'puertos__ruta_asociada', 'rutas'
    ).all()

    equipos_data = []
    for equipo in equipos_queryset:
        puertos_list = []
        monitoreados = 0
        libres = 0

        for puerto in equipo.puertos.all():
            puertos_list.append({
                'numero': puerto.numero,
                'estado': puerto.estado,
                'enlace': puerto.ruta_asociada.nombre if puerto.ruta_asociada else ''
            })
            if puerto.estado == 'Monitoreado':
                monitoreados += 1
            elif puerto.estado == 'Libre':
                libres += 1

        nombre_otu_sin_espacios = equipo.nombre.replace(" ", "_")
        ruta_estatica_imagen = f"img/imagenes-OTUs/{nombre_otu_sin_espacios}.png"
        if finders.find(ruta_estatica_imagen):
            url_imagen_final = static(ruta_estatica_imagen)
        else:
            url_imagen_final = static('img/default.png')

        equipos_data.append({
            'lat': float(equipo.latitud) if equipo.latitud else None,
            'lon': float(equipo.longitud) if equipo.longitud else None,
            'otu': equipo.nombre,
            'modelo': equipo.modelo,
            'puertos_monitoreados': monitoreados,
            'puertos_libres': libres,
            'puertos': puertos_list,
            'imagen': url_imagen_final,
            'rutas_asociadas': [r.nombre for r in equipo.rutas.all()]
        })

    return equipos_data




@login_required
@permission_required('mapas.view_alarmaveex', raise_exception=True)
def mapa_alarmas(request):
    """
    Renderiza la nueva interfaz del Mapa en Vivo para Monitoreo de Alarmas en tiempo real.
    """
    rutas_queryset = Ruta.objects.select_related('otu').prefetch_related('coordenadas', 'reservas', 'tramos_inventario').all()
    rutas_data = []
    
    for r in rutas_queryset:
        reservas_list = []
        for res in r.reservas.all():
            try:
                reservas_list.append({
                    "nombre": res.nombre,
                    "tipo": res.tipo if res.tipo else "Desconocido",
                    "reserva_m": float(res.reserva_m) if res.reserva_m else 0.0,
                    "lat": float(res.latitud) if res.latitud else 0.0,
                    "lon": float(res.longitud) if res.longitud else 0.0
                })
            except (ValueError, TypeError):
                continue
        
        tramos = [
            tramo
            for tramo in r.tramos_inventario.all()
            if tramo.vigente
        ]
        tipo_trazado = tramos[0].tipo_trazado if tramos else 'Sin Clasificar'
        distancia = resolver_distancia_ruta(r, tramos)
        
        # Calcular el hub_origen (site) de forma automática con paridad al dashboard de inventario
        hub_origen = 'No disponible'
        if r.otu:
            hub_origen = r.otu.nombre
        for t in tramos:
            if t.hub_site and t.hub_site != 'N/A':
                hub_origen = t.hub_site
                break
        
        rutas_data.append({
            'nombre': r.nombre,
            'coordenadas': [[float(c.latitud), float(c.longitud)] for c in r.coordenadas.all()],
            'otu': hub_origen,
            'distancia': (
                redondear_km(distancia, 3)
                if distancia.valor_m is not None
                else 'No disponible'
            ),
            'distancia_fuente': distancia.fuente,
            'olt': r.olt or 'No disponible',
            'pon': r.pon or 'No disponible',
            'enlace': r.enlace or '',
            'reservas_nodos': reservas_list,
            'tipo_trazado': tipo_trazado
        })
        
    cantidad_csv = Ruta.objects.count()
    cantidad_otu = OTU.objects.count()
    equipos_data = obtener_equipos_desde_db()
    
    # Obtener límites geográficos para centrar el mapa
    lat_min, lat_max, lon_min, lon_max = get_geo_bounds()
    
    # Calcular centro aproximado
    centro_lat = (lat_min + lat_max) / 2
    centro_lon = (lon_min + lon_max) / 2
    
    context = {
        'page_heading': 'Monitoreo de Alarmas en Tiempo Real',
        'cantidad_csv': cantidad_csv,
        'cantidad_otu': cantidad_otu,
        'centro_lat': centro_lat,
        'centro_lon': centro_lon,
        'frontend_data': {
            "rutas": rutas_data,
            "equipos_data": equipos_data,
            "centro_lat": centro_lat,
            "centro_lon": centro_lon,
        }
    }
    return render(request, 'mapa_vivo.html', context)
