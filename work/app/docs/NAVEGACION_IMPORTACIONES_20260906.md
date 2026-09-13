# Importaciones guiadas por tareas — 2026-09-06

## Respaldo previo

Antes de implementar la navegación se creó y publicó el checkpoint:

- Commit: `cb846867c15f850c86225ede1f38ea37cb1fd561`.
- Repositorio: `https://github.com/COTENER/FiberGenius`.
- Rama: `agent/import-fibergenius-rc2-modelo-definitivo`.
- HEAD local y remoto verificados iguales después del push normal, con upstream.
- 69 archivos de código, pruebas y documentación. No incluye bases de datos,
  archivos cargados, backups ni logs. `work/app/data/` permanece local sin seguimiento.

## Qué se implementó

La pantalla de importaciones pregunta «¿Qué quieres cargar?» y presenta:

1. Infraestructura: Sites, ODF, datos adicionales de puertos.
2. Fibras y conexiones: alta/actualización global, terminaciones A/B.
3. Troncales y recorrido: troncales, tramos, fibras por tramo.
4. Complementos: geometría CSV, reservas y elementos físicos.

Las áreas empiezan cerradas; al abrir una se cierra la anterior. Sus opciones
son botones nativos, utilizables con teclado. Las etiquetas explican requisitos
y cargas opcionales, en lugar de una numeración global de pasos obligatorios.

El inicio rápido «Tengo ODF y sus conexiones» abre la guía:

**Registrar ODF → Conectar A/B → Consultar resultado.**

No obliga a pasar por Sites, puertos vacíos, alta global ni troncal. Permite
empezar por conexiones si ya existen los ODF. Cada botón abre el modal actual,
con plantilla y ejemplo. La guía no crea datos ni marca pasos como completados
sin comprobar una importación. Su apertura se conserva al recargar mediante
`?tarea=odf-conexiones`, sin perder otros filtros de la URL.

«Avanzado / Integraciones» conserva equipos OTU, asociación OTU/troncal, IDs
ONMSI y los dos formatos ZIP. La plantilla técnica de terminaciones sigue
disponible bajo sus permisos. Las operaciones de puertos se explican y enlazan
a su pantalla existente; no se confunden con datos descriptivos del puerto.

## Garantías y límites del cambio

- Se conservan las diez plantillas principales y los quince procesadores.
- No se modificaron CSV, modelos, migraciones, servicios de carga ni permisos.
- No se modificaron Dashboard, mapa ni gráficos.
- El historial, progreso y recuperación de cargas pendientes se conservan.
- Abrir otra tarjeta mientras hay una carga activa vuelve a mostrar su progreso;
  no cambia el tipo del archivo que ya se está procesando.
- El modal tiene nombre accesible, devuelve el foco al cerrar y mantiene la
  navegación por Tab dentro del diálogo. Cerrar no cancela una carga iniciada.
- La prevalidación sigue siendo estructural. La pantalla declara que el servidor
  valida datos y relaciones al ejecutar; no se presenta como una vista previa
  completa contra la base de datos.

La centralización de contratos, el informe completo de errores descargable y
la prevalidación real del servidor siguen siendo fases posteriores de la
propuesta, no parte de esta reorganización visual.

## Comprobación

- Grupo focalizado: 47 pruebas correctas (navegación, carga guiada, troncales,
  progreso y terminaciones).
- JavaScript: sintaxis de scripts y 23 aserciones sobre apertura/cierre,
  persistencia de guía, áreas, selección de importador, protección de carga activa
  y recuperación de una referencia de progreso que ya no está disponible.
- Suite completa de `mapas`: 460 pruebas descubiertas, 447 correctas y 13 de
  PostgreSQL omitidas; `OK (skipped=13)`, código de salida 0, 399,711 segundos.
  Evidencia local: `.tmp-review/suite_navegacion_importaciones.log`.
- `manage.py check` correcto; sin migraciones pendientes de generar; diff sin
  errores de espacios. No se aplicaron migraciones nuevas.
- Navegador integrado: se intentó conectar mediante la habilidad de navegador;
  el entorno devolvió `missing field sandboxPolicy`. No se declara comprobación
  visual interactiva, capturas ni prueba de usabilidad con un usuario realizadas.

Pruebas nuevas: `mapas/tests_navegacion_importaciones.py` y
`scripts/tests/import-navigation.cjs`.

El checkpoint anterior está publicado. Los cambios de esta navegación se
mantienen locales para revisión, sin un segundo push automático.
