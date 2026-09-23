# Propuesta de corrección de lógica — Fiber Genius

Estado: propuesta revisada contra el código; implementación pendiente.
Base: AUDITORIA_LOGICA_20260905.md y sus reproducciones aisladas.

## Decisión principal

Conservar las entidades existentes y delimitar las operaciones que las modifican. GUI, CSV, administración y operaciones masivas deben usar los mismos servicios y reglas. Las versiones masivas pueden escribir por lotes, compartiendo validación, resultado y auditoría con las unitarias; no necesitan llamar miles de veces a una función de una sola fila.

La solución no requiere una entidad Cable, otra numeración de fibras ni una reescritura del Dashboard. Las correcciones de cálculo pueden cambiar cifras incorrectas; conservar el diseño no significa conservar cifras erróneas. Se mantendrán los contratos existentes y se agregarán campos de contexto cuando hagan falta, sin cambiar silenciosamente su significado.

## Las cuatro pasadas de revisión

| Pasada | Comprobación | Ajuste resultante |
|---|---|---|
| 1. Corregir cada reproducción | Estado, capacidad, calidad, errores parciales y auditoría | Operaciones pequeñas, atómicas y con responsabilidades precisas. |
| 2. Compatibilidad con reglas existentes | Estado informado prioritario, ruta pendiente, restaurar Sin información y relación A/B | Conservar esas reglas. Cambiar explícitamente la excepción de sincronización automática de un solo tramo. |
| 3. Operaciones combinadas | CSV parcial, recarga, movimiento, ajuste de capacidad y concurrencia | Actualizar solo campos informados; comprobar nuevamente bajo bloqueo; auditar dentro de la misma transacción. |
| 4. Facilidad de uso y casos incompletos | Referencias repetidas, fibras sin tramo, puertos especiales y topología desconocida | Mostrar contexto y pendientes; no inventar posiciones ni exigir que se complete todo para poder registrar información válida. |

Estas pasadas son revisión de diseño. Las pruebas de la auditoría demuestran fallos actuales; no demuestran que una solución todavía no implementada ya funcione.

## 1. Estados, identidad y asignaciones de fibras

Reglas únicas:

- codigo_fibra mantiene la identidad estable; no se recalcula al cambiar puerto, ruta, nombre o posición.
- fibra_numero se conserva como referencia informada. Puede repetirse entre identidades distintas. No se afirma que siempre represente una numeración física verificada.
- FibraTramo.numero_hilo expresa la posición de esa fibra en ese tramo. Se conserva la unicidad tramo/posición y tramo/fibra.
- Asignar una ruta no crea posiciones de hilo. Una operación compuesta puede asignar ruta y posiciones si ambas fueron informadas expresamente, dentro de una misma transacción.
- Cambiar un estado no crea, elimina ni renumera asignaciones y tampoco mueve terminaciones.
- Las fibras sin ruta siguen pudiendo crearse, importarse y conectarse a ODF.

### Cambio funcional explícito: mismo alcance con uno o varios tramos

Recomendación: informar el estado global modifica únicamente el global, independientemente del número de tramos. Se elimina el efecto automático especial de un tramo, actualmente en _sincronizar_estado_tramo_unico.

| Operación | Global | Tramos | Terminaciones |
|---|---|---|---|
| Informar estado global | Cambia a INFORMADO | Se conservan | Se conservan |
| Restablecer global a Sin información | SIN_INFORMACION / NO_INFORMADO | Se conservan | Se conservan |
| Cambiar estado de un tramo | Se infiere solo si no tiene prioridad INFORMADO | Cambia el seleccionado | Se conservan |
| Aplicar estado al global y tramos registrados | Cambia a INFORMADO | Cambian los existentes expresamente incluidos | Se conservan |
| Conectar o desconectar puerto | Solo cambia si se solicita | Solo cambian si se solicita el alcance adicional | Cambia el extremo seleccionado |

Restablecer el global conserva la regla actual de no inferir inmediatamente en esa misma acción. Un recálculo explícito o cambio posterior del detalle podrá volver a inferirlo. La condición física y el servicio no se borran por estas operaciones.

