# Correcciones de la etapa 4 — 21 de septiembre de 2026

## Alcance

Corrección autorizada de E4-01 (error 500 ante bloqueos de SQLite) y E4-02
(consulta de progreso que omitía la expiración por inactividad), detectados en
`PRUEBAS_ETAPA_4_20260921.md`. Ese informe conserva los resultados anteriores.

No se cambiaron Dashboard, diseño, importadores, modelo de inventario ni esquema
de base de datos. No se ejecutaron cargas sobre la base real, migraciones,
reinicios del servidor habitual, commits ni push.

## Cambios implementados

### Bloqueos transitorios

- `services/errores_bd.py` clasifica exclusivamente `OperationalError` de SQLite
  por sus códigos BUSY/LOCKED, incluidos códigos extendidos, o sus mensajes
  conocidos cuando el controlador no expone código. No convierte errores de
  disco, errores de programación ni errores PostgreSQL en bloqueos SQLite.
- La edición de datos del puerto propaga el error fuera de `transaction.atomic`
  para revertir antes de responder. El manejador genérico también revierte
  cualquier escritura anterior a un fallo inesperado.
- La gestión de conexión propaga igualmente los errores operativos después de
  que los servicios atómicos reviertan.
- El middleware transforma los bloqueos conocidos de mutaciones API en HTTP
  503, código `BASE_DATOS_OCUPADA`, `Retry-After: 2` y `Cache-Control: no-store`.
  La interfaz existente muestra el mensaje: esperar, actualizar datos y volver
  a intentar. **No hay reejecución automática de escrituras.**
- Se mantiene el control de formulario desactualizado y el identificador de
  terminación esperada para desconectar.
- Si se bloquea exclusivamente la escritura auxiliar de `UserActivity`, se
  omite esa actualización. Esto no omite autenticación ni control de inactividad.
  Los errores no transitorios siguen propagándose.

El bloqueo del motor no desaparece: la operación puede necesitar un nuevo intento
del usuario. Lo corregido es el error genérico, el tratamiento de rollback y la
respuesta recuperable. No se generaliza el resultado a todas las mutaciones que
puedan tener manejadores específicos distintos de los ensayados.

### Inactividad durante el sondeo

- Se comprueba la sesión antes de la excepción de sondeo del middleware.
- Consultar progreso no renueva `_fg_last_activity` ni escribe actividad o
  sesión mientras esta siga vigente.
- Una sesión inactiva devuelve 401 y se cierra; el cliente existente indica que
  debe iniciar sesión de nuevo sin volver a cargar el archivo.
- Si SQLite bloquea la lectura/cierre de sesión, se devuelve 503 sin datos del
  lote. No se elude la autorización. Los errores de middleware se tratan allí
  explícitamente, porque `process_exception` solo cubre los de la vista.
- El lote aceptado sigue su curso aunque expire la sesión. La recuperación no
  depende de que el usuario mantenga abierto el navegador.

## Validación

- 87 pruebas focalizadas correctas, incluyendo 11 nuevas regresiones: rollback
  tras escritura, ausencia de repetición automática, errores no transitorios,
  telemetría, expiración, sondeo sin escrituras y continuidad del lote.
- Ensayo HTTP: **99/99 comprobaciones correctas**. En dos ediciones simultáneas
  se obtuvo 200/503, no 200/500. El sondeo inactivo devolvió 401, no 200.
- Interrupción y recuperación reales: 12.000 fibras recuperadas una sola vez;
  segunda ejecución sin cambios de datos ni auditoría.
- Verificación independiente: **24/24**, incluyendo integridad SQLite y todos
  los registros preexistentes de las tablas de inventario/auditoría examinadas.
- Prueba adicional durante escritura efectiva: **5/5**. La edición devolvió 503,
  la importación confirmó las 12.000 actualizaciones y conservó la conexión.
- JavaScript de progreso y recuperación: correcto.
- `manage.py check`: correcto; `makemigrations --check --dry-run`: sin cambios.
- Suite completa: **695 detectadas, 682 correctas, 13 omitidas (PostgreSQL),
  cero fallos**, 379,351 s de pruebas. Registro:
  `outputs/stage12/stage4-fix-full-tests.log`.

Los ocho tests específicamente SQLite se omiten si se ejecutan contra otro
motor; no constituyen validación PostgreSQL.

## Rendimiento y límites pendientes

**No se ha eliminado la espera de navegación durante escrituras masivas.** En
el nuevo ensayo las páginas respondieron HTTP 200, pero la más lenta tardó
29,156 segundos. Se estaban ejecutando otros ensayos en copias independientes,
por lo que no es una comparación de rendimiento bajo condiciones idénticas con
la medición previa de 12 segundos. Tampoco permite afirmar una mejora de latencia.

SQLite conserva su contención de escritura. No se cambió el modo de diario ni
se dividieron transacciones para aparentar mayor velocidad. La validación de
concurrencia y tiempos en PostgreSQL sigue pendiente, como se acordó.

No se ensayaron aquí IIS, tarea programada productiva, reinicio de Windows,
instalación offline o legibilidad visual: pertenecen a las siguientes etapas.

## Evidencias

- `outputs/stage12/stage4-fix-tests.log`: pruebas focalizadas.
- `outputs/stage12/stage4-fix-full-tests.log`: suite completa.
- `outputs/stage4_fix_20260921/report.json`: HTTP, permisos, sesión y recuperación.
- `outputs/stage4_fix_20260921/independent_checks.json`: verificación SQL.
- `outputs/stage4_navigation_fix_20260921/report.json`: escritura concurrente,
  códigos HTTP y latencias.
- `outputs/stage4_fix_20260921/final_manifest.json`: hashes del código final y
  resumen de la suite. El único cambio de código entre el ensayo HTTP y la suite
  final fue marcar los ocho tests específicos de SQLite para omitirlos en otro
  motor; el código funcional ensayado fue el mismo.

Los ensayos usaron nuevas copias SQLite y procesos propios en los puertos 8018
y 8019, detenidos al terminar. Las evidencias permanecen en directorios ignorados
por Git; no deben publicarse las copias de base de datos.
