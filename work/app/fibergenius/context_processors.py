"""Contexto global específico del despliegue de FiberGenius."""

from django.conf import settings

def deployment(request):
    return {
        "MAP_TILE_URL_LIGHT": settings.MAP_TILE_URL_LIGHT,
        "MAP_TILE_URL_DARK": settings.MAP_TILE_URL_DARK,
        "MAP_TILE_URL_SATELLITE": settings.MAP_TILE_URL_SATELLITE,
        "MAP_TILE_ATTRIBUTION": settings.MAP_TILE_ATTRIBUTION,
    }
