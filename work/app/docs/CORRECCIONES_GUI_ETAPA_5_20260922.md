# Correcciones de GUI — Etapa 5

Fecha: 22/09/2026. Referencia: `REVISION_GUI_ETAPA_5_20260922.md`.

Las seis observaciones GUI-01 a GUI-06 se corrigieron. Los cambios son de presentación y accesibilidad; no se modificaron modelos, migraciones, importadores, cálculos ni servicios de inventario.

## Cambios

| Observación | Corrección |
| --- | --- |
| GUI-01: inspector de ODF bloquea controles | La columna interna puede reducirse sin expandirse por el ancho mínimo de la tabla. Filtros reorganizados según el espacio disponible, con desplazamiento horizontal limitado a la tabla. Verificado el selector de filas y la paginación con el inspector abierto, sin clics forzados. |
| GUI-02: identificadores invaden otras columnas | Nombres y códigos admiten saltos de línea dentro de su celda, conservando el texto completo. Las etiquetas de estado no se parten. |
| GUI-03: letra demasiado pequeña | Tamaño mínimo explícito de 12 px en los cuatro archivos de estilos de inventario afectados. Paneles contextuales adaptados para que sus cifras no queden fuera al aumentar la letra. |
| GUI-04: longitud recortada | Iconos y espacios internos más compactos; cifras completas, subtítulos con salto de línea y sin alterar el valor calculado. |
| GUI-05: controles sobre los créditos del mapa | Separación basada en la altura real de la atribución, actualizada al cambiar tamaño/proveedor. Se mantienen íntegros los créditos. |
| GUI-06: paginación sin etiquetas | Flechas «Página anterior/siguiente», botones «Ir a la página N» y página activa identificada con `aria-current`. |

Se actualizaron las versiones de los recursos estáticos afectados. El Dashboard y su distribución, incluidos sus gráficos y la ubicación del mapa, no se modificaron en este trabajo. No se cambió el proveedor ni se añadieron dominios o dependencias externas.

## Verificación

- **156/156 comprobaciones específicas en Edge**: filtros contenidos, celdas sin invasión, tamaño de letra, cifras completas, paneles sin desbordamiento, selector de filas ODF, paginación accesible y controles separados de la atribución.
- **60/60 comprobaciones de formularios**: apertura, límites y cierre por Escape en 390×844, 320×568 y 844×390. No se guardaron formularios.
- Barrido de las **11 pestañas en ocho tamaños**, con recargas adicionales en móvil, temas claro/oscuro y capturas. Evidencia final en `outputs/stage5_fixed_visual_release_20260922/`.
- Barrido final: 11 respuestas HTTP 200, 110 mediciones sin desbordamiento global, 46 verificaciones correctas y ninguna excepción JavaScript capturada. Se registraron cancelaciones de consultas al navegar, no fallos de los flujos de aceptación.
- `manage.py check --settings=fibergenius.settings_pruebas`: correcto.
- Pruebas JavaScript: `gui-stage5.cjs` (23 contratos de interfaz), `dashboard-review.cjs` (18), `map-basemap.cjs` (45), `map-provider.cjs` (12), `inventory-context.cjs` (28) y `responsive-inventory.cjs`: correctas.
- `git diff --check`: sin errores de formato.

Los contratos de interfaz son comprobaciones de código, no sustituyen la geometría ni las capturas de navegador. Se conservan ambas evidencias.

## Evidencias finales

- `outputs/stage5_fixed_acceptance_20260922/report.json`: 156 verificaciones y capturas de las correcciones.
- `outputs/stage5_fixed_forms_20260922/report.json`: 60 verificaciones de formularios.
- `outputs/stage5_fixed_visual_release_20260922/report.json`: barrido final, huellas de archivos y comparación antes/después del inventario.
- `outputs/stage5_fixed_acceptance_20260922/integrity.json`: alcance de los archivos cambiados, huellas finales y comparación de las 21 tablas de inventario/auditoría de las copias.
- `work/app/scripts/tests/gui-stage5.cjs`: contratos de interfaz incorporados para prevenir regresiones.

Se utilizaron copias aisladas y una cuenta de pruebas sin contraseña utilizable. Los servidores temporales se cierran al terminar; no se reinició el servidor habitual ni se alteró su base. El navegador integrado no pudo inicializarse y se empleó Edge instalado con sesiones efímeras.

Integridad final confirmada: 21 tablas sin diferencias en las tres copias finales; 13 archivos funcionales cambiados, todos dentro del alcance de interfaz; ningún archivo funcional fuera de ese alcance; puertos temporales 8020, 8021 y 8023 cerrados.

Las carpetas intermedias `stage5_fixed_visual_20260922`, `stage5_fixed_visual_final_20260922` y `stage5_fixed_checks_20260922` documentan la iteración, no el cierre. En una ronda se ajustó código mientras el barrido seguía activo; por eso se repitió el barrido sobre el código definitivo. Una comprobación inicial de filtros incluía inputs ocultos: se corrigió el instrumento para medir únicamente controles visibles.

## Límites

