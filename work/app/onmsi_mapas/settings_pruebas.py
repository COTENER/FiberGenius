"""Configuración aislada para validar las mejoras sin tocar RC1 ni PostgreSQL."""

import os
from pathlib import Path

from .settings import *  # noqa: F401,F403

SECRET_KEY = "fibergenius-rc2-pruebas-no-produccion"
DEBUG = True
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = ["http://testserver"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get(
            "FIBERGENIUS_TEST_DB",
            str(BASE_DIR / "fibergenius_rc2_pruebas.sqlite3"),
        ),
    }
}

ROOT_URLCONF = "onmsi_mapas.urls"
MEDIA_ROOT = Path(BASE_DIR) / "media_pruebas"
STATIC_ROOT = Path(BASE_DIR) / "staticfiles_pruebas"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

FIBERGENIUS_WEBHOOK_SECRET = "secreto-pruebas-webhook"
FIBERGENIUS_WEBHOOK_MAX_SKEW_SECONDS = 300
FIBERGENIUS_ALARM_PAGE_SIZE = 200

VEEX_ENABLED = True
VEEX_API_URL = ""
VEEX_API_USER = ""
VEEX_API_PASS = ""
VEEX_TLS_VERIFY = True
VEEX_CA_BUNDLE = ""

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
