import json
import logging
from decimal import Decimal
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.timezone import now, localtime
from mapas.models import AlarmaVeex, EventLog
import threading

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
    except Exception as e:
        logger.error(f"Error en sync_veex_id_in_background: {e}")


logger = logging.getLogger('mapas')

@csrf_exempt
def webhook_alarma(request):
    """
    Recibe los traps SNMP reenviados desde Chile en formato JSON.
    Guarda la alarma en la base de datos para mostrarla en tiempo real en el mapa,
    usando la estructura Padre (AlarmaVeex) e Hijo (EventLog).
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Método no permitido. Use POST.'}, status=405)
    
    try:
        data = json.loads(request.body.decode('utf-8'))
        logger.info(f"Webhook VeEX recibido: {data}")
        
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
            except Exception:
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
        if route_name:
            try:
                from mapas.models import Ruta
                ruta_inv = Ruta.objects.filter(nombre__iexact=route_name).first()
                if ruta_inv and ruta_inv.otu:
                    resolved_otu_name = ruta_inv.otu.nombre
            except Exception as e:
                logger.error(f"Error resolviendo OTU del inventario para '{route_name}': {str(e)}")

        alarma_timestamp = data.get('timestamp') or localtime(now()).strftime('%Y-%m-%d %H:%M:%S')
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
                            fecha_resolucion=alarma_timestamp,
                            device_serial=resolved_otu_name or data.get('device_serial') or 'UNKNOWN',
                            device_ip=data.get('device_ip') or '0.0.0.0',
                            port=port,
                            distance=distance,
                            route_name=route_name or 'Ruta Desconocida',
                            latitude=latitude,
                            longitude=longitude,
                            test_message=data.get('test_message') or ''
                        )
                        
                        if distance is not None:
                            threading.Thread(target=sync_veex_id_in_background, args=(recent_resolved.id, distance, alarma_timestamp, event_log.id, alarm_type)).start()
                            
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
                fecha_resolucion=alarma_timestamp if is_resolved_status else None, # Fecha de corrección
                device_serial=resolved_otu_name or data.get('device_serial') or 'UNKNOWN',
                device_ip=data.get('device_ip') or '0.0.0.0',
                port=port,
                distance=distance,
                route_name=route_name or 'Ruta Desconocida',
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
                fecha_resolucion=alarma_timestamp if is_resolved_status else None,
                device_serial=resolved_otu_name or data.get('device_serial') or 'UNKNOWN',
                device_ip=data.get('device_ip') or '0.0.0.0',
                port=port,
                distance=distance,
                route_name=route_name or 'Ruta Desconocida',
                latitude=latitude,
                longitude=longitude,
                test_message=data.get('test_message') or ''
            )
            
            # Lanzar sincronización en background si hay distancia
            if distance is not None:
                threading.Thread(target=sync_veex_id_in_background, args=(incident.id, distance, alarma_timestamp, event_log.id, alarm_type)).start()
            
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
                fecha_resolucion=alarma_timestamp if is_resolved_status else None,
                device_serial=resolved_otu_name or data.get('device_serial') or 'UNKNOWN',
                device_ip=data.get('device_ip') or '0.0.0.0',
                port=port,
                distance=distance,
                route_name=route_name or 'Ruta Desconocida',
                latitude=latitude,
                longitude=longitude,
                test_message=data.get('test_message') or ''
            )

            # Lanzar sincronización en background para el log, e incidentalmente para el incidente
            if distance is not None:
                threading.Thread(target=sync_veex_id_in_background, args=(incident.id, distance, alarma_timestamp, event_log.id, alarm_type)).start()

            return JsonResponse({
                'status': 'success',
                'message': 'Incidente actualizado y log de auditoría registrado con éxito',
                'alarma_id': incident.id
            }, status=200)

    except json.JSONDecodeError:
        logger.error("Error al decodificar JSON en webhook de alarmas")
        return JsonResponse({'status': 'error', 'message': 'JSON inválido'}, status=400)
    except Exception as e:
        logger.error(f"Error inesperado en webhook_alarma: {str(e)}")
        return JsonResponse({'status': 'error', 'message': f'Error interno: {str(e)}'}, status=500)


def get_alarmas_activas(request):
    """
    Retorna la lista de alarmas para actualizar el mapa en vivo en tiempo real (AJAX).
    Incluye tanto las alarmas activas como las resueltas (historial).
    """
    # Obtenemos las últimas 100 alarmas generales (tanto activas como resueltas)
    alarmas = AlarmaVeex.objects.all()[:100]
    
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
        
    return JsonResponse({'status': 'success', 'alarmas': data})
