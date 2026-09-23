# Etapa 4 — Usuarios, permisos y recuperación

Fecha: 21 de septiembre de 2026.

## Resultado

**Ensayo terminado, con dos defectos observados y una limitación de rendimiento.**
No se certifica todavía esta etapa como libre de observaciones.

- 147 pruebas automatizadas: correctas, ninguna omitida.
- Ensayo HTTP principal: 99 comprobaciones, 97 conformes y 2 no conformes.
- Verificación independiente de SQLite: 24 comprobaciones correctas.
- Ensayo adicional durante escritura efectiva: 5 comprobaciones, 4 conformes y
  1 no conforme; reproduce el mismo defecto de bloqueo, no un tercer defecto.
- JavaScript: `functional-recovery.cjs` terminó correctamente (153 aserciones de
  recuperación y 45 comprobaciones de mapas); `import-progress.cjs` también pasó.

Se ensayó SQLite, no PostgreSQL. No se modificaron código funcional, migraciones,
configuración real, Dashboard ni inventario de operación. Los usuarios y cambios
del ensayo existen solamente en copias aisladas. No se hizo commit ni push.

## Versión y aislamiento

HEAD: `33f073ccda25641a1e0a26273dae8c049549d15a`, con los cambios locales existentes.
Este hash por sí solo **no identifica todo el código probado**: `report.json`
incluye SHA-256 de los archivos versionados y no ignorados presentes al inicio.
La comparación al final confirmó que no habían cambiado.

Se copió mediante SQLite Backup la evidencia previa
`outputs/audit_20260921/audit.sqlite3`; no se abrió la base real para escritura.
El ensayo principal utilizó `outputs/stage4_20260921_run2/isolated.sqlite3` y el
puerto 8018. El adicional utilizó otra copia, bajo
`outputs/stage4_navigation_20260921`, y el puerto 8019.
Ambos servidores propios quedaron detenidos y sus puertos cerrados. El servidor
habitual no fue reiniciado ni detenido.

El primer intento, conservado en `outputs/stage4_20260921`, se interrumpió por una
lectura del instrumento de prueba coincidente con el reemplazo del JSON de
progreso en Windows. Se corrigió solamente el instrumento para reintentar los
errores transitorios de lectura y se ejecutó el ensayo completo en otra copia.
No se corrigió ni ocultó ningún fallo de la aplicación para obtener resultados.

## Roles y permisos comprobados

Los nombres de los grupos no otorgan privilegios por sí mismos. Se configuraron
tres perfiles de prueba mediante los permisos reales de Django:

| Perfil | Permisos del ensayo | Resultado |
|---|---|---|
| Administrador | Superusuario; gestión y lectura | Acceso a inventario, usuarios, grupos e importación; creación de ODF de cuatro puertos libres |
| Analista | Lectura de inventario, altas/cambios de troncales, edición de puertos y altas/cambios/bajas de terminaciones | Reserva, cancelación, conexión de fibra existente y desconexión permitidas; usuarios/grupos y creación de hilo sin permiso rechazados |
| Consulta | Lectura de inventario | Pantallas y consultas permitidas; escritura e importación rechazadas en el servidor |

Para el ensayo de recuperación se añadieron al analista, explícitamente y solo
en esa copia, `add_inventariofibra` y `change_inventariofibra`.
No se afirma que exista un perfil productivo universal llamado «Analista» con
esos permisos; la matriz de entrega debe asignarse según la responsabilidad real.

Se verificaron siete pantallas y cuatro APIs de consulta por cada perfil.
Consulta recibió HTTP 403 en las 15 entradas de importación registradas y en
las operaciones de creación de troncal/ODF, edición y gestión del puerto.
La comparación de todas las tablas `inv_*` y `aud_*` confirmó que los intentos
prohibidos no alteraron datos. La protección CSRF real rechazó un POST sin token.
La suite cubrió también las autorizaciones específicas de operaciones de puertos.

El progreso de un lote ajeno devolvió 404 a consulta; sin sesión, 302 al login;
administrador pudo consultarlo. No se expusieron credenciales en las evidencias.

## Sesiones y ediciones

- Login HTTP real con los tres perfiles y contraseñas sintéticas aleatorias.
- La expiración se simuló atrasando `_fg_last_activity` de sesiones de prueba,
  sin cambiar el reloj ni el tiempo de inactividad de la aplicación.
- Una página con sesión inactiva redirigió al login con `reason=inactive`.
- Una edición con sesión inactiva devolvió 401 y no cambió el puerto.
- El progreso con esa misma condición todavía devolvió 200: observación E4-02.
- Una edición basada en valores antiguos devolvió 400 y conservó la edición
  anterior válida.
- Una desconexión con identificador de terminación antiguo devolvió 409 y
  conservó la nueva fibra conectada.
- Dos ediciones HTTP simultáneas devolvieron 200 y 500, respectivamente. Solo
  quedó el cambio ganador, pero el rechazo no fue controlado: E4-01.

## Interrupción real y recuperación del mismo lote

Archivo sintético de 12.000 fibras globales. Lote:
`6fcf8156-6a65-48ef-977c-6fc12432f406`.

1. Se subió el archivo por HTTP con el usuario analista.
2. Se terminó el árbol del proceso de prueba después de que el progreso indicara
   1.000 filas procesadas en fase de escritura (67 %), antes del commit.
