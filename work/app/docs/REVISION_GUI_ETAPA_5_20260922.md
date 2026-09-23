# Etapa 5 — Revisión de GUI y legibilidad

Fecha: 22/09/2026. Resultado: **revisión realizada, con observaciones pendientes de corregir**. No se declara cerrada la aceptación visual ni lista la entrega a producción.

## Alcance y protección del entorno

- Se revisaron las 11 pestañas principales: Resumen, Calidad, Mapa, Troncales y tramos, Fibras y reservas, ODF, Puertos ODF, Importación, Sites, Usuarios y Roles.
- Navegador real: Microsoft Edge instalado, automatizado en modo headless con Playwright. La herramienta de navegador integrado no pudo inicializarse; se utilizó este mecanismo alternativo, con capturas PNG y lectura del DOM.
- Bases SQLite aisladas dentro de `outputs/stage5_*_20260922`, derivadas de la copia de auditoría previa. No es una afirmación sobre el contenido más reciente de la base del usuario.
- Cuenta de prueba `revision.visual`, únicamente en las copias, con contraseña inutilizable y sesión local de pruebas. Sin usar el perfil del navegador del usuario.
- Servidores temporales en 8020, 8021 y 8022, cerrados al terminar. El servidor habitual 8001 no se reinició.
- No se guardaron formularios ni se importaron archivos. Las rondas de interacción bloquearon peticiones mutantes al servidor local; no se intentó ninguna.
- Sin modificaciones al código funcional, Dashboard, mapa ni datos reales. Solo auxiliares de prueba, evidencias y este informe.
- Referencia Git: `33f073ccda25641a1e0a26273dae8c049549d15a`, con cambios locales preexistentes. La versión exacta probada queda identificada por las huellas de archivos en el informe JSON, no solo por HEAD.
- Integridad final: 21 tablas de inventario/auditoría sin diferencias en las seis copias de prueba; comparación antes/después del barrido inicial correcta; archivos previamente inventariados sin cambios y los tres puertos temporales cerrados.

## Comprobaciones realizadas

| Comprobación | Resultado |
| --- | --- |
| Apertura de las 11 pestañas | HTTP 200, sin excepciones JavaScript capturadas |
| Ocho tamaños: 1440×900, 1024×768, 768×1024, 430×932, 390×844, 360×800, 320×568 y 844×390 | Sin desbordamiento horizontal global; esto no descarta solapamientos internos |
| Recarga adicional a 390 y 320 píxeles | 110 mediciones de página en total, sin desbordamiento global |
| Menú móvil: abrir y cerrar con Escape, en las 11 pestañas a 390/320 | 44 verificaciones correctas |
| Tema claro y oscuro | Capturas de las 11 pestañas; observaciones de legibilidad indicadas abajo |
| Nuevos ODF, troncal, tramo, fibra, reserva, puerto y Site; historial, operaciones y gestión de puerto | 60 verificaciones de límites del diálogo/cierre correctas en 390×844, 320×568 y 844×390 |
| Buscar sin resultados y limpiar en Troncales, Fibras, ODF y Puertos | 8 verificaciones correctas, esperando la carga asíncrona |
| Formulario de usuario | Ancho correcto en escritorio, 390 y 320 |
| Formulario de rol, abierto desde su enlace real | Límites correctos en cuatro tamaños; cierre por Escape correcto |
| Asistente de importación: apertura de carga ODF | Límites correctos en tres tamaños; sin seleccionar ni subir archivos |
| Reflujo equivalente a 200 %: viewport CSS 720×450, DPR 2 | 11 pestañas sin desbordamiento global |
| Enlace «Saltar al contenido principal» activado por teclado | Foco trasladado al contenido en las 11 pestañas |
| Paginación siguiente/anterior de cuatro inventarios | Funciona; **ODF requiere contraer el inspector para evitar el bloqueo descrito en GUI-01** |

La simulación de reflujo no equivale a una prueba del zoom nativo en todos los navegadores. No se probaron teléfonos físicos, Safari ni Firefox. La revisión de teclado se limita a los controles descritos, no certifica accesibilidad completa.

## Observaciones y solución mínima recomendada

### GUI-01 — Prioridad alta: el inspector de ODF tapa controles

En 1440×900, el selector `#odf-page-size` queda debajo del encabezado del inspector. La flecha «Página siguiente» también recibe intercepción de clics por el panel lateral. Es un bloqueo real de interacción, no solo un defecto cosmético.

Evidencia: `stage5_interactions_20260922/report.json`, medición `odf.covered`; `stage5_finish_20260922/report.json`, fallo de clic con identificación del inspector; captura `stage5_visual_20260922/odf-1440.png`. La paginación funciona al contraer el panel, según `stage5_pagination_final_20260922/report.json`.

Solución: contener filtros y tabla dentro de la primera columna del layout, permitir que los filtros se reorganicen según el ancho disponible y mantener el desplazamiento horizontal dentro de la tabla. Revisar los mínimos de `.odf-filters`, las tarjetas y `.odf-analysis-main`; no ocultar controles ni obligar al usuario a cerrar el inspector. Código relacionado: `network-inventory.css` desde la sección de layout en torno a la línea 722 y `odf-inventory.css:337`.

### GUI-02 — Prioridad media: nombres largos invaden otras columnas

En Troncales, el nombre de la primera fila excede el borde de su celda en unos 22 píxeles y se mezcla visualmente con Origen. En Puertos, los códigos de fibra medidos exceden su celda unos 24 píxeles. Las celdas combinan texto sin salto y desbordamiento visible.

