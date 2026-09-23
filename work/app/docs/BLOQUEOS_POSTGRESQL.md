# Compatibilidad de bloqueos de inventario con PostgreSQL

## Alcance

Se adaptan las consultas identificadas que combinaban `select_for_update()`
con un `select_related()` hacia relaciones opcionales. No se cambia el motor
de la instalación actual, no se migran datos y no se modifican esquemas, CSV,
permisos ni pantallas.

PostgreSQL rechaza un `FOR UPDATE` aplicado al lado nullable de un `OUTER JOIN`.
Para estos casos se usa `select_for_update(of=('self',))`: las relaciones siguen
leyéndose, pero la consulta bloquea únicamente las filas del modelo principal.
No se excluyen filas sin troncal, usuario o extremos, porque son válidas en el
inventario. SQLite continúa ejecutando estos flujos sin añadir bloqueos de fila.

Referencia: [Django — select_for_update](https://docs.djangoproject.com/en/6.0/ref/models/querysets/#select-for-update).

## Consultas adaptadas

- Fibra y terminación en conexiones unitarias y masivas.
- Actualización masiva de fibras, sincronización de estado y asignación de troncal.
- Importación de fibras globales y asignaciones FibraTramo.
- Procesamiento atómico del lote con usuario opcional.
- Edición web de fibra y lectura de tramos existentes al crear un tramo.

Se conservan los bloqueos explícitos de ruta y los bloqueos de puertos/ODF que
ya existían. La lectura de una terminación no bloquea anticipadamente su puerto:
se sigue el orden explícito fibra, terminación y puertos por PK. No se han
introducido reintentos automáticos ni eliminado validaciones para ocultar errores.

También se corrige la comparación del estado persistido `OCUPADO` en la suite
de concurrencia PostgreSQL; antes usaba la etiqueta `Ocupado`.

## Validación y límites

`mapas.tests_bloqueos_relaciones` comprueba los flujos con relaciones opcionales,
las recargas y la conservación de datos sin troncal. Inspecciona el SQL compilado
y el alcance del bloqueo incluso cuando se ejecuta con SQLite, para que este
motor no oculte una regresión. Incluye un control que detecta deliberadamente
el patrón anterior incompatible.

Esas comprobaciones **no equivalen a ejecutar PostgreSQL ni prueban concurrencia
real**. Los escenarios de concurrencia están en
`mapas.tests_modelo_fibra_postgresql`; en SQLite se omiten explícitamente.

Validación local del 21/09/2026: 275 pruebas detectadas en la selección ampliada
de importaciones, GUI de inventario, fibras, conexiones, auditoría y eliminación;
262 ejecutadas correctamente y 13 omitidas por requerir PostgreSQL. Las nueve
pruebas nuevas de relaciones opcionales están incluidas. `manage.py check` y
`makemigrations --check --dry-run` terminaron sin incidencias ni migraciones
nuevas. No se ejecutó la suite completa en esta adaptación.

Antes de aprobar el despliegue PostgreSQL:

1. Instalar las dependencias productivas; `requirements.lock` ya declara el
   controlador PostgreSQL. No basta con `requirements.demo.txt`.
2. Preparar una instancia de pruebas y configurar el perfil protegido descrito
   en `CIERRE_MODELO_FIBRAS.md`. Nunca utilizar la base productiva como base de tests.
3. Ejecutar ambos módulos con `fibergenius.settings_postgresql_tests` y, después,
   la suite funcional completa sobre ese mismo motor.
4. Comprobar importación, recarga, reserva, conexión, movimiento y edición
   simultáneos; conservar cualquier evidencia de timeout o deadlock.

No se certifica una migración general a PostgreSQL solo por este ajuste: su
ejecución real, las migraciones y la instalación productiva siguen requiriendo
la aceptación del entorno de destino.
