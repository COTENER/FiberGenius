"""Clasificación limitada de bloqueos transitorios; nunca reejecuta escrituras."""
import sqlite3

from django.db import OperationalError, connection


def es_bloqueo_sqlite(exc):
    if connection.vendor != 'sqlite' or not isinstance(exc, OperationalError):
        return False
    codigo = getattr(exc.__cause__, 'sqlite_errorcode', None)
    if isinstance(codigo, int):
        return (codigo & 0xff) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    # Compatibilidad con versiones/controladores sin sqlite_errorcode.
    mensaje = str(exc).lower()
    return any(texto in mensaje for texto in (
        'database is locked', 'database table is locked', 'database schema is locked',
    ))
