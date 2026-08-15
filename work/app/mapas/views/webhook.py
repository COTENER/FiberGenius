import json
import hashlib
import hmac
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.db import DatabaseError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.utils.dateparse import parse_datetime
from django.utils.timezone import get_current_timezone, is_naive, localtime, make_aware, now
from mapas.models import AlarmaVeex, EventLog, Ruta


_sync_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='veex-sync')


def _enviar_sincronizacion(*args):
    _sync_executor.submit(sync_veex_id_in_background, *args)


def _validar_firma_webhook(request):
    secret = getattr(settings, 'FIBERGENIUS_WEBHOOK_SECRET', '')
    if not secret:
        return False, 'Webhook no configurado.'
    timestamp = request.headers.get('X-FiberGenius-Timestamp', '')
    signature = request.headers.get('X-FiberGenius-Signature', '')
    try:
        timestamp_int = int(timestamp)
    except (TypeError, ValueError):
        return False, 'Timestamp de firma ausente o inválido.'
    max_skew = int(getattr(settings, 'FIBERGENIUS_WEBHOOK_MAX_SKEW_SECONDS', 300))
    if abs(int(time.time()) - timestamp_int) > max_skew:
        return False, 'Firma expirada.'
    signed_payload = timestamp.encode('ascii') + b'.' + request.body
    expected = hmac.new(secret.encode('utf-8'), signed_payload, hashlib.sha256).hexdigest()
    supplied = signature.removeprefix('sha256=')
    if not hmac.compare_digest(expected, supplied):
        return False, 'Firma inválida.'
    return True, ''


def _parsear_timestamp_evento(value):
    parsed = parse_datetime(str(value)) if value else None
    if parsed and is_naive(parsed):
        parsed = make_aware(parsed, get_current_timezone())
    return parsed or now()

def sync_veex_id_in_background(incident_id, distance, timestamp, event_log_id=None, alarm_type=None):
    try:
        from mapas.veex_api import fetch_veex_alarm_id, get_sor_file
        from mapas.models import AlarmaVeex, EventLog
        from django.core.files.base import ContentFile
        veex_id = fetch_veex_alarm_id(distance, timestamp, alarm_type)
        if veex_id:
            AlarmaVeex.objects.filter(id=incident_id).update(veex_alarm_id=veex_id)
            if event_log_id:
                EventLog.objects.filter(id=event_log_id).update(veex_alarm_id=veex_id)
            logger.info(f"Sincronizado veex_alarm_id={veex_id} para AlarmaVeex ID={incident_id} y EventLog ID={event_log_id}")

            # Paso C: Descargar archivo .sor y guardarlo localmente
            sor_content = get_sor_file(veex_id)
            if sor_content:
                # Obtenemos instancias para usar el método save() del FileField
                incident = AlarmaVeex.objects.get(id=incident_id)
                file_name = f"padre_{incident.id}_veex_{veex_id}.sor"
                incident.archivo_sor.save(file_name, ContentFile(sor_content), save=True)
                
                if event_log_id:
                    log = EventLog.objects.get(id=event_log_id)
                    log_file_name = f"log_{log.id}_veex_{veex_id}.sor"
                    log.archivo_sor.save(log_file_name, ContentFile(sor_content), save=True)
                logger.info(f"Archivo SOR descargado y guardado permanentemente para Incidente {incident_id} (Log {event_log_id})")
            else:
                logger.warning(f"No se pudo descargar el archivo SOR de VeEX para veex_alarm_id={veex_id}")
    except Exception:
        logger.exception("Error en sync_veex_id_in_background")


logger = logging.getLogger('fibergenius.integration')

