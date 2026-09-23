# Barra de uso de fibras en Troncales

**Registro histórico: sustituido el 22/09/2026 por el gráfico de troncales por
tipo de trazado, a solicitud del usuario.** El gráfico de fibras y su agregado
de estados ya no forman parte de este panel. Las pruebas específicas se
reemplazaron por `tests_resumen_trazado_troncales.py` y `routes-trace-chart.cjs`.

Cambio solicitado: añadir una barra horizontal apilada al resumen lateral,
con Disponibles / Ocupadas / Reservadas / Sin información. Se conserva el
ranking, la posición del mapa y el Dashboard.

## Criterio de conteo

`_resumen_troncales` agrega estados de `InventarioFibra` asociadas a las rutas
del queryset filtrado. Reutiliza `_anotar_cobertura_fibras`, la misma regla de
estado efectivo de la pestaña Fibras. El informado tiene prioridad; la inferencia
desde tramos mantiene las reglas existentes. No se cambia ningún estado.

Cada fibra global cuenta una vez, independientemente del número de asignaciones
a tramos. No se utilizan capacidad declarada, puertos ni sumas de hilos por tramo
como denominador. Las fibras sin troncal no pertenecen a este resumen.

La API añade `summary.estados_fibras`; su total es también `summary.fibras`.
Las cuatro categorías suman ese total. El ranking mantiene su criterio previo
de capacidad y uso por troncal, que no es el denominador de esta barra.

Si no hay fibras, se muestra un mensaje sin una barra ficticia. Si falta el
resumen o sus cantidades no concilian, se informa que no está disponible.
Porcentajes visuales redondeados a dos decimales; anchos calculados sin redondeo.
Leyenda textual y descripción accesible de la barra, sin depender solo del color.

## Verificación

- 16 pruebas Django correctas: siete nuevas y nueve de regresión de lecturas y
  troncales sin tramos. Casos de 1/2/3 tramos, cobertura incompleta, prioridad
  del estado informado, filtros, ausencia de fibras y contrato API.
- 24 comprobaciones JS de proporciones, leyenda, accesibilidad y estados vacíos
  o inconsistentes. Contratos GUI 47; contexto 28; sintaxis correcta.
- 261 comprobaciones de navegador en seis contextos y ocho tamaños, sobre una
  copia aislada: `outputs/ranking_usage_20260922/`. Cero errores JS y escrituras
  HTTP; servidor temporal cerrado. Capturas de escritorio, móvil y tema oscuro.
- PostgreSQL real y suite completa no ejecutados en este ajuste.

Copia de los cuatro archivos de aplicación previos:
`outputs/antes_barra_troncales_20260922/`. Preserva modificaciones anteriores;
una reversión debe limitarse a este cambio. Sin migraciones ni edición de datos.
> Nota 23/09/2026: documento histórico. Los cambios tipográficos y rediseños
> posteriores fueron revertidos a petición del usuario. Véase
> [REVERSION_VISUAL_20260923.md](REVERSION_VISUAL_20260923.md).

