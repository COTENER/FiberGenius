# Entrega de inventario — Windows / PostgreSQL / IIS

Guía de cierre, 2026-09-16. No constituye evidencia de instalación en BHP.
No cambia el diseño del inventario ni requiere vaciar sus tablas.

## Alcance y configuración

Usar `fibergenius.settings_production`, nunca `settings_pruebas` o `runserver`.
Configurar `FIBERGENIUS_ENV_FILE` en el entorno de la cuenta del servicio y de las
tareas. Por defecto: `C:\ProgramData\COTENER\FiberGenius\config\.env`.
El archivo debe existir antes de arrancar. La configuración compartida y la
productiva leen ese mismo archivo; las variables del proceso tienen prioridad.
No dejar un `.env` de desarrollo dentro del paquete ni credenciales de ejemplo.

Partir de `.env.production.example` y reemplazar secreto, credenciales, dominio y
rutas. `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS` deben corresponder al DNS/HTTPS
aprobado. Mantener `FIBERGENIUS_OPERATIONS_UI_ENABLED=False` y `VEEX_ENABLED=False`
para la entrega de inventario. La primera bandera también cierra los endpoints
de operaciones (webhook, descargas, proactivo, analítica y on-demand); encender
VeEX por sí solo no habilita esas rutas. No se eliminan tablas de monitoreo.

Usar `FIBERGENIUS_MAP_TILE_LIGHT/DARK/SATELLITE` y `FIBERGENIUS_MAP_ATTRIBUTION`.
Los nombres anteriores `MAP_TILE_URL_*` siguen aceptándose en producción y tienen
prioridad cuando se definen: no mantener valores contradictorios. Las fichas y
las pantallas principales comparten la configuración; las teselas necesitan la
autorización de red/proveedor del navegador cliente. Leaflet se sirve localmente.

## Paquete sin Internet

Construir en **Windows x64 y Python 3.12**, en un entorno de compilación separado,
con `pip`, `setuptools`, `wheel` y, si el paquete nativo lo requiere, herramientas
de compilación aprobadas. No modificar el entorno del servidor para construir.

1. Revisar el commit candidato, cambios, secretos y dependencias.
2. Ejecutar en la máquina de preparación con Internet:

   ```powershell
   python scripts/prepare_offline.py C:\FG-build\release-candidate
   ```

   El destino debe ser nuevo. Se verifican los hashes del lock original, se
   generan wheels y un `requirements.offline.lock` con sus hashes, y se registra
   el entorno de construcción en `build.json`. Un fallo de descarga/compilación
   detiene el proceso; no entregar un directorio parcialmente generado.

3. Conservar juntos código de la versión, `wheels`, lock offline, `build.json`,
   lock fuente y manuales. Identificar el commit y calcular/verificar el hash del
   paquete por un canal confiable. Excluir `.git`, `.venv`, `.env`, bases locales,
   logs, datos operativos, backups y temporales del paquete de código.
4. En una copia nueva del código, con Python 3.12 x64 previamente instalado:

   ```powershell
   .\scripts\windows\install-offline.ps1 -BundlePath C:\FG-package\dependencies -PythonPath C:\Python312\python.exe
   ```

   El instalador usa `--no-index --require-hashes` y no reemplaza un `.venv`
   existente. No instala IIS, PostgreSQL ni servicios; tampoco migra datos.
5. Probar de verdad con Internet deshabilitado. La preparación de scripts no
   acredita por sí sola que todas las dependencias se hayan construido.

## Base, estáticos y servicio

Instalar PostgreSQL según el estándar BHP y crear roles separados para operación
y mantenimiento cuando corresponda. No usar un superusuario PostgreSQL como
cuenta diaria de la aplicación. Configurar rutas de `pg_dump.exe` y
`pg_restore.exe` compatibles en el .env.

Con configuración real, ejecutar primero el plan de migraciones y el check:

```powershell
.\.venv\Scripts\python.exe manage.py check --deploy --settings=fibergenius.settings_production
.\.venv\Scripts\python.exe manage.py migrate --plan --settings=fibergenius.settings_production
```

Aplicar `migrate` solo durante la instalación/corte autorizado y después de un
backup verificable; no usar `--fake` para resolver inconsistencias. Ejecutar
`collectstatic --noinput` contra un STATIC_ROOT dedicado a esta aplicación.
Ensayar previamente migraciones nuevas y actualización sobre PostgreSQL aislado.

Configurar el supervisor aprobado (por ejemplo NSSM) para ejecutar:

```text
<version>\work\app\.venv\Scripts\python.exe <version>\work\app\scripts\run_production.py
```

El entry point exige el perfil productivo, escucha únicamente `127.0.0.1:8001` y
confía únicamente en un proxy local. IIS debe reenviar a esa dirección y
**sobrescribir** `X-Forwarded-For`, `X-Forwarded-Proto` y `X-Forwarded-Host` con
valores verificados por IIS, no conservar valores arbitrarios enviados por el
cliente. Usar HTTPS, un host permitido y límites de subida coherentes con
`FIBERGENIUS_MAX_UPLOAD_BYTES` más el margen multipart de la aplicación.
Si la arquitectura no usa IIS local, ajustar y revisar el entry point antes de
desplegar; no ampliar su confianza a cualquier proxy.

