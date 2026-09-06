"""Configuración aislada para validar las mejoras sin tocar RC1 ni PostgreSQL."""

import os
import tempfile
from pathlib import Path

import sys

from .settings import *  # noqa: F401,F403

# En Windows el directorio temporal global puede estar protegido o conservar
# bloqueos de procesos externos. Las pruebas deben trabajar en una ubicación
# aislada y controlada por el proyecto.
TEST_TEMP_ROOT = Path(BASE_DIR) / ".tmp_pruebas"
TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TEST_TEMP_ROOT)

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

ROOT_URLCONF = "fibergenius.urls"
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

# Las pruebas no abren archivos (evita bloqueos temporales en Windows). Cuando
# este perfil se usa para levantar el servidor local, conserva el LOGGING base
# y escribe normalmente en la carpeta ``logs``.
if "test" in sys.argv:
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"console": {"class": "logging.StreamHandler"}},
        "root": {"handlers": ["console"], "level": "WARNING"},
    }
