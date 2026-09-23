# Base para un upgrade de monitoreo — 2026-09-16

## Alcance de esta entrega

Preparar una correspondencia estable entre los puertos de monitoreo OTU/VeEX y
las fibras existentes. **No implementar ni activar todavía el monitoreo.**

La migración `0049_base_monitoreo_futuro` crea únicamente:

- `mon_fuentes`: una instancia de origen, por ejemplo un servidor VeEX. Dos
  servidores del mismo proveedor tienen identidades distintas.
- `mon_canales`: una asociación explícita de fuente + identificador externo con
  una `InventarioFibra`, con extremo de medición A/B opcional.

No modifica, borra, renumera ni rellena tablas de inventario o monitoreo previo.
No incorpora credenciales, URLs externas, señales, workers ni peticiones de red.
No incorpora pantallas, entradas de menú, permisos de usuario ni registro Admin.
No se crean asociaciones de puertos a partir de nombres de rutas ni de etiquetas como F17.

## Terminología para el usuario

Se utiliza **Puerto de monitoreo** (del OTU/VeEX), no «canal». Es distinto del
**Puerto ODF**, donde termina físicamente la fibra del inventario.

Ejemplo: **OTU-01 / puerto 7 → fibra F17 → troncal CP4–ER314**.
Si existe un selector óptico, su posición forma parte del identificador externo
cuando sea necesaria para distinguir el punto de medición.

Por compatibilidad se conservan los nombres internos `CanalMonitoreo`,
`mon_canales` y los servicios `registrar_canal`, `retirar_canal` y
`canales_de_troncal`. El cambio es de terminología, no de relaciones ni datos.

## Relación oficial

```text
Fuente de monitoreo
  └─ Puerto de monitoreo ──→ Fibra ──→ Troncal
                              ├─ Terminación ──→ Puerto ODF ──→ ODF ──→ Site
                              └─ Fibra por tramo ──→ tramo del recorrido
```

La tabla de puertos de monitoreo no duplica la troncal: se obtiene desde la
fibra. Una troncal puede tener F1, F17 y F28 relacionados con puertos de monitoreo distintos.
Una fibra puede tener más de uno, por ejemplo medición desde A y desde B.
No existe una restricción de un puerto de monitoreo por troncal.

La fibra se selecciona por su identidad, no por su número F1/F17 aislado. Se
admiten fibras con troncal pendiente sin inventarla. Registrar un puerto de monitoreo tampoco
crea una fibra, un tramo, un puerto ODF o una terminación ni cambia estados de uso.
`extremo_origen=''` es información pendiente, nunca se interpreta como A.

## Identidad y retiro

- Fuente y asociación de puerto de monitoreo disponen de UUID además de su clave primaria.
- El identificador externo debe ser único dentro de su fuente entre asociaciones
  vigentes. Si un proveedor numera puertos por equipo, el futuro adaptador deberá
  usar su ID inequívoco o una clave que incluya equipo y puerto; no solo `7`.
- Se respetan mayúsculas/minúsculas del identificador externo; no se presume la
  semántica del proveedor. Se quitan espacios exteriores.
- Una asociación se registra mediante `registrar_canal`. La API/GUI futura tendrá
  que autenticar, autorizar y auditar esta operación; el servicio no es un endpoint.
- `retirar_canal` marca la asociación como retirada, sin eliminarla. Es idempotente.
- Cambiar fibra, fuente, extremo o ID externo requiere retirar la asociación y
  crear otra. La nueva asociación obtiene otro UUID; el registro anterior conserva
  su fibra y debe seguir siendo el destino de su historial futuro.
- Una asociación retirada no se reactiva mediante `save`. Las restricciones de
  base protegen duplicados vigentes, extremos y fechas; la inmutabilidad adicional
  se valida en `save`. No usar `QuerySet.update`/SQL para reasignar identidades.
- Las claves foráneas protegen la fibra y la fuente contra borrado mientras tengan
  asociaciones. Retirar no borra la asociación; un proceso futuro de archivo o
  purga deberá tener una política explícita de conservación histórica.
