# Auditoría de lógica de Fiber Genius — 5 de septiembre de 2026

## Resultado y alcance

La arquitectura permite seguir trabajando sobre ella, pero todavía no puede calificarse como cerrada o plenamente consistente. Los problemas principales son reglas distintas entre GUI, importadores y servicios, efectos secundarios al cambiar estados y comprobaciones incompletas de calidad.

Se revisó el código local, incluidos sus cambios sin commit, sobre HEAD `6fb78d3fa75a29dd2721d27afc1d97207b91387f`. Las conclusiones no describen solamente ese commit ni certifican la versión remota. No se modificó código de aplicación, Dashboard, mapa ni datos operativos. Se añadieron este informe y un script de diagnóstico aislado.

La GUI se revisó mediante sus vistas, JavaScript y pruebas de endpoints; no hubo recorrido visual del navegador en esta auditoría. No se auditó exhaustivamente seguridad, backup, despliegue ni integraciones OTDR. Las comprobaciones funcionales se ejecutaron con SQLite desechable, no con PostgreSQL productivo.

## Arquitectura que existe realmente

| Componente | Función actual | Observación |
|---|---|---|
| InventarioFibra | Identidad global, referencia de hilo, ruta opcional, estado informado/inferido y condición | La referencia no identifica por sí sola una fibra. Puede existir antes de conocer su ruta. |
| FibraTramo | Relación de una fibra global con un tramo, posición y estado local | Unicidad por tramo/posición y por tramo/fibra. No duplica la identidad global. |
| TerminacionFibra | Relaciona A o B con un puerto | Es la fuente oficial de conectividad ODF. |
| Puerto → ODF → Rack → Sala → Site | Ubicación física | La ubicación pendiente usa una jerarquía especial SIN ASIGNAR en el importador. |
| Ruta → InventarioTramo → NodoRed | Recorrido técnico ordenado | Hay validaciones de continuidad en vistas/importaciones y triggers de base de datos. |
| CoordenadaRuta | Trazado geográfico | Dibujar una línea no demuestra continuidad de fibras. |
| Reserva / EmpalmeFibra | Elementos externos y relaciones de empalme | La existencia de un empalme no convierte automáticamente la vista A/B en un trazador de circuitos completos. |

El código de capacidad documenta InventarioFibra como hilo lógico y FibraTramo como presencia física por tramo. Por eso no es riguroso afirmar que `fibra_numero` sea siempre un número físico original confirmado: actualmente es una referencia y su significado depende de la información cargada. Dos referencias F17 no prueban por sí solas que existan dos cables físicos.

## Hallazgos prioritarios

### 1. Cambiar el estado puede fallar con una posición válida distinta de la referencia — alta

`services/fibras.py:140` llama a `_preparar_tramo_unico`, que en la línea 606 usa `numero_hilo or fibra.fibra_numero`. Una fibra con referencia F12 y posición F19 es válida para el importador, pero cambiar su estado a Ocupado provoca «asignación física contradictoria».

Reproducción: prueba diagnóstica 01. El cambio se revierte; no hay pérdida de datos en este caso, pero queda bloqueada una operación legítima. También afecta a conexiones/desconexiones cuando se solicita cambiar el estado global.

Corrección mínima: si existe FibraTramo, resolverlo por identidad de fibra y tramo y conservar su posición. Una operación de estado no debe recalcular la numeración.

### 2. Recargar estados globales puede crear una posición nunca informada — alta

`views/importacion.py:726` evita materializar posiciones al crear una fibra global. Sin embargo, en la recarga, cambiar el estado invoca la sincronización de tramo único y vuelve a crearlas.

Reproducción: prueba 02. Primera carga de F17/Disponible deja cero FibraTramo; la segunda, cambiando únicamente a Ocupado, crea F17 en el tramo.

Corrección mínima: separar creación/asignación de posiciones del servicio de estado. La ruta puede permanecer conocida y su detalle de fibras pendiente. Proponer el mismo número en un formulario es válido si el usuario confirma la asignación; no debe ser efecto secundario de actualizar un estado.

### 3. La edición de ODF puede devolver error y guardar cambios parciales — alta

`views/inventario.py:2593` guarda el ODF antes de llamar al ajuste de capacidad. El ajuste falla si un puerto sobrante contiene información; el `except` de la línea 2603 captura la excepción dentro de la transacción exterior y retorna HTTP 400. El guardado previo queda confirmado.

Reproducción: prueba 10. Se solicita cambiar conector y reducir capacidad de 20 a 1; un puerto 20 con observaciones impide reducir. La respuesta es error, la capacidad sigue en 20, pero el conector sí cambió.

Corrección mínima: colocar la unidad completa de modificación dentro de una transacción cuyo error salga antes de construir la respuesta, o marcar rollback explícito. Revisar el mismo patrón en otras vistas, sin afirmar que todas tengan el mismo fallo.

### 4. Ajustar capacidad puede crear más puertos que la capacidad declarada — alta

`services/inventario.py:141` comprueba los puertos no numéricos más los numéricos existentes, pero después crea todos los numéricos faltantes de 1 a capacidad sin incorporar esa creación al total proyectado.

