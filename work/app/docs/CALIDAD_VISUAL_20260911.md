# Calidad de información: rediseño visual

## Referencias y decisiones

Se revisaron los patrones de resumen, dimensiones y acceso a registros de:

- [Microsoft Purview: Data quality health report](https://learn.microsoft.com/en-us/purview/data-quality-health-report).
- [Collibra: About the data quality dashboard](https://productresources.collibra.com/docs/collibra/latest/Content/UnifiedDataQuality/Dashboard/co_about-data-quality-dashboard.htm).

Se adaptó la separación entre visión general y detalle, no sus fórmulas de puntuación ni sus integraciones. Fiber Genius no dispone de un histórico de mediciones ni de un denominador global de calidad validado: no se inventan tendencias, porcentajes generales, objetivos ni niveles de servicio.

## Presentación

- Tres indicadores: observaciones pendientes, inconsistencias y controles sin casos detectados sobre controles disponibles.
- Barras horizontales de pendientes por control, de mayor a menor. Las longitudes comparan cantidades absolutas frente al máximo visible; no representan porcentajes de completitud. Cada barra conserva la cifra exacta, incluyendo cero, y abre los registros del control.
- Anillo de resultados de controles: sin casos, con pendientes o con inconsistencias. Las categorías son excluyentes para cada control. Se explicita que un control sin casos puede no tener registros evaluables, y no certifica el inventario.
- Panel separado y compacto de relaciones inconsistentes, con acceso al detalle. No mezcla fallas físicas ni alarmas de servicio.
- La tabla no se carga al entrar: se consulta únicamente tras seleccionar un control. Se mantienen búsqueda, paginación, Site y fichas 360°. Cerrar detalle conserva el Site y regresa a la vista general.
- Explicaciones amplias bajo un desplegable nativo; significado de las cifras y advertencia sobre activos únicos siempre visibles.
- CSS y SVG nativos, sin librerías adicionales, CDN ni JavaScript para dibujar los gráficos. Colores de la aplicación, reglas para tema oscuro, ancho reducido, teclado y movimiento reducido. Los gráficos conservan etiquetas y cifras legibles sin depender solo del color.

## Lógica y seguridad

Los doce controles y sus permisos no cambian. `_resumen_visual` transforma los conteos existentes sin consultar ni escribir en la base. Las barras usan porcentajes únicamente como coordenadas de dibujo; la interfaz no los presenta como calidad. Los valores geométricos se renderizan sin localización decimal para mantener SVG/CSS válidos en español.

Sin cambios en modelos, migraciones, importadores, dashboard operativo, estados ni conexiones. Sin commit ni push. Se conservan los cambios previos del árbol de trabajo.

## Validaciones

- 119 pruebas de regresión correctas: Calidad (29), Dashboard, revisión de pantallas, GUI operativa y administración.
- Siete regresiones nuevas cubren entrada sin tabla, enlaces de barras con Site, anillo de controles frente a cantidad de observaciones, cero resultados, orden y escala de barras, números SVG y permisos.
- 28 comprobaciones JavaScript correctas del contexto de Site existente.
- `manage.py check`: correcto. `makemigrations --check --dry-run`: sin cambios. `git diff --check`: sin errores.
- Base actual con `PRAGMA query_only=ON`: vista general, dos variantes de Site y doce controles respondieron 200, sin escrituras. Vista general: 13 consultas y 0,28 s en esta medición local; el detalle se consulta solo al seleccionarlo.
- El navegador integrado falló al conectar antes de abrir la aplicación. No se ha verificado visualmente el resultado, los tamaños de pantalla ni la interacción real del navegador. Las pruebas de render no sustituyen esa comprobación.
- No se ejecutó la suite completa ni pruebas PostgreSQL para este cambio de presentación.
