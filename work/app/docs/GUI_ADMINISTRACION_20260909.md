# Correcciones de GUI — 9 de septiembre de 2026

## Alcance implementado

- `ui_access.py` concentra las reglas de consulta de las pantallas y las reglas existentes de importación. Menú y vistas reutilizan las mismas condiciones; no se conceden permisos a usuarios ni se migran roles.
- Resumen conserva la autorización real `mapas.view_ruta`, compartida con mapa y troncales. El formulario de Roles muestra ahora ese permiso, no `can_view_reports`. Los interruptores de un mismo permiso se sincronizan entre módulos.
- Importación es visible y accesible con todos los permisos requeridos por al menos un importador, o con `view_loteimportacion` para consultar el historial. Los botones de carga se muestran según el permiso de cada importador. Sin ninguno de esos permisos, GET y POST están bloqueados.
- Usuarios y Roles ocultan acciones no autorizadas. Un controlador de modal común conserva el formulario ante errores, impide doble envío, descarta respuestas antiguas y distingue una validación de un guardado confirmado mediante JSON.
- Una pérdida de conexión o espera superior a 30 segundos al guardar muestra que el resultado es incierto y solicita revisar el listado antes de reenviar. Nunca se reenvía automáticamente. Los botones se recuperan; el modal no se cierra mientras se envía.
- Roles conserva la selección enviada al fallar la validación, sin modificar permisos persistidos. El siguiente envío sigue funcionando en el modal.
- La tabla de Usuarios tiene su traducción en el código local y no consulta el CDN de DataTables.
- Sites tiene búsqueda por nombre y paginación de 25 registros desde servidor. El filtro se conserva al navegar; las coordenadas cero no se muestran como ausentes.
- El botón Importar CSV de Sites abre el importador central de Sites, sin subir nada automáticamente. Ya no existe el modal de carga síncrona en esa pantalla. El endpoint antiguo se conserva por compatibilidad, usando el servicio auditado existente.
- Las cuatro pantallas opcionales de Operaciones devuelven 404 cuando `FIBERGENIUS_OPERATIONS_UI_ENABLED=False`. No se modificaron sus consultas SQL ni se desactivaron integraciones/webhooks.

## Validación

- 111 pruebas de regresión correctas: GUI operativa, Dashboard, revisión de pantallas, navegación y progreso de importación, y administración.
- Tras el ajuste final de la agrupación de permisos, 14 pruebas focalizadas de administración correctas.
- 166 comprobaciones JavaScript correctas, incluyendo 36 de administración: validación/reenvío, 401/403/500, conexión, respuesta inesperada, doble envío, respuestas tardías y permisos compartidos.
- Las 13 pantallas principales responden 200 mediante RequestFactory con la base actual en modo `PRAGMA query_only=ON`; los nuevos recursos estáticos se resuelven localmente.
- `manage.py check` correcto; `makemigrations --check --dry-run` sin cambios; `git diff --check` sin errores.
- No se ejecutó la suite completa del proyecto ni validación contra PostgreSQL en este bloque.

## Límites y pendientes

- El navegador integrado no pudo conectarse. No se certifica la presentación visual, resoluciones, foco o interacción real con ratón hasta completar esa revisión.
- La compatibilidad SQL de Dashboard y Ranking de Operaciones queda para un bloque separado, antes de habilitarlos.
- No se modificaron modelos de fibras, migraciones, gráficos del Dashboard ni datos de inventario. No se realizó commit ni push.
