"""Reglas compartidas por las pantallas y su navegación (sin otorgar permisos)."""
from functools import wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404

IMPORT_PERMISSIONS = {
    'sites_inventario': ('mapas.add_hubsite', 'mapas.change_hubsite'),
    'reservas': ('mapas.add_reserva', 'mapas.change_reserva'),
    'ruta_otu': ('mapas.add_ruta', 'mapas.change_ruta'),
    'equipos_otu': (
        'mapas.add_otu', 'mapas.change_otu',
        'mapas.add_puertootu', 'mapas.change_puertootu',
    ),
    'coordenadas_rutas': ('mapas.add_coordenadaruta', 'mapas.change_coordenadaruta'),
    'asociar_puertos': ('mapas.change_puertootu',),
    'id_rutas': ('mapas.add_idruta', 'mapas.change_idruta'),
    'coordenadas_inventario': ('mapas.add_coordenadaruta', 'mapas.change_coordenadaruta'),
    'coordenadas_csv': ('mapas.add_coordenadaruta', 'mapas.change_coordenadaruta'),
    'tramos_inventario': ('mapas.add_inventariotramo', 'mapas.change_inventariotramo'),
    'fibras_globales': ('mapas.add_inventariofibra', 'mapas.change_inventariofibra'),
    'fibras_inventario': (
        'mapas.change_inventariofibra',
        'mapas.add_fibratramo', 'mapas.change_fibratramo',
    ),
    'terminaciones_fibra': (
        'mapas.add_inventariofibra',
        'mapas.change_inventariofibra',
        'mapas.add_terminacionfibra',
        'mapas.change_terminacionfibra',
        'mapas.change_detallepuertoodf',
    ),
    'odf_inventario': ('mapas.add_inventarioodf', 'mapas.change_inventarioodf'),
    'puertos_odf_inventario': (
        'mapas.add_detallepuertoodf',
        'mapas.change_detallepuertoodf',
    ),
}



SCREEN_PERMISSIONS = {
    'quality': ('mapas.view_ruta', 'mapas.view_inventariofibra', 'mapas.view_inventarioodf', 'mapas.view_detallepuertoodf', 'mapas.view_hubsite'),
    'summary': ('mapas.view_ruta',),
    'map': ('mapas.view_ruta',),
    'routes': ('mapas.view_ruta',),
    'fibers': ('mapas.view_inventariofibra', 'mapas.view_reserva'),
    'odfs': ('mapas.view_inventarioodf',),
    'ports': ('mapas.view_detallepuertoodf',),
    'sites': ('mapas.view_hubsite',),
    'users': ('auth.view_user',),
    'groups': ('auth.view_group',),
    'alarms': ('mapas.view_alarmaveex',),
    'operations': ('mapas.can_view_reports',),
    'ranking': ('mapas.can_view_reports',),
    'thresholds': ('mapas.view_perfilumbral',),
}
OPERATIONS_SCREENS = {'alarms', 'operations', 'ranking', 'thresholds'}


def can_open(user, screen):
    if not user.is_authenticated:
        return False
    if screen in OPERATIONS_SCREENS and not getattr(settings, 'FIBERGENIUS_OPERATIONS_UI_ENABLED', False):
        return False
    if screen == 'imports':
        return user.has_perm('mapas.view_loteimportacion') or any(
            user.has_perms(perms) for perms in IMPORT_PERMISSIONS.values()
        )
    return any(user.has_perm(perm) for perm in SCREEN_PERMISSIONS[screen])


def screen_required(screen):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if screen in OPERATIONS_SCREENS and not getattr(settings, 'FIBERGENIUS_OPERATIONS_UI_ENABLED', False):
                raise Http404('Módulo de operaciones deshabilitado.')
            if not can_open(request.user, screen):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


def navigation_access(user):
    access = {screen: can_open(user, screen) for screen in (*SCREEN_PERMISSIONS, 'imports')}
    access['inventory'] = any(access[key] for key in ('summary', 'map', 'routes', 'fibers', 'odfs', 'ports'))
    access['administration'] = any(access[key] for key in ('sites', 'users', 'groups', 'thresholds'))
    access['operations_section'] = any(access[key] for key in OPERATIONS_SCREENS - {'thresholds'})
    return access