Configurar cuenta técnica, inicio automático, recuperación ante caída y permisos
NTFS mínimos: lectura del código/configuración; escritura solo donde corresponde
en datos, media, logs, backups y temporales. Probar un reinicio de Windows. No
publicar DATA_ROOT, BACKUP_ROOT ni el .env mediante IIS. No servir MEDIA_ROOT
indiscriminadamente: las descargas privadas deben conservar su autorización.

Instalar las tareas existentes `install-import-worker-task.ps1` e
`install-backup-task.ps1` con la cuenta y permisos aprobados. Revisar su ejecución
sin sesión interactiva y sus logs. La cola de importaciones permite recuperar
pendientes después de reiniciar; probarlo con un lote de ensayo en staging.

## Acceso

Django Admin deriva al login principal protegido por bloqueo temporal e
inactividad. Su autorización staff/permisos se mantiene. Rotar claves de prueba,
separar roles de consulta, edición y administración, y comprobar IP de cliente
tras IIS para evitar que el bloqueo por IP agrupe a todos bajo loopback.

## Backup, restore y vuelta atrás

Preferir restauración en una **base y directorios alternativos**, usando un .env
alternativo con esos destinos. Detener web y worker durante el corte. Respaldar
la base y archivos actuales antes de sustituirlos. Verificar que el backup elegido
es confiable y corresponde a la versión del software; no restaurar paquetes de
origen desconocido.

El comando PostgreSQL solicita `--single-transaction --exit-on-error`: los cambios
de base no deben quedar aplicados parcialmente si el comando falla. Esto requiere
ensayo real y capacidad suficiente en PostgreSQL. `BACKUP_COMMAND_TIMEOUT_SECONDS`
limita la ejecución (3600 por defecto); dimensionarlo al tamaño real y al límite
de la tarea programada. Los procesos de dump/restore no solicitan claves por consola.

**La base y los archivos NO forman una única transacción.** El restore conserva
directorios anteriores con sufijo `before-restore`, pero no sustituye un plan de
recuperación global. Si cualquier fase falla, mantener el servicio detenido,
revisar el log y volver al destino anterior o recuperar desde la copia previa.
No reiniciar la aplicación sobre una combinación de base y archivos sin validar.

El .env se excluye intencionalmente del backup por contener secretos. BHP debe
custodiarlo de manera segura, junto con la versión de código y certificados que
correspondan. Hacer la copia de base/archivos sin cargas activas para mantener
coherencia. Si queda `.backup.lock` tras una caída, confirmar que no existe ningún
backup en ejecución antes de retirarlo manualmente; no eliminar locks por edad.

Ensayo obligatorio: backup PostgreSQL + archivos, verificación, restore en destino
alternativo, migraciones/check, login, totales, relaciones y archivos. Medir tiempo.
Definir copia externa, retención, alertas, RPO/RTO y responsables con BHP. Un dump
diario no equivale a pérdida cero. La necesidad de WAL/PITR debe acordarse según RPO.

Para actualizar: conservar versión anterior, backup y configuración; ensayar la
migración; detener escrituras; actualizar y validar antes de abrir acceso. Si hay
migraciones no reversibles, volver de código solamente no basta: usar el plan de
restauración validado, no una reversión improvisada de tablas.

## Validaciones pendientes de infraestructura

- Suite real de PostgreSQL, incluyendo sus 13 pruebas de concurrencia.
- Instalación nueva y traslado controlado SQLite → PostgreSQL si se conservan datos.
- Instalación offline del paquete y comprobación de versión/dependencias.
- IIS/HTTPS, proxy, cuentas/roles, arranque y tareas en servidor equivalente.
- Backup/restore completo y copia corporativa fuera del servidor.
- GUI, mapas, importaciones/exportaciones y carga concurrente desde PCs BHP.

No presentar estos puntos como terminados por haber agregado scripts o tests mock.

## Evidencia local de las correcciones (2026-09-16)

- Suite completa Windows/SQLite: 591 pruebas, sin fallos, 13 omitidas por requerir
  PostgreSQL. Los cuatro fallos anteriores de módulos deshabilitados se corrigieron
  activando explícitamente el módulo dentro de sus pruebas, no en producción.
- Se añadieron pruebas de configuración externa, precedencia de variables, cierre
  de endpoints, acceso Admin, mapas, comandos PostgreSQL y herramientas de entrega.
- Verificación focalizada final: 15 pruebas correctas, incluidos los últimos
  cambios de herramientas de entrega y el acceso anónimo al módulo habilitado.
- JavaScript: 184 aserciones correctas, incluidas 16 de configuración de mapas.
- `check` de pruebas correcto; `makemigrations --check --dry-run` sin cambios.
- Sintaxis de los scripts Python y del instalador PowerShell comprobada.

No se modificó la base del inventario ni se instalaron servicios. El entorno
existente de desarrollo no se actualizó: la instalación limpia con el lock de
producción, el build offline y las validaciones PostgreSQL/IIS siguen pendientes.