En la GUI, mantener la pregunta sobre informar el estado global. Para cumplir además la operación de recorrido solicitada anteriormente, incluir una opción explícita «Aplicar también a los tramos registrados (N)», desactivada por defecto. Si faltan tramos, indicar cuántos están registrados y cuáles quedan pendientes. Con cero asignaciones, la opción adicional no aplica; informar el global sigue siendo posible.

La corrección de esta excepción es una decisión funcional visible en la propuesta. Algunas pruebas actuales exigen materialización 1:1 y deben actualizarse únicamente para los contratos que cambien, conservando las demás protecciones.

### Presentación y selección

En un tramo: mostrar su posición local, por ejemplo RUTA-118 / T001 / F19. En inventario global o búsqueda: añadir ODF/puerto A/B y referencia; el valor enviado al servidor sigue siendo el ID estable. Dos resultados llamados F17 deben poder distinguirse antes de seleccionarlos.

En una ruta de un único tramo puede mostrarse su posición como número de la troncal. Con varios tramos, mostrar la posición del tramo seleccionado o un resumen de posiciones. No adoptar el primer tramo como identidad permanente: puede cambiar el recorrido o faltar allí la asignación de esa fibra.

No imponer nuevas columnas obligatorias a archivos válidos para reparar el backend. Las plantillas generadas pueden llevar la identidad precargada. Las búsquedas sin código solo resuelven cuando existe una coincidencia inequívoca; no elegir el primer resultado.

## 2. Una operación completa para editar ODF y ajustar capacidad

Unificar GUI y CSV sobre el servicio existente de inventario, con una operación que:

1. Identifique y bloquee el ODF y los puertos afectados.
2. Valide ubicación, metadatos, capacidad y conjunto final de puertos.
3. Calcule qué posiciones se conservan, crean o retiran.
4. Aplique metadatos y capacidad en una transacción única.
5. Recalcule contadores desde los puertos resultantes.
6. Registre los cambios confirmados y devuelva el resultado.

Las excepciones salen de la transacción antes de convertirse en respuesta HTTP o error de fila. Así una reducción rechazada tampoco guarda el conector o la ubicación.

La reducción solo puede retirar posiciones sobrantes libres, sin terminaciones, reservas ni información que deba conservarse. La comprobación debe considerar todos los metadatos de puerto, no solo observaciones. Los valores generados por defecto y las etiquetas especiales requieren tratamiento explícito: no se presume que un dato carece de valor.

El CSV usa el mismo ajuste que la GUI, evitando guardar primero una capacidad que el modelo rechaza por contar todavía los puertos sobrantes.

### Numeración de puertos

Para nuevos ODF del flujo automático: capacidad positiva N y posiciones numéricas 1…N.

Las entradas numéricas se normalizan para buscar la misma posición, por ejemplo 01 y 1. Antes de renombrar datos existentes, se detectan colisiones. Si existen ambos registros, no se fusionan ni se trasladan conexiones automáticamente.

Si un ODF existente usa X1 u otras etiquetas textuales, se conservan sus puertos y operaciones ordinarias. El ajuste automático 1…N se detiene con una explicación si no puede obtener un resultado inequívoco. No se añade 1…N encima de las posiciones textuales. Dar soporte completo a una nueva convención de nombres sería un alcance adicional.

La validación obligatoria es del conjunto final, incluyendo los puertos nuevos. Su cantidad nunca puede superar la capacidad declarada.

## 3. Contadores e importaciones de metadatos

La fuente oficial de libres, reservados y ocupados es el estado de los puertos registrados. El mismo cálculo alimenta modelo, importadores, API y recálculos masivos.

Si capacidad=48 y solo existen 40 puertos, las ocho posiciones faltantes son inventario pendiente, no ocho puertos libres. Para ODF nuevos la generación automática evita ese hueco. En registros existentes se diagnostica el faltante; no se generan posiciones como efecto secundario de importar una observación.

Para puertos existentes, el importador de metadatos escribe únicamente los campos informados y autorizados. Excluye estado_puerto y capacidad_puertos de esas escrituras. Los estados iniciales de puertos nuevos son una operación de alta distinta, con Libre/Reservado según la regla admitida; Ocupado requiere una terminación oficial.

La importación mantiene semántica de parche:

- Filas omitidas se conservan.
- Campos omitidos/vacíos siguen las reglas de conservación ya establecidas; borrados solo mediante la indicación explícita admitida.
- Mismos datos recargados no duplican identidades ni conexiones.
- Un error revierte la unidad de importación declarada. El lote fallido y sus errores se registran fuera de la transacción revertida.
- Se conserva el detalle de fila, objeto y causa. No se reemplazan errores de validación conocidos por mensajes genéricos.

## 4. Calidad: cargado, pendiente o verificado

Conservar los estados estructurales existentes y añadir las comprobaciones que faltan. No es necesario persistir otra columna de estado.

Un resultado válido exige recorrido cubierto, nodos suficientes para comprobar continuidad, terminaciones y ubicación verificable según el nivel de detalle conocido. Los nodos pendientes o la jerarquía especial SIN ASIGNAR nunca cuentan como ubicación confirmada.

Si faltan datos, permitir el inventario y mostrar el hallazgo de topología/ubicación pendiente. Si los datos conocidos se contradicen, señalar la contradicción. No confundir falta de evidencia con evidencia de error.

La comparación respeta A/B como etiquetas técnicas, no orientación geográfica. Si el nodo terminal identifica un ODF concreto, comparar ese ODF; si solo identifica un Site, la comprobación llega hasta Site y no acredita un ODF específico. Un mismo Site en ambos extremos requiere conservar los extremos técnicos sin inventar orientación.

Cuando un tramo conocido está Ocupado y el global informado dice Disponible, mostrar la discrepancia aunque falten otros tramos; no hace falta cobertura completa para detectar esa evidencia. Mantener la prioridad del estado informado y no cambiarlo silenciosamente. Para inferir Disponible desde el detalle, conservar la exigencia de cobertura completa conocida.

El campo tecnico completo puede seguir expresando cobertura estructural por compatibilidad; valido debe ser falso cuando no se pudo verificar lo necesario y la GUI debe mostrar el motivo. Ninguna etiqueta visible debe presentarlo como verificado sin esa comprobación.

## 5. Auditoría y operaciones masivas

Reutilizar AuditoriaFibra y AuditoriaPuertoODF. Registrar identidad estable, tramo, posición anterior/nueva, estados anterior/nuevo, usuario, origen, lote y alcance de la operación. Los detalles completos van en metadatos JSON; los textos abreviados son para presentación.

Asignar, renumerar o retirar una posición pasa por el mismo servicio desde GUI, CSV y administración. La auditoría se confirma junto con los datos y no registra movimientos inexistentes en una recarga idéntica. El lote sí conserva evidencia de esa recarga.

Las escrituras masivas validan el lote y vuelven a comprobar conflictos antes de guardar. Se recalculan los tramos y ODF afectados al terminar, en lugar de repetir el recálculo para cada fila. No desactivar globalmente las señales: cualquier supresión interna debe ser controlada por el servicio que garantice recálculo y auditoría finales.

## 6. Ubicación pendiente, capacidad y permisos

GUI y CSV aceptan ubicación completa o toda pendiente con la misma regla. Para una recarga, ubicación vacía conserva la existente. El marcador SIN ASIGNAR se presenta como ubicación pendiente; no se interpreta como Site real verificado.

La creación del ODF automático exige capacidad positiva en ambos canales. Un registro histórico de capacidad desconocida no se elimina ni se transforma en capacidad cero por una celda vacía; sigue permitiendo editar sus metadatos. Cambiar a cero un ODF existente no puede convertirse en una orden de borrar todos sus puertos.

Mantener un permiso único de creación de identidad de fibra. Si el rol Administrador debe ser el único habilitado, comprobar y ajustar su asignación; no introducir comprobaciones diferentes en GUI y CSV. Crear una identidad nueva y conectar una identidad existente continúan siendo acciones distintas. Los CSV globales siguen disponibles para los usuarios autorizados.

## 7. Concurrencia y PostgreSQL

Una única transacción es necesaria, pero no suficiente. El servicio vuelve a leer los registros relevantes bajo bloqueo y escribe solo sus campos. Conectar, mover, reservar, cambiar capacidad e importar metadatos deben coordinar los mismos objetos y adquirirlos en un orden determinista.

Dentro del dominio ODF, el orden objetivo para conectividad es fibra, terminación, ODF afectados y puertos afectados; dentro de cada grupo, ID ascendente. Ajustar capacidad usa ODF y puertos en ese mismo orden relativo. Las operaciones de ruta/tramo y las combinadas deben revisar su orden completo y las escrituras de señales; esta propuesta no da ese grafo por validado sin probarlo.