Reproducción: prueba 06. Un ODF con puerto X1, ajustado a capacidad 4, termina con X1, 1, 2, 3 y 4: cinco puertos.

Corrección mínima: validar el conjunto final antes de escribir. Si se adopta exclusivamente numeración 1…N, rechazar nuevas etiquetas incompatibles en todos los canales y tratar las existentes explícitamente. Si se conservan etiquetas textuales, el ajuste debe respetar su ocupación de capacidad. También debe unificarse el tratamiento de 1 y 01; hoy el modelo los distingue como textos.

### 5. Dos mecanismos calculan distintos números de puertos libres — alta

`models.py:1243` cuenta los puertos existentes con estado LIBRE. `views/importacion.py:3915` calcula capacidad menos ocupados menos reservados, incluyendo posiciones no materializadas.

Reproducción: prueba 07. ODF de capacidad 20 con un puerto libre registrado: una carga de metadatos deja libres=20; el recálculo oficial deja libres=1.

Corrección mínima: usar un único cálculo desde puertos registrados; mostrar posiciones pendientes por separado cuando el inventario esté incompleto. Si se garantiza materialización total, verificar esa garantía antes de declarar disponibilidad. El cálculo interno puede corregirse conservando el diseño del Dashboard.

### 6. Calidad puede declarar válido un recorrido sin nodos de inicio/fin — alta

`services/calidad.py:63` comprueba existencia de asignaciones y terminaciones, pero no exige suficiente topología para verificar el recorrido. Al faltar nodos, omite la comparación de Sites y puede devolver `COMPLETO`, `valido=True`, sin hallazgos.

Reproducción: prueba 03, con un tramo sin nodos ni textos de extremos, asignación y terminaciones A/B.

Corrección mínima: diferenciar datos cargados de trazabilidad verificada. Permitir importar tramos incompletos, pero señalar topología pendiente y no afirmar validación total. Verificar también la cadena de nodos conocidos en el evaluador.

Una sospecha fue descartada: modificar un tramo vecino para romper explícitamente la continuidad entre nodos conocidos es bloqueado por el trigger de migración 0033. La prueba 04 confirma esa protección. No se debe reportar que la base carece de controles de continuidad.

### 7. GUI y CSV siguen usando reglas distintas de numeración — media/alta

El modelo local y la migración 0047 permiten referencias repetidas. Sin embargo, `views/inventario.py:1348` y `:1610` aún rechazan una referencia repetida dentro de una ruta. `views/operacion_inventario.py:1213` devuelve `fibra_numero` como número principal, aunque la posición real por tramo sea distinta.

Confirmación por lectura de código y el test existente de dos referencias F17 con posiciones F1 y F19.

Corrección mínima: validar identidad estable y posición por tramo de manera uniforme. La GUI debe mostrar contexto suficiente: troncal, tramo, posición y ODF/puerto. No presentar automáticamente el número del primer tramo como identidad permanente de toda la troncal: una fibra puede tener recorrido parcial y no estar asignada allí.

### 8. La sincronización global/local cambia según haya uno o varios tramos — decisión funcional pendiente

`services/puertos.py:523` puede informar la fibra como Disponible al desconectar. El servicio global sincroniza físicamente solamente cuando hay un tramo.

Reproducción: prueba 05. Con dos tramos Ocupados, desconectar y solicitar liberar la fibra deja global=Disponible y ambos tramos=Ocupado.

La GUI actual habla de informar el estado de la fibra y, al conectar, menciona explícitamente el estado global. La prioridad del estado INFORMADO es una regla ya implementada. Por tanto, esta diferencia no prueba por sí sola un error de almacenamiento: demuestra que la antigua propuesta «liberar todos los tramos» no está implementada como tal.

Corrección recomendada: mantener explícito el alcance. Informar estado global debe tener el mismo efecto con uno o varios tramos. Aplicar el estado al recorrido completo debe ser una acción expresamente confirmada, sobre asignaciones existentes, con auditoría. Nunca crear tramos o posiciones para completar esa acción.

### 9. La renumeración por CSV no deja un evento de movimiento de posición — media

`views/importacion.py:2771` cambia la posición y guarda directamente FibraTramo. No registra posición anterior/nueva en AuditoriaFibra. El lote permite conocer la importación, pero no reconstruye necesariamente el cambio individual.

Reproducción: prueba 08, con estado global informado. La posición pasa de F12 a F13 y no aumenta la auditoría de fibra.

Corrección mínima: reutilizar AuditoriaFibra para asignar, renumerar y retirar una posición. Conservar códigos, tramo, posición anterior/nueva, usuario y lote en metadatos estructurados. El importador y la GUI deben invocar el mismo servicio.

### 10. El CSV rechaza una reducción de capacidad que el servicio permite — media

`services/inventario.py:207` guarda la capacidad nueva antes de ajustar puertos. El modelo rechaza la reducción porque aún cuenta los puertos sobrantes. La GUI usa otro orden.

Reproducción: prueba 09. Reducir por CSV de 20 a 4 falla aunque todos los puertos sobren como libres y sin información; invocar el servicio de ajuste sí funciona.

