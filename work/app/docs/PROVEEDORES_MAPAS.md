# Mapa base: CARTO, respaldo de calles y satélite opcional

Resumen, Mapa de red y las vistas previas de troncales/fibras comparten un
controlador local de Leaflet. No cambia
el inventario, su posición, filtros, selección, centro ni zoom al cambiar de fondo.

## Configuración

En el `.env` efectivo de la instalación (o `FIBERGENIUS_ENV_FILE`):

```dotenv
FIBERGENIUS_CARTO_API_KEY=
FIBERGENIUS_MAP_FALLBACK_ENABLED=True
FIBERGENIUS_MAP_TILE_STREETS=https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}
FIBERGENIUS_MAP_TILE_SATELLITE=https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}
```

Solicitar la clave en https://carto.com/basemaps/apikey/ y pegar el valor de
teselas recibido en `FIBERGENIUS_CARTO_API_KEY`. Reiniciar el servicio después.
No es una clave del API general de la plataforma CARTO. Declarar correctamente
el uso comercial y revisar las condiciones del proveedor.

La clave se añade como `?key=...` exclusivamente a URLs HTTPS de
`basemaps.cartocdn.com` y sus subdominios. Una clave explícita en la URL tiene
precedencia. No se envía a Esri ni a un servidor cartográfico interno.
La clave de teselas es visible en el navegador: restringirla al origen autorizado
en el panel CARTO. Las imágenes envían solamente el origen como Referer en HTTPS,
no las rutas ni filtros del inventario; no se altera la política global de seguridad.

Los alias productivos `MAP_TILE_URL_LIGHT/DARK/STREETS/SATELLITE` tienen
precedencia sobre `FIBERGENIUS_MAP_TILE_LIGHT/DARK/STREETS/SATELLITE`.

## Comportamiento

- Con clave: CARTO claro/oscuro es el principal.
- CARTO sin clave: se selecciona **Esri calles** con un aviso, sin pedir teselas CARTO.
- Fallos de al menos la mitad de las teselas de una tanda, o carga que no finaliza
  en 12 segundos: se activa el respaldo. Errores menores muestran un aviso.
- No hay bucles automáticos de reintentos. Si el respaldo falla, se conserva
  el inventario y se ofrece «Reintentar mapa».
- El selector «Proveedor del mapa base» permite cambiar manualmente o volver
  al principal. Cambiar el tema de la aplicación no desactiva el respaldo.
- Un cartel «API KEY REQUIRED» servido como imagen HTTP 200 no se detecta como
  error de red. Usar el selector manual; no se ocultan ni recortan marcas del proveedor.
- El respaldo utiliza `World_Street_Map` (calles). `World_Imagery` (satélite)
  permanece disponible solo por selección manual, no como tercer salto automático.
- Si falla Esri calles, el sistema avisa y conserva el inventario; el usuario
  puede reintentar o elegir satélite. Las elecciones manuales se conservan
  al cambiar el tema. Se mantienen las atribuciones de cada proveedor.
  No se cachean/proxifican teselas en el servidor.

Las peticiones de teselas salen del navegador del usuario, no del servidor Django.
Los clientes necesitan HTTPS a `*.basemaps.cartocdn.com` y
`server.arcgisonline.com` (además del acceso a Fiber Genius). No hace falta abrir
puertos entrantes al servidor para estos proveedores. Leaflet, CSS y JS son locales.
Esto no garantiza disponibilidad de terceros ni sustituye revisar sus condiciones.
Los endpoints públicos World Street Map y World Imagery configurados no incluyen token; los servicios
modernos de ArcGIS pueden requerirlo y no son intercambiables automáticamente.

El servicio raster antiguo `World_Street_Map` tiene retiro anunciado para
**diciembre de 2029**. Se usa como respaldo temporal y su URL es configurable;
planificar el reemplazo antes de esa fecha. Fuente:
https://www.esri.com/arcgis-blog/products/arcgis-living-atlas/announcements/sunsetting-legacy-basemaps

Para instalaciones sin Internet, configurar **todas** las URLs de mapas con
proveedores internos (o dejar calles y satélite vacíos) y desactivar el respaldo automático.
Desactivar `FIBERGENIUS_MAP_FALLBACK_ENABLED` no elimina la selección manual de
calles o satélite configurados; vaciar esas URLs si tampoco deben ofrecerse.

No hay migraciones de base de datos. En producción ejecutar `collectstatic`
al desplegar estos recursos y reiniciar el servicio. La configuración de clave
no habilita ni modifica integraciones VeEX/OTDR.

## Verificación inicial (2026-09-21, antes del cambio a calles)

- `manage.py check`: correcto. `makemigrations --check --dry-run`: sin cambios.
- 62 pruebas focalizadas de GUI, dashboard y configuración: correctas.
- Los nueve scripts JavaScript de `scripts/tests` pasan. El controlador tiene
  36 comprobaciones propias: fallos, timeout, selección manual, tema,
  eventos atrasados, recuperación y limpieza al cerrar el mapa.
- Edge sobre copia SQLite aislada: HTTP 200 en Resumen y Mapa de red, con
  12 y 25 teselas reales de Esri descargadas, respectivamente. Sin errores JS.
  Cambiar de fondo conservó 115 y 65 elementos gráficos del inventario.
- Leaflet real con respuestas simuladas: HTTP 403 del principal, fallo de ambos,
  recuperación manual, cambio después de HTTP 200 y reapertura de las vistas
  previas de fibras/troncales. Centro, zoom y capas conservados.
- La suite general ejecutó 642 pruebas: 628 correctas, 13 omitidas por requerir
  PostgreSQL y una aserción antigua que buscaba literalmente el manejador
  `baselayerchange` eliminado. Se actualizó para comprobar el controlador común
  y la actualización del estilo de selección; su reejecución individual pasó.
  No se repitió íntegramente la suite después de actualizar esa aserción.

Pendiente de la instalación: probar una clave CARTO real con las restricciones
del dominio definitivo. No se creó una cuenta ni se añadieron credenciales reales.

## Cambio a respaldo de calles (2026-09-21)

- Se separaron `STREETS` y `SATELLITE`: CARTO falla hacia calles, nunca hacia
  satélite automáticamente. Satélite permanece como elección manual.
- 32 pruebas focalizadas Django correctas; nueve scripts JavaScript correctos,
  con 45 comprobaciones propias del controlador. Check y migraciones sin novedades.
- Edge, copia aislada: calles reales visibles en Resumen y Mapa de red, sin
  errores JavaScript. Se conservaron las 115/65 capas gráficas del inventario.
- Fallos simulados del principal y del respaldo, reintento, selección manual de
  calles/satélite, tema oscuro y reapertura de las fichas de fibras/troncales:
  correctos, sin pérdida del centro, zoom ni capas.
- No se repitió la suite general para este cambio de proveedor. No se tocó la
  base de datos operativa ni se configuró una clave CARTO real.