La lectura detectó además select_for_update combinado con select_related sobre ruta, que es nullable, en services/puertos.py:35 y otros servicios. Django documenta tanto el bloqueo de relaciones incluidas como la limitación de relaciones anulables. La solución debe bloquear explícitamente la tabla necesaria —por ejemplo con of=('self',) donde proceda— y bloquear por separado las relacionadas que deban protegerse, o separar las lecturas. No excluir fibras sin ruta para evitar el problema. Fuente: [Django 6.0, select_for_update](https://docs.djangoproject.com/en/6.0/ref/models/querysets/#select-for-update).

Este riesgo adicional está sustentado por lectura del código y documentación; no se presenta como una reproducción ya ejecutada en PostgreSQL. SQLite no prueba esos bloqueos. Los casos de aceptación deben incluir una fibra sin ruta, dos conexiones al mismo puerto, conexión simultánea con importación de metadatos, movimientos entre ODF, reducción de capacidad frente a conexión y renumeraciones concurrentes.

## Secuencia de implementación y cierre

| Entrega | Alcance | Hallazgos cubiertos |
|---|---|---|
| A. ODF consistente | Transacción completa, capacidad, contadores y campos permitidos; bloqueo compartido | 3, 4, 5, 10 y 11 |
| B. Fibra consistente | Identidad, asignaciones explícitas, estado con alcance uniforme y auditoría por tramo | 1, 2, 7, 8 y 9 |
| C. Información verificable | Calidad, ubicación pendiente, mensajes, permisos y selección contextual | 6 y 12; completa 7 |
| Cierre transversal | Regresión y comprobación PostgreSQL de los flujos modificados | Todos los contratos afectados |

Cada entrega debe quedar probada antes de continuar. Las pruebas PostgreSQL forman parte de la validación de bloqueos, no un requisito que se pueda omitir y aun así declarar concurrencia cerrada.

## Matriz de aceptación

| Caso | Resultado exigido |
|---|---|
| Referencia F12 con posición F19; informar Ocupado | Global actualizado; F19 y conexiones conservadas. |
| Recargar global sin ninguna asignación | Sigue sin FibraTramo. |
| Cambiar global con 1, 2 o 3+ tramos | Igual alcance global; no modifica posiciones ni estados locales. |
| Aplicar también a tramos registrados | Solo asignaciones expresamente incluidas; auditoría; faltantes siguen pendientes. |
| Restablecer global a Sin información | No borra evidencia local ni infiere inmediatamente. |
| Dos referencias F17 distintas | Permitidas por identidad; posiciones locales únicas; selección inequívoca. |
| Dos fibras en la misma posición del mismo tramo | Rechazadas sin cambios parciales. |
| Asociar ruta sin informar posiciones | Ruta asignada, posiciones pendientes. |
| Editar conector y solicitar reducción no permitida | Error con causa; se conservan todos los valores anteriores. |
| Reducir ODF con sobrantes libres sin información | Mismo resultado desde GUI y CSV. |
| ODF con X1 y ajuste automático ambiguo | Se conserva; mensaje explicativo; nunca se crean más de N puertos. |
| Puertos 1 y 01 ya coexistentes | Detectar colisión sin fusionarlos ni mover terminaciones. |
| Capacidad 48, cuarenta puertos registrados | Ocho pendientes; libres calculados desde filas existentes. |
| Metadatos importados mientras otro usuario conecta | Conserva la conexión y su estado; contadores coherentes. |
| Falta topología o Site real | Registro admitido; trazabilidad sin verificar. |
| Continuidad explícita inválida | Sigue bloqueada por las protecciones de integridad. |
| Renumeración por CSV y recarga idéntica | Un cambio auditado; segunda carga sin movimiento ficticio. |
| Fibra sin ruta en PostgreSQL | Se conecta sin excluirla ni fallar por el join anulable. |

La revisión no autoriza deducir numeración física, fusionar fibras ni modificar datos BHP ambiguos. Esas decisiones dependen de la correspondencia real del inventario. Las correcciones de funcionamiento descritas pueden prepararse conservando esa información.