Corrección mínima: una operación común y atómica para editar ODF y ajustar capacidad, usada por GUI y CSV. La reducción conserva las protecciones para puertos conectados, reservados o con información.

## Riesgos adicionales detectados por inspección

### 11. El importador de metadatos puede sobrescribir un estado concurrente

`views/importacion.py:3842` lee los estados existentes sin bloquear las filas y `:3895` los vuelve a escribir mediante bulk_update, incluyendo estado_puerto aunque el importador declara conservarlo. Entre lectura y escritura, una operación concurrente podría conectar o reservar el puerto y la carga reponer el estado antiguo.

Es un riesgo de concurrencia por inspección, no un resultado reproducido con PostgreSQL. `transaction.atomic` garantiza atomicidad, pero no evita por sí solo lecturas obsoletas. Corregir omitiendo estado_puerto de las actualizaciones de metadatos existentes y coordinando los bloqueos/cálculos afectados; comprobarlo con PostgreSQL real.

### 12. Ubicación pendiente y permisos de creación requieren uniformidad

El CSV ODF acepta toda la ubicación vacía mediante SIN ASIGNAR; la GUI llama a resolver_rack, que exige Site, Sala y Rack. La GUI también admite capacidad cero mientras el CSV de nuevo ODF exige una positiva. Es una diferencia de comportamiento, no corrupción demostrada.

La creación desde Gestionar conexión exige add_inventariofibra, no exclusivamente is_superuser. Si «solo administrador» significa un rol funcional, verificar la asignación efectiva de ese permiso. No se evaluaron los permisos reales de los usuarios y no se afirma que exista acceso no autorizado.

## Qué conservar y qué aclarar con BHP

- Conservar codigo_fibra como identidad independiente de numeración, puerto y ruta.
- Conservar TerminacionFibra como conexión oficial y los estados de puerto separados de uso de fibra.
- Conservar una posición por fibra/tramo y su unicidad dentro del tramo.
- Conservar la posibilidad de cargar fibras y terminaciones con ruta pendiente.
- No deducir que dos F1 en dos ODF son fibras distintas: pueden ser los extremos de la misma fibra. Tampoco unirlos únicamente porque coincida el número.
- Pedir a BHP confirmar qué representa el número aportado y la correspondencia de extremos. Si entrega una posición normalizada por tramo, conservar también la referencia original cuando difiera.
- No renumerar automáticamente para hacer pasar un archivo ambiguo. La propuesta «F1…Fn» requiere correspondencia confirmada.
- No incorporar un modelo de Cable únicamente para resolver etiquetas repetidas. Si BHP necesita inventariar varios cables paralelos dentro del mismo tramo, habrá que aclarar el alcance físico de ese tramo: hoy solo existe una posición F17 por tramo, sin dimensión de cable.

## Orden mínimo de corrección

1. Separar estado, asignación y numeración; resolver posiciones existentes por relación, sin creación implícita. Alinear GUI e importadores.
2. Hacer atómica la edición completa de ODF, unificar ajuste de capacidad y contadores, y evitar sobrescritura de estados desde metadatos.
3. Distinguir completitud de validación de topología; añadir hallazgos de datos pendientes.
4. Acordar el alcance explícito de sincronización global/recorrido y auditar cambios por tramo.
5. Unificar ubicación pendiente, mensajes y selección de fibra. Comprobar PostgreSQL antes de dar por cerrada la concurrencia.

Estas correcciones pueden realizarse principalmente en servicios y sus consumidores, conservando las tablas centrales y el diseño del Dashboard. Cambiar el significado de la numeración para BHP o agregar una entidad física nueva sería una decisión adicional, no parte automática de una reparación.

## Evidencia de ejecución

- Suite focalizada existente: 132 casos seleccionados; 119 ejecutados correctamente y 13 omitidos por requerir PostgreSQL. No es la suite completa de la aplicación.
- makemigrations --check --dry-run con settings_pruebas: sin cambios por generar. Esto no acredita que una instalación operativa haya aplicado todas las migraciones.
- Diagnóstico adicional: 10 casos correctos como reproducciones; nueve documentan comportamientos problemáticos o divergentes y uno confirma la protección de continuidad. Estos tests afirman el comportamiento actual, no la solución deseada.
- Base usada por el diagnóstico: SQLite de tests en memoria, con las migraciones del árbol local aplicadas y destruida al terminar.
- No se ejecutó la suite de concurrencia contra PostgreSQL ni se certifica producción.

Script reproducible: [auditoria_logica_20260905.py](../.tmp-review/auditoria_logica_20260905.py). Se conserva como artefacto de revisión en una carpeta temporal ignorada; no se integró como prueba permanente de la aplicación.

Desde work/app:

```powershell
.\.venv\Scripts\python.exe .tmp-review\auditoria_logica_20260905.py
```

Que las pruebas anteriores pasen demuestra compatibilidad con los escenarios que cubren. Los nuevos casos muestran por qué eso no bastaba para afirmar que el modelo ya estaba cerrado.
