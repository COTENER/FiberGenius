# Importaciones masivas de Fiber Genius

## Contrato operativo

- Cada archivo se valida completo antes de confirmar cambios.
- Si una fila es inválida, se revierte el archivo completo.
- En columnas opcionales, una celda vacía conserva el valor existente.
- `__BORRAR__` o `__ELIMINAR__` limpia explícitamente un valor opcional.
- El valor `0` es un dato real y no equivale a vacío.
- La ocupación de un puerto ODF solo se crea mediante una terminación oficial
  o mediante la plantilla explícita de operaciones de puertos.
- Cada ejecución crea un `LoteImportacion` con usuario, archivo, SHA-256,
  resultado y detalle de error.

Las pantallas auxiliares de Sites, reservas y fibras por tramo son adaptadores
del mismo motor. No mantienen reglas de importación independientes.

## Encabezados y columnas no reconocidas

- Se revisa la cabecera original antes de que pandas pueda renombrar columnas
  duplicadas. `Direccion,Direccion` y los aliases equivalentes se rechazan.
- Los procesadores declaran sus columnas admitidas. Una columna desconocida,
  aunque esté vacía, detiene el archivo completo: el mensaje indica su nombre
  y, cuando corresponde, sugiere una posible corrección. No se renombra ni
  descarta automáticamente; el usuario debe corregirla o retirarla.
- Los aliases ya soportados se conservan. La cabecera en blanco se rechaza;
  en Excel de operaciones se permiten columnas completamente vacías de formato,
  pero no datos bajo una cabecera vacía.
- La validación cubre CSV generales, CSV dentro de ZIP, archivos auxiliares de
  fibras/reservas y Excel de operaciones. No cambia estados ni identidades.
- `hub_site` en el CSV geográfico se conserva por compatibilidad, pero cuando
  contiene datos su omisión se muestra como advertencia en el historial.
- En Fibras por Tramo se conservan las columnas antiguas `tipo_de_servicio`,
  `origen`, `destino` y `conector`. No modifican la fibra global ni sus
  terminaciones; su omisión se advierte explícitamente en el resultado y lote.
- Las diez plantillas principales y sus ejemplos se verifican mediante pruebas.
  El ejemplo OTU utiliza OTU, Puerto, Modelo, Latitud, Longitud y Estado, con
  coordenadas coherentes por equipo. El formato antiguo que repita Descripcion
  y OTU sigue rechazándose; no se decide arbitrariamente cuál prevalece.

## Revisar y confirmar (septiembre 2026)

- En el modal general, **Validar archivo** envía CSV/ZIP al servidor y muestra su
  avance. La validación de cabeceras/alias y relaciones es la del procesador
  oficial, no una segunda lista JavaScript. El límite de tamaño procede de la
  configuración del servidor; las ayudas describen las plantillas recomendadas.
- La revisión utiliza una transacción anidada que siempre se revierte, incluidos
  puertos generados, terminaciones, estados y auditoría de inventario. No es una
  consulta SQL de solo lectura: toma bloqueos mientras revisa y puede consumir
  valores de secuencias PostgreSQL. No conserva cambios de inventario. El lote de
  revisión sí se conserva, identificado por `metadatos_origen.modo = validar`.
- La revisión se procesa en la misma cola y con los mismos permisos que la carga.
  Un resultado correcto habilita **Confirmar carga**. La confirmación incluye un
  comprobante firmado ligado al usuario, tipo y hash del archivo (vigencia de una
  hora). Si cambia el archivo o vence, se solicita revisarlo nuevamente.
- Al confirmar se ejecutan otra vez todas las reglas contra los datos actuales;
  una revisión no reserva puertos ni garantiza que nadie los modifique después.
  Un conflicto posterior rechaza atómicamente la carga.
- Los adaptadores/API existentes mantienen compatibilidad con aplicación directa;
  no se convierten en otro motor y siguen validando dentro de la transacción.
- El progreso se recupera desde el historial con **Ver avance**, incluso sin
  almacenamiento local del navegador. Una pérdida de conexión no significa
  fracaso; se reintenta con espera creciente. Si la sesión terminó se pide iniciar
  sesión y recuperar el avance, sin repetir automáticamente la carga.
