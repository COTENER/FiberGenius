import logging

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_GET, require_POST
from django.http import Http404, JsonResponse
import json
from ..models import Ruta, TrazaReferencia, DiagnosticoProactivo, PerfilUmbral, CoordenadaRuta
from ..veex_api import create_on_demand_task # Reutilizaremos esta API
from ..management.commands.georeferenciar_eventos import interpolate_on_route, haversine

logger = logging.getLogger('mapas')

@login_required
@permission_required('mapas.view_diagnosticoproactivo', raise_exception=True)
def visor_proactivo(request):
    """
    Renderiza la interfaz para ver el análisis proactivo de una ruta.
    """
    route_name = request.GET.get('route_name')
    ruta = None
    margen_corte = 2.0
    referencia_actual = None
    if route_name:
        ruta = Ruta.objects.filter(nombre=route_name).first()
        if ruta:
            referencia_actual = ruta.trazas_referencia.filter(activa=True).order_by('-fecha_creacion').first()
            if ruta.perfil_umbral:
                margen_corte = ruta.perfil_umbral.margen_corte_fibra_db
            else:
                default_perfil = PerfilUmbral.objects.first()
                if default_perfil:
                    margen_corte = default_perfil.margen_corte_fibra_db

    context = {
        'ruta': ruta,
        'referencia_actual': referencia_actual,
        'margen_corte': margen_corte,
        'perfil_activo': ruta.perfil_umbral if ruta and ruta.perfil_umbral else (PerfilUmbral.objects.first() if PerfilUmbral.objects.exists() else None)
    }
    return render(request, 'proactivo/visor_proactivo.html', context)

@login_required
@permission_required('mapas.add_trazareferencia', raise_exception=True)
def visor_establecer_referencia(request):
    """
    Renderiza la interfaz para disparar una traza y guardarla como referencia.
    """
    route_name = request.GET.get('route_name')
    ruta = None
    referencia_actual = None
    margen_corte = 2.0 # Default fallback
    if route_name:
        ruta = Ruta.objects.filter(nombre=route_name).first()
        if ruta:
            referencia_actual = ruta.trazas_referencia.filter(activa=True).order_by('-fecha_creacion').first()
            if ruta.perfil_umbral:
                margen_corte = ruta.perfil_umbral.margen_corte_fibra_db
            else:
                default_perfil = PerfilUmbral.objects.first()
                if default_perfil:
                    margen_corte = default_perfil.margen_corte_fibra_db

    context = {
        'ruta': ruta,
        'referencia_actual': referencia_actual,
        'margen_corte': margen_corte,
        'perfiles': PerfilUmbral.objects.all(),
        'perfil_activo': ruta.perfil_umbral if ruta and ruta.perfil_umbral else (PerfilUmbral.objects.first() if PerfilUmbral.objects.exists() else None)
    }
    return render(request, 'proactivo/visor_establecer_referencia.html', context)


