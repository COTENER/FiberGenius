# Cierre operativo del modelo de fibras

## Condición previa para datos históricos

La procedencia de los estados históricos no se infiere ni se inventa. La nueva
lógica no debe utilizarse operacionalmente sobre el inventario histórico antes
de ejecutar este procedimiento controlado:

1. Detener cargas e importaciones.
2. Generar y verificar un backup completo.
3. Aplicar el código y las migraciones.
4. Limpiar el inventario de fibras con el procedimiento controlado aprobado.
5. Recargar el inventario BHP normalizado.
6. Ejecutar el motor de calidad.
7. Validar totales, estados, terminaciones y cobertura contra la fuente BHP.

La migración `0043_coherencia_estado_fibra` únicamente degrada combinaciones
históricas incoherentes `estado conocido / NO_INFORMADO` a
`Desconocido / NO_INFORMADO`. No crea una procedencia `MIGRADO` ni realiza un
backfill interpretativo.

## Fuente oficial e invariantes

- El estado informado por una persona o importación autorizada siempre tiene
  prioridad sobre la inferencia de tramos.
- `NO_INFORMADO` solo es válido con estado global `Desconocido`.
- La relación física ODF es `TerminacionFibra -> Puerto -> ODF -> Rack -> Sala
  -> Site`.
- Una ruta de exactamente un tramo materializa obligatoriamente el mismo hilo
  en `FibraTramo`; si no es posible, toda la asignación de ruta se revierte.
- Las importaciones BHP no mueven terminaciones ni consumen reservas de forma
  implícita.
- El importador histórico de puertos permanece retirado (`HTTP 410`) y exige
  permisos de alta y cambio incluso para alcanzar esa respuesta.

## Suite PostgreSQL real

Las pruebas de concurrencia no son representativas en SQLite. Deben ejecutarse
contra una base PostgreSQL desechable, nunca contra producción:

```powershell
$env:FIBERGENIUS_TEST_PG_DATABASE='fibergenius_test_source'
$env:FIBERGENIUS_TEST_PG_TEST_DATABASE='fg_test_fibergenius_modelo_fibras'
$env:FIBERGENIUS_ALLOW_PG_TESTS='YES'
$env:FIBERGENIUS_TEST_PG_USER='postgres'
$env:FIBERGENIUS_TEST_PG_PASSWORD='<secreto>'
$env:FIBERGENIUS_TEST_PG_HOST='127.0.0.1'
$env:FIBERGENIUS_TEST_PG_PORT='5432'
$env:DEBUG='false'
.\.venv\Scripts\python.exe manage.py test `
  mapas.tests_modelo_fibra_postgresql `
  --settings=fibergenius.settings_postgresql_tests --noinput
```

El perfil crea/usa como base de prueba el nombre indicado en
`FIBERGENIUS_TEST_PG_TEST_DATABASE`. El nombre es obligatorio, debe comenzar
por `fg_test_`, debe ser distinto de la base de conexión y no puede coincidir
con `DB_NAME`, `POSTGRES_DB` ni `FIBERGENIUS_PRODUCTION_DATABASE`. El usuario
debe tener permisos para crear, migrar y eliminar exclusivamente esa base de
pruebas.

La suite cubre estado informado contra inferencia, dos conexiones al mismo
puerto, dos asignaciones de la misma ruta/hilo, movimientos cruzados,
importación concurrente con edición y constraints críticos.

## Orden de bloqueos y deadlocks

Las conexiones y movimientos bloquean primero la fibra y su terminación, y
después todos los puertos afectados en orden ascendente de clave primaria. Las
asignaciones de ruta bloquean fibra y ruta. Este orden elimina el ciclo conocido
de movimientos cruzados. Si PostgreSQL reporta un deadlock nuevo, debe
conservarse el log y reproducirse antes de introducir reintentos automáticos.

## Pendiente separado

Las tres pruebas históricas de presupuesto de consultas del Dashboard/mapa
continúan fuera de este alcance. No se modificaron Dashboard, KPIs, gráficos,
mapa, middleware ni sus límites de consultas para cerrar el modelo de fibras.
