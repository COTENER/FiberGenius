# Etapas 1 y 2: checkpoint, regresión y rendimiento

Fecha: 21 de septiembre de 2026.

## Alcance y protección

Trabajo sobre la rama `agent/import-fibergenius-rc2-modelo-definitivo`, HEAD
`33f073ccda25641a1e0a26273dae8c049549d15a`. Se conservaron los cambios anteriores
del working tree; no se hizo commit, push, reset ni restauración de archivos.

Checkpoint previo verificado en:
`C:\VS-Fiber_genius\outputs\checkpoint-stage12-20260921-160128`.
Incluye un bundle de Git, parche binario, ZIP de 421 archivos y manifiesto SHA-256.
Se verificó el bundle y cada archivo del ZIP. Es una copia de **código**; excluye
archivos ignorados, base de datos y configuración privada, y no reemplaza el
backup operativo de datos/configuración.

Las mediciones utilizaron copias aisladas de SQLite bajo `outputs/stage12`.
La suite utilizó bases temporales. No se modificó el inventario activo, no se
aplicaron migraciones en él y no se reinició el servicio.

## Cambios de esta etapa

- `services/importacion_tramos.py`: persistencia por lotes de asignaciones
  explícitas FibraTramo. Validación del modelo, capacidad, relación con la
  troncal y conflictos de posiciones antes de escribir; restricciones SQL
  activas. Lotes de actualización y auditoría limitados preventivamente a 50
  filas, con prueba que impone un máximo de 999 parámetros por consulta.
  Auditoría y contadores calculados explícitamente en la misma
  transacción. La inferencia global sigue usando el servicio canónico y no
  reemplaza estados INFORMADO. No crea fibras, rutas ni terminaciones.
- `views/importacion.py`: reutiliza ese servicio y divide consultas de códigos
  en lotes. Conserva formato CSV, prevalidación, asignación explícita de troncal
  ya existente, modo parche, resultados e atomicidad del archivo completo.
- `views/operacion_inventario.py`: exporta puertos mediante proyección de
  columnas de las relaciones oficiales, evitando construir objetos no usados.
  La exportación de fibras omite URL de detalle y trazabilidad de GUI, que no
  forman parte del archivo. Conserva filtros, orden, columnas y protección de
  fórmulas. No cambia la serialización normal de la GUI.
- `tests_rendimiento_inventario.py`: 16 pruebas nuevas de paridad funcional,
  conflictos, capacidad, auditoría, rollback, recarga, estados y consultas.
- `tests_bloqueos_relaciones.py`: la prueba de FibraTramo exige que se bloqueen
  las posiciones, sin exigir un LEFT JOIN que el nuevo guardado no necesita.
  Se conserva el guard que exige `OF self` si existen relaciones opcionales.

No hay cambios de modelos/migraciones, plantillas, JavaScript, CSS ni diseño del
dashboard en esta etapa. El cotejo contra el manifiesto previo confirmó que,
entre los archivos preexistentes, solo cambiaron las dos vistas indicadas y la
comprobación de bloqueos descrita.

## Medición local antes/después

Mismo script, copia inicial, volumen y configuración; suite pesada detenida
durante ambas mediciones. Una ejecución comparativa por versión, por lo que los
tiempos son orientativos, no un SLA ni una medición de concurrencia. Se midió el
backend completo de exportación/consumo del archivo, no navegador, red o IIS.

| Operación | Volumen | Antes | Después | Consultas antes → después |
| --- | ---: | ---: | ---: | ---: |
| Importar FibraTramo | 1.900 asignaciones / 1.000 fibras | 51,327 s | 4,754 s | 49.746 → 6.079 |
| Recargar el mismo archivo | 1.900 asignaciones | 56,800 s | 5,623 s | 45.596 → 3.806 |
| Exportar puertos CSV | 46.602 puertos | 12,029 s | 2,560 s | 48 → 1 |
| Exportar puertos Excel | 46.602 puertos | 23,020 s | 14,755 s | 48 → 1 |
| Exportar fibras CSV | 16.992 fibras | 15,715 s | 10,995 s | 41 → 41 |
| Exportar fibras Excel | 16.992 fibras | 24,561 s | 23,554 s | 41 → 41 |

