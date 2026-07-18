import requests
import logging
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger('mapas')

VEEX_API_URL = getattr(settings, 'VEEX_API_URL', '')
VEEX_API_USER = getattr(settings, 'VEEX_API_USER', '')
VEEX_API_PASS = getattr(settings, 'VEEX_API_PASS', '')


def _tls_verify():
    ca_bundle = getattr(settings, 'VEEX_CA_BUNDLE', '')
    return ca_bundle or bool(getattr(settings, 'VEEX_TLS_VERIFY', True))

def get_veex_token():
    """Obtiene el JWT Token de la API de VeEX y lo cachea para reuso."""
    if not getattr(settings, 'VEEX_ENABLED', False) or not all((VEEX_API_URL, VEEX_API_USER, VEEX_API_PASS)):
        return None
    token = cache.get('veex_jwt_token')
    if token:
        return token

    try:
        url = f"{VEEX_API_URL}/auth/token"
        payload = {
            "username": VEEX_API_USER,
            "password": VEEX_API_PASS
        }
        response = requests.post(url, json=payload, verify=_tls_verify(), timeout=10)
        response.raise_for_status()
        
        data = response.json()
        token = data.get('token')
        if token:
            # Cacheamos el token por 1 hora (o menos según expire)
            cache.set('veex_jwt_token', token, 3600)
            return token
    except Exception as e:
        logger.error(f"Error obteniendo token de VeEX: {e}")
    return None


def fetch_veex_alarm_id(distance, timestamp, alarm_type=None):
    """Busca en la API de VeEX el ID de la alarma basado en la distancia, hora y tipo opcional."""
    token = get_veex_token()
    if not token:
        return None

    try:
        # Ya no filtramos por dateTime en la URL debido a problemas de zona horaria (mismatch de 3 horas)
        url = f"{VEEX_API_URL}/monitoring-alarms"
        headers = {"Authorization": f"Bearer {token}"}
        
        response = requests.get(url, headers=headers, verify=_tls_verify(), timeout=10)
        response.raise_for_status()
        data = response.json()
        
        items = data.get('items', [])
        # Ordenar de más nuevo a más antiguo por activeAt (string ISO 8601 se ordena cronológicamente bien)
        items.sort(key=lambda x: x.get('activeAt', ''), reverse=True)
        
        # Buscar el item más reciente que coincida con la distancia y el tipo
        for item in items:
            api_distance = item.get('distanceMeters')
            if api_distance is not None and abs(float(api_distance) - float(distance)) < 1.0:
                # Si se provee alarm_type, asegurar que coincida (ej. "Event Loss" vs "EventLoss")
                if alarm_type:
                    api_type = item.get('type', '').replace(' ', '').lower()
                    expected_type = alarm_type.replace(' ', '').lower()
                    if api_type != expected_type:
                        continue # No es el mismo tipo de evento, seguimos buscando
                
                # Retorna el ID de VeEX más reciente
                return item.get('id')
                
        return None
    except Exception as e:
        logger.error(f"Error buscando ID de alarma en VeEX: {e}")
        return None


def get_sor_file(veex_alarm_id):
    """Descarga el archivo SOR filtrado desde la API de VeEX."""
    token = get_veex_token()
    if not token:
        return None

    try:
        url = f"{VEEX_API_URL}/monitoring-alarms/{veex_alarm_id}/sor"
        headers = {"Authorization": f"Bearer {token}"}
        
        response = requests.get(url, headers=headers, verify=_tls_verify(), timeout=15)
        response.raise_for_status()
        
        return response.content
    except Exception as e:
        logger.error(f"Error descargando SOR para alarma {veex_alarm_id}: {e}")
        return None


def get_veex_device_lasers():
    """Obtiene los láseres instalados en el equipo VeEX."""
    token = get_veex_token()
    if not token:
        return []

    try:
        url = f"{VEEX_API_URL}/device"
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(url, headers=headers, verify=_tls_verify(), timeout=10)
        response.raise_for_status()
        
        data = response.json()
        # Los láseres están en supportedMeasurementParameters -> laserUnits -> keys
        lasers = list(data.get('supportedMeasurementParameters', {}).get('laserUnits', {}).keys())
        return lasers
    except Exception as e:
        logger.error(f"Error obteniendo láseres: {e}")
        return []


def get_monitoring_port_id(route_name):
    """Busca el ID del puerto de monitoreo que coincide con el nombre de la ruta en la nota."""
    token = get_veex_token()
    if not token:
        return None

    try:
        url = f"{VEEX_API_URL}/monitoring-ports"
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(url, headers=headers, verify=_tls_verify(), timeout=10)
        response.raise_for_status()
        
        ports = response.json()
        for port in ports:
            # Comparamos la nota (case-insensitive) con la ruta
            if port.get('note', '').strip().lower() == route_name.strip().lower():
                return port.get('id')
                
        return None
    except Exception as e:
        logger.error(f"Error obteniendo puerto para ruta {route_name}: {e}")
        return None


def create_on_demand_task(port_id, laser):
    """Crea una tarea de traza bajo demanda."""
    token = get_veex_token()
    if not token:
        return None

    try:
        url = f"{VEEX_API_URL}/on-demand-tasks"
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "monitoringPortId": port_id,
            "measurementSettings": {
                "measurementType": "Auto",
                "laser": laser
            }
        }
        response = requests.post(url, headers=headers, json=payload, verify=_tls_verify(), timeout=10)
        response.raise_for_status()
        
        # Asumimos que retorna el ID de la tarea creada, ej {"onDemandId": "123-abc"}
        data = response.json()
        return data.get('onDemandId', data.get('id'))
    except Exception as e:
        logger.error(f"Error creando tarea on-demand en puerto {port_id}: {e}")
        return None


def check_on_demand_status(task_id):
    """Consulta el estado de una tarea bajo demanda."""
    token = get_veex_token()
    if not token:
        return "Failed"

    try:
        url = f"{VEEX_API_URL}/on-demand-tasks/{task_id}"
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(url, headers=headers, verify=_tls_verify(), timeout=10)
        response.raise_for_status()
        
        data = response.json()
        return data.get('status') # puede ser "pending", "running", "completed", "failed", etc.
    except Exception as e:
        logger.error(f"Error consultando estado de on-demand {task_id}: {e}")
        return "Failed"


def download_on_demand_sor(task_id):
    """Descarga el archivo SOR de una tarea completada."""
    token = get_veex_token()
    if not token:
        return None

    try:
        url = f"{VEEX_API_URL}/on-demands/{task_id}/sor"
        headers = {"Authorization": f"Bearer {token}"}
        params = {"filterTrace": "true"}
        
        response = requests.get(url, headers=headers, params=params, verify=_tls_verify(), timeout=15)
        response.raise_for_status()
        
        return response.content
    except Exception as e:
        logger.error(f"Error descargando SOR para on-demand {task_id}: {e}")
        return None
