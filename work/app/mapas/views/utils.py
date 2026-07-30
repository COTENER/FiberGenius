"""
Utilidades compartidas para las vistas de la app mapas.
"""
import logging
from django.conf import settings

logger = logging.getLogger('mapas')


def clasificar_tipo_evento(adicional, estado, duracion_min):
    """
    Clasifica el tipo de evento basándose en los campos adicional, estado y duracion_min.
    Centraliza la lógica que antes se repetía 3+ veces en las vistas.

    Retorna: 'FIBER_CUT', 'DISCONNECTIONS', 'ATTENUATION', 'INJECTION' o None.
    """
    if adicional == 'FIBER_CUT':
        estado_upper = (estado or "").strip().upper()
        duracion = int(duracion_min) if duracion_min is not None else 0
        if estado_upper == 'UNCLEARED' or duracion > 60:
            return 'FIBER_CUT'
        else:
            return 'DISCONNECTIONS'
    elif adicional in ['ATTENUATION', 'INJECTION']:
        return adicional
    return None


def get_geo_bounds():
    """
    Retorna los límites geográficos desde settings.
    """
    bounds = settings.GEO_BOUNDS
    return (
        bounds['LAT_MIN'],
        bounds['LAT_MAX'],
        bounds['LON_MIN'],
        bounds['LON_MAX'],
    )


def get_color_evento(tipo_display, adicional=None):
    """
    Retorna el color de marcador para un tipo de evento.
    """
    if tipo_display == 'FIBER_CUT':
        return 'red'
    elif tipo_display == 'DISCONNECTIONS':
        return '#33daff'
    elif adicional == 'ATTENUATION' or tipo_display == 'ATTENUATION':
        return 'orange'
    elif adicional == 'INJECTION' or tipo_display == 'INJECTION':
        return 'blue'
    return 'gray'
