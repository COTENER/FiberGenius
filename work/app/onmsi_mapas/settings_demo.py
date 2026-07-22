"""Configuración Docker para la demo RC2 con SQLite poblada."""

import os
from pathlib import Path

from .settings_pruebas import *  # noqa: F401,F403


def lista_env(nombre, valor_por_defecto):
    valor = os.environ.get(nombre, valor_por_defecto)
    return [item.strip() for item in valor.split(",") if item.strip()]


SECRET_KEY = os.environ["FIBERGENIUS_SECRET_KEY"]
DEBUG = os.environ.get("FIBERGENIUS_DEBUG", "false").lower() in {"1", "true", "yes"}
ALLOWED_HOSTS = lista_env(
    "FIBERGENIUS_ALLOWED_HOSTS",
    "localhost,127.0.0.1",
)
CSRF_TRUSTED_ORIGINS = lista_env(
    "FIBERGENIUS_CSRF_TRUSTED_ORIGINS",
    "http://localhost:8001",
)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("FIBERGENIUS_DEMO_DB", "/data/fibergenius.sqlite3"),
        "OPTIONS": {"timeout": 30},
    }
}

ROOT_URLCONF = "onmsi_mapas.urls_production"
STATIC_URL = "/static/"
STATIC_ROOT = Path(os.environ.get("FIBERGENIUS_STATIC_ROOT", "/data/static"))
MEDIA_ROOT = Path(os.environ.get("FIBERGENIUS_MEDIA_ROOT", "/data/media"))
RUTA_CARPETAS_ENLACES = os.environ.get("RUTA_CARPETAS_ENLACES", "/data/enlaces")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SESSION_COOKIE_SECURE = os.environ.get("FIBERGENIUS_SECURE_COOKIES", "true").lower() in {
    "1",
    "true",
    "yes",
}
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # RC1 contiene CSS de terceros que referencia imágenes opcionales ausentes.
    # La variante sin manifiesto sirve los archivos existentes sin bloquear el arranque.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
