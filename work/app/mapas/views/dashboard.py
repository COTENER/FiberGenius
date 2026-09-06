"""
Vistas del dashboard analítico.
"""
import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required, permission_required
from django.db import connection

from ..models import Ruta
from .utils import get_geo_bounds

logger = logging.getLogger('mapas')


def contar_rutas_db():
    return Ruta.objects.count()


@login_required
@permission_required('mapas.can_view_reports', raise_exception=True)
def dashboard(request):
    anio_seleccionado = request.GET.get('anio')
    otu_seleccionada = request.GET.get('otu')
    ruta_seleccionada = request.GET.get('ruta')
    severidad_seleccionada = request.GET.get('severidad')
    tipo_evento_seleccionado = request.GET.get('tipo_evento')

    cantidad_csv = contar_rutas_db()

    lat_min, lat_max, lon_min, lon_max = get_geo_bounds()

    with connection.cursor() as cursor:
        # Años
        anios_query = "SELECT DISTINCT EXTRACT(YEAR FROM fecha_registro) FROM mon_alarmas_veex WHERE fecha_registro IS NOT NULL ORDER BY 1 DESC"
        cursor.execute(anios_query)
        anios_disponibles = [int(row[0]) for row in cursor.fetchall()]

        # OTUs
        otus_query = """
            SELECT DISTINCT o.nombre
            FROM mon_alarmas_veex a
            JOIN inv_rutas_fibra r
              ON r.id = a.ruta_obj_id
              OR (a.ruta_obj_id IS NULL AND a.route_name = r.nombre)
            JOIN inv_equipos_otu o ON r.otu_id = o.id
            WHERE a.device_serial IS NOT NULL
            AND a.alarm_type IN ('High Loss', 'Reflective', 'Fiber Break', 'Event Reflectance', 'Event Loss')
            AND a.latitude BETWEEN %s AND %s
            AND a.longitude BETWEEN %s AND %s
            ORDER BY o.nombre
        """
        cursor.execute(otus_query, [lat_min, lat_max, lon_min, lon_max])
        otus_disponibles = [row[0] for row in cursor.fetchall()]

        # Rutas
        rutas_query = """
            SELECT DISTINCT COALESCE(r.nombre, a.route_name) AS ruta_nombre
            FROM mon_alarmas_veex a
            LEFT JOIN inv_rutas_fibra r ON r.id = a.ruta_obj_id
            WHERE COALESCE(r.nombre, a.route_name) IS NOT NULL
            AND a.alarm_type IN ('High Loss', 'Reflective', 'Fiber Break', 'Event Reflectance', 'Event Loss')
            AND a.latitude BETWEEN %s AND %s
            AND a.longitude BETWEEN %s AND %s
        """
        rutas_params = [lat_min, lat_max, lon_min, lon_max]

        if otu_seleccionada:
            rutas_query += " AND COALESCE(r.nombre, a.route_name) IN (SELECT rr.nombre FROM inv_rutas_fibra rr JOIN inv_equipos_otu o ON rr.otu_id = o.id WHERE o.nombre = %s)"
            rutas_params.append(otu_seleccionada)

        rutas_query += " ORDER BY ruta_nombre"

        cursor.execute(rutas_query, rutas_params)
        rutas_disponibles = [row[0] for row in cursor.fetchall()]

        # WHERE reutilizable
        where_clauses = " WHERE a.latitude BETWEEN %s AND %s AND a.longitude BETWEEN %s AND %s"
        filter_params = [lat_min, lat_max, lon_min, lon_max]

        if anio_seleccionado:
            where_clauses += " AND EXTRACT(YEAR FROM a.fecha_registro) = %s"
            filter_params.append(anio_seleccionado)
        if otu_seleccionada:
            where_clauses += " AND COALESCE(r.nombre, a.route_name) IN (SELECT rr.nombre FROM inv_rutas_fibra rr JOIN inv_equipos_otu o ON rr.otu_id = o.id WHERE o.nombre = %s)"
            filter_params.append(otu_seleccionada)
        if ruta_seleccionada:
            where_clauses += " AND COALESCE(r.nombre, a.route_name) = %s"
            filter_params.append(ruta_seleccionada)
        if severidad_seleccionada:
            where_clauses += " AND a.alarm_level = %s"
            filter_params.append(severidad_seleccionada)
        if tipo_evento_seleccionado:
            if tipo_evento_seleccionado == 'DISCONNECTIONS':
                where_clauses += " AND alarm_type = 'Fiber Break' AND status = 'Resolved' AND EXTRACT(EPOCH FROM (CAST(fecha_resolucion AS TIMESTAMP) - CAST(timestamp AS TIMESTAMP)))/60 <= 60"
            elif tipo_evento_seleccionado == 'FIBER_CUT':
                where_clauses += " AND alarm_type = 'Fiber Break' AND (status != 'Resolved' OR EXTRACT(EPOCH FROM (CAST(fecha_resolucion AS TIMESTAMP) - CAST(timestamp AS TIMESTAMP)))/60 > 60)"
            else:
                where_clauses += " AND alarm_type = %s"
                filter_params.append(tipo_evento_seleccionado)

        # Consulta y lógica para gráficos
        query = f"""
            SELECT alarm_level, 
                   CASE 
                       WHEN alarm_type = 'Fiber Break' AND status = 'Resolved' AND EXTRACT(EPOCH FROM (CAST(fecha_resolucion AS TIMESTAMP) - CAST(timestamp AS TIMESTAMP)))/60 <= 60 THEN 'DISCONNECTIONS' 
                       WHEN alarm_type = 'Fiber Break' THEN 'FIBER_CUT'
                       WHEN alarm_type IN ('Event Reflectance', 'Reflective') THEN 'REFLEXION'
                       WHEN alarm_type IN ('Event Loss', 'High Loss') THEN 'ATTENUATION'
                       ELSE alarm_type 
                   END as TipoEvento,
                   status, EXTRACT(YEAR FROM fecha_registro) as Anio, EXTRACT(QUARTER FROM fecha_registro) as Trimestre,
                   EXTRACT(MONTH FROM a.fecha_registro) as Mes,
                   COALESCE(r.nombre, a.route_name) AS ruta_nombre, COUNT(*) as Cantidad
            FROM mon_alarmas_veex a
            LEFT JOIN inv_rutas_fibra r ON r.id = a.ruta_obj_id
            {where_clauses}
            GROUP BY a.alarm_level, TipoEvento, a.status, Anio, Trimestre, Mes,
                     COALESCE(r.nombre, a.route_name)
        """
        cursor.execute(query, filter_params)
        all_data = cursor.fetchall()

        # Procesamiento de datos para gráficos
        cantidad_eventos = 0
        severidad_data, eventos_data = {}, {}
        estado_data = {'Resolved': 0, 'Active': 0}
        time_chart_data, time_event_data = {}, {}
        eventos_por_tipo_y_anio, eventos_por_mes_data = {}, {}
        
        rutas_conteo = {}
        yoy_data = {}
        heatmap_data = {}

        for row in all_data:
            severidad, tipo_evento, estado, anio, trimestre, mes, nombre, cantidad = row
            severidad = severidad.upper() if severidad else 'MINOR'
            if tipo_evento not in ['ATTENUATION', 'FIBER_CUT', 'INJECTION', 'DISCONNECTIONS', 'REFLEXION']:
                continue
            
            anio = int(anio) if anio else 0
            mes = int(mes) if mes else 0
            trimestre = int(trimestre) if trimestre else 0
            
            cantidad_eventos += cantidad
            severidad_data[severidad] = severidad_data.get(severidad, 0) + cantidad
            eventos_data[tipo_evento] = eventos_data.get(tipo_evento, 0) + cantidad
            if estado in estado_data:
                estado_data[estado] += cantidad
            if anio not in time_chart_data:
                time_chart_data[anio] = {}
            if trimestre not in time_chart_data[anio]:
                time_chart_data[anio][trimestre] = {}
            time_chart_data[anio][trimestre][severidad] = time_chart_data[anio][trimestre].get(severidad, 0) + cantidad
            if anio not in time_event_data:
                time_event_data[anio] = {}
            if trimestre not in time_event_data[anio]:
                time_event_data[anio][trimestre] = {}
            time_event_data[anio][trimestre][tipo_evento] = time_event_data[anio][trimestre].get(tipo_evento, 0) + cantidad
            if anio not in eventos_por_tipo_y_anio:
                eventos_por_tipo_y_anio[anio] = {}
            eventos_por_tipo_y_anio[anio][tipo_evento] = eventos_por_tipo_y_anio[anio].get(tipo_evento, 0) + cantidad
            if anio not in eventos_por_mes_data:
                eventos_por_mes_data[anio] = {}
            eventos_por_mes_data[anio][mes] = eventos_por_mes_data[anio].get(mes, 0) + cantidad
            
            if nombre:
                rutas_conteo[nombre] = rutas_conteo.get(nombre, 0) + cantidad
            
            if anio not in yoy_data:
                yoy_data[anio] = {m: 0 for m in range(1, 13)}
            yoy_data[anio][mes] += cantidad
            
            if mes not in heatmap_data:
                heatmap_data[mes] = {'CRITICAL': 0, 'MAJOR': 0, 'MINOR': 0}
            heatmap_data[mes][severidad] = heatmap_data[mes].get(severidad, 0) + cantidad

        # Top 5 Rutas
        top_5_rutas = sorted(rutas_conteo.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # YoY Data (Compare 2 most recent years)
        sorted_anios = sorted(list(yoy_data.keys()), reverse=True)
        yoy_series = []
        if len(sorted_anios) > 0:
            current_yr = sorted_anios[0]
            yoy_series.append({"name": str(current_yr), "data": [yoy_data[current_yr][m] for m in range(1, 13)]})
            if len(sorted_anios) > 1:
                prev_yr = sorted_anios[1]
                yoy_series.append({"name": str(prev_yr), "data": [yoy_data[prev_yr][m] for m in range(1, 13)]})

        # Heatmap Series (Month vs Severidad)
        meses_nombres = {1: 'Ene', 2: 'Feb', 3: 'Mar', 4: 'Abr', 5: 'May', 6: 'Jun', 7: 'Jul', 8: 'Ago', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dic'}
        heatmap_series = []
        for sev in ['CRITICAL', 'MAJOR', 'MINOR']:
            data_arr = []
            for m in range(1, 13):
                data_arr.append({
                    "x": meses_nombres[m],
                    "y": heatmap_data.get(m, {}).get(sev, 0)
                })
            heatmap_series.append({"name": sev, "data": data_arr})
            
        # Criticidad Gauge (% of Active)
        total_estado = estado_data['Resolved'] + estado_data['Active']
        criticidad_pct = (estado_data['Active'] / total_estado * 100) if total_estado > 0 else 0

        frontend_data = {
            'severidad_labels': list(severidad_data.keys()), 'severidad_values': list(severidad_data.values()),
            'eventos_labels': list(eventos_data.keys()), 'eventos_values': list(eventos_data.values()),
            'estado_labels': list(estado_data.keys()), 'estado_values': list(estado_data.values()),
            'time_chart_data': time_chart_data, 'time_event_data': time_event_data,
            'eventos_por_tipo_y_anio': eventos_por_tipo_y_anio, 'cantidad_eventos': cantidad_eventos,
            'eventos_por_mes_data': eventos_por_mes_data, 'rutas_disponibles': rutas_disponibles,
            'top_5_rutas_labels': [x[0] for x in top_5_rutas], 'top_5_rutas_values': [x[1] for x in top_5_rutas],
            'yoy_series': yoy_series, 'heatmap_series': heatmap_series, 'criticidad_pct': round(criticidad_pct, 1)
        }

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse(frontend_data)

        context = {
            'cantidad_csv': cantidad_csv, 'anios_disponibles': anios_disponibles,
            'otu_seleccionada': otu_seleccionada, 'otus_disponibles': otus_disponibles,
            'ruta_seleccionada': ruta_seleccionada, 'rutas_disponibles': rutas_disponibles,
            'cantidad_eventos': cantidad_eventos, 'frontend_data': frontend_data,
        }

        return render(request, 'dashboard.html', context)
