"""Perfil explícito para ejecutar las pruebas de concurrencia en PostgreSQL."""

import os

from django.core.exceptions import ImproperlyConfigured

from .settings_pruebas import *  # noqa: F401,F403


if os.environ.get('FIBERGENIUS_ALLOW_PG_TESTS', '').strip() != 'YES':
    raise ImproperlyConfigured(
        'Las pruebas PostgreSQL requieren FIBERGENIUS_ALLOW_PG_TESTS=YES.'
    )

nombre = os.environ.get('FIBERGENIUS_TEST_PG_DATABASE', '').strip()
nombre_pruebas = os.environ.get(
    'FIBERGENIUS_TEST_PG_TEST_DATABASE',
    '',
).strip()
if not nombre or not nombre_pruebas:
    raise ImproperlyConfigured(
        'Defina FIBERGENIUS_TEST_PG_DATABASE y '
        'FIBERGENIUS_TEST_PG_TEST_DATABASE explícitamente.'
    )
if not nombre_pruebas.casefold().startswith('fg_test_'):
    raise ImproperlyConfigured(
        'FIBERGENIUS_TEST_PG_TEST_DATABASE debe comenzar por fg_test_.'
    )
if nombre.casefold() == nombre_pruebas.casefold():
    raise ImproperlyConfigured(
        'La base de conexión y la base temporal de pruebas deben ser distintas.'
    )
for variable in ('DB_NAME', 'POSTGRES_DB', 'FIBERGENIUS_PRODUCTION_DATABASE'):
    produccion = os.environ.get(variable, '').strip()
    if produccion and produccion.casefold() == nombre_pruebas.casefold():
        raise ImproperlyConfigured(
            f'La base de pruebas coincide con la base declarada en {variable}.'
        )

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': nombre,
        'USER': os.environ.get('FIBERGENIUS_TEST_PG_USER', 'postgres'),
        'PASSWORD': os.environ.get('FIBERGENIUS_TEST_PG_PASSWORD', ''),
        'HOST': os.environ.get('FIBERGENIUS_TEST_PG_HOST', '127.0.0.1'),
        'PORT': os.environ.get('FIBERGENIUS_TEST_PG_PORT', '5432'),
        'CONN_MAX_AGE': 0,
        'TEST': {
            'NAME': nombre_pruebas,
        },
    },
}