3. Al reabrir la base, el lote estaba `PENDIENTE`, el archivo seguía disponible y
   todas las tablas de inventario y auditoría coincidían exactamente con el
   snapshot tomado antes del envío. No quedaron las primeras 1.000 fibras.
4. Se reinició únicamente el servidor aislado, se ensayó la expiración del
   usuario que había enviado el archivo y se ejecutó el comando existente
   `procesar_importaciones`, respetando su antigüedad mínima real de 30 segundos.
5. El mismo lote terminó `COMPLETADO`: exactamente 12.000 fibras diferentes y
   12.000 eventos `CREAR_FIBRA`, sin tener que enviar otra carga.
6. Una segunda ejecución atendió cero lotes. No cambió ningún dato, auditoría ni
   campo del lote confirmado. El archivo temporal había sido retirado después
   del commit. Al volver a iniciar sesión se pudo consultar el resultado.

La comprobación SQL independiente confirmó integridad SQLite, ausencia de
duplicados de las 12.000 identidades y conservación exacta de todos los registros
preexistentes de las 21 tablas de inventario/auditoría examinadas.

Esto valida el comando de recuperación en el ensayo; **no demuestra que la tarea
programada de Windows esté instalada o funcionando con la cuenta productiva**.
Ese punto corresponde a preparación de entrega.

## Navegación durante escritura masiva

Además de navegar durante la ejecución del worker, se hizo un segundo ensayo
que esperó explícitamente la fase de escritura: 1.000 de 12.000 actualizaciones,
67 %, antes de iniciar las peticiones HTTP concurrentes.

Las siete páginas y cuatro consultas devolvieron 200. Sin embargo, el Dashboard
tardó 12,344 s y las pantallas de troncales/ODF/puertos aproximadamente 8–9 s.
Una edición del puerto devolvió 500 por bloqueo de la base en 0,469 s.
La importación confirmó las 12.000 actualizaciones y conservó estado y
terminación oficial del puerto.

No son mediciones de IIS ni de PostgreSQL, ni una garantía de fluidez. Confirman
la limitación de SQLite observada también en las pruebas masivas de la etapa 3.

## Observaciones y corrección recomendada

### E4-01 — Conflicto de escritura SQLite devuelve HTTP 500

Reproducido con dos editores y con edición durante una importación. El log sitúa
el fallo en `update_detalle_puerto`, al guardar el modelo; SQLite informa
`database is locked` y el manejador genérico devuelve 500.

**Impacto:** operación rechazada con un mensaje genérico y posible confusión del
usuario. No se observó pérdida de datos ni corrupción de conexiones.

**Recomendación:** distinguir los bloqueos transitorios, revertir la operación y
devolver una respuesta controlada (por ejemplo, 503 con indicación de reintento),
conservando la protección contra formularios desactualizados. No repetir
automáticamente movimientos o importaciones desde el navegador. Revisar el
tratamiento compartido de las mutaciones antes de aplicar una solución general.
PostgreSQL debe validarse aparte; no se da por resuelto por cambiar de motor.

### E4-02 — El sondeo omite el control de inactividad

`AuditSecurityMiddleware.__call__` devuelve anticipadamente para el endpoint de
progreso, antes de evaluar `_fg_last_activity`. La exclusión evita escrituras
auxiliares durante cargas, pero también omite verificar la inactividad.

**Impacto:** la consulta del progreso propio sigue disponible después del límite
de inactividad hasta que otra petición aplique el cierre o venza la sesión por
su mecanismo normal. No permite editar ni consultar lotes ajenos en lo ensayado.

**Recomendación:** separar «no actualizar actividad por sondeo» de «no comprobar
expiración». Mantener la comprobación de autenticación/propietario y el manejo
de bloqueos sin hacer que el sondeo renueve indefinidamente la sesión. La carga
ya aceptada debe continuar y conservar su resultado aunque expire la sesión.

### Rendimiento — Esperas de navegación bajo SQLite

Las respuestas HTTP correctas no significan navegación instantánea. Registrar
estas esperas como limitación del entorno y repetir la prueba en PostgreSQL.
No introducir ahora un cambio de base de datos ni sacrificar la atomicidad para
acelerar artificialmente la demostración.

## Evidencias y pendientes

- `outputs/stage12/stage4-tests.log` y `stage4-tests.json`: 147 pruebas.
- `outputs/stage4_20260921_run2/report.json`: roles, códigos HTTP, checks,
  hashes de código y progreso observado al interrumpir.
- `outputs/stage4_20260921_run2/independent_checks.json`: 24 comprobaciones SQL
  y hashes de los instrumentos del ensayo principal.
- `outputs/stage4_20260921_run2/worker-first.log` y `worker-second.log`: una
  recuperación y posteriormente cero lotes atendidos.
- `outputs/stage4_navigation_20260921/report.json` y `server.log`: navegación
  y edición durante escritura efectiva.
- Instrumentos locales: `.tmp-review/stage4_e2e.py`, `stage4_verify.py` y
  `stage4_navigation.py`; permanecen ignorados junto con las evidencias privadas.

Queda pendiente corregir y repetir E4-01/E4-02 con autorización, validar
concurrencia real en PostgreSQL y probar la tarea instalada bajo la cuenta de
servicio. No se realizó inspección visual de navegador, tamaños móviles, IIS,
reinicio de Windows ni restauración de backups: no forman parte de este ensayo.
