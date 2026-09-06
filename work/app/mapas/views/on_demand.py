import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError
from django.shortcuts import render, get_object_or_404
from django.http import FileResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required, permission_required
from mapas.models import Ruta, TrazaOnDemand
from mapas.veex_api import (
    get_veex_device_lasers,
    get_monitoring_port_id,
    create_on_demand_task,
    check_on_demand_status,
    download_on_demand_sor
)
from django.core.files.base import ContentFile
from django.utils.timezone import now

logger = logging.getLogger('fibergenius.integration')
_ON_DEMAND_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='fibergenius-ondemand')

@login_required
@permission_required('mapas.view_trazaondemand', raise_exception=True)
def visor_ondemand(request):
    """Renderiza la vista principal para trazas bajo demanda."""
    route_name = request.GET.get('route_name', '')
    
    # Buscar si ya existe una traza completada recientemente para esta ruta?
    # O siempre obligar al usuario a solicitar una nueva. El requerimiento dice:
    # "abra una nueva pestaña y ahi haya una opcion para dar click y obtener la traza"
    
    # Si viene con un traza_id específico:
    traza_id = request.GET.get('traza_id')
    traza = None
    if traza_id:
        traza = TrazaOnDemand.objects.filter(id=traza_id).first()
    
    context = {
        'route_name': route_name,
        'traza': traza
    }
    return render(request, 'visor_ondemand.html', context)


@login_required
@permission_required('mapas.add_trazaondemand', raise_exception=True)
@require_POST
def iniciar_traza_ondemand(request):
    """Endpoint para iniciar el flujo de traza bajo demanda en background."""
    try:
        if not getattr(settings, 'VEEX_ENABLED', False):
            return JsonResponse(
                {'status': 'error', 'message': 'Integración VeEX deshabilitada.'},
                status=503,
            )
        data = json.loads(request.body)
        route_name = str(data.get('route_name', '')).strip()
        
        if not route_name:
            return JsonResponse({'status': 'error', 'message': 'El nombre de ruta es obligatorio'})
            
        ruta = Ruta.objects.filter(nombre=route_name).first()
        if not ruta:
            return JsonResponse({'status': 'error', 'message': 'La ruta no existe.'}, status=404)

        traza = TrazaOnDemand.objects.create(
            route_name=route_name,
            ruta_obj=ruta,
            status='Pending'
        )

        _ON_DEMAND_EXECUTOR.submit(process_on_demand_flow, traza.id)
        
        return JsonResponse({
            'status': 'success',
            'traza_id': traza.id,
            'message': 'Proceso de traza bajo demanda iniciado'
        })
    except Exception:
        logger.exception("Error inesperado al iniciar una traza on-demand")
        return JsonResponse(
            {'status': 'error', 'message': 'No se pudo iniciar la traza bajo demanda.'},
            status=500,
        )


def process_on_demand_flow(traza_id):
    """Hilo en background que ejecuta los pasos 2 al 6."""
    try:
        traza = TrazaOnDemand.objects.get(id=traza_id)
        
        # Paso 2: Láser
        lasers = get_veex_device_lasers()
        if not lasers:
            traza.status = 'Failed'
            traza.error_message = 'No se encontraron láseres en el equipo'
            traza.save()
            return
            
        # Preferir SM1550 o SM1625, sino el primero
        laser_to_use = lasers[0]
        for l in ['SM1550', 'SM1625', 'SM1650']:
            if l in lasers:
                laser_to_use = l
                break
                
        # Paso 3: Obtener Puerto
        port_id = get_monitoring_port_id(traza.route_name)
        if not port_id:
            traza.status = 'Failed'
            traza.error_message = f'No se encontró el puerto asignado a la ruta: {traza.route_name}'
            traza.save()
            return
            
        # Paso 4: Crear Task
        traza.status = 'Running'
        traza.save()
        
        task_id = create_on_demand_task(port_id, laser_to_use)
        if not task_id:
            traza.status = 'Failed'
            traza.error_message = 'No se pudo crear la tarea en el equipo VeEX'
            traza.save()
            return
            
        traza.veex_on_demand_id = task_id
        traza.save()
        
        # Paso 5: Polling (Polling máximo por 3 minutos)
        max_retries = 36 # 36 * 5s = 180 segundos
        retries = 0
        task_status = 'pending'
        
        while task_status in ['pending', 'running', 'queued'] and retries < max_retries:
            time.sleep(5)
            task_status = check_on_demand_status(task_id)
            if task_status:
                task_status = task_status.lower()
            retries += 1
            
        if task_status != 'completed':
            traza.status = 'Failed'
            traza.error_message = f'La tarea expiró o falló. Estado final: {task_status}'
            traza.save()
            return
            
        # Paso 6: Descargar SOR
        sor_content = download_on_demand_sor(task_id)
        if not sor_content:
            traza.status = 'Failed'
            traza.error_message = 'Error al descargar el archivo .sor resultante'
            traza.save()
            return
            
        # Guardar el archivo
        filename = f"ondemand_{traza.id}_{traza.route_name}_{now().strftime('%Y%m%d_%H%M%S')}.sor"
        traza.archivo_sor.save(filename, ContentFile(sor_content))
        traza.status = 'Completed'
        traza.save()
        
    except Exception as exc:
        logger.exception("Error en flujo on-demand para traza %s", traza_id)
        try:
            updated = TrazaOnDemand.objects.filter(id=traza_id).update(
                status='Failed',
                error_message=str(exc),
            )
            if not updated:
                logger.warning("No existe la traza on-demand %s para marcarla como fallida", traza_id)
        except DatabaseError:
            logger.exception(
                "No se pudo marcar como fallida la traza on-demand %s",
                traza_id,
            )


@login_required
@permission_required('mapas.view_trazaondemand', raise_exception=True)
def get_on_demand_status(request, traza_id):
    """Endpoint para consultar el estado desde el frontend."""
    traza = get_object_or_404(TrazaOnDemand, id=traza_id)
    
    return JsonResponse({
        'status': traza.status,
        'route_name': traza.route_name,
        'error_message': traza.error_message,
        'has_file': bool(traza.archivo_sor)
    })


@login_required
@permission_required('mapas.view_trazaondemand', raise_exception=True)
def descargar_traza_ondemand(request, traza_id):
    """Descarga un SOR bajo demanda sin reutilizar permisos de alarmas."""
    traza = get_object_or_404(TrazaOnDemand, id=traza_id)
    if not traza.archivo_sor:
        return JsonResponse(
            {'status': 'error', 'message': 'La traza no tiene un archivo SOR asociado.'},
            status=404,
        )

    try:
        archivo = traza.archivo_sor.open('rb')
    except (OSError, ValueError):
        logger.warning(
            'No se pudo abrir el archivo SOR de la traza bajo demanda %s',
            traza.id,
            exc_info=True,
        )
        return JsonResponse(
            {'status': 'error', 'message': 'No se pudo leer el archivo SOR.'},
            status=404,
        )

    return FileResponse(
        archivo,
        as_attachment=True,
        filename=Path(traza.archivo_sor.name).name,
        content_type='application/octet-stream',
    )
