#!/bin/sh
set -eu

DB_PATH="${FIBERGENIUS_DEMO_DB:-/data/fibergenius.sqlite3}"
mkdir -p "$(dirname "$DB_PATH")" /data/static /data/media /data/enlaces

if [ ! -s "$DB_PATH" ]; then
    if [ -s /app/fibergenius_rc2_pruebas.sqlite3 ]; then
        cp /app/fibergenius_rc2_pruebas.sqlite3 "$DB_PATH"
        echo "SQLite de demostración copiada a $DB_PATH"
    else
        echo "Se creará una SQLite vacía en $DB_PATH"
    fi
fi

python manage.py migrate --noinput

if [ -n "${DJANGO_SUPERUSER_USERNAME:-}" ] && [ -n "${DJANGO_SUPERUSER_PASSWORD:-}" ]; then
    python manage.py createsuperuser --noinput 2>/dev/null || true
fi

python manage.py collectstatic --noinput --verbosity 0

exec waitress-serve --listen=0.0.0.0:8000 onmsi_mapas.wsgi:application
