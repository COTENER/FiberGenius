"""Contexto global de presentación para Fiber Genius."""

from django.conf import settings
from .ui_access import IMPORT_PERMISSIONS, navigation_access
from .map_configuration import map_configuration


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
    map_config = map_configuration(light, dark, satellite, attribution)
    light, dark, satellite = (map_config[name]['url'] for name in ('light', 'dark', 'satellite'))
    return {
        "fg_map_config": map_config,
        "fg_access": navigation_access(request.user),
        "fg_import_access": {kind: request.user.has_perms(perms) for kind, perms in IMPORT_PERMISSIONS.items()},
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