@csrf_exempt
@require_POST
def webhook_alarma(request):
    """
    Recibe los traps SNMP reenviados desde Chile en formato JSON.
    Guarda la alarma en la base de datos para mostrarla en tiempo real en el mapa,
    usando la estructura Padre (AlarmaVeex) e Hijo (EventLog).
    """
    if not getattr(settings, 'VEEX_ENABLED', False):
        return JsonResponse({'status': 'error', 'message': 'Integración VeEX deshabilitada.'}, status=404)
    firma_valida, mensaje_firma = _validar_firma_webhook(request)
    if not firma_valida:
        return JsonResponse({'status': 'error', 'message': mensaje_firma}, status=401)
    
    try:
        data = json.loads(request.body.decode('utf-8'))
        logger.info("Webhook VeEX autenticado recibido")
        
        # Validar campos mínimos obligatorios
        route_name = data.get('route_name', '').strip()
        status = data.get('status', '').strip()
        alarm_type = data.get('alarm_type', '').strip()
        alarm_level = data.get('alarm_level', '').strip()
        latitude_str = data.get('latitude', '').strip()
        longitude_str = data.get('longitude', '').strip()
        port = data.get('port', '').strip() or '1'
        
        # Si es un mensaje de prueba de SNMP sin coordenadas
        if data.get('test_message') and not (latitude_str and longitude_str):
            latitude = Decimal('-12.046374')  # Coordenada por defecto para pruebas
            longitude = Decimal('-77.042793')
            route_name = route_name or 'Ruta de Prueba SNMP'
            status = status or 'Active'
            alarm_type = alarm_type or 'SNMP Test'
            alarm_level = alarm_level or 'Warning'
        else:
            if not (latitude_str and longitude_str):
                return JsonResponse({'status': 'error', 'message': 'Coordenadas faltantes (latitude/longitude)'}, status=400)
            try:
                latitude = Decimal(latitude_str)
                longitude = Decimal(longitude_str)
            except (InvalidOperation, TypeError, ValueError):
                return JsonResponse({'status': 'error', 'message': 'Formato de coordenadas inválido'}, status=400)

        # Parsear distancia
        distance = None
        distance_str = data.get('distance')
        if distance_str:
            try:
                distance = float(distance_str)
            except (ValueError, TypeError):
                pass

        # Buscar el nombre de la OTU en la base de datos de inventario según la ruta
        resolved_otu_name = None
        ruta_inv = None
        if route_name:
            try:
                ruta_inv = Ruta.objects.filter(nombre__iexact=route_name).first()
                if ruta_inv and ruta_inv.otu:
                    resolved_otu_name = ruta_inv.otu.nombre
            except DatabaseError:
                logger.exception("Error resolviendo la OTU del inventario para %r", route_name)

        alarma_timestamp = data.get('timestamp') or localtime(now()).strftime('%Y-%m-%d %H:%M:%S')
        timestamp_evento = _parsear_timestamp_evento(alarma_timestamp)
        is_resolved_status = (status or '').lower() in ['resolved', 'cleared']

        # PASO A: Buscar Incidente Activo por Huella (routeName + port)
        incident = AlarmaVeex.objects.filter(
            route_name=route_name or 'Ruta Desconocida',
            port=port,
            status='Active'
        ).first()

        # Deduplicación de traps exactos (para evitar procesar el mismo trap retransmitido)
        if incident:
            existing_log = EventLog.objects.filter(
                incident=incident,
                status=status or 'Active',
                timestamp=alarma_timestamp
            ).exists()
            if existing_log:
                logger.info(f"Trap duplicado recibido para ruta '{route_name}' puerto '{port}' (status: '{status}') con timestamp '{alarma_timestamp}'. Ignorando.")
                return JsonResponse({
                    'status': 'success',
                    'message': 'Alarma duplicada ignorada'
                }, status=200)

        # PASO B: Si NO se encuentra un incidente activo
        if not incident:
            # Si el trap entrante es de resolución pero no hay incidente activo, buscamos uno resuelto recientemente
            # para ver si es un duplicado o si pertenece a la misma ráfaga de resolución simultánea.
            if is_resolved_status:
                recent_resolved = AlarmaVeex.objects.filter(
                    route_name=route_name or 'Ruta Desconocida',
                    port=port,
                    status='Resolved'
                ).order_by('-fecha_registro').first()
                
                if recent_resolved:
                    from django.utils.timezone import now
                    time_diff = (now() - recent_resolved.fecha_registro).total_seconds()
                    
                    if time_diff <= 15 or recent_resolved.fecha_resolucion == alarma_timestamp:
                        # Registrar el log de auditoría en la tabla Hijo asociado al incidente ya resuelto
                        event_log = EventLog.objects.create(
                            incident=recent_resolved,
                            status=status or 'Active',
                            alarm_type=alarm_type or 'Falla de Fibra',
                            alarm_level=alarm_level or 'Critical',
                            timestamp=alarma_timestamp,
                            timestamp_evento=timestamp_evento,
                            fecha_resolucion=alarma_timestamp,
                            device_serial=resolved_otu_name or data.get('device_serial') or 'UNKNOWN',
                            device_ip=data.get('device_ip') or '0.0.0.0',
                            port=port,
                            distance=distance,
                            route_name=route_name or 'Ruta Desconocida',
                            ruta_obj=ruta_inv,
                            latitude=latitude,
                            longitude=longitude,
                            test_message=data.get('test_message') or ''
                        )
                        
                        if distance is not None:
                            _enviar_sincronizacion(recent_resolved.id, distance, alarma_timestamp, event_log.id, alarm_type)
                            
                        logger.info(f"Trap de resolución redundante agrupado en el incidente Padre resuelto recientemente (ID: {recent_resolved.id}). Evitando duplicación.")
                        return JsonResponse({
                            'status': 'success',
                            'message': 'Log de resolución registrado en incidente resuelto existente',
                            'alarma_id': recent_resolved.id
                        }, status=200)

            incident_status = 'Resolved' if is_resolved_status else 'Active'
            
            incident = AlarmaVeex.objects.create(
                status=incident_status,
                alarm_type=alarm_type or 'Falla de Fibra',
                alarm_level=alarm_level or 'Critical',
                timestamp=alarma_timestamp, # Última fecha de incidencia / inicio
                timestamp_evento=timestamp_evento,
                fecha_resolucion=alarma_timestamp if is_resolved_status else None, # Fecha de corrección
                device_serial=resolved_otu_name or data.get('device_serial') or 'UNKNOWN',
                device_ip=data.get('device_ip') or '0.0.0.0',
                port=port,
                distance=distance,
                route_name=route_name or 'Ruta Desconocida',
                ruta_obj=ruta_inv,
                latitude=latitude,
                longitude=longitude,
                test_message=data.get('test_message') or ''
            )
            
            # Registrar el log en la tabla Hijo EventLog
            event_log = EventLog.objects.create(
                incident=incident,
                status=status or 'Active',
                alarm_type=alarm_type or 'Falla de Fibra',
                alarm_level=alarm_level or 'Critical',
                timestamp=alarma_timestamp,
                timestamp_evento=timestamp_evento,
                fecha_resolucion=alarma_timestamp if is_resolved_status else None,
                device_serial=resolved_otu_name or data.get('device_serial') or 'UNKNOWN',
                device_ip=data.get('device_ip') or '0.0.0.0',
                port=port,
                distance=distance,
                route_name=route_name or 'Ruta Desconocida',
                ruta_obj=ruta_inv,
                latitude=latitude,
                longitude=longitude,
                test_message=data.get('test_message') or ''
            )
            
            # Lanzar sincronización en background si hay distancia
            if distance is not None:
                _enviar_sincronizacion(incident.id, distance, alarma_timestamp, event_log.id, alarm_type)
            
            logger.info(f"NUEVO incidente registrado en Padre AlarmaVeex (ID: {incident.id}) y log registrado en EventLog.")
            return JsonResponse({
                'status': 'success',
                'message': 'Nuevo incidente y log registrados con éxito',
                'alarma_id': incident.id
            }, status=200)

        # PASO C: Si SÍ se encuentra un incidente activo
        else:
            # Caso Cierre/Resolución
            if is_resolved_status:
                incident.status = 'Resolved'
                incident.fecha_resolucion = alarma_timestamp # Fecha de corrección
                incident.timestamp_evento = timestamp_evento
                incident.alarm_type = alarm_type or incident.alarm_type
                incident.alarm_level = alarm_level or incident.alarm_level
                if distance is not None:
                    incident.distance = distance
                if latitude is not None and longitude is not None:
                    incident.latitude = latitude
                    incident.longitude = longitude
                incident.save()
                
                logger.info(f"Incidente activo ID {incident.id} RESUELTO (fecha_resolucion: {incident.fecha_resolucion})")
            
            # Caso Mutación o trap activo continuo
            else:
                # Actualizar campos del incidente con la información del último trap
                incident.alarm_type = alarm_type or incident.alarm_type
                incident.alarm_level = alarm_level or incident.alarm_level
                incident.timestamp = alarma_timestamp # Última fecha de incidencia actualizada
                incident.timestamp_evento = timestamp_evento
                if distance is not None:
                    incident.distance = distance
                if latitude is not None and longitude is not None:
                    incident.latitude = latitude
                    incident.longitude = longitude
                incident.save()
                
                logger.info(f"Incidente activo ID {incident.id} MUTADO / ACTUALIZADO (última incidencia: {incident.timestamp})")

            # Registrar obligatoriamente el log de auditoría en la tabla Hijo EventLog
            event_log = EventLog.objects.create(
                incident=incident,
                status=status or 'Active',
                alarm_type=alarm_type or 'Falla de Fibra',
                alarm_level=alarm_level or 'Critical',
                timestamp=alarma_timestamp,
                timestamp_evento=timestamp_evento,
                fecha_resolucion=alarma_timestamp if is_resolved_status else None,
                device_serial=resolved_otu_name or data.get('device_serial') or 'UNKNOWN',
                device_ip=data.get('device_ip') or '0.0.0.0',
                port=port,
                distance=distance,
                route_name=route_name or 'Ruta Desconocida',
                ruta_obj=ruta_inv,
                latitude=latitude,
                longitude=longitude,
                test_message=data.get('test_message') or ''
            )

            # Lanzar sincronización en background para el log, e incidentalmente para el incidente
            if distance is not None:
                _enviar_sincronizacion(incident.id, distance, alarma_timestamp, event_log.id, alarm_type)

            return JsonResponse({
                'status': 'success',
                'message': 'Incidente actualizado y log de auditoría registrado con éxito',
                'alarma_id': incident.id
            }, status=200)

    except json.JSONDecodeError:
        logger.error("Error al decodificar JSON en webhook de alarmas")
        return JsonResponse({'status': 'error', 'message': 'JSON inválido'}, status=400)
    except Exception:
        logger.exception("Error inesperado en webhook_alarma")
        return JsonResponse(
            {'status': 'error', 'message': 'Error interno al procesar la alarma.'},
            status=500,
        )


