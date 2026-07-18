"""
Vistas del ranking de rutas con más eventos.
"""
import csv
import logging
from datetime import datetime

from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from django.contrib.auth.decorators import login_required, permission_required
from django.db import connection

from ..models import Ruta
from .utils import get_geo_bounds
from .dashboard import contar_rutas_db

logger = logging.getLogger('mapas')


@login_required
@permission_required('mapas.can_view_reports', raise_exception=True)
def ranking(request):
    anio_seleccionado = request.GET.get('anio')
    otu_seleccionada = request.GET.get('otu')
    ruta_seleccionada = request.GET.get('ruta')
    show_all_regulares = request.GET.get('show_all_regulares') == 'true'
    show_all_opticos = request.GET.get('show_all_opticos') == 'true'

    cantidad_csv = contar_rutas_db()

    lat_min, lat_max, lon_min, lon_max = get_geo_bounds()

    with connection.cursor() as cursor:
        # Años
        anios_query = "SELECT DISTINCT EXTRACT(YEAR FROM fecha_registro) FROM mon_alarmas_veex WHERE fecha_registro IS NOT NULL ORDER BY 1 DESC"
        cursor.execute(anios_query)
        anios_disponibles = []
        for row in cursor.fetchall():
            try:
                if row[0]: anios_disponibles.append(int(row[0]))
            except (ValueError, TypeError):
                continue

        # OTUs
        otus_query = """
            SELECT DISTINCT o.nombre
            FROM mon_alarmas_veex a
            JOIN inv_rutas_fibra r ON a.route_name = r.nombre
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
            SELECT DISTINCT route_name
            FROM mon_alarmas_veex
            WHERE route_name IS NOT NULL
            AND alarm_type IN ('High Loss', 'Reflective', 'Fiber Break', 'Event Reflectance', 'Event Loss')
            AND latitude BETWEEN %s AND %s
            AND longitude BETWEEN %s AND %s
        """
        rutas_params = [lat_min, lat_max, lon_min, lon_max]

        if otu_seleccionada:
            rutas_query += " AND route_name IN (SELECT r.nombre FROM inv_rutas_fibra r JOIN inv_equipos_otu o ON r.otu_id = o.id WHERE o.nombre = %s)"
            rutas_params.append(otu_seleccionada)

        rutas_query += " ORDER BY route_name"

        cursor.execute(rutas_query, rutas_params)
        rutas_disponibles = [row[0] for row in cursor.fetchall()]

        # WHERE reutilizable
        where_clauses = " WHERE latitude BETWEEN %s AND %s AND longitude BETWEEN %s AND %s"
        filter_params = [lat_min, lat_max, lon_min, lon_max]

        if anio_seleccionado:
            where_clauses += " AND EXTRACT(YEAR FROM fecha_registro) = %s"
            filter_params.append(anio_seleccionado)
        if otu_seleccionada:
            where_clauses += " AND route_name IN (SELECT r.nombre FROM inv_rutas_fibra r JOIN inv_equipos_otu o ON r.otu_id = o.id WHERE o.nombre = %s)"
            filter_params.append(otu_seleccionada)
        if ruta_seleccionada:
            where_clauses += " AND route_name = %s"
            filter_params.append(ruta_seleccionada)

        limit_clause_regulares = ""
        if not show_all_regulares:
            limit_clause_regulares = "LIMIT 6"

        top_rutas_query = f"""
            SELECT route_name, COALESCE((SELECT o.nombre FROM inv_rutas_fibra r JOIN inv_equipos_otu o ON r.otu_id = o.id WHERE r.nombre = route_name LIMIT 1), MAX(device_serial)) as device_serial,
                SUM(CASE WHEN alarm_type IN ('High Loss', 'Event Loss') THEN 1 ELSE 0 END) AS attenuation_count,
                SUM(CASE WHEN alarm_type = 'INJECTION' THEN 1 ELSE 0 END) AS injection_count,
                SUM(CASE WHEN alarm_type IN ('Reflective', 'Event Reflectance', 'REFLEXION') THEN 1 ELSE 0 END) AS reflexion_count,
                SUM(CASE WHEN alarm_type = 'Fiber Break' AND (status != 'Resolved' OR EXTRACT(EPOCH FROM (CAST(fecha_resolucion AS TIMESTAMP) - CAST(timestamp AS TIMESTAMP)))/60 > 60) THEN 1 ELSE 0 END) AS fiber_cut_count,
                SUM(CASE WHEN alarm_type = 'Fiber Break' AND status = 'Resolved' AND EXTRACT(EPOCH FROM (CAST(fecha_resolucion AS TIMESTAMP) - CAST(timestamp AS TIMESTAMP)))/60 <= 60 THEN 1 ELSE 0 END) AS disconnections_count,
                COUNT(*) as total_count
            FROM mon_alarmas_veex {where_clauses} GROUP BY route_name ORDER BY total_count DESC {limit_clause_regulares}
        """
        cursor.execute(top_rutas_query, filter_params)
        top_rutas_raw = cursor.fetchall()

        top_rutas_data = []
        for i, row in enumerate(top_rutas_raw):
            top_rutas_data.append({
                'top': i + 1, 'ruta': row[0], 'otu': row[1], 'attenuation': row[2],
                'injection': row[3], 'reflexion': row[4], 'fiber_cut': row[5], 'disconnections': row[6], 'total': row[7]
            })

        # Eventos ópticos
        tipos_eventos_opticos = ['First Connector', 'Switch', 'Reflection', 'Splice', 'Fiber End']
        sum_cases_eventos = ",\n".join([
            f"SUM(CASE WHEN event_type = '{tipo}' THEN 1 ELSE 0 END) AS {tipo.replace(' ', '_').lower()}_count"
            for tipo in tipos_eventos_opticos
        ])

        limit_clause_opticos = ""
        if not show_all_opticos:
            limit_clause_opticos = "LIMIT 6"

        opticos_where = " WHERE latitude BETWEEN %s AND %s AND longitude BETWEEN %s AND %s"
        opticos_params = [lat_min, lat_max, lon_min, lon_max]

        if ruta_seleccionada:
            opticos_where += " AND Node = %s"
            opticos_params.append(ruta_seleccionada)

        if otu_seleccionada:
            opticos_where += " AND Node IN (SELECT DISTINCT route_name FROM mon_alarmas_veex WHERE device_serial = %s)"
            opticos_params.append(otu_seleccionada)

        top_opticos_query = f"""
            SELECT
                Node AS Ruta,
                (SELECT device_serial FROM mon_alarmas_veex WHERE route_name = Node LIMIT 1) AS OTU,
                SUM(CASE WHEN event_test_status = 'fail' THEN 1 ELSE 0 END) AS fail_count,
                {sum_cases_eventos},
                COUNT(*) as total_count
            FROM mon_otdr_eventos_geo
            {opticos_where}
            GROUP BY Node
            ORDER BY fail_count DESC
            {limit_clause_opticos}
        """

        try:
            cursor.execute(top_opticos_query, opticos_params)
            top_opticos_raw = cursor.fetchall()
        except Exception as e:
            logger.warning(f"Error en ranking opticos: {e}. Intentando consulta sin columnas nuevas.")
            # Fallback: Quitar event_test_status de la consulta si falla
            query_segura = f"""
                SELECT
                    Node AS Ruta,
                    (SELECT device_serial FROM mon_alarmas_veex WHERE route_name = Node LIMIT 1) AS OTU,
                    0 AS fail_count,
                    {sum_cases_eventos},
                    COUNT(*) as total_count
                FROM mon_otdr_eventos_geo
                {opticos_where}
                GROUP BY Node
                ORDER BY total_count DESC
                {limit_clause_opticos}
            """
            try:
                cursor.execute(query_segura, opticos_params)
                top_opticos_raw = cursor.fetchall()
            except Exception as e2:
                logger.error(f"Fallo critico en ranking opticos: {e2}")
                top_opticos_raw = []

        top_opticos_data = []
        for i, row in enumerate(top_opticos_raw):
            top_opticos_data.append({
                'top': i + 1,
                'ruta': row[0],
                'otu': row[1] if row[1] else 'N/A',
                'fail_count': row[2],
                'first_connector_count': row[3],
                'switch_count': row[4],
                'reflection_count': row[5],
                'splice_count': row[6],
                'fiber_end_count': row[7],
                'total': row[8]
            })

        frontend_data = {
            'top_rutas_data': top_rutas_data,
            'top_opticos_data': top_opticos_data,
            'rutas_disponibles': rutas_disponibles,
            'show_all_regulares': show_all_regulares,
            'show_all_opticos': show_all_opticos
        }

        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse(frontend_data)

        context = {
            'cantidad_csv': cantidad_csv,
            'anios_disponibles': anios_disponibles,
            'otu_seleccionada': otu_seleccionada,
            'otus_disponibles': otus_disponibles,
            'ruta_seleccionada': ruta_seleccionada,
            'rutas_disponibles': rutas_disponibles,
            'top_rutas_data': top_rutas_data,
            'top_opticos_data': top_opticos_data,
            'frontend_data': frontend_data,
            'show_all_regulares': show_all_regulares,
            'show_all_opticos': show_all_opticos,
            'cantidad_eventos': sum(item['total'] for item in top_rutas_data) if top_rutas_data else 0
        }

        return render(request, 'ranking.html', context)


