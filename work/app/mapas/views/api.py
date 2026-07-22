"""
Vistas de integración con APIs externas ONMSI.
"""
import logging
from io import StringIO

from django.http import JsonResponse
from django.contrib.auth.decorators import login_required, permission_required
from django.views.decorators.http import require_POST
from django.core import management
from django.conf import settings
import requests
from requests.auth import HTTPBasicAuth

from ..models import IDRuta

logger = logging.getLogger('mapas')


@login_required
@permission_required('mapas.change_ruta', raise_exception=True)
@require_POST
def ejecutar_script_actualizacion(request):
    if request.method == 'POST':
        try:
            ruta = request.POST.get('nombre_ruta')
            out = StringIO()

            RUTA_CARPETAS_ENLACES = settings.RUTA_CARPETAS_ENLACES

            kwargs = {'stdout': out}
            if ruta:
                kwargs['ruta'] = ruta

            msg_ruta = f" PARA {ruta}" if ruta else ""

            # PASO 1: Actualizar Mediciones (Script 1)
            out.write(f">>> PASO 1/4: ACTUALIZANDO MEDICIONES (XML API){msg_ruta}...\n")
            management.call_command('actualizar_mediciones', **kwargs)

            # PASO 2: Actualizar Eventos (Script 2)
            out.write(f"\n>>> PASO 2/4: DESCARGANDO DETALLES DE EVENTOS (JSON API){msg_ruta}...\n")
            management.call_command('actualizar_eventos', **kwargs)

            # PASO 3: Georreferenciar (Script 3)
            out.write(f"\n>>> PASO 3/4: GEORREFERENCIANDO EVENTOS{msg_ruta}...\n")
            management.call_command('georeferenciar_eventos', root=RUTA_CARPETAS_ENLACES, recursive=True, **kwargs)

            # PASO 4: Cálculos Finales
            out.write(f"\n>>> PASO 4/4: EJECUTANDO CÁLCULOS FINALES (OTDR){msg_ruta}...\n")
            management.call_command('actualizar_calculos_otdr', **kwargs)

            output_str = out.getvalue()

            return JsonResponse({
                'status': 'success',
                'message': 'Ciclo completo de actualización (Pasos 1-4) finalizado con éxito.',
                'output': output_str
            })

        except Exception as e:
            logger.error(f"Error durante actualización: {e}", exc_info=True)
            return JsonResponse({
                'status': 'error',
                'message': 'Ocurrió un error crítico durante la actualización.',
                'error_details': str(e)
            }, status=500)

    return JsonResponse({'status': 'error', 'message': 'Método no permitido.'}, status=405)


@login_required
@permission_required('mapas.change_ruta', raise_exception=True)
@require_POST
def ejecutar_alarmas(request):
    """Vista exclusiva para el botón 'Play': Solo actualiza alarmas."""
    if request.method == 'POST':
        try:
            out = StringIO()

            out.write(">>> INICIANDO ACTUALIZACIÓN DE ALARMAS (Tabla datos)...\n")
            management.call_command('actualizar_alarmas', stdout=out)

            output_str = out.getvalue()

            return JsonResponse({
                'status': 'success',
                'message': 'Las alarmas se han actualizado correctamente.',
                'output': output_str
            })

        except Exception as e:
            logger.error(f"Error al actualizar alarmas: {e}", exc_info=True)
            return JsonResponse({
                'status': 'error',
                'message': 'Error al actualizar alarmas.',
                'error_details': str(e)
            }, status=500)

    return JsonResponse({'status': 'error', 'message': 'Método no permitido.'}, status=405)