@login_required
@permission_required('mapas.change_ruta', raise_exception=True)
@require_POST
def api_asignar_perfil_ruta(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            route_name = data.get('route_name')
            perfil_id = data.get('perfil_id')

            ruta = Ruta.objects.filter(nombre=route_name).first()
            if not ruta:
                return JsonResponse({'status': 'error', 'message': 'Ruta no encontrada'})

            perfil = PerfilUmbral.objects.filter(id=perfil_id).first()
            if not perfil:
                return JsonResponse({'status': 'error', 'message': 'Perfil no encontrado'})

            ruta.perfil_umbral = perfil
            ruta.save()

            return JsonResponse({'status': 'success', 'message': 'Perfil asignado correctamente a la ruta'})
        except (json.JSONDecodeError, TypeError, ValueError):
            return JsonResponse(
                {'status': 'error', 'message': 'Los datos de asignación no son válidos.'},
                status=400,
            )
        except Exception:
            logger.exception("Error inesperado al asignar un perfil a una ruta")
            return JsonResponse(
                {'status': 'error', 'message': 'No se pudo asignar el perfil.'},
                status=500,
            )
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'})

@login_required
@permission_required('mapas.add_trazareferencia', raise_exception=True)
def api_establecer_referencia(request):
    """
    Inicia una prueba On-Demand, pero la marcará para ser guardada como TrazaReferencia
    una vez finalizada. (Obsoleto, se manejará vía frontend con on-demand + promover)
    """
    return JsonResponse({'status': 'error', 'message': 'Usar flujo OnDemand + Promover'}, status=400)


@login_required
@permission_required('mapas.add_trazareferencia', raise_exception=True)
@require_POST
def promover_referencia(request):
    """
    Recibe un ID de TrazaOnDemand completada y la promueve a TrazaReferencia oficial.
    """
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            traza_id = data.get('traza_id')
            nombre_referencia = str(data.get('nombre_referencia', '')).strip()
            if not nombre_referencia:
                return JsonResponse({'status': 'error', 'message': 'El nombre es obligatorio.'}, status=400)

            from ..models import TrazaOnDemand
            from django.core.files.base import ContentFile

            traza_on_demand = get_object_or_404(TrazaOnDemand, id=traza_id)
            if traza_on_demand.status != 'Completed' or not traza_on_demand.archivo_sor:
                return JsonResponse({'status': 'error', 'message': 'La traza base no ha finalizado correctamente.'}, status=400)

            ruta = get_object_or_404(Ruta, nombre=traza_on_demand.route_name)

            with transaction.atomic():
                Ruta.objects.select_for_update().get(pk=ruta.pk)
                TrazaReferencia.objects.filter(ruta=ruta).update(activa=False)
                nueva_ref = TrazaReferencia(
                    ruta=ruta,
                    nombre=nombre_referencia,
                    activa=True
                )
                file_content = traza_on_demand.archivo_sor.read()
                nombre_archivo = get_valid_filename(nombre_referencia) or 'referencia'
                nueva_ref.archivo_sor.save(
                    f"referencia_{ruta.id}_{nombre_archivo}.sor",
                    ContentFile(file_content),
                    save=False,
                )
                nueva_ref.save()

            return JsonResponse({
                'status': 'success',
                'message': 'Traza guardada exitosamente como Referencia Oficial.'
            })

        except Http404:
            raise
        except (json.JSONDecodeError, TypeError, ValueError):
            return JsonResponse(
                {'status': 'error', 'message': 'Los datos de la referencia no son válidos.'},
                status=400,
            )
        except Exception:
            logger.exception("Error inesperado al promover una traza de referencia")
            return JsonResponse(
                {'status': 'error', 'message': 'No se pudo guardar la referencia.'},
                status=500,
            )

    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)


@login_required
@permission_required('mapas.view_trazareferencia', raise_exception=True)
def get_referencia_data(request, ref_id):
    """
    Descarga el archivo SOR de la referencia y lo parsea en memoria usando pyotdr.
    Retorna la lista de coordenadas (X, Y) y eventos detectados en formato JSON para graficar.
    """
    import tempfile
    import pyotdr.read
    import os

    referencia = get_object_or_404(TrazaReferencia, id=ref_id)

    if not referencia.archivo_sor:
        return JsonResponse({'status': 'error', 'message': 'La referencia no tiene un archivo SOR asociado.'}, status=404)

    try:
        sor_content = referencia.archivo_sor.read()

        with tempfile.NamedTemporaryFile(delete=False, suffix='.sor') as tmp:
            tmp.write(sor_content)
            tmp_path = tmp.name

        status, results, tracedata = pyotdr.read.sorparse(tmp_path)
        os.remove(tmp_path)

        if status != "ok":
            return JsonResponse({'status': 'error', 'message': f'Error parseando archivo SOR: {status}'}, status=500)

        x_data = []
        y_data = []
        for line in tracedata:
            parts = line.strip().split('\t')
            if len(parts) == 2:
                x_data.append(round(float(parts[0]) * 1000, 2))
                y_data.append(round(float(parts[1]), 3))

        events_list = []
        if 'KeyEvents' in results:
            key_events = results['KeyEvents']
            for key in key_events.keys():
                if key.startswith('event '):
                    ev = key_events[key]
                    events_list.append({
                        'number': ev.get('event number'),
                        'distance': round(float(ev.get('distance', 0)) * 1000, 2),
                        'type': ev.get('type'),
                        'loss': ev.get('splice loss'),
                        'reflectance': ev.get('refl loss'),
                        'slope': ev.get('slope')
                    })

        return JsonResponse({
            'status': 'success',
            'data': {
                'x': x_data,
                'y': y_data,
                'events': events_list
            }
        })

    except Exception:
        logger.exception('Error obteniendo la traza de referencia')
        return JsonResponse(
            {'status': 'error', 'message': 'No se pudo procesar la traza de referencia.'},
            status=500,
        )

