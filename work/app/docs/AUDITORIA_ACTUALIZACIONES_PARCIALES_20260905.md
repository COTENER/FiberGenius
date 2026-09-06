# Auditoría transversal de actualizaciones — 2026-09-05

## Conclusión y alcance

Sí: la protección aprobada para el importador de puertos debe extenderse a los
otros caminos de edición del inventario. No todos tienen el mismo defecto.
Hay problemas reproducidos sin concurrencia, formularios con información antigua
y escrituras demasiado amplias que necesitan validación concurrente en PostgreSQL.

Revisión del código local actual, incluidas sus modificaciones sin commit.
Se inspeccionaron importadores oficiales, edición de Sites/ODF/puertos/fibras/rutas,
servicios de terminaciones, fibras y topología, señales, administración Django
y JavaScript de los editores. Inspección complementaria de OTU, IDs de rutas y
asignación de perfiles. No es una auditoría completa de seguridad, telemetría,
backups ni usuarios. Tampoco una verificación visual mediante navegador.

No se modificó código funcional, migraciones, Dashboard ni inventario cargado.
Se añadió este informe y un diagnóstico en `.tmp-review`, directorio ignorado.

## Evidencia ejecutada

Archivo: `.tmp-review/auditoria_actualizaciones_20260905.py`.
Ejecutado con `.venv/Scripts/python.exe` desde `work/app`.
Runner Django con `fibergenius.settings_pruebas`, base SQLite de pruebas en memoria,
migraciones aplicadas y base de pruebas destruida al terminar.

**10/10 pruebas de diagnóstico correctas.** Son aserciones sobre comportamientos
actuales: las que reproducen defectos no equivalen a pruebas de una corrección.
No se ejecutó la suite completa de la aplicación ni PostgreSQL real en esta revisión.

| Prueba | Resultado observado |
|---|---|
| 01 Crear Site con nombre existente y opcionales vacíos | Responde éxito y borra coordenadas/dirección previas. |
| 02 CSV de Sites, solo cambio de dirección | Conserva coordenadas: control positivo. |
| 03 Editor de troncal sin `hilos_reservados` | Un tramo sin detalle pasa de 3 reservados a 0. |
| 04 Editar troncal con error posterior | Devuelve HTTP 400 pero conserva el nuevo nombre de ruta. |
| 05 CSV de notas de puerto, cambio intercalado | Reescribe estado y patchcord antiguos, aunque no venían en el CSV. |
| 06 API de notas de puerto con campos limitados | Conserva reserva y patchcord: control positivo. |
| 07 Payload de fibra con estado antiguo del formulario | Revierte Ocupado a Disponible al guardar una nota. |
| 08 Mismo cambio de nota de fibra sin enviar estado | Conserva Ocupado: control positivo. |
| 09 CSV de reservas sin longitud de reserva | Reescribe longitud antigua tras un cambio intercalado. |
| 10 CSV de troncal cambiando OLT | Conserva distancia omitida: control positivo. |

Las pruebas 05 y 09 intercalan un cambio con hooks dentro de la misma transacción
para demostrar qué valores se escriben. No son dos conexiones concurrentes ni
demuestran temporización/bloqueos de PostgreSQL. La 07 reproduce una petición con
datos antiguos: el servidor no distingue un estado reenviado sin editar de un cambio
de estado intencional. El JavaScript actual envía el formulario completo.

## Hallazgos confirmados

### 1. Puerto CSV: no solo puede sobrescribir estado

`mapas/views/importacion.py:3841-3895` lee los puertos sin bloqueo y ejecuta
`bulk_update` con una lista fija: bandeja, estado, conector, patchcord, destino,
observaciones y lote. Aunque el archivo solo tenga observaciones, guarda también
los valores previamente leídos de los demás campos.

La corrección aprobada debe cubrir **todos los campos omitidos**, no solo estado.
Agrupar actualizaciones por conjunto de campos realmente informados permite
mantener rendimiento masivo sin reescribir toda la fila. Capacidad del ODF tampoco
pertenece a una actualización descriptiva de puertos.

### 2. Sites GUI: crear puede actualizar y borrar

`mapas/views/sites.py:26-45` usa `update_or_create(nombre=...)` con valores por
defecto vacíos/None. La pantalla presenta un formulario de creación, no una
confirmación para sustituir un Site existente.

Recomendación: si existe, informar que ya está registrado y dirigir a una edición
explícita. No convertir el intento de crear en un borrado de datos opcionales.
El importador oficial de Sites ya conserva columnas/celdas vacías y admite borrado
explícito de coordenadas por pares (`importacion.py:666-718`).

### 3. Troncales GUI: valores omitidos se convierten en vacíos o ceros

`mapas/views/inventario.py:2371-2452` actualiza numerosos campos con
`data.get(..., '')` o `data.get(..., 0)`, en vez de comprobar si se pidió cambiarlos.

Caso reproducido: una ruta de un tramo sin FibraTramo tiene 3 reservados; el
editor no envía `hilos_reservados` y el servidor guarda 0.
El JS conserva ocupados/libres en `routes-inventory.js:509`, pero no reservados.
La rama con detalle de fibras conserva/recalcula sus contadores: no afirmar que
el defecto afecte igualmente a todos los tramos.