def export_top_rutas_csv(request):
    """Genera un CSV con los datos de las rutas, respetando filtros."""
    anio_seleccionado = request.GET.get('anio')
    otu_seleccionada = request.GET.get('otu')
    ruta_seleccionada = request.GET.get('ruta')
    show_all = request.GET.get('show_all_regulares') == 'true'

    where_clauses = " WHERE latitude BETWEEN -60.00 AND -17.00 AND longitude BETWEEN -76.00 AND -66.00"
    filter_params = []

    if anio_seleccionado:
        where_clauses += " AND EXTRACT(YEAR FROM fecha_registro) = %s"
        filter_params.append(anio_seleccionado)
    if otu_seleccionada:
        where_clauses += " AND device_serial = %s"
        filter_params.append(otu_seleccionada)
    if ruta_seleccionada:
        where_clauses += " AND route_name = %s"
        filter_params.append(ruta_seleccionada)

    limit_clause = ""
    if not show_all:
        limit_clause = "LIMIT 6"

    top_rutas_query = f"""
        SELECT route_name, COALESCE((SELECT o.nombre FROM inv_rutas_fibra r JOIN inv_equipos_otu o ON r.otu_id = o.id WHERE r.nombre = route_name LIMIT 1), MAX(device_serial)) as device_serial,
            SUM(CASE WHEN alarm_type IN ('High Loss', 'Event Loss') THEN 1 ELSE 0 END) AS attenuation_count,
            SUM(CASE WHEN alarm_type = 'INJECTION' THEN 1 ELSE 0 END) AS injection_count,
            SUM(CASE WHEN alarm_type IN ('Reflective', 'Event Reflectance', 'REFLEXION') THEN 1 ELSE 0 END) AS reflexion_count,
            SUM(CASE WHEN alarm_type = 'Fiber Break' AND (status != 'Resolved' OR EXTRACT(EPOCH FROM (CAST(fecha_resolucion AS TIMESTAMP) - CAST(timestamp AS TIMESTAMP)))/60 > 60) THEN 1 ELSE 0 END) AS fiber_cut_count,
            SUM(CASE WHEN alarm_type = 'Fiber Break' AND status = 'Resolved' AND EXTRACT(EPOCH FROM (CAST(fecha_resolucion AS TIMESTAMP) - CAST(timestamp AS TIMESTAMP)))/60 <= 60 THEN 1 ELSE 0 END) AS disconnections_count,
            COUNT(*) as total_count
        FROM mon_alarmas_veex {where_clauses} GROUP BY route_name ORDER BY total_count DESC {limit_clause}
    """

    with connection.cursor() as cursor:
        cursor.execute(top_rutas_query, filter_params)
        top_rutas_raw = cursor.fetchall()

    response = HttpResponse(content_type='text/csv')
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"reporte_rutas_eventos{timestamp}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(['Ruta', 'OTU', 'Attenuation', 'Injection', 'Reflexion', 'Fiber Cut', 'Disconnections', 'Total'])

    for row in top_rutas_raw:
        writer.writerow([row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7]])

    return response