No se ejecutó de nuevo la suite Django completa en este cambio exclusivamente de interfaz; permanece para la regresión final de la etapa 7. Tampoco se certifica Safari, Firefox, teléfonos físicos ni conformidad integral de accesibilidad. La mejora secundaria de densidad de filas de Sites no forma parte de las seis correcciones.

No se realizó commit ni push. Las etapas 6 (preparación de entrega), 7 (regresión final) y la validación PostgreSQL siguen siendo trabajos separados.

## Ampliación posterior: Sites en pantallas pequeñas

A petición del usuario se implementó también la mejora secundaria de densidad de Sites:

- Tabla con ancho mínimo legible y desplazamiento horizontal mediante tacto o teclado.
- Filas con menor relleno en móvil, conservando los textos completos y la tipografía de 14 px.
- Aviso de desplazamiento y paginación que puede distribuirse en varias líneas.
- Sin cambios de modelos, datos, filtros, permisos, Dashboard ni mapa.

Validación visual en Edge con una copia aislada: ocho tamaños entre 320 y 1440 px, temas claro/oscuro, búsqueda, limpiar y paginación. Las primeras cinco filas medidas en móvil ocuparon 63 px cada una. Resultado final: 22/22 comprobaciones, sin errores JavaScript ni desbordamiento de página. Los Sites permanecieron intactos y el servidor temporal quedó detenido.

Evidencia: `outputs/sites_compact_final_20260922/report.json` y capturas de la misma carpeta. La primera ronda detectó una espera insuficiente del instrumento al navegar a la segunda página; se repitió esperando la navegación y la aparición de filas. No se cambió la lógica de paginación.

Regresión focalizada: `manage.py test mapas.tests_gui_administracion --settings=fibergenius.settings_pruebas --noinput`: 15/15 pruebas correctas, incluidos textos largos completos y coordenadas cero. Se ajustó el nuevo test para aceptar el formato decimal de cero sin imponer un separador regional. Esta ejecución no sustituye la suite completa pendiente de la etapa 7.

## Corrección posterior a captura del usuario: ranking del inspector

La revisión previa dejó pasar un desbordamiento **interno** del ranking de
troncales: la página y el cuerpo del inspector no desbordaban, pero las pistas
automáticas de grid crecían por el contenido y las filas/barras rebasaban la tarjeta.
Por ello, el resultado anterior no era suficiente para declarar correcto ese panel.

Se reprodujo con los mismos nombres de rutas de la captura y se corrigieron
las pistas y tamaños mínimos de ranking/items. Nombre completo y cantidad
quedan en líneas separadas, sin reducir fuente ni ocultar el fallo con recorte.
Se actualizó la versión del CSS compartido en las cuatro pantallas consumidoras.
No se cambiaron JavaScript funcional, cálculos, datos ni Dashboard.

Evidencia antes: `outputs/ranking_before_20260922/report.json`, falla geométrica
interna reproducida aunque `bodyOverflow` era cero.
Evidencia final: `outputs/ranking_final_20260922/report.json`, 39/39 verificaciones,
troncales/fibras/ODF/puertos en ocho anchos entre 320 y 1920 px, cambio a Tramos,
cinco resultados y filtro por teclado. Sin errores JavaScript ni escrituras HTTP.
Se inspeccionaron capturas en claro y se generó captura en oscuro; no se certifica
con esto una revisión de contraste integral. Servidor aislado cerrado al terminar.

En una ronda intermedia, la comprobación de cinco resultados se adelantó a la
respuesta asíncrona al volver de Tramos a Troncales. Se corrigió la espera del
instrumento, no el comportamiento de la aplicación, y se repitió el barrido final.
Los contratos `gui-stage5.cjs` pasan 27 controles; `responsive-inventory.cjs` pasa.
El servidor habitual devuelve HTTP 200 con el CSS corregido. Recargar la pantalla
para descartar caché del navegador. No se hizo commit ni push.

## Unificación visual posterior de los seis inspectores

El usuario señaló después que evitar desbordamientos no había resuelto la
densidad y alineación general. Esa observación era correcta: quedaban alturas
mínimas artificiales, interlineado/espaciado excesivo, nombres ODF en cursiva
y colocación implícita de celdas en Reservas. La prueba geométrica anterior no
certificaba la calidad visual del conjunto.

Se ajustó únicamente CSS compartido y su versión en las cuatro plantillas:

- Interlineado y tipografía consistentes; cifras alineadas, sin cursiva en ODF.
- Nombre completo, barra y una fila de cantidad/contexto en los rankings.
  Fibras conserva su metadata más extensa en una fila completa, sin comprimirla.
- Reserva: total y metros con sus etiquetas en columnas explícitas; sin partir
  «georreferenciados» por una celda implícita demasiado pequeña.
- Sin altura mínima artificial en resúmenes/rankings vacíos. Inspector con altura
  natural y máximo de pantalla, conservando scroll cuando hace falta.
- No cambian ancho del panel, posición del mapa, números, estados, filtros,
  JavaScript funcional, modelos ni base activa.

Mediciones en escritorio, píxeles aproximados:

| Panel | Resumen antes → después | Ranking antes → después |
|---|---|---|
| Troncales | 258 → 208 | 547 → 386 |
| Tramos | 258 → 208 | 566 → 403 |
| Fibras | 322 → 258 | 527 → 471 |
| Reservas vacías | 191 → 127 | 239 → 106 |
| ODF | 258 → 208 | 527 → 451 |
| Puertos | 258 → 208 | 527 → 451 |

Fibras conserva cuatro estados y un pie de dos líneas: no se fuerza a la altura
de una tarjeta de tres estados ni se oculta información para igualarla.

Evidencia: `outputs/ranking_compact_before_20260922` y
`outputs/ranking_compact_final_20260922`. Resultado final: 161/161 controles,
seis contextos en ocho anchos (320–1920 px), nombres/metadatos dentro de las
tarjetas, cifras alineadas, alturas acotadas, Reservas sin datos y filtro del
ranking por teclado. Capturas inspeccionadas de los seis contextos; sin errores
JavaScript ni escrituras HTTP. Los servidores temporales quedaron detenidos.

Contratos estáticos GUI: 31 correctos; responsive-inventory correcto;
inventory-context: 28 correctos. El CSS nuevo responde HTTP 200 en el servidor
habitual. No se ejecutó la suite Django completa ni se hizo commit/push.

## Estructura definitiva de lectura solicitada por el usuario

El ajuste posterior separa físicamente porcentaje y contexto en los cuatro
renderizadores, en vez de intentar distribuir una cadena conjunta mediante CSS.
Orden compartido: nombre completo; cantidad/porcentaje en una misma línea;
barra; Site, libres o notas en una línea de ancho completo debajo.
Troncales/Tramos no reservan fila de notas cuando no hay ninguna. Fibras muestra
el porcentaje ya entregado por la API y conserva sus tres cantidades de contexto.
No se recalculan capacidades, estados ni porcentajes.

Se eliminaron separadores redundantes entre elementos y se alineó el gráfico
con el comienzo de la leyenda. Se conserva el comportamiento de filtros,
teclado, colapsado, tamaños compactos y campos completos. Reservas mantiene su
disposición de dos columnas, sin inventar porcentajes para elementos físicos.

CSS y los cuatro JS tienen versión de recurso nueva en sus plantillas.
Evidencia: `outputs/ranking_structured_20260922/`, 209/209 verificaciones en
seis contextos y ocho tamaños. Se comprueba también el orden geométrico de
nombre, cantidad/porcentaje, barra y notas. Cero errores JS y escrituras HTTP;
servidor aislado cerrado al terminar. Capturas revisadas de escritorio/móvil.
Contratos GUI: 37 correctos, contexto de inventario: 28, responsive correcto y
sintaxis de los cuatro JS correcta. Esta revisión visual sigue sin sustituir
la regresión completa ni la aceptación estética del usuario.

## Propuesta reversible: leyenda inferior y menos recuadros

El usuario autorizó probar una composición diferente con posibilidad de volver
a la anterior. Se guardó el estado previo de los ocho archivos afectados en
`outputs/paneles_antes_leyenda_20260922/`, conservando los cambios locales previos.
No se debe usar un reset de Git para revertir este ajuste.

- Dona centrada de 88 px; debajo, estado, cantidad y porcentaje alineados en
  filas de ancho completo. Se mantienen las categorías sin información.
- Un solo aviso de filtros activos; secciones con separadores, sin tarjetas
  anidadas. En ODF se aclara que la distribución corresponde a sus puertos.
- Troncales usa indicadores, sin una dona que sugiera una distribución entre
  magnitudes distintas. Reservas mantiene cantidades y metros sin porcentajes.
- El contenido y orden del ranking se conservan. No se modifican Dashboard,
  ubicación del mapa, modelos, importadores ni cálculos de capacidad.
- Durante la inspección visual apareció un fallo anterior: volver a una pestaña
  ya cargada conservaba el resumen/ranking de la otra pestaña. Cada tabla ahora
  conserva su resumen, lo restituye al activarse e impide que respuestas de una
  pestaña oculta reemplacen el panel visible.

Verificación final: `outputs/ranking_legend_final_20260922/`, con capturas y
reporte de 259 comprobaciones correctas en seis contextos y ocho tamaños
(320 a 1920 px). Incluye volver de Tramos a Troncales, filtro por teclado,
alineación y orden de filas, leyenda inferior y ausencia de desbordamiento.
Cero errores JavaScript y escrituras HTTP; base copiada y servidor temporal
cerrado. Se inspeccionaron capturas de las seis variantes, móvil y tema oscuro.
Contratos GUI: 47 correctos; contexto de inventario: 28; responsive correcto;
sintaxis JS y `manage.py check` correctos. Sin suite completa ni commit/push.
El resultado estético queda sujeto a la aceptación del usuario.
> Nota 23/09/2026: documento histórico. Los cambios tipográficos y rediseños
> posteriores fueron revertidos a petición del usuario. Véase
> [REVERSION_VISUAL_20260923.md](REVERSION_VISUAL_20260923.md).