@login_required
@permission_required('mapas.view_alarmaveex', raise_exception=True)
@require_GET
def get_alarmas_activas(request):
    """
    Retorna la lista de alarmas para actualizar el mapa en vivo en tiempo real (AJAX).
    Incluye tanto las alarmas activas como las resueltas (historial).
    """
    limite_default = int(getattr(settings, 'FIBERGENIUS_ALARM_PAGE_SIZE', 200))
    try:
        limite = min(max(int(request.GET.get('limit', limite_default)), 1), 500)
        offset = max(int(request.GET.get('offset', 0)), 0)
    except ValueError:
        return JsonResponse({'status': 'error', 'message': 'Paginación inválida.'}, status=400)
    incluir_resueltas = request.GET.get('include_resolved') == '1'
    consulta = AlarmaVeex.objects.all()
    if not incluir_resueltas:
        consulta = consulta.filter(status__iexact='Active')
    total = consulta.count()
    alarmas = consulta[offset:offset + limite]
    
    data = []
    for a in alarmas:
        data.append({
            'id': a.id,
            'status': a.status,
            'alarm_type': a.alarm_type,
            'alarm_level': a.alarm_level,
            'timestamp': a.timestamp,
            'fecha_resolucion': a.fecha_resolucion or '',
            'veex_alarm_id': a.veex_alarm_id,
            'device_serial': a.device_serial,
            'device_ip': a.device_ip,
            'port': a.port,
            'distance': a.distance,
            'route_name': a.route_name,
            'latitude': float(a.latitude),
            'longitude': float(a.longitude),
            'test_message': a.test_message,
            'fecha_registro': a.fecha_registro.strftime('%d/%m/%Y %H:%M:%S')
        })
        
    return JsonResponse({
        'status': 'success',
        'alarmas': data,
        'pagination': {'total': total, 'limit': limite, 'offset': offset},
    })