Otro problema confirmado por lectura del JS: `reserva_km` es un campo editable,
pero también se guarda en `form.dataset.preserved` y se aplica **después** de leer
FormData (`routes-inventory.js:491-493,509`). El valor previo puede reemplazar
lo que el usuario acaba de escribir. No se hizo prueba visual de este caso.

### 4. Guardado parcial también en troncales

`update_ruta_manual` guarda Ruta antes de procesar/guardar el tramo. Captura errores
dentro de `@transaction.atomic` sin marcar rollback (`inventario.py:2478-2482`).
Un error de conversión posterior deja el nombre de la ruta cambiado aunque responda
400. Esto amplía el problema previamente reproducido en edición de ODF.

Recomendación: validar el cambio completo y ejecutar una operación atómica cuyo
error salga de la transacción antes de traducirse a una respuesta. No basta con
decorar la vista si luego se captura el error sin rollback.

### 5. Formularios antiguos: un bloqueo al guardar no conoce la intención

Los editores envían todos sus campos, no solo los modificados:

- Fibra: `static/js/fiber-inventory.js:527-536`.
- ODF: `static/js/odf-inventory.js:501-523`.
- Puerto: `static/js/planta-interna-paginada.js:1055-1074`.
- Troncal: `static/js/routes-inventory.js:491-509` (incluye valores no visibles).

Si se abre un formulario, se actualiza el registro desde otra pestaña/importación
y luego se guarda el formulario antiguo, el servidor puede interpretar los valores
antiguos como instrucciones nuevas. No exige dos usuarios ni dos escrituras
exactamente simultáneas. Reproducido con el estado global de una fibra.

El editor normal de puerto sí rechaza un estado diferente y solicita Gestionar
conexión (`inventario.py:1842-1861`): en ese caso evita corromper el estado, aunque
puede bloquear una edición de notas por un estado antiguo que el JS reenvió.

Solución: enviar solo cambios y, para campos modificados/operaciones sensibles,
comparar bajo transacción con el valor original leído o una revisión del registro.
Si cambió el mismo dato o una dependencia crítica, avisar y pedir recargar/revisar.
Cambios independientes pueden conservarse juntos. No bloquear registros mientras
un usuario tenga un formulario abierto.

### 6. Reservas/elementos: escritura completa sin bloqueo

`importacion.py:1533-1566`: preserva opcionales vacíos al preparar el parche, pero
consulta sin `select_for_update` y luego hace `save()` completo. Se reprodujo que
reescribe `reserva_m` antiguo cuando el CSV no lo había informado.

Corregir consulta/escritura y conservar el modo parche. No cambiar silenciosamente
el contrato de las columnas requeridas (Ruta, Nombre, Tipo, Latitud, Longitud).
Si el archivo las declara, actualmente son valores solicitados: el sistema no
puede deducir que el cliente solo quería cambiar el nombre.

## Cobertura del resto de los caminos

| Área / camino | Evaluación y alcance de la protección |
|---|---|
| ODF CSV | Prepara solo metadatos informados, pero `guardar_odf_normalizado` consulta sin bloqueo y guarda completo (`services/inventario.py:176-208`). Riesgo estático de sobrescritura, pendiente PG. Integrar con operación atómica de capacidad ya aprobada. |
| ODF GUI | Recarga con bloqueo y respeta algunos campos omitidos; persisten formulario completo, ajuste de capacidad aun sin cambio solicitado y guardado parcial ya identificado. |
| Fibras globales CSV | Usa parche y servicio de metadatos con campos limitados/bloqueo (`services/fibras.py:155-195`). Conservar esta separación. Quitar creación implícita de asignaciones al cambiar estado es un cambio aprobado aparte. |
| Fibras por tramo | Bloquea fibras/tramos/asignaciones, conserva estado y observaciones no informados (`importacion.py:2686-2797`). Posición y vínculo sí son parte explícita de esta carga; no deben prohibirse por esta regla. Pendiente concurrencia PG y auditoría de movimientos ya observada. |
| Terminaciones | Reutiliza servicios; conexiones modifican necesariamente terminación, ocupación y contadores. No suprimir esos efectos legítimos. Revisar listas de escritura/bloqueos masivos, especialmente joins opcionales, en PG. |
| Operaciones de puertos | Cambios explícitos, servicios y permisos: deben seguir pudiendo conectar, desconectar, reservar y cancelar reserva. No convertirlas en importaciones descriptivas. |
| Sites, OTU, datos generales de rutas CSV | Preservan opcionales y leen con bloqueo. Sus `save()` amplios no equivalen por sí solos al defecto del puerto: en PG el bloqueo y la lectura actual importan. Limitar escrituras como defensa y reducir bloqueos a objetos afectados; no reescribirlos innecesariamente. |
| Tramos técnicos y legacy | Respetan campos informados y bloquean tramos. Topología declarada sí se actualiza como parte de su contrato; señales pueden recalcular estados inferidos. No tratar toda recalculación como corrupción. Revisar política de efectos según campos cambiados. |
| Geometría CSV/ZIP | `_reemplazar_geografia_ruta` recarga Ruta bajo bloqueo, reemplaza coordenadas y guarda solo distancia/lote (`importacion.py:503-523`). Es reemplazo explícito de geometría, no parche de puntos: conservar contrato e informar su alcance. |
| IDs de rutas | Actualiza identidad ONMSI y enlace/lote; no es un editor general de Ruta. Consulta/guardado sin bloqueo: incluir endurecimiento de su operación, no afirmar pérdida de metadatos generales que no escribe. |
| Nodos | `resolver_nodo` bloquea y usa `update_fields` (`services/topologia.py:81-95`). Nombre suministrado modifica el nodo compartido, legítimo solo si esa es la intención; los fallbacks de códigos no deberían sustituir nombres descriptivos involuntariamente. Revisión de política, no nuevo defecto reproducido. |
| Sala/Rack | GUI/importación crean o resuelven ubicación por identidad. Administración Django permite edición; aplicar coherencia cuando una relocalización afecte ODF dependientes. No se probó una sobrescritura específica en estas entidades. |
| Django Admin | FibraTramo y TerminacionFibra están protegidas de edición directa. ODF, puerto, Site, Ruta, etc. usan guardado amplio y no hay control de formulario desactualizado. InventarioFibra tiene servicio de estado, pero guarda otros campos mediante `super().save_model`. No asumir que todo Admin usa ya el mismo contrato que GUI. |
| Perfiles asociados a Ruta | `views/proactivo.py:88-95` guarda Ruta completa para cambiar solo perfil; `views/umbrales.py:90` ya actualiza únicamente perfil. Unificar con escritura limitada. Riesgo estático, no PG reproducido. |
| Editor de umbrales | `views/umbrales.py:56-71` rellena parámetros omitidos con defaults al actualizar. Es un formulario completo; si se admite edición parcial debe separar creación de actualización y controlar versiones. Trabajo adyacente, sin cambiar ahora reglas de monitorización. |

