"""Comprobación de salud para demo y producción."""

import logging

from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return JsonResponse({"status": "ok", "database": "ok"})
    except Exception:
        logger.warning("La comprobación de salud no pudo consultar la base de datos", exc_info=True)
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