También se midieron ODF (1.045), troncales (359) y tramos (359), CSV/Excel: entre
0,305 y 1,032 s después, excepto ODF CSV, 0,260 s. Sus generadores no se cambiaron;
las variaciones en estas operaciones breves no se atribuyen a optimización.

La medición final de FibraTramo se repitió en un proceso nuevo después del ajuste
de tamaño de lotes; no realizó primero las exportaciones como la medición base.
No se atribuye toda la variación del tiempo o de RAM exclusivamente al código:
la disminución de consultas y la paridad de resultados son verificaciones más
estables. La primera medición optimizada, con la misma secuencia completa,
registró 6,811 s para crear y 15,101 s para recargar.

La RAM observada corresponde al proceso Python real: pico aproximado de
165,1 MiB antes y 156,8 MiB después en las operaciones no perfiladas. El proceso
se reutilizó entre operaciones: no son mediciones independientes por archivo.
Los perfiles de diagnóstico se ejecutaron aparte y no se usan para comparar
tiempos. Excel de fibras sigue costando unos 24 segundos; no se presenta como
instantáneo ni como una mejora de velocidad significativa.

## Equivalencia y pruebas

- Los cinco CSV coinciden byte a byte antes/después.
- Los cinco Excel conservan exactamente el contenido interno `xl/*`, incluidas
  hojas, valores y formatos. Se excluyen timestamps del contenedor y fecha de
  creación del documento.
- Coinciden asignaciones físicas, identidad global, estados/orígenes,
  contadores y auditoría. Se cotejó también el contenido completo de eventos
  de auditoría, excluyendo solo ID del evento y fecha de creación.
- El lote incluye troncales con 1, 2 y 3 tramos; estados físicos mixtos y fibras
  con estado global INFORMADO y no informado. La recarga no duplica datos ni
  eventos.
- Las pruebas nuevas cubren rechazo de posiciones ocupadas por otra fibra,
  duplicados, capacidad/formato, renumeración conservando identidad, reutilizar
  una posición física sin fibra, campos omitidos/limpieza explícita, error SQL
  y fallo posterior a la escritura con rollback de filas y auditoría.
- Suite inicial: 668 detectadas, 655 correctas y 13 omitidas por PostgreSQL.
- Pruebas focalizadas después: 92 correctas.
- Pruebas finales de límites SQL y bloqueos: 25 correctas.
- Primera suite posterior: 684 detectadas, 670 correctas, 13 omitidas y un fallo
  en la comprobación que exigía un LEFT JOIN, no en el resultado del importador.
  Se ajustó la prueba manteniendo la exigencia de bloqueo y se repite la suite.
- Una ejecución paralela intermedia detectó 2.500 parámetros en un lote de
  auditoría al incorporar el nuevo límite de 999. Se redujo ese lote y se
  verificó en las 25 pruebas anteriores. El runner paralelo no pudo transportar
  el traceback de ese fallo entre procesos; no se toma esa ejecución como válida.
- Suite completa final (`cierre-tests.log`, dos procesos aislados): **684
  detectadas, 671 correctas, 13 omitidas por PostgreSQL, cero fallos/errores**.
  Tiempo de pruebas: 355,323 s; ejecución total: 376,56 s.
- Los 11 scripts JavaScript existentes pasan ejecutados individualmente. El
  lanzador paralelo `node --test` fue bloqueado por el sandbox (`spawn EPERM`);
  se ejecutaron directamente sin necesidad de cambiar el código.
- `manage.py check`: correcto. `makemigrations --check --dry-run`: sin cambios.
  `git diff --check`: sin errores de whitespace.

## Evidencia local y límites

`outputs/stage12` conserva logs/JSON de pruebas, reportes `before`, `after` y
`after-final` (medición final de FibraTramo),
archivos exportados y `comparison.json` con los 15 controles de paridad.
Estos artefactos contienen datos de inventario y no deben publicarse en GitHub.

Quedan fuera de este cierre: pruebas contra PostgreSQL real y su concurrencia,
carga HTTP simultánea completa a volumen productivo, tiempos/límites de IIS,
validación del servidor definitivo, conectividad corporativa y restauración en
el entorno de entrega. No se certifica producción únicamente con estas pruebas.