Los bloqueos de los servicios no permiten certificar todavía concurrencia en PG.
La revisión anterior identificó `select_for_update().select_related(...)` sobre
relaciones opcionales; debe comprobarse/corregirse la granularidad de bloqueo sin
excluir fibras provisionales. SQLite no valida esos escenarios de PostgreSQL.

## Regla transversal recomendada

1. **Crear**: aplicar defaults y generar dependencias definidas (p. ej. puertos de un
   ODF nuevo). No convertir silenciosamente una creación duplicada en actualización.
2. **Actualizar datos**: escribir exclusivamente los campos solicitados. En cargas
   masivas, agrupar por conjunto de campos; un lote no necesita guardar todo el modelo.
3. **CSV vacío/omitido**: preservar según contrato actual. Borrado con marcador explícito
   donde ya existe. GUI: un campo realmente editado a vacío puede solicitar borrado;
   distinguirlo de campo ausente. No cambiar todas las semánticas de vacío de golpe.
4. **Operación funcional**: validar y guardar conjuntamente sus efectos necesarios.
   Conectar sí debe ocupar el puerto; editar una nota no debe conectar ni liberar.
5. **Formulario desactualizado**: comparar versión/valores originales de los campos
   modificados y dependencias críticas. Avisar de conflicto sin perder la edición
   del usuario. No añadir confirmaciones rutinarias a cada campo.
6. **Importación con valores antiguos declarados**: un parche no adivina antigüedad.
   Validación/previsualización debe explicar lo que se sustituye; una plantilla
   generada puede llevar revisión para detectar cambios posteriores. No imponer
   nuevos identificadores manuales ni columnas obligatorias sin acordarlo.
7. **Transacción y auditoría**: validar estado final, bloquear filas necesarias en orden
   compatible entre servicios, guardar y auditar como unidad. Un error no debe dejar
   media operación. Registrar lote/resultado fallido fuera del rollback del inventario.
8. **Derivados**: conservar recálculos correctos de contadores, topología y estados
   inferidos; evitar dispararlos indiscriminadamente por una nota. No alterar la
   prioridad del estado informado ni el diseño del Dashboard.

## Orden simple para corregir

1. Pérdidas sin concurrencia: Sites crear/editar, omisiones del editor de troncal,
   reserva editable sobrescrita por JS y rollback de Ruta/ODF.
2. Campos de escritura: puertos CSV, reservas, ODF y asignación de perfiles; revisar
   servicios masivos y Admin con el mismo criterio, preservando caminos ya correctos.
3. Formularios antiguos: cambios reales y detección de conflicto. Es un ajuste de
   comportamiento GUI que debe aprobarse; no exige rediseñar las pantallas.
4. Pruebas de regresión deseadas (invirtiendo diagnósticos de defectos), recargas
   idénticas, borrado explícito y errores por fila; después concurrencia real PG.

No se propone rehacer el modelo de fibras, eliminar importadores operativos, borrar
data o instalar un sistema de colas/bloqueos adicional. Antes de implementar se deben
cerrar los detalles funcionales; esta solicitud autorizó la verificación, no cambios.
