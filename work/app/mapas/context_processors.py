"""Contexto global de presentación para Fiber Genius."""

from django.conf import settings


def fibergenius_ui(request):
    """Expone identidad, módulos activos y cartografía configurable a la GUI."""
    light = getattr(
        settings,
        "MAP_TILE_URL_LIGHT",
        getattr(settings, "FIBERGENIUS_MAP_TILE_LIGHT", ""),
    )
    dark = getattr(
        settings,
        "MAP_TILE_URL_DARK",
        getattr(settings, "FIBERGENIUS_MAP_TILE_DARK", ""),
    )
    satellite = getattr(
        settings,
        "MAP_TILE_URL_SATELLITE",
        getattr(settings, "FIBERGENIUS_MAP_TILE_SATELLITE", ""),
    )
    attribution = getattr(
        settings,
        "MAP_TILE_ATTRIBUTION",
        getattr(settings, "FIBERGENIUS_MAP_ATTRIBUTION", "Cartografía configurable"),
    )
    return {
        "fg_version": getattr(settings, "FIBERGENIUS_VERSION", "RC2 GUI 0.5.0"),
        "fg_veex_enabled": getattr(settings, "FIBERGENIUS_OPERATIONS_UI_ENABLED", False),
        "fg_map_tile_light": light,
        "fg_map_tile_dark": dark,
        "fg_map_tile_satellite": satellite,
        "fg_map_attribution": attribution,
        # Compatibilidad con las pantallas operativas existentes.
        "MAP_TILE_URL_LIGHT": light,
        "MAP_TILE_URL_DARK": dark,
        "MAP_TILE_URL_SATELLITE": satellite,
        "MAP_TILE_ATTRIBUTION": attribution,
    }
