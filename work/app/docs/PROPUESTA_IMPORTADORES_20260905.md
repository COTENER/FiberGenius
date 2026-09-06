# Importadores de Fiber Genius: propuesta revisada

## Decisión recomendada

Simplificar las decisiones del usuario, no mezclar las entidades del inventario.
Mantener el motor y los servicios existentes. Presentar cuatro áreas de trabajo
y una sección avanzada para integraciones y compatibilidad.

Esta es una propuesta para evaluar: en este bloque no se han reemplazado las
plantillas ni reorganizado la pantalla de importaciones. Las correcciones de
integridad aprobadas sí afectan a los procesadores actuales.

## Qué hay realmente hoy

Fuentes: `mapas/views/importacion.py` (registro y procesadores),
`mapas/templates/configuracion/configuracion_index.html` (tarjetas, guías y
validación inicial), `mapas/services/importacion_puertos.py` (operaciones) y
`docs/IMPORTACIONES_MASIVAS.md` (motor y recuperación).

- 15 tipos registrados en el motor general; no son 15 pasos obligatorios.
- 10 tarjetas principales de inventario físico y otro modo operativo.
- Además, las operaciones de puertos tienen su propio flujo explícito.
- Terminaciones admite dos formatos: A/B en una fila y formato técnico.
- Geografía tiene CSV y dos entradas ZIP de compatibilidad.
- OTU, asociación OTU/ruta y ONMSI no son requisitos del inventario físico.

### Observaciones prioritarias

1. **Demasiadas opciones al mismo nivel.** Datos básicos, recorridos, metadatos
   opcionales y acciones de conexión parecen pasos equivalentes.
2. **Secuencia visual engañosa.** No hace falta cargar todos los archivos ni
   empezar por una troncal. Crear ODF ya materializa sus puertos. El formato
   simplificado de terminaciones puede crear fibras sin troncal; el CSV global
   no es un prerrequisito universal.
3. **Contratos descritos en varios lugares.** Los requisitos están en Python,
   guías JavaScript y plantillas. Pueden divergir aunque el motor sea único.
4. **La prevalidación visible es estructural.** `validateImportFile()` comprueba
   extensión, tamaño, cabeceras y muestra cinco filas. La validación de relaciones
   se hace al ejecutar en servidor. El texto actual lo aclara, pero no existe
   todavía una previsualización completa de cambios contra la base.
5. **Diagnóstico masivo recortado.** `_errores_csv` presenta una muestra y
   `_cerrar_lote_fallido` conserva el mensaje limitado a 1.000 caracteres. Falta
   un detalle descargable completo por fila/campo/valor/motivo.
6. **Integraciones mezcladas con inventario.** La clave interna `ruta_otu` se
   utiliza también para troncales. No debe trasladar al usuario la obligación de
   instalar OTU o recargar geometría para editar metadatos.
7. **Actualización y operación deben distinguirse.** Un archivo descriptivo de
   puertos no debe mover fibras ni cambiar una reserva. El flujo de operaciones
   de puertos debe seguir existiendo, pero no confundirse con una carga inicial.

## Propuesta de navegación

| Área visible | Opciones dentro | Dependencia real |
|---|---|---|
| Infraestructura | Sites; ODF; datos adicionales de puertos (opcional) | ODF puede tener ubicación pendiente. Su capacidad genera posiciones. |
| Fibras y conexiones | Registrar fibras sin conexión; cargar A/B; actualizar desde plantilla del sistema; operaciones de puertos | Terminar requiere ODF/puertos existentes, no una troncal obligatoria. |
| Troncales y recorrido | Troncales; tramos; fibras por tramo | La asignación requiere fibra y tramo existentes. |
| Complementos de planta externa | Recorrido geográfico CSV; reservas y elementos físicos | La geometría no crea recorrido técnico de fibra. |

En **Avanzado / Integraciones**: equipos OTU, asociación OTU/ruta, IDs ONMSI,
formatos técnicos y ZIP anteriores. No eliminarlos sin confirmar quién los usa.
Mantener permisos actuales o endurecerlos explícitamente, nunca ampliarlos por
haber agrupado tarjetas.

No son cuatro CSV universales: son cuatro áreas. Cada tabla conserva su identidad
y sus reglas. Mostrar solo las opciones y dependencias pertinentes al caso.

## Flujo mínimo de carga

### Solo se conocen ODF y conexiones

1. Cargar ODF y capacidad; Site/Sala/Rack se completan cuando se conozcan.
2. Cargar terminaciones simplificadas, A/B en la misma fila. Puede informarse un
   solo extremo, conservando las cabeceras del otro con valores vacíos.
3. Más adelante, asociar troncal y recorrido de manera explícita.

No exigir antes CSV de Sites, puertos vacíos, fibras globales ni troncales.
Si las fibras ya existen sin terminación y la identificación es ambigua, usar
una plantilla generada con identidad estable; no crear duplicados por adivinación.

### Se conocen las fibras, pero no sus ODF

Conservar el alta global independiente. Identidad estable generada o suministrada
según contrato; no exigir una ruta ni una terminación ficticia. Cuando se conozca
el ODF, enlazar la fibra existente con plantilla identificada o Gestionar conexión.