Evidencia: mediciones `troncales.table` y `puertos.table` del informe de interacciones; captura `troncales-1440.png`. Estilos relacionados: `odf-inventory.css:370–371`.

Solución: anchos mínimos adecuados y salto de línea para identificadores largos, o elipsis con una vía accesible de consultar el valor completo. No recortar identificadores sin permitir leerlos y no cambiar su contenido en la base de datos.

### GUI-03 — Prioridad media: letras excesivamente pequeñas

Se observan encabezados y tablas de 9–10 píxeles y textos de análisis contextual de 7–8 píxeles. La página cabe, pero su lectura exige esfuerzo. Los textos secundarios tenues agravan la dificultad, especialmente en el tema oscuro; no se realizó una certificación de contraste.

Evidencia: muestras DOM `small` del informe visual; `network-inventory.css`, entre otras, líneas 213, 225, 272, 278, 287 y 323; `odf-inventory.css:370–371`.

Solución: establecer una escala consistente, inicialmente 12–13 píxeles para tablas y textos auxiliares, y ajustar espaciado/ancho sin mover componentes del Dashboard. Verificar ambos temas después del cambio.

### GUI-04 — Prioridad baja: el KPI de longitud queda pegado al borde y recortado

El valor `1,737.19` ocupa unos 106 píxeles en una zona de texto de 87; su extremo derecho excede ligeramente el borde de la tarjeta. La tarjeta usa `overflow: hidden`. Algunos subtítulos también quedan abreviados.

Evidencia: captura `troncales-1440.png` y medición `distance` en `stage5_finish_20260922/report.json` (borde del texto 627.41; borde de tarjeta 626.66).

Solución: reducir espacio del icono o ajustar la distribución/tamaño de letra según el espacio real. No abreviar ni cambiar el total calculado como solución al problema de presentación. Estilos relacionados: `odf-inventory.css:43–65`.

### GUI-05 — Prioridad media: controles del mapa superpuestos a la atribución en móvil

En 390×844, los controles inferiores «Capas» y pantalla completa se superponen al texto de atribución de Esri, que ocupa varias líneas.

Evidencia: `stage5_visual_20260922/mapa-390.png`.

Solución: reservar espacio para la atribución y ubicar los controles por encima de ella en pantallas estrechas. Mantener los créditos completos. No mover el mapa dentro del Dashboard ni cambiar el proveedor para corregir este problema de layout.

### GUI-06 — Prioridad media: paginación sin nombres accesibles descriptivos

Las flechas de Troncales y Fibras aparecen en el árbol accesible solo como `‹` y `›`, a diferencia de ODF y Puertos. Los controles sí funcionan usando sus nombres actuales, pero no describen la acción de forma clara para tecnologías de asistencia.

Evidencia: archivos `troncales-dom.txt` y `fibras-dom.txt`; plantillas `inventario_externo.html:56,75` y `planta_externa.html:58,77`.

Solución: añadir nombres «Página anterior»/«Página siguiente» y etiquetar las páginas y la página activa consistentemente, sin modificar la lógica de consulta.

### Mejora secundaria — Sites móvil

En la captura de 390 píxeles, las filas alcanzan aproximadamente 190 píxeles de alto; apenas aparecen dos registros en pantalla. No se clasificó como fallo de carga. Conviene limitar la expansión visual de la descripción y facilitar su consulta completa, conservando la tabla desplazable.

## Evidencias y errores del instrumento de prueba

Las carpetas bajo `outputs/` se mantienen locales y normalmente están ignoradas por Git:

- `stage5_visual_20260922/`: 56 capturas, DOM, huellas de código/datos y barrido de pantallas.
- `stage5_interactions_20260922/`: formularios y mediciones de solapamiento. La ejecución inicial se detuvo al intentar tratar la respuesta parcial de creación de grupo como una página completa.
- `stage5_interactions_retry_20260922/`: filtros, formulario real de grupo y asistente de importación. La primera prueba de reflujo se detuvo porque ODF contiene dos elementos `main`; se corrigió el selector del instrumento a `#contenido-principal`.
- `stage5_finish_20260922/`: reflujo y teclado completos, y reproducción del bloqueo de paginación de ODF. Los fallos por buscar «Página siguiente» en Troncales/Fibras se reclasifican como selector incorrecto del instrumento y carencia de etiqueta descriptiva, no como fallo funcional de paginación.
- `stage5_pagination_20260922/`: primera repetición; dos comprobaciones fueron prematuras al vaciarse temporalmente la tabla durante la carga asíncrona.
- `stage5_pagination_final_20260922/`: repetición esperando datos; 8/8 comprobaciones de paginación correctas, ODF con inspector contraído.
- `stage5_finish_20260922/integrity.json`: comparación de las tablas de inventario/auditoría de las copias, huellas de código y cierre de puertos temporales.

El barrido inicial registró dos peticiones `ERR_ABORTED` durante la navegación (consulta de troncales y puertos). Las consultas y flujos posteriores respondieron correctamente; no se utiliza ese registro aislado como evidencia de un fallo del servidor.

El mapa utilizó el respaldo Esri con teselas reales. La copia de pruebas no contiene geometrías de troncales; no se validó en esta etapa el dibujo de recorridos geográficos completos. La falta de geometría no se clasifica como regresión de GUI.

## Cierre recomendado

Corregir primero GUI-01 y GUI-02; después los ajustes de legibilidad, KPI, atribución y etiquetas accesibles. Repetir las mismas capturas y controles sobre el código corregido, conservando la distribución del Dashboard y del mapa. Esta etapa no sustituye la preparación de entrega (6), la regresión final (7) ni la concurrencia PostgreSQL pendiente.