def export_top_opticos_csv(request):
    """Genera un CSV con los datos de las rutas ópticas, respetando filtros."""
    show_all = request.GET.get('show_all_opticos') == 'true'
    otu_seleccionada = request.GET.get('otu')
    ruta_seleccionada = request.GET.get('ruta')

    lat_min, lat_max, lon_min, lon_max = get_geo_bounds()

    opticos_where = " WHERE latitude BETWEEN %s AND %s AND longitude BETWEEN %s AND %s"
    opticos_params = [lat_min, lat_max, lon_min, lon_max]

    if ruta_seleccionada:
        opticos_where += " AND Node = %s"
        opticos_params.append(ruta_seleccionada)

    if otu_seleccionada:
        opticos_where += " AND Node IN (SELECT DISTINCT route_name FROM mon_alarmas_veex WHERE device_serial = %s)"
        opticos_params.append(otu_seleccionada)

    tipos_eventos_opticos = ['First Connector', 'Switch', 'Reflection', 'Splice', 'Fiber End']
    sum_cases_eventos = ",\n".join([
        f"SUM(CASE WHEN event_type = '{tipo}' THEN 1 ELSE 0 END) AS {tipo.replace(' ', '_').lower()}_count"
        for tipo in tipos_eventos_opticos
    ])

    limit_clause = ""
    if not show_all:
        limit_clause = "LIMIT 6"

    top_opticos_query = f"""
        SELECT
            Node AS Ruta,
            (SELECT device_serial FROM mon_alarmas_veex WHERE route_name = Node LIMIT 1) AS OTU,
            SUM(CASE WHEN event_test_status = 'fail' THEN 1 ELSE 0 END) AS fail_count,
            {sum_cases_eventos},
            COUNT(*) as total_count
        FROM mon_otdr_eventos_geo
        {opticos_where}
        GROUP BY Node
        ORDER BY fail_count DESC
        {limit_clause}
    """

    with connection.cursor() as cursor:
        cursor.execute(top_opticos_query, opticos_params)
        top_opticos_raw = cursor.fetchall()

    response = HttpResponse(content_type='text/csv')
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"reporte_rutas_opticos_{timestamp}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(['Ruta', 'OTU', 'Fallos', 'First_Connector', 'Switch', 'Reflection', 'Splice', 'Fiber_End', 'Total_Optico'])

    for row in top_opticos_raw:
        writer.writerow([row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8]])

    return response
