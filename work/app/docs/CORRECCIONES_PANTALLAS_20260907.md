# Correcciones de pantallas — 7 de septiembre de 2026

## Alcance

Se corrigieron las siete observaciones de Dashboard, mapa, ODF y consultas
de inventario, además del destino interno de las troncales multitramo.
No se cambiaron modelos, migraciones, estados del inventario ni el diseño
del Dashboard. Los datos operativos existentes no se modificaron.

## Comportamiento

- El Site activo se sincroniza al cambiar el selector, enviar el formulario
  o limpiar los filtros de ODF/Puertos. Un Site explícito de una consulta no
  se reemplaza silenciosamente por el anterior. El almacenamiento del navegador
  es opcional; su indisponibilidad no bloquea las consultas.
- Los filtros resuelven los extremos Site/ODF por sus relaciones canónicas.
  Los textos históricos se usan solo cuando el extremo no tiene relación
  canónica. Una troncal seleccionada mantiene el total de todos sus tramos.
- Dashboard cuenta las troncales registradas aunque su recorrido esté pendiente.
- Dashboard y Troncales comparten la clasificación y filtrado de trazado:
  geografía primero; si no tiene clasificación geográfica, tramos técnicos.
  Aéreo y soterrado incluyen rutas con ese componente; híbrido incluye mezclas.
  La geografía marcada DESCONOCIDO no se interpreta como trazado conocido.
- Mapa de red solicita y aplica el Site activo. La ruta conserva su geometría
  completa, pero los marcadores Site se limitan al seleccionado. Sin filtro
  se entrega toda la red, como antes.
- El minimapa conserva los segmentos aéreos/soterrados de una ruta híbrida.
- El destino interno del Dashboard corresponde al último tramo. Si falta,
  no se reutiliza el destino de un tramo intermedio.

## Protección de desconexiones

La respuesta de consulta de puertos incluye `conexion.terminacion_id`.
La GUI lo envía como `terminacion_esperada` al desconectar.

El endpoint GUI exige esa identidad. Una página antigua sin el campo recibe
un error de validación que pide actualizar. Si la conexión fue sustituida,
se devuelve HTTP 409 con código `CONEXION_DESACTUALIZADA`, sin desconectar la
nueva fibra, cambiar su estado ni registrar una operación exitosa.

La comprobación se realiza dentro de la transacción y los bloqueos existentes.
El servicio interno conserva su parámetro opcional para no romper las
operaciones explícitas CSV/Excel existentes. No se cambian sus plantillas.

## Verificación

- Regresiones Python: `mapas.tests_revision_pantallas`.
- Regresiones JavaScript: `node scripts/tests/inventory-context.cjs`.
- Suite general: `manage.py test mapas --settings=fibergenius.settings_pruebas`.
- Los tests usan una base aislada, nunca el inventario operativo.
- La verificación visual quedó pendiente: el navegador integrado no logró
  conectarse. Las pruebas de JavaScript no sustituyen esa verificación.
- No se ejecutaron escenarios de concurrencia contra PostgreSQL real.

Se actualizaron las versiones de los recursos JavaScript. El proceso Django
debe cargar el código nuevo y el usuario debe recargar la página antes de operar.

### Resultados de esta revisión

- Suite general: 522 pruebas, 509 correctas y 13 omitidas de PostgreSQL.
- Pasada final tras los ajustes de filtros: 85 pruebas correctas (18 nuevas
  regresiones, 46 de pantallas existentes y 21 de gestión de puertos).
- JavaScript: 65 comprobaciones correctas, incluidas 26 nuevas de contexto,
  mapa híbrido y contrato de desconexión.
- `manage.py check`: correcto. `makemigrations --check --dry-run`: sin cambios.
- Diez vistas/consultas sobre la base operativa respondieron HTTP 200 mediante
  RequestFactory, con SQLite `query_only=ON` para impedir escrituras. Esto valida
  la respuesta del servidor, no la representación visual en el navegador.
