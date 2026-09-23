import logging
import time

from django.utils.timezone import now
from django.conf import settings
from django.contrib.auth import logout
from django.core.cache import cache
from django.db import OperationalError
from django.http import JsonResponse
from django.shortcuts import redirect
from .models import UserActivity
from .services.errores_bd import es_bloqueo_sqlite


audit_logger = logging.getLogger('fibergenius.audit')
security_logger = logging.getLogger('fibergenius.security')


def _es_sondeo_progreso_importacion(request):
    """El sondeo no renueva actividad; conserva autenticación y expiración."""
    return request.path.startswith('/api/importaciones/progreso/')


def _request_identity(request):
    user = getattr(request, 'user', None)
    username = (
        user.get_username()
        if user is not None and user.is_authenticated
        else 'anonymous'
    )
    return username, request.META.get('REMOTE_ADDR', 'unknown')


class AuditSecurityMiddleware:
    """Audita mutaciones y accesos denegados sin registrar datos sensibles."""

    MUTATING_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}

    def __init__(self, get_response):
        self.get_response = get_response

    def process_exception(self, request, exception):
        # La sesión también usa SQLite: su lectura puede fallar en el decorador
        # de autenticación, antes de entrar en progreso_importacion. No devolver
        # datos del lote ni eludir autenticación; solamente pedir reintento.
        sondeo = _es_sondeo_progreso_importacion(request)
        mutacion_api = request.path.startswith('/api/') and request.method in self.MUTATING_METHODS
        if (sondeo or mutacion_api) and es_bloqueo_sqlite(exception):
            datos = {
                'estado': 'PROCESANDO',
                'etapa': 'Esperando acceso a la base de datos',
                'mensaje': 'No vuelva a cargar el archivo; se reintentará consultar el avance.',
            } if sondeo else {
                'status': 'error',
                'code': 'BASE_DATOS_OCUPADA',
                'message': (
                    'La base de datos está ocupada por otra operación. '
                    'Espere unos segundos, actualice los datos y vuelva a intentar. '
                    'La operación no se repetirá automáticamente.'
                ),
            }
            response = JsonResponse(datos, status=503)
            response['Cache-Control'] = 'no-store, max-age=0'
            response['Retry-After'] = '2'
            return response
        return None

    def __call__(self, request):
        # process_exception no recibe excepciones de este middleware; hay que
        # tratar también aquí un bloqueo al leer o cerrar la sesión.
        try:
            respuesta_sesion = self._verificar_sesion(request)
        except OperationalError as exc:
            respuesta_sesion = self.process_exception(request, exc)
            if respuesta_sesion is None:
                raise
        if respuesta_sesion is not None:
            return respuesta_sesion
        if _es_sondeo_progreso_importacion(request):
            return self.get_response(request)

        response = self.get_response(request)
        username, ip_address = _request_identity(request)
        if request.method in self.MUTATING_METHODS:
            audit_logger.info(
                'user=%s ip=%s method=%s path=%s status=%s',
                username, ip_address, request.method, request.path, response.status_code,
            )
        if response.status_code == 403:
            security_logger.warning(
                'access_denied user=%s ip=%s method=%s path=%s',
                username, ip_address, request.method, request.path,
            )
        return response

    def _verificar_sesion(self, request):
        if request.user.is_authenticated:
            current = int(time.time())
            last_activity = request.session.get('_fg_last_activity')
            idle_timeout = max(
                60, int(settings.FIBERGENIUS_IDLE_TIMEOUT_SECONDS)
            )
            if (
                isinstance(last_activity, (int, float))
                and current - int(last_activity) > idle_timeout
            ):
                username, ip_address = _request_identity(request)
                security_logger.info(
                    'session_idle_timeout user=%s ip=%s seconds=%s',
                    username,
                    ip_address,
                    idle_timeout,
                )
                logout(request)
                if (
                    request.path.startswith('/api/')
                    or request.headers.get('X-Requested-With')
                    == 'XMLHttpRequest'
                ):
                    return JsonResponse(
                        {
                            'status': 'error',
                            'message': 'La sesión expiró por inactividad.',
                        },
                        status=401,
                    )
                return redirect(f'{settings.LOGIN_URL}?reason=inactive')

            touch_seconds = max(
                1, int(settings.FIBERGENIUS_IDLE_TOUCH_SECONDS)
            )
            if not _es_sondeo_progreso_importacion(request) and (
                not isinstance(last_activity, (int, float))
                or current - int(last_activity) >= touch_seconds
            ):
                request.session['_fg_last_activity'] = current

        return None


class ActiveUserMiddleware:
    """
    Middleware que registra la última actividad del usuario.
    Si el usuario está autenticado, actualiza (o crea) su registro en UserActivity
    con el timestamp de ahora mismo.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if _es_sondeo_progreso_importacion(request):
            return self.get_response(request)

        if request.user.is_authenticated:
            intervalo = max(
                30,
                int(getattr(settings, 'FIBERGENIUS_ACTIVITY_UPDATE_SECONDS', 180)),
            )
            cache_key = f'fibergenius:user-activity:{request.user.pk}'
            if cache.add(cache_key, True, timeout=intervalo):
                try:
                    UserActivity.objects.update_or_create(
                        user=request.user,
                        defaults={'last_activity': now()},
                    )
                except OperationalError as exc:
                    if not es_bloqueo_sqlite(exc):
                        raise
                    # Es telemetría auxiliar, no el control de inactividad.
                    # No impedir leer inventario por un bloqueo de esta escritura.
                    cache.delete(cache_key)

        response = self.get_response(request)
        return response