### Se conoce el recorrido

Troncal → tramos → asignaciones de fibras por tramo. Cada fila indica qué fibra
pasa por qué tramo y qué posición ocupa. La troncal no crea asignaciones ni un
cambio de estado las deduce, aunque exista un solo tramo.

La selección por troncal + número puede simplificar la identidad cuando ambos
son conocidos y únicos. Mantener `codigo_fibra`/identidad estable para provisionales,
actualizaciones y casos ambiguos. El usuario no debería inventar ni memorizar IDs.

## Contrato común propuesto

- Una plantilla canónica por operación; aliases antiguos como adaptadores de
  entrada, sin una segunda lógica de negocio.
- Un registro declarativo pequeño en servidor para cabeceras, aliases,
  obligatoriedad por modalidad, tipos, ejemplos y permisos. Generar de él la
  ayuda y plantillas; no crear un lenguaje de reglas ni un framework adicional.
- En actualizaciones: columnas/celdas opcionales vacías conservan datos;
  borrado explícito conserva el marcador ya existente; cero sigue siendo dato.
- Distinguir **Registrar/completar**, **Actualizar metadatos** y **Ejecutar una
  operación**. No convertir un CSV incompleto en una orden de desconexión.
- Mismo archivo y mismos datos: sin duplicar fibras, conexiones ni eventos de
  movimiento. Diferenciar filas procesadas de objetos realmente modificados.
- Errores de integridad bloquean el lote completo. Ubicación/recorrido pendientes
  son advertencias de calidad, no motivo para rechazar una carga válida.
- Máximo una terminación por extremo y por puerto. Número global único dentro
  de la troncal; posición única en cada tramo. Una fibra puede recorrer varios
  tramos. F17 de dos fibras distintas no se admite en la misma troncal.
- Los movimientos requieren acción explícita y validación contra el estado
  actual. Actualizar ambos extremos mediante identidad estable precargada.

## Validación, progreso y errores

Reutilizar lotes, archivo persistido, worker y progreso actuales.

Primera mejora: informe estructurado de errores completo y descargable. Columnas:
fila, entidad, campo, valor, motivo y orientación de corrección. Acceso limitado
por permisos y política de retención; no exponer trazas internas ni archivos ajenos.

Segunda: prevalidación real del servidor y resumen **crear / modificar / sin
cambios / advertencias / errores**. Separar gradualmente preparar/validar/aplicar
en los procesadores; no simular una importación escribiendo en producción para
luego deshacerla. Evitar efectos de señales y archivos durante la prevalidación.

La confirmación debe usar exactamente el archivo validado (hash y tipo). Volver
a comprobar relaciones y permisos dentro de la transacción final: una vista
previa no congela la base mientras el usuario la revisa. Si cambió una dependencia
crítica, pedir nueva revisión; no aplicar un plan antiguo silenciosamente.

Progreso por etapas reales: archivo recibido, validación, aplicación y confirmación.
100 % solo después del commit. Evitar porcentajes ficticios durante consultas largas.

## Revisión iterativa de alternativas

| Pasada | Alternativa examinada | Decisión |
|---|---|---|
| Simplicidad | Un único CSV para Site/ODF/fibra/tramo/estados | Descartar: muchas columnas, repetición y actualizaciones ambiguas. |
| Integridad | Eliminar alta global y obligar a tener troncal | Descartar: impide inventario progresivo y otros clientes. |
| Experiencia | Mantener todos los botones, solo renombrarlos | Insuficiente: conserva la duda sobre qué cargar y cuándo. |
| Compatibilidad | Retirar todos los importadores antiguos inmediatamente | Descartar: puede romper cargas e integraciones existentes. |
| Resultado | Agrupar por tarea, compartir contratos y servicios, retirar redundancias por etapas | Mejor equilibrio para el código actual y las reglas aprobadas. |

## Orden sugerido y criterios de aceptación

1. Ordenar navegación y ayuda, sin cambiar archivos ni reglas; corregir requisitos
   visuales que parecen obligatorios cuando no lo son.
2. Contrato central de cabeceras/aliases/plantillas y errores descargables.
3. Prevalidación real y resumen de cambios, por familia de importador, reutilizando
   lotes/progreso; conservar ejecución transaccional e idempotencia.
4. Plantillas de actualización precargadas y, solo tras revisar uso, ocultar o
   retirar formatos redundantes con compatibilidad documentada.

Validar cada etapa con: carga inicial; recarga idéntica; archivo parcialmente
incorrecto sin cambios; opcionales omitidos; borrado explícito; fibras sin ruta;
extremo faltante; movimiento prohibido sin operación; uno/dos/cuatro tramos;
números únicos por troncal; ODF sin ubicación; permisos; errores masivos;
recuperación de lote tras reinicio. Concurrencia real PostgreSQL queda para el
bloque que el usuario decidió posponer.

No propongo otra base, microservicios, Celery ni un modelo adicional de cables
para resolver la organización de importadores. Tampoco cambios al Dashboard.