@login_required
@permission_required('mapas.view_coordenadaruta', raise_exception=True)
@require_GET
def api_calcular_coordenadas(request, ruta_id):
    """
    Calcula (interpola) las coordenadas geográficas (lat, lon) para una lista de distancias ópticas (OTDR) dadas.
    Espera en GET parámetros list 'd', ejemplo: ?d=20850.91&d=10500.5
    """
    if request.method != 'GET':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    try:
        ruta = Ruta.objects.get(id=ruta_id)
        distancias_str = request.GET.getlist('d')
        if not distancias_str:
            return JsonResponse({'coordenadas': []})

        distancias = []
        for d in distancias_str:
            try:
                distancias.append(float(d))
            except ValueError:
                continue

        # Obtener los puntos de la ruta desde la DB
        coords_db = CoordenadaRuta.objects.filter(ruta=ruta).order_by('orden')
        if not coords_db.exists():
            return JsonResponse({'error': 'La ruta no tiene coordenadas geográficas registradas'}, status=404)

        # Extraer puntos (lat, lon) como espera la función
        points = []
        for c in coords_db:
            points.append((float(c.latitud), float(c.longitud)))

        # Calcular distancias geográficas acumuladas
        geo_cum = [0.0]
        seg_len = []
        for i in range(1, len(points)):
            d_geo = haversine(points[i-1][1], points[i-1][0], points[i][1], points[i][0])
            seg_len.append(d_geo)
            geo_cum.append(geo_cum[-1] + d_geo)

        route_geo_len = geo_cum[-1]

        # Obtener la longitud óptica total de la fibra para escalar
        route_optical_len = ruta.distancia_m if ruta.distancia_m else route_geo_len

        ref_activa = ruta.trazas_referencia.filter(activa=True).order_by('-fecha_creacion').first()
        if ref_activa and ref_activa.distancia_km:
            route_optical_len = ref_activa.distancia_km * 1000.0

        scale = route_geo_len / route_optical_len if route_optical_len > 0 else 1.0

        resultados = []
        for d_opt in distancias:
            s_target = d_opt * scale
            lat, lon = interpolate_on_route(points, geo_cum, seg_len, s_target, extrapolate=False)
            resultados.append({
                'distancia_m': d_opt,
                'lat': round(lat, 6),
                'lng': round(lon, 6)
            })

        return JsonResponse({'coordenadas': resultados})

    except Ruta.DoesNotExist:
        return JsonResponse({'error': 'Ruta no encontrada'}, status=404)
    except Exception:
        logger.exception("Error calculando coordenadas para la ruta %s", ruta_id)
        return JsonResponse(
            {'error': 'No se pudieron calcular las coordenadas de la ruta.'},
            status=500,
        )
@login_required
@permission_required('mapas.view_trazaondemand', raise_exception=True)
def api_get_ondemand_data(request, traza_id):
    import tempfile
    import pyotdr.read
    import os
    from ..models import TrazaOnDemand

    traza = get_object_or_404(TrazaOnDemand, id=traza_id)

    if not traza.archivo_sor:
        return JsonResponse({'status': 'error', 'message': 'La traza no tiene un archivo SOR asociado.'}, status=404)

    try:
        sor_content = traza.archivo_sor.read()

        with tempfile.NamedTemporaryFile(delete=False, suffix='.sor') as tmp:
            tmp.write(sor_content)
            tmp_path = tmp.name

        try:
            status, results, tracedata = pyotdr.read.sorparse(tmp_path)

            if status != "ok":
                return JsonResponse({'status': 'error', 'message': f'Error parseando archivo SOR: {status}'}, status=500)

            x_data = []
            y_data = []
            for line in tracedata:
                parts = line.strip().split('\t')
                if len(parts) == 2:
                    x_data.append(round(float(parts[0]) * 1000, 2))
                    y_data.append(round(float(parts[1]), 3))

            events_list = []
            if 'KeyEvents' in results:
                key_events = results['KeyEvents']
                for key in key_events.keys():
                    if key.startswith('event '):
                        ev = key_events[key]
                        events_list.append({
                            'number': ev.get('event number'),
                            'distance': round(float(ev.get('distance', 0)) * 1000, 2),
                            'type': ev.get('type'),
                            'loss': ev.get('splice loss'),
                            'reflectance': ev.get('refl loss'),
                            'slope': ev.get('slope')
                        })

            # Extraer Link Loss
            link_loss = 0.0
            if 'KeyEvents' in results:
                summary = results['KeyEvents'].get('Summary', {})
                link_loss += float(summary.get('total loss', 0.0))

                num_events = int(results['KeyEvents'].get('num events', 0))
                for i in range(1, num_events): # Excluye el último evento (EOF)
                    ev = results['KeyEvents'].get(f'event {i}')
                    if ev:
                        try:
                            link_loss += float(ev.get('splice loss', 0.0))
                        except (ValueError, TypeError):
                            pass

            link_loss = round(link_loss, 2) if link_loss > 0 else None

            return JsonResponse({
                'status': 'success',
                'data': {
                    'x': x_data,
                    'y': y_data,
                    'events': events_list,
                    'link_loss': link_loss
                }
            })

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    except Exception:
        logger.exception('Error obteniendo la traza bajo demanda')
        return JsonResponse(
            {'status': 'error', 'message': 'No se pudo procesar la traza bajo demanda.'},
            status=500,
        )
