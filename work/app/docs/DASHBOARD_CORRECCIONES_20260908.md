# Dashboard: correcciones funcionales y ajustes visuales

## Alcance

- Se conservan los datos, los modelos y la estructura principal del Dashboard.
- Los filtros de Site y trazado se trasladan explícitamente a las exportaciones,
  fibras, reservas, troncales y mapa. El trazado selecciona rutas; no filtra ODF
  ni puertos. Esta diferencia se explica junto a los controles.
- Fibras y reservas muestran un selector de trazado, también usado para exportar
  y eliminable con Limpiar. El mapa completo informa del trazado heredado y
  permite quitarlo. Los rankings abren el activo por identificador exacto y su
  filtro puede quitarse sin que reaparezca desde la URL al recargar.
- El API geográfico incorpora `sites_relacionados`, resueltos mediante las
  relaciones oficiales de los extremos técnicos, con fallback legacy cuando
  no existe vínculo oficial. Se mantiene el contrato anterior de segmentos.
- La disponibilidad ODF usa el detalle de puertos, no capacidad menos ocupados.
  Se cuentan aparte las posiciones sin registrar. El filtro de destino del
  enlace «ODF sin libres confirmados» utiliza la misma regla que el Dashboard.
- El nombre de Site se normaliza a su capitalización canónica al ingresar al
  Dashboard. La selección no pierde ODF/Sites al escribir otra capitalización.
- «Inventario cargado» no afirma calidad ni operatividad. El gráfico distingue
  «Sin estado informado» de completitud del recorrido.

## Presentación

- Textos de ayudas/rankings aumentados a 11–12 px, con ajuste de línea.
- Rankings con enlaces identificables y foco visible para teclado.
- Sección de pendientes: estado de fibras, geometría de troncales y registro
  de puertos. No son nuevos estados operativos ni alarmas de servicio.
- Advertencia separada si falla una tesela del mapa. Las capas del inventario
  permanecen disponibles; una carga posterior sin errores retira la advertencia.
- Versiones de recursos estáticos actualizadas para invalidar caché.

## Verificación

- Pruebas de regresión: `mapas.tests_dashboard_correcciones`.
- Pruebas de comportamiento JavaScript: `scripts/tests/dashboard-review.cjs`.
- JavaScript: 130 comprobaciones correctas (20 nuevas y 110 previas).
- Suite general: 537 pruebas, 13 omitidas por requerir PostgreSQL; detectó una
  aserción que esperaba el rótulo anterior «ODF sin puertos libres». Se actualizó
  a «ODF sin libres confirmados», comprobando además que no se afirma 100% de
  compromiso cuando faltan puertos. Log: `.tmp-review/dashboard-correcciones-suite.log`.
- Tras el último ajuste de combinación Site/trazado, las 30 pruebas focalizadas
  de pantallas pasaron. La prueba nueva adicional se ejecutó en esa pasada;
  la suite general había descubierto 537 pruebas antes de añadirla.
- Validación final tras actualizar la aserción de texto: 76 pruebas correctas
  (GuiOperativaTests, DashboardCorreccionesTests y RevisionPantallasTests),
  99,226 segundos. Log: `.tmp-review/dashboard-correcciones-final.log`.
  No se repitió toda la suite general después de esa actualización de la prueba.
- `manage.py check`: correcto; `makemigrations --check --dry-run`: sin cambios.
- Consultas de las vistas sobre la base local con `PRAGMA query_only=ON`: HTTP 200.
- La herramienta de navegador no pudo iniciar por un problema del entorno
  (`sandboxPolicy`). No se afirma validación visual/interactiva en navegador;
  esa revisión sigue pendiente. Las pruebas JavaScript usan un entorno simulado.
- No se hizo commit ni push.
- Servidor reiniciado sin importaciones activas. Recursos JavaScript nuevos y
  aplicación/login responden HTTP 200 en `http://127.0.0.1:8001`.
