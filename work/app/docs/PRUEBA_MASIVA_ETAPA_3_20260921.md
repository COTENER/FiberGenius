# Etapa 3 — Prueba masiva de principio a fin

Fecha: 21/09/2026. Resultado: **flujo completado, sin fallos inesperados de
integridad en el escenario ensayado**, con dos observaciones operativas
clasificadas al final de este informe.

## Alcance y aislamiento

Se usaron los endpoints HTTP reales de carga asíncrona y consulta de progreso,
con sesión autenticada y CSRF, sobre una copia SQLite independiente en
`outputs/stage3_20260921/isolated.sqlite3`. Se ejecutó Django runserver en
`127.0.0.1:8017`, sin autoreload. La sesión se preparó con un usuario administrador
de la copia; el login por contraseña y la matriz de roles pertenecen a la etapa 4.

La copia inicial contenía 16.992 fibras, 1.045 ODF, 46.602 puertos, 18.661
terminaciones, 359 troncales, 359 tramos y 2.126 asignaciones FibraTramo.
Se conservaron esos registros: sus valores originales se compararon mediante
huellas SHA-256, incluyendo auditorías y referencias de lote, antes y después.

**No se modificó la base activa, no se reinició Fiber Genius real y no se cambió
código de la aplicación, GUI, Dashboard, configuración productiva ni migraciones.**
El servidor temporal se cerró y se comprobó que el puerto 8017 dejó de escuchar.
No se hizo commit ni push.

## Volumen sintético añadido

| Elemento | Cantidad |
| --- | ---: |
| ODF de 48 puertos | 834 |
| Puertos creados automáticamente por capacidad | 40.032 |
| Troncales | 417 |
| Fibras globales | 20.000 |
| Terminaciones oficiales A/B | 40.000 |
| Tramos técnicos | 1.041 |
| Asignaciones físicas de fibra por tramo | 49.952 |

Distribución de fibras según recorrido: 5.024 en un tramo y 4.992 en cada grupo
de dos, tres y cuatro tramos. Los recorridos comienzan en su ODF A, mantienen
continuidad entre nodos y terminan en su ODF B. Los números F1–F48 se reutilizan
en troncales distintas, con códigos globales diferentes y relaciones explícitas.

Se conservaron 32 puertos libres al final. No se inventaron fibras ni posiciones
físicas para completar la capacidad sobrante de los tramos.

## Ejecución y controles

**22 trabajos HTTP, 68 controles durante el flujo y 23 verificaciones
independientes de solo lectura: todos los controles aprobados.**
Siete trabajos debían fallar deliberadamente; no son fallos inesperados.

El flujo incluyó:

1. Crear y recargar ODF, comprobando generación de puertos libres por capacidad.
2. Crear y recargar troncales.
3. Prevalidar 20.000 fibras sin persistir datos ni auditorías; aplicar el mismo
   archivo con su token de validación y volver a cargarlo.
4. Rechazar una condición inválida en la última fila, informando la fila.
5. Forzar un error SQL al insertar la fibra 1.000 de un lote válido, después de
   lotes anteriores de escritura; verificar rollback de inventario y auditoría.
6. Conectar los extremos A/B de las 20.000 fibras y recargar sus conexiones.
7. Rechazar un puerto repetido y un intento de mover un extremo existente hacia
   un puerto libre. Ambos archivos dejaron el inventario y la auditoría intactos.
8. Actualizar metadatos de 20.000 puertos y recargarlos sin cambiar conexiones
   ni estados de ocupación.
9. Crear y recargar recorridos de uno a cuatro tramos, sin asignaciones de
   fibras automáticas.
10. Importar y recargar las 49.952 asignaciones explícitas FibraTramo.
11. Rechazar una posición fuera de capacidad en la última fila y una posición
    física duplicada, con indicación de fila y sin cambios parciales.
12. Forzar un error SQL al final de la actualización de las 49.952 asignaciones;
    verificar que se restauran también los registros escritos anteriormente.

Los dos errores SQL se provocaron con triggers específicos de prueba en la
copia aislada. Se retiraron al terminar cada caso. No se introdujeron puntos de
fallo ni parches en el código de Fiber Genius.

La verificación independiente cotejó todas las identidades, no una muestra:

