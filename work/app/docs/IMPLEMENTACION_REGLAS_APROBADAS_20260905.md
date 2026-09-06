# Implementación de reglas aprobadas — 2026-09-05

## Alcance

Se implementaron las correcciones de lógica aprobadas en conversación. Este
documento actualiza el estado de las auditorías anteriores, no las reescribe.
En particular, la decisión posterior del usuario exige número de fibra único
dentro de la troncal: queda sustituida la alternativa previa de repetir F17
con distintos códigos dentro de la misma troncal.

No se cambió el diseño del Dashboard, mapas ni sus gráficos. Se preservaron
las modificaciones que ya existían en el árbol de trabajo. No se hizo commit,
push, limpieza del inventario ni renumeración de datos.

## Cambios realizados

| Regla | Implementación |
|---|---|
| Recorrido explícito | Cambiar estado global o asignar troncal ya no crea FibraTramo ni cambia su estado, incluso con un solo tramo. |
| Asignación válida | GUI con selección explícita y CSV de fibras por tramo siguen definiendo el recorrido. La inferencia ascendente desde detalle conocido se conserva. |
| Unicidad por troncal | Restricción `uq_fibra_ruta_numero_ci`, validación en servicios y errores en importadores. Sin troncal pueden existir números iguales con identidad distinta. |
| ODF atómico | Metadatos y capacidad se guardan en una sola transacción. Un ajuste fallido no deja cambios parciales. |
| Capacidad excepcional | GUI: administrador/superusuario y confirmación anterior/nueva. CSV: actor administrador para cambiar una capacidad existente. Admin Django remite cambios de capacidad/ubicación existentes al flujo confirmado. |
| Puertos reales | Contadores de Libre/Reservado/Ocupado desde registros reales. Alta normal de ODF crea 1…N. No se añadió un estado nuevo de puerto. |
| Numeración especial | El ajuste automático rechaza etiquetas especiales o colisiones como 1/01; no mezcla, renombra ni elimina esos puertos. Metadatos del ODF pueden seguir editándose. |
| Reducción segura | No retira posiciones con conexión, reserva, destino, patchcord, observaciones u otros datos relevantes. Audita aumentos/reducciones. |
| Carga progresiva | ODF sin ubicación completa usa la jerarquía controlada SIN ASIGNAR; puede completarse después conservando ODF y puertos. |
| Calidad separada | Ausencias de recorrido/ubicación son advertencias. Se distinguen de contradicciones entre ODF, Site y recorrido; no se certifica completitud verificada con topología desconocida. |
| Cambios parciales | CSV descriptivos no reescriben estados ni campos omitidos. Escrituras limitadas en Sites, OTU, rutas, IDs y reservas; bloqueos preservados/reforzados. |
| Edición GUI | Formularios de ODF, puertos, fibras y troncal envían cambios, comparan valores originales bajo transacción y rechazan conflictos sobre el mismo campo. |
| Precisión GUI | El editor de troncal recibe valores exactos separados del resumen redondeado. Los campos propios de un tramo no se editan como si fueran globales en una troncal multitramo. |
| Administración | Snapshot firmado del registro y guardado de campos editados. Terminaciones y FibraTramo conservan protección contra edición directa. |
| Auditoría | Movimientos/asignaciones de FibraTramo incluyen antes/después, GUI/Excel, usuario y lote cuando corresponde; recarga idéntica no crea otro movimiento. |
| Altas seguras | Alta manual repetida de Site no borra coordenadas. Coordenadas decimales se validan sin convertirlas antes a float. Error al crear ruta revierte también su alta parcial. |

La columna `fibra` del detalle por tramo mantiene su significado de posición
de ese tramo. La identidad global y la posición no se sustituyen mutuamente.
No se obliga a tener una troncal para crear la fibra ni su terminación.

Las operaciones explícitas de puertos continúan conectando, moviendo,
reservando y liberando mediante sus servicios. La protección de una importación
descriptiva no deshabilita esas acciones legítimas.

## Migración y datos locales

- Migración nueva: `0048_reglas_aprobadas_inventario`, dependiente de 0047.
- Verifica conflictos antes de añadir la restricción; no renumera ni borra fibras.
- Aplicada en `fibergenius_rc2_pruebas.sqlite3` después de respaldo SQLite consistente.
- Respaldo verificado:
  `backups/pre_reglas_aprobadas_20260905_215154_248365.sqlite3`.
- `PRAGMA integrity_check`: correcto, usando las funciones SQLite de Django.

Conteos antes y después, idénticos:

| Entidad | Cantidad |
|---|---:|
| Sites | 491 |
| ODF | 1.045 |
| Puertos ODF | 46.602 |
| Fibras globales | 16.992 |
| Tramos | 359 |
| Fibras por tramo | 2.126 |
| Terminaciones | 18.661 |
| Troncales | 359 |

## Verificación

- `manage.py check`: correcto.
- `makemigrations --check --dry-run`: sin cambios pendientes.
- Sintaxis JavaScript: cinco archivos comprobados con Node.
- Helper de edición: cuatro comprobaciones de parche, borrado explícito,
  conservación de omitidos y precisión numérica.
- Regresiones focalizadas: 84 pruebas correctas en el último grupo ejecutado.
- Suite completa de `mapas`: 451 pruebas descubiertas, 438 correctas y 13
  omitidas de PostgreSQL; resumen Django `OK (skipped=13)`, en 306,477 segundos.
  Evidencia local: `.tmp-review/suite_reglas_aprobadas.log`.

Las pruebas nuevas incluyen rutas con 1, 2 y 4 tramos, conflictos de edición,
rollback, permisos de capacidad, etiquetas especiales, unicidad en base de datos,
recarga idempotente, auditoría y renderizado de pantallas y administración.

## Límites y trabajo posterior

- PostgreSQL y concurrencia real quedan pospuestos por decisión del usuario.
  Los hooks de pruebas SQLite verifican escrituras limitadas, no sustituyen
  transacciones concurrentes reales ni certifican los joins de bloqueo en PG.
- El acceso al navegador integrado falló con `missing field sandboxPolicy`.
  Se verificaron renderizado y JavaScript, pero no se declara revisión visual
  interactiva realizada.
- Clientes anteriores de las API sin `_original` siguen siendo compatibles;
  la detección de formularios antiguos cubre los editores actualizados que
  envían esa referencia. No equivale a un versionado obligatorio universal de API.
- Edición de umbrales/monitorización, infraestructura y otros bloques ajenos
  quedan fuera. Los contadores se recalculan según sus servicios; no se ejecutó
  una limpieza ni regeneración masiva de puertos antiguos.
- Los resúmenes de capacidad conservan su compatibilidad visual legacy para
  troncales de un tramo sin detalle físico. Ese fallback no crea FibraTramo ni
  certifica su recorrido en la evaluación de calidad. Revisar su presentación
  aparte si se desea retirar también esa compatibilidad de los resúmenes.
- Un ajuste de capacidad por CSV es una operación explícita del archivo de un
  administrador, no el diálogo GUI de confirmación. La vista previa completa
  de cambios se propone para el siguiente bloque.
- Reorganización y mejora de importadores: ver
  `PROPUESTA_IMPORTADORES_20260905.md`. Propuesta, todavía no implementada.
