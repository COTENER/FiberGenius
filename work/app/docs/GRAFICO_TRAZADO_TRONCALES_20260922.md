# Troncales por tipo de trazado

Sustituye el gráfico de estados de fibras por un gráfico de rutas:
Aéreas / Soterradas / Híbridas / Sin clasificar.

## Reglas

- Cada ruta del conjunto filtrado cuenta una vez. Sus tramos no multiplican el
  total. Las cuatro categorías son excluyentes y suman el total de troncales.
- Se utiliza `tipo_trazado_ruta`, la misma clasificación de la tabla, incluida
  su precedencia de coordenadas sobre tramos y la clasificación híbrida.
- No se crean estados operativos ni se cambian datos. El estado heredado del
  primer tramo no se utiliza en el gráfico ni se modifica en este ajuste.
- Se conserva el ranking y el detalle seleccionado; Dashboard y mapa no cambian.
- La API agrega `summary.trazados`. Se retira `summary.estados_fibras`, agregado
  exclusivamente para la propuesta visual anterior, y su consulta de estados.
- Sin rutas: mensaje sin barra ficticia. Sin clasificación: categoría gris.
  Se mantiene leyenda textual y descripción accesible de las proporciones.

## Evidencia

16 pruebas Django correctas (7 nuevas y 9 de regresión): tipos, mezcla de
tramos, 1/2/3 tramos, precedencia geográfica, ausencia de datos, filtros,
joins duplicados y contrato API. 24 comprobaciones JS de la barra, 47 contratos
GUI y 28 de contexto; sintaxis y diff sin errores.

261 comprobaciones visuales y de interacción en seis contextos y ocho tamaños
(320–1920 px), con copia aislada: `outputs/ranking_trace_20260922/`.
Capturas inspeccionadas de escritorio y móvil. Sin errores JS ni escrituras
HTTP; servidor de pruebas cerrado. No se ejecutó la suite completa ni
PostgreSQL real.

Consulta de solo lectura a la base local actual: 359 rutas SIN CLASIFICAR.
Por eso el gráfico actual muestra 100 % gris; no se imputó una clasificación.

Copia previa en `outputs/antes_trazado_troncales_20260922/`. Las dos pruebas
específicas de la barra anterior se sustituyeron por las de trazado; sus copias
permanecen en ese respaldo. Sin migraciones, cambios de datos ni commit/push.
> Nota 23/09/2026: documento histórico. Los cambios tipográficos y rediseños
> posteriores fueron revertidos a petición del usuario. Véase
> [REVERSION_VISUAL_20260923.md](REVERSION_VISUAL_20260923.md).

