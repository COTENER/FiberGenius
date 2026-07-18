from django.utils.timezone import now
from django.contrib.auth.models import User
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
            # Usar update_or_create para manejar usuarios que no tienen actividad previa.
            UserActivity.objects.update_or_create(
                user=request.user,
                defaults={'last_activity': now()}
            )

        response = self.get_response(request)
        return response