@login_required
@permission_required('mapas.change_ruta', raise_exception=True)
@require_POST
def actualizar_medicion_individual(request):
    """
    Recibe 'nombre_ruta' por POST.
    Busca el ID en IDRuta.
    Llama a la API externa esperando JSON.
    """
    ruta_nombre = request.POST.get('nombre_ruta')

    if not ruta_nombre:
        return JsonResponse({'status': 'error', 'message': 'No se proporcionó el nombre de la ruta.'}, status=400)

    # 1. Buscar el ID en la base de datos
    try:
        obj_id = IDRuta.objects.get(ruta=ruta_nombre)
        id_onmsi = obj_id.id_onmsi
    except IDRuta.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': f'No se encontró un ID ONMSI asociado a la ruta "{ruta_nombre}". Carga el CSV de IDs.'}, status=404)

    # 2. Configuración de la API Externa
    base_url = str(settings.MEDICION_INDIVIDUAL_API_URL).rstrip('/')
    if not base_url:
        return JsonResponse(
            {'status': 'error', 'message': 'La integración ONMSI no está configurada.'},
            status=503,
        )
    api_url = f"{base_url}/{id_onmsi}/measurements/start"
    usuario = settings.MEDICION_INDIVIDUAL_API_USER
    password = settings.MEDICION_INDIVIDUAL_API_PASSWORD

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    # 3. Hacer la petición
    try:
        response = requests.post(
            api_url,
            auth=HTTPBasicAuth(usuario, password),
            json={},
            headers=headers,
            timeout=10,
            verify=getattr(settings, 'ONMSI_CA_BUNDLE', '') or getattr(settings, 'ONMSI_TLS_VERIFY', True),
        )

        if response.status_code in [200, 201, 202, 204]:
            return JsonResponse({'status': 'success', 'message': f'Medición iniciada correctamente para {ruta_nombre} (ID: {id_onmsi}).'})
        else:
            return JsonResponse(
                {'status': 'error', 'message': f'La API ONMSI respondió con error {response.status_code}.'},
                status=502,
            )

    except requests.exceptions.RequestException as e:
        logger.error(f"Error de conexión con equipo de medición: {e}")
        return JsonResponse({'status': 'error', 'message': 'Error de conexión con el equipo de medición.'}, status=503)

