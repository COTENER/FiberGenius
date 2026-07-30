from django.utils.timezone import now
from django.conf import settings
from django.core.cache import cache
from .models import UserActivity

class ActiveUserMiddleware:
    """
    Middleware que registra la última actividad del usuario.
    Si el usuario está autenticado, actualiza (o crea) su registro en UserActivity
    con el timestamp de ahora mismo.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            intervalo = max(
                30,
                int(getattr(settings, 'FIBERGENIUS_ACTIVITY_UPDATE_SECONDS', 180)),
            )
            cache_key = f'fibergenius:user-activity:{request.user.pk}'
            if cache.add(cache_key, True, timeout=intervalo):
                UserActivity.objects.update_or_create(
                    user=request.user,
                    defaults={'last_activity': now()},
                )

        response = self.get_response(request)
        return response
