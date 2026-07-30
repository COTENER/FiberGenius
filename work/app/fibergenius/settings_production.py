"""Production settings for the BHP Windows Server deployment."""
from pathlib import Path
import os
from decouple import Config, Csv, RepositoryEnv
from .settings import *  # noqa: F401,F403

ENV_FILE = os.environ.get(
    "FIBERGENIUS_ENV_FILE",
    r"C:\ProgramData\COTENER\FiberGenius\config\.env",
)
if not Path(ENV_FILE).exists():
    raise RuntimeError(f"Fiber Genius environment file not found: {ENV_FILE}")
fg = Config(RepositoryEnv(ENV_FILE))

SECRET_KEY = fg("SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = fg("ALLOWED_HOSTS", cast=Csv())
CSRF_TRUSTED_ORIGINS = fg("CSRF_TRUSTED_ORIGINS", cast=Csv())

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": fg("DB_NAME", default="fibergenius_bhp_prod"),
        "USER": fg("DB_USER", default="fibergenius_app"),
        "PASSWORD": fg("DB_PASSWORD"),
        "HOST": fg("DB_HOST", default="127.0.0.1"),
        "PORT": fg("DB_PORT", default="5432"),
        "CONN_MAX_AGE": fg("DB_CONN_MAX_AGE", default=60, cast=int),
        "OPTIONS": {"connect_timeout": 10},
    }
}

DATA_ROOT = Path(fg("DATA_ROOT", default=r"C:\ProgramData\COTENER\FiberGenius\data"))
LOG_ROOT = Path(fg("LOG_ROOT", default=r"C:\ProgramData\COTENER\FiberGenius\logs"))
STATIC_ROOT = Path(fg("STATIC_ROOT", default=r"C:\ProgramData\COTENER\FiberGenius\static"))
MEDIA_ROOT = Path(fg("MEDIA_ROOT", default=r"C:\ProgramData\COTENER\FiberGenius\media"))
STATIC_URL = "/static/"
MEDIA_URL = "/media/"

for directory in (DATA_ROOT, LOG_ROOT, STATIC_ROOT, MEDIA_ROOT):
    directory.mkdir(parents=True, exist_ok=True)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

ROOT_URLCONF = "fibergenius.urls_production"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SESSION_COOKIE_SECURE = fg("SESSION_COOKIE_SECURE", default=True, cast=bool)
CSRF_COOKIE_SECURE = fg("CSRF_COOKIE_SECURE", default=True, cast=bool)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_SSL_REDIRECT = fg("SECURE_SSL_REDIRECT", default=True, cast=bool)
SECURE_HSTS_SECONDS = fg("SECURE_HSTS_SECONDS", default=31536000, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = fg(
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False, cast=bool
)
SECURE_HSTS_PRELOAD = fg("SECURE_HSTS_PRELOAD", default=False, cast=bool)

GEO_BOUNDS = {
    "LAT_MIN": fg("GEO_LAT_MIN", default=-60.00, cast=float),
    "LAT_MAX": fg("GEO_LAT_MAX", default=-17.00, cast=float),
    "LON_MIN": fg("GEO_LON_MIN", default=-76.00, cast=float),
    "LON_MAX": fg("GEO_LON_MAX", default=-66.00, cast=float),
}

MAP_TILE_URL_LIGHT = fg("MAP_TILE_URL_LIGHT", default="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png")
MAP_TILE_URL_DARK = fg("MAP_TILE_URL_DARK", default="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png")
MAP_TILE_URL_SATELLITE = fg("MAP_TILE_URL_SATELLITE", default="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}")
MAP_TILE_ATTRIBUTION = fg("MAP_TILE_ATTRIBUTION", default="OpenStreetMap contributors / CARTO / Esri")

TEMPLATES[0]["OPTIONS"]["context_processors"].append("fibergenius.context_processors.deployment")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "[{asctime}] {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
        "application": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_ROOT / "django.log"),
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 10,
            "formatter": "standard",
            "encoding": "utf-8",
        },
    },
    "root": {"handlers": ["console", "application"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console", "application"], "level": "WARNING", "propagate": False},
        "mapas": {"handlers": ["console", "application"], "level": "INFO", "propagate": False},
    },
}

# VeEX integration. Production credentials are read only from the external env file.
VEEX_ENABLED = fg("VEEX_ENABLED", default=False, cast=bool)
VEEX_API_URL = fg("VEEX_API_URL", default="")
VEEX_API_USER = fg("VEEX_API_USER", default="")
VEEX_API_PASS = fg("VEEX_API_PASS", default="")
VEEX_TLS_VERIFY = fg("VEEX_TLS_VERIFY", default=True, cast=bool)
VEEX_CA_BUNDLE = fg("VEEX_CA_BUNDLE", default="")
FIBERGENIUS_WEBHOOK_SECRET = fg("FIBERGENIUS_WEBHOOK_SECRET", default="")
FIBERGENIUS_WEBHOOK_MAX_SKEW_SECONDS = fg("FIBERGENIUS_WEBHOOK_MAX_SKEW_SECONDS", default=300, cast=int)
FIBERGENIUS_ALARM_PAGE_SIZE = fg("FIBERGENIUS_ALARM_PAGE_SIZE", default=200, cast=int)

# ONMSI/OTDR integration. No production credentials are embedded in the package.
RUTA_CARPETAS_ENLACES = fg("RUTA_CARPETAS_ENLACES", default=str(DATA_ROOT / "enlaces"))
MEDICIONES_API_URL = fg("MEDICIONES_API_URL", default="")
MEDICIONES_API_USER = fg("MEDICIONES_API_USER", default="")
MEDICIONES_API_PASSWORD = fg("MEDICIONES_API_PASSWORD", default="")
EVENTOS_API_URL = fg("EVENTOS_API_URL", default="")
EVENTOS_API_USER = fg("EVENTOS_API_USER", default="")
EVENTOS_API_PASSWORD = fg("EVENTOS_API_PASSWORD", default="")
MEDICION_INDIVIDUAL_API_URL = fg("MEDICION_INDIVIDUAL_API_URL", default="")
MEDICION_INDIVIDUAL_API_USER = fg("MEDICION_INDIVIDUAL_API_USER", default="")
MEDICION_INDIVIDUAL_API_PASSWORD = fg("MEDICION_INDIVIDUAL_API_PASSWORD", default="")
ONMSI_TLS_VERIFY = fg("ONMSI_TLS_VERIFY", default=True, cast=bool)
ONMSI_CA_BUNDLE = fg("ONMSI_CA_BUNDLE", default="")