- `vigente` significa correspondencia actual, **no monitoreo activo ni fibra sana**.

`canales_de_troncal` consulta las asociaciones vigentes mediante las relaciones
oficiales. Renombrar una ruta no rompe su vínculo; no se consulta por nombre.

## Lo que deliberadamente NO se conecta todavía

Las tablas y procesos anteriores `AlarmaVeex`, `EventLog`, `Medicion`,
`PruebaOTDR`, `TrazaOnDemand`, `TrazaReferencia` y `DiagnosticoProactivo` no cambian
en este bloque. Tampoco cambia la asociación legacy `PuertoOTU.ruta_asociada`.
Esa relación de uno a uno **no será la fuente del monitoreo multihilo nuevo**.

Crear esta base o activar `VEEX_ENABLED`/`FIBERGENIUS_OPERATIONS_UI_ENABLED` no
transforma los flujos legacy en multihilo. No activar esos módulos como resultado
de esta entrega; mantenerlos deshabilitados en el perfil de inventario.

No se añaden todavía referencias al puerto de monitoreo en los históricos, porque falta
definir y validar cómo el proveedor identifica cada medición. No se asignarán
alarmas antiguas a un hilo basándose únicamente en el nombre de la troncal.

## Contrato para la futura actualización

1. Resolver la identidad real fuente/equipo/puerto de monitoreo con datos y documentación del
   proveedor. Validar dirección, extremo de medición y recorrido antes de localizar
   una falla. Si falta la correspondencia, conservar el evento como no correlacionado.
2. Relacionar mediciones, trazas y alarmas con el puerto de monitoreo mediante migraciones
   aditivas y relaciones opcionales para los registros históricos. Guardar contexto
   del momento de adquisición (troncal, hilo y conexión) cuando sea necesario para
   que cambios posteriores no reinterpreten eventos antiguos.
3. Conservar el historial de mediciones y eventos; los comandos legacy que
   sobrescriben el último resultado no son un archivo histórico completo.
4. Definir identidad externa e idempotencia por fuente, tanto para eventos como
   mediciones, y referencias de traza por puerto de monitoreo/condiciones de adquisición.
5. Mostrar primero la troncal afectada y después el hilo medido. No propagar una
   alarma de F17 a todos los hilos ni convertir automáticamente un estado de uso
   Disponible/Ocupado en un diagnóstico óptico.
6. Añadir adaptadores, colas persistentes, reintentos, autenticación, permisos,
   auditoría y activación controlada. Probar pérdidas de conexión y reinicios.
7. Definir volumen, frecuencia y retención; dimensionar índices, almacenamiento de
   SOR y copias, y probar concurrencia y carga real en PostgreSQL.
8. Regularizar por migraciones la tabla georreferenciada OTDR legacy si se reutiliza.

Este trabajo conserva el núcleo del inventario, pero no promete que el upgrade
definitivo solo necesitará crear tablas: también adaptará los procesos legacy y
podrá añadir columnas, índices y restricciones compatibles en las tablas de monitoreo.

## Instalación y validación

Esta entrega prepara el código y la migración. Su prueba se ejecuta en una base
aislada, no contra el inventario activo. Aplicar la migración en el despliegue
autorizado, con backup y las comprobaciones habituales; no hace falta recargar datos.
Revertirla elimina las dos tablas nuevas y su configuración: no hacerlo después
de registrar puertos de monitoreo sin respaldar y evaluar sus dependencias.

Pruebas: varias fibras por troncal, varias fuentes con el mismo ID externo,
extremos A/B, retiro/reasignación conservando identidad, restricciones de base,
protección contra borrado y ausencia de cambios de inventario o activación web.
La prueba de upgrade pasa de 0048 a 0049 y verifica que la fibra preexistente
conserva su identidad, troncal y estado. Esto no certifica concurrencia PostgreSQL
ni integración con equipos reales.
