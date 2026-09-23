# Correcciones de inventario — 8 de septiembre de 2026

## Alcance

Se corrigen las cinco observaciones de la revisión anterior. No se cambia el
modelo de fibras, el diseño del Dashboard, los gráficos ni los datos existentes.
No hay migraciones nuevas.

1. **Distancia geográfica:** `inicio_segmento` es una separación visual, también
   utilizada al cambiar de trazado. El resumen conserva la distancia entre los
   puntos, igual que el importador y el mapa. Se mantiene la prioridad de la
   distancia declarada y del inventario técnico sobre la geografía.
2. **Conectar fibras de otro Site:** el buscador de Gestionar conexión envía
   `site=` explícito. Puede encontrar una fibra cuyo otro extremo está en otro
   Site o que aún no tiene ubicación/troncal. El filtro de la tabla no cambia;
   tampoco los permisos ni las validaciones del servicio de conexión.
3. **Contadores de troncales:** búsqueda y filtros seleccionan las troncales;
   posteriormente se cuentan todas sus relaciones. Buscar una fibra o un Site
   ya no recorta la cantidad mostrada de fibras/tramos/reservas de la troncal.
4. **Respuestas de búsqueda fuera de orden:** cancelación con AbortController
   y versión de consulta. Teclear, cambiar la consulta o cerrar/reabrir el diálogo
   invalida los resultados pendientes. Una respuesta antigua no pisa la actual.
5. **Descargas sin sesión:** los cuatro clientes de inventario comparten una
   validación de respuesta antes de guardar archivos. Se rechazan redirecciones,
   errores HTTP, HTML y respuestas sin MIME/attachment de CSV o XLSX. Se informa
   al usuario cuando debe iniciar sesión de nuevo.

## Verificación

- Suite completa `mapas`: 531 pruebas, 518 correctas y 13 omitidas por requerir
  PostgreSQL; sin fallos (526,723 segundos). Evidencia local:
  `.tmp-review/correcciones-20260908-suite.log`.
- Pruebas focalizadas de pantallas y motor: 30 correctas.
- Revisión de pantallas repetida tras ampliar cobertura de exportaciones:
  23 correctas; verifica CSV/XLSX de los seis recursos exportables.
- JavaScript: 110 comprobaciones correctas (45 nuevas, 65 existentes).
- `manage.py check`: correcto.
- `makemigrations --check --dry-run`: sin cambios pendientes.
- `git diff --check`: sin errores de formato.
- Seis vistas principales renderizadas correctamente con la base local en modo
  SQLite `query_only`; ninguna escritura de inventario durante esta comprobación.
- Reinicio con cero importaciones pendientes/procesando. Servidor en
  `http://127.0.0.1:8001`; login y JavaScript actualizado responden HTTP 200.

Las pruebas JavaScript utilizan un entorno simulado; no sustituyen una revisión
visual e interactiva en navegador. La concurrencia PostgreSQL requiere una
instancia PostgreSQL y no se valida mediante SQLite. No se hizo commit ni push.
