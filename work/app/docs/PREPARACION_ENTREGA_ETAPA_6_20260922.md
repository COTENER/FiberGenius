# Etapa 6 — Preparación de entrega

Fecha: 2026-09-22. Estado: preparación local realizada; **entrega productiva
y paquete offline completo pendientes de ensayo**.

## Alcance

Revisados configuración externa, perfiles, dependencias, entry point, scripts
Windows, tareas, logs, backup/restore y documentación. Sin cambios de inventario,
modelos, datos, Dashboard o mapa. Sin instalar servicios/tareas, actualizar el
entorno activo, aplicar migraciones ni hacer commit/push.

Se agregó `CHECKLIST_ENTREGA.md`: identificación del paquete, configuración,
permisos, temporales, almacenamiento, instalación, actualización, supervisión,
respaldo corporativo y ensayo de recuperación. Se enlazó desde la guía productiva.

## Corrección de la herramienta de instalación

El instalador offline antes comprobaba hashes del wheelhouse y compatibilidad
de dependencias, pero no que sus versiones coincidieran con el lock del código.
Ahora también ejecuta `scripts/check_dependencies.py` y rechaza diferencias.
Ese comando es de solo lectura: no carga Django/.env, no consulta bases y no
instala ni descarga. Puede usarse antes de aceptar una instalación.

No se modificó el lock ni se cambiaron versiones para hacer pasar la revisión.

## Diferencias del entorno local

Windows x64 / Python 3.12.13 es compatible con el runtime documentado, pero no
conforma todavía una instalación productiva del lock:

| Paquete | Lock | Instalado |
|---|---|---|
| Django | 6.0.8 | 6.0.7 |
| asgiref | 3.11.1 | 3.12.1 |
| certifi | 2026.5.20 | 2026.7.22 |
| charset-normalizer | 3.4.7 | 3.4.9 |
| numpy | 2.4.6 | 2.5.1 |
| sqlparse | 0.6.0 | 0.5.5 |
| tzdata | 2026.2 | 2026.3 |
| psycopg2-binary | 2.9.12 | Ausente |
| pywin32 | 312 | Ausente |

`pip check` no detecta incompatibilidades declaradas, pero la comparación exacta
devuelve código 1 por estas nueve diferencias. Son controles distintos.

No hay wheelhouse preparado en el proyecto. Faltan setuptools/wheel en el
entorno inspeccionado, y pg_dump/pg_restore/psql no están disponibles en PATH.
Esto no prueba que PostgreSQL no exista en otro directorio; no se buscó ni
modificó una instalación ajena. No se construyó ni certificó un paquete offline.

## Pruebas locales

- `mapas.tests_production_readiness` + `mapas.tests_backups`: 23 pruebas correctas.
- Incluyen configuración externa y su precedencia, cierre del módulo operativo,
  entry point, comparador de versiones, metadata/hash de wheels y backup/restore
  real sobre SQLite y archivos sintéticos en directorios aislados.
- Las comprobaciones de comandos PostgreSQL usan mocks: no cuentan como restore
  PostgreSQL real ni prueba de concurrencia.
- Los nueve scripts PowerShell pasan análisis sintáctico. No se registraron tareas.
- `pip check`: correcto. Control de versiones: rechazo esperado del entorno local.
- Avisos de pruebas: staticfiles_pruebas no recolectado y override de DATABASES
  en pruebas aisladas; no son fallos, ni acreditan collectstatic productivo.
- La suite completa final y el barrido sobre el paquete de entrega son etapa 7.

## Identificación y evidencia

Base Git: `33f073ccda25641a1e0a26273dae8c049549d15a`, rama
`agent/import-fibergenius-rc2-modelo-definitivo`. El árbol contiene cambios
previos y de esta preparación: **HEAD solo no identifica lo que se entregaría**.

`outputs/stage6_delivery_20260922/` conserva el resultado del comparador,
pruebas, análisis de scripts y manifiesto SHA-256 de fuentes seleccionadas.
Es una evidencia local de preparación, NO un instalador ni una versión publicada.
El commit definitivo y hash del paquete se fijarán después de la etapa 7.

## Pendientes para autorizar producción

1. Construir dependencias e instalar offline en un entorno separado con el lock.
   Repetir pruebas con esas versiones exactas antes de certificar la entrega.
2. PostgreSQL real: migraciones, traslado de datos si aplica, permisos,
   concurrencia, dump/restore y recuperación integral. Diferido a petición del usuario.
3. IIS/HTTPS, cuenta de servicio, ACL, TEMP/TMP, tareas sin sesión interactiva,
   reinicio de Windows, límites de carga y rotación de logs con varios procesos.
4. Configuración definitiva de BHP: DNS, volúmenes/capacidad, credenciales,
   respaldo externo, retención, RPO/RTO, responsables y red de las PCs cliente.
5. Regresión final, revisión del diff, congelar versión y aceptación de entrega.

No se presentan estos pendientes como resueltos por documentación, sintaxis
correcta o pruebas con mocks. La configuración real no se solicitó ni se expuso.
