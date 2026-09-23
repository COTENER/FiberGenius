"""Contexto global específico del despliegue de FiberGenius."""

from django.conf import settings
from mapas.map_configuration import map_configuration

def deployment(request):
    config = map_configuration(settings.MAP_TILE_URL_LIGHT, settings.MAP_TILE_URL_DARK,
                               settings.MAP_TILE_URL_SATELLITE, settings.MAP_TILE_ATTRIBUTION)
    return {
        "MAP_TILE_URL_LIGHT": config['light']['url'],
        "MAP_TILE_URL_DARK": config['dark']['url'],
        "MAP_TILE_URL_SATELLITE": config['satellite']['url'],
        "MAP_TILE_ATTRIBUTION": settings.MAP_TILE_ATTRIBUTION,
    }