@login_required
@permission_required('mapas.view_alarmaveex', raise_exception=True)
def descargar_sor(request, alarm_id):
    """
    Descarga el archivo SOR filtrado desde la API de VeEX y lo devuelve al navegador.
    """
    from ..veex_api import get_sor_file
    from django.http import HttpResponse, Http404
    from mapas.models import AlarmaVeex, EventLog, TrazaOnDemand

    incident_id = request.GET.get('incident_id')
    log_id = request.GET.get('log_id')
    ondemand_id = request.GET.get('ondemand_id')
    sor_content = None

    if ondemand_id:
        traza = TrazaOnDemand.objects.filter(id=ondemand_id).first()
        if traza and traza.archivo_sor:
            try:
                sor_content = traza.archivo_sor.read()
            except Exception:
                pass

    if log_id:
        log_historico = EventLog.objects.filter(id=log_id).first()
        if log_historico and log_historico.archivo_sor:
            try:
                sor_content = log_historico.archivo_sor.read()
            except Exception:
                pass

    if not sor_content and incident_id:
        alarma_padre = AlarmaVeex.objects.filter(id=incident_id).first()
        if alarma_padre and alarma_padre.archivo_sor:
            try:
                sor_content = alarma_padre.archivo_sor.read()
            except Exception:
                pass

    if not sor_content:
        sor_content = get_sor_file(alarm_id)
    
    if not sor_content:
        # En caso de error, puedes devolver JSON o texto plano
        return HttpResponse(
            "No se pudo obtener la traza SOR. Verifica la conexión con el servidor de VeEX o si la alarma existe.",
            status=404
        )

    # El contenido binario se empaqueta en una respuesta HTTP que fuerza la descarga
    response = HttpResponse(sor_content, content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="traza_alarma_{alarm_id}.sor"'
    return response

@login_required
@permission_required('mapas.view_alarmaveex', raise_exception=True)
def get_traza_data(request, alarm_id):
    """
    Descarga el archivo SOR y lo parsea en memoria usando pyotdr.
    Retorna la lista de coordenadas (X, Y) y eventos detectados en formato JSON para graficar.
    """
    from ..veex_api import get_sor_file
    from mapas.models import AlarmaVeex, EventLog, TrazaOnDemand
    import tempfile
    import pyotdr.read
    import os

    incident_id = request.GET.get('incident_id')
    log_id = request.GET.get('log_id')
    ondemand_id = request.GET.get('ondemand_id')
    sor_content = None

    # 0. Intentar cargar desde TrazaOnDemand
    if ondemand_id:
        traza = TrazaOnDemand.objects.filter(id=ondemand_id).first()
        if traza and traza.archivo_sor:
            try:
                sor_content = traza.archivo_sor.read()
            except Exception:
                pass

    # 1. Intentar cargar desde EventLog local
    if log_id:
        log_historico = EventLog.objects.filter(id=log_id).first()
        if log_historico and log_historico.archivo_sor:
            try:
                sor_content = log_historico.archivo_sor.read()
            except Exception as e:
                pass # Fallback a VeEX si hay error leyendo disco

    # 2. Intentar cargar desde AlarmaVeex local
    if not sor_content and incident_id:
        alarma_padre = AlarmaVeex.objects.filter(id=incident_id).first()
        if alarma_padre and alarma_padre.archivo_sor:
            try:
                sor_content = alarma_padre.archivo_sor.read()
            except Exception as e:
                pass

    # 3. Fallback: Descargar desde VeEX API
    if not sor_content:
        sor_content = get_sor_file(alarm_id)

    if not sor_content:
        return JsonResponse({'status': 'error', 'message': 'No se pudo obtener la traza SOR ni localmente ni desde VeEX.'}, status=404)

    # pyotdr parsea archivos, así que usamos un temporal
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.sor') as tmp:
            tmp.write(sor_content)
            tmp_path = tmp.name
        
        status, results, tracedata = pyotdr.read.sorparse(tmp_path)

        if status != "ok":
            return JsonResponse({'status': 'error', 'message': f'Error parseando archivo SOR: {status}'}, status=500)

        # tracedata es una lista de strings: "dist_km \t db \n"
        x_data = []
        y_data = []
        for line in tracedata:
            parts = line.strip().split('\t')
            if len(parts) == 2:
                # Convertimos distancia de km a metros
                x_data.append(round(float(parts[0]) * 1000, 2))
                y_data.append(round(float(parts[1]), 3))

        # Extraer eventos
        events_list = []
        if 'KeyEvents' in results:
            key_events = results['KeyEvents']
            for key in key_events.keys():
                if key.startswith('event '):
                    ev = key_events[key]
                    events_list.append({
                        'number': ev.get('event number'),
                        'distance': round(float(ev.get('distance', 0)) * 1000, 2), # metros
                        'type': ev.get('type'),
                        'loss': ev.get('splice loss'),
                        'reflectance': ev.get('refl loss'),
                        'slope': ev.get('slope')
                    })
        
        # Parámetros fijos
        resolution = 0
        if 'FxdParams' in results:
            resolution = results['FxdParams'].get('resolution', 0)
            
        link_loss = 0
        if 'KeyEvents' in results and 'Summary' in results['KeyEvents']:
            link_loss = results['KeyEvents']['Summary'].get('total loss', 0)

        return JsonResponse({
            'status': 'success',
            'data': {
                'x': x_data,
                'y': y_data,
                'events': events_list,
                'resolution': resolution,
                'link_loss': link_loss
            }
        })

    except Exception:
        logger.exception('Error procesando la traza SOR de la alarma %s', alarm_id)
        return JsonResponse(
            {'status': 'error', 'message': 'No se pudo procesar la traza SOR.'},
            status=500,
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                logger.warning('No se pudo eliminar el temporal SOR %s', tmp_path)