- Un latido cada cinco segundos indica que el proceso está vivo; no aumenta el
  porcentaje artificialmente. Tras 90 segundos sin señal se advierte, sin inventar
  un resultado definitivo. La recuperación tras reinicio usa el worker existente.

## Resultados y errores íntegros

- El historial muestra hasta 20 mensajes y permite descargar **todos los errores
  detectados**, sin el recorte anterior de 1000 caracteres. El informe es texto
  UTF-8, para no interpretar como fórmulas los datos incluidos en mensajes.
- La descarga exige sesión y ser propietario del lote, superusuario o tener
  permiso de consulta del historial. El UUID no sustituye la autorización.
- La validación es por etapas: un problema estructural puede impedir evaluar
  relaciones posteriores. El informe conserva íntegros los errores de la etapa
  que falló; no promete detectar conflictos que solo aparecerían tras corregirla.
- `total` cuenta filas de entrada. `creadas`, `actualizadas`, `sin_cambios` y
  `omitidas` explican el resultado; `unidad` aclara qué se cuenta. La fecha o el
  lote de procedencia por sí solos no constituyen un cambio de negocio.
- En A/B los contadores principales son terminaciones, no filas: una fila puede
  crear dos. Los cambios en fibras globales se informan aparte. En OTU se cuentan
  filas compuestas equipo/puerto; en geografía, puntos. Una recarga geográfica
  idéntica conserva los puntos; un reemplazo informa los puntos retirados aparte.
- Los campos nuevos se guardan en los metadatos del lote: no hay migraciones.
  No se reconstruyen contadores históricos que no registraron esa distinción.

## Confirmación del resultado

- En CSV y en operaciones de puertos por Excel, los cambios y el resultado del
  lote se confirman dentro de la misma transacción. Si falla el cierre del lote,
  también se revierten las operaciones, los estados y su auditoría.
- El resultado definitivo procede de `LoteImportacion`, no del archivo JSON de
  progreso. El sondeo realiza una lectura ligera por UUID y comprueba el
  propietario en la base, sin escribir en ella.
- Un error al escribir el avance se registra en el log, pero no convierte en
  fallida una importación confirmada. El sondeo recupera el resultado desde la
  base aunque el JSON falte, esté dañado o conserve un estado anterior.
- Si temporalmente no puede consultarse la base, el sondeo devuelve una espera
  recuperable (HTTP 503), nunca un resultado definitivo inventado.
- Si falla una importación y no puede confirmarse su cierre en la base, se
  conserva su archivo pendiente para recuperación. No se debe volver a cargar
  a ciegas: primero debe consultarse el estado del lote.

## Recuperación después de reinicios

Validación del cierre de importadores (06/09/2026): suite `mapas` con 508 pruebas,
495 correctas y 13 escenarios PostgreSQL omitidos bajo SQLite; 39 aserciones
JavaScript de navegación, revisión y recuperación correctas. `manage.py check`
y `makemigrations --check --dry-run` correctos. Servidor local reiniciado y
respuesta HTTP verificada. La verificación visual con navegador integrado no
pudo realizarse por un fallo de conexión de la herramienta.

La petición web guarda primero el lote en estado `PENDIENTE` y conserva el
archivo dentro de `DATA_ROOT\importaciones\pendientes`. El proceso inmediato
atiende normalmente la carga. Si IIS o el proceso de aplicación se reinicia,
el comando siguiente recupera los lotes pendientes:

```powershell
.\.venv\Scripts\python.exe manage.py procesar_importaciones --settings=fibergenius.settings_production
```

Para instalar la revisión automática cada minuto en el Programador de tareas
de Windows:

```powershell
.\scripts\windows\install-import-worker-task.ps1
```

La cuenta técnica debe poder leer y escribir `DATA_ROOT` y acceder a la base
de datos configurada por Fiber Genius. La tarea usa `IgnoreNew`, por lo que dos
ejecuciones programadas no procesan en paralelo el mismo lote.

Los archivos exitosos se eliminan del área pendiente. Los fallidos se mueven a
`DATA_ROOT\importaciones\fallidos` para diagnóstico controlado. El worker
conserva los archivos fallidos y estados de progreso durante 30 días por
defecto; puede ajustarse con `FIBERGENIUS_IMPORT_RETENTION_DAYS`.