- Fibra global → troncal y número; estados globales y su procedencia.
- Fibra → extremos A/B → puerto → ODF → rack → sala → Site.
- Fibra → posiciones físicas → tramo → troncal, secuencia y continuidad.
- Capacidades y contadores almacenados frente a los registros reales.
- Metadatos, tildes, comas y saltos de línea conservados.
- Unicidad de extremos, puertos y asignaciones; ausencia de residuos fallidos.
- Auditorías de creación, asignación de troncal y asignación de tramos sin
  duplicación de esos eventos.
- `PRAGMA integrity_check` y `PRAGMA foreign_key_check` correctos.
- Fuentes de la aplicación idénticas a las utilizadas al iniciar la prueba.

## Tiempos observados

| Operación | Tiempo HTTP observado |
| --- | ---: |
| Crear 834 ODF y 40.032 puertos | 32,1 s |
| Prevalidar 20.000 fibras | 26,7 s |
| Aplicar 20.000 fibras | 29,7 s |
| Recargar las mismas fibras | 15,3 s |
| Conectar 40.000 extremos A/B | 111,7 s |
| Recargar las conexiones | 118,5 s |
| Actualizar metadatos de 20.000 puertos | 20,4 s |
| Crear 1.041 tramos | 95,1 s |
| Recargar los tramos | 116,2 s |
| Crear 49.952 asignaciones FibraTramo | 63,0 s |
| Recargar las asignaciones | 118,6 s |
| Detectar y revertir el fallo SQL al final del recorrido | 97,5 s |

Son tiempos locales de una ejecución, incluyendo comunicación y sondeo; no
constituyen un SLA ni una medición de IIS/PostgreSQL. No se midió la RAM máxima.

## Observaciones clasificadas

### 1. Progreso durante escrituras SQLite

Se registraron **60 sondeos con timeout/error de conexión**, con un límite de
cinco segundos por petición del instrumento. Los trabajos terminaron con el
resultado esperado y el sondeo recuperó su estado final. No se registraron
respuestas HTTP no-200 en los sondeos que sí devolvieron respuesta.

Esto no significa que el progreso sea continuo durante una carga grande.
Debe validarse disponibilidad y navegación con usuarios reales en la etapa 4,
y repetirse en PostgreSQL/IIS cuando estén disponibles. No se cambió a WAL ni
se alteró el motor para ocultar esta limitación.

### 2. Historial de recarga de fibras globales

La recarga de las 20.000 fibras no cambió datos de negocio ni creó fibras nuevas,
pero añadió **20.000 eventos ACTUALIZAR_METADATOS**, cuyo único campo fue
`lote_importacion`, y **5.000 RESTABLECER_ESTADO** por las filas que declaraban
explícitamente «Sin información».

Es el comportamiento actual: registrar nueva procedencia y restablecimientos
explícitos. No son fibras duplicadas ni una doble aplicación del mismo lote;
se subió un nuevo lote. Sin embargo, aumenta el volumen del historial incluso
cuando el resultado de negocio no cambia. Conviene decidir si se mantiene esta
granularidad o se reduce el ruido antes de realizar recargas frecuentes.
**No se modificó esa regla durante esta etapa.**

Las otras seis recargas ensayadas (ODF, troncales, terminaciones, metadatos de
puertos, tramos y asignaciones) conservaron también las auditorías previas.

## Evidencia y límites del cierre

`outputs/stage3_20260921/report.json` contiene los 22 trabajos, sus entradas y
hashes, sondeos, errores esperados, controles, conteos iniciales/finales y huellas
del código. `independent_checks.json` contiene las 23 verificaciones y el detalle
de auditoría. En esa carpeta están también los CSV sintéticos y el log del servidor.

Instrumentos: `.tmp-review/stage3_e2e.py` y `.tmp-review/stage3_verify.py`.
Sus hashes están incluidos en la verificación independiente. HEAD base:
`33f073ccda25641a1e0a26273dae8c049549d15a`, con los cambios locales anteriores;
el manifiesto SHA-256, y no solo el HEAD, identifica las fuentes ensayadas.

La copia incluye inventario preexistente y debe mantenerse privada. No publicar
la carpeta de evidencias ni la base de prueba en GitHub.

Quedan fuera: matriz de roles y sesiones, edición concurrente y recuperación
tras interrupción del servicio (etapa 4); revisión visual de todas las pestañas
(etapa 5); instalación offline e infraestructura de entrega (etapa 6);
regresión de la versión candidata final (etapa 7). PostgreSQL sigue pendiente.
