# Checklist de entrega y operación — Fiber Genius

Vigente: 2026-09-22. Complementa `PRODUCCION_WINDOWS.md`.
Marcar cada punto con fecha, responsable y evidencia. Una casilla pendiente no
equivale a una validación aprobada. No ejecutar cambios productivos sin ventana
autorizada. No usar la base de desarrollo para ensayar una restauración.

## 1. Identificar la versión

- [ ] Completar la regresión final de la etapa 7 antes de declarar la versión candidata.
- [ ] Registrar commit completo, versión visible y SHA-256 del paquete final.
  Si hay cambios sin commit, el hash de HEAD no identifica todo el código.
- [ ] Entregar código, migraciones, recursos estáticos, manuales, `.env.production.example`,
  `requirements.lock`, dependencias offline, `requirements.offline.lock` y `build.json`.
- [ ] Excluir `.git`, `.venv`, `.env` reales, bases SQLite, logs, media, backups,
  archivos de clientes, outputs y carpetas temporales. No comprimir todo el workspace.
- [ ] Custodiar por separado configuración real, secretos y certificados.
  Los hashes detectan alteraciones; no sustituyen un origen confiable del paquete.

## 2. Entorno limpio / offline

- [ ] Preparación separada en Windows x64 / Python 3.12, con pip, setuptools,
  wheel y compilador aprobado si una dependencia nativa lo requiere.
- [ ] Ejecutar `scripts/prepare_offline.py` con un destino nuevo; conservar la
  salida de compilación, el lock original y el hash del paquete.
- [ ] Instalar con `scripts/windows/install-offline.ps1` en una copia nueva.
  No actualizar el `.venv` de la instalación anterior en el sitio.
- [ ] El instalador debe terminar sin errores de hashes, dependencias o versiones.
  Después, desde `work/app`, con el Python de esa instalación:

  ```powershell
  .\.venv\Scripts\python.exe -m pip check
  .\.venv\Scripts\python.exe scripts\check_dependencies.py
  ```

  Ambos deben devolver código 0. `pip check` revisa compatibilidad declarada;
  el segundo compara los 25 paquetes fijados en el lock actual y el runtime.
  No instala paquetes ni consulta Internet. No verifica la integridad de los
  archivos ya instalados ni prohíbe paquetes adicionales de herramientas.
- [ ] Ensayar instalación y arranque con salida de Internet bloqueada.
  La instalación sin índice de pip no prueba por sí sola ese aislamiento.
- [ ] Ejecutar la regresión sobre estas versiones exactas: los resultados del
  entorno de desarrollo con otras versiones no certifican el paquete productivo.

## 3. Configuración y almacenamiento

- [ ] `FIBERGENIUS_ENV_FILE` definido para web, worker, backup y mantenimiento.
  Deben leer la misma configuración, salvo restauraciones aisladas deliberadas.
  Revisar variables del proceso: prevalecen sobre el archivo `.env`.
- [ ] Reemplazar todos los `CHANGE_ME`, dominio de ejemplo y rutas no existentes.
  SECRET_KEY aleatoria; contraseñas únicas; usuarios de prueba sin acceso productivo.
- [ ] DNS/HTTPS real en `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS`.
- [ ] Perfil `fibergenius.settings_production`; operaciones y VeEX deshabilitados
  durante la entrega de inventario. No usar `settings_pruebas` ni `runserver`.
- [ ] `DATA_ROOT`, `MEDIA_ROOT`, `LOG_ROOT`, `STATIC_ROOT`, `BACKUP_ROOT` resueltos
  y separados. Backups fuera de DATA_ROOT y MEDIA_ROOT, nunca anidados en ellos.
  No anidar unos destinos operativos dentro de otros para facilitar recuperación.
- [ ] Ubicar los datos físicos de PostgreSQL en el volumen acordado **al instalar
  PostgreSQL**. `DB_NAME` selecciona una base; `DATA_ROOT` NO mueve PGDATA ni InnoDB.
- [ ] Definir TEMP/TMP de la cuenta de servicio en Windows y comprobar espacio y
  acceso. Añadirlos al `.env` no configura automáticamente el temporal del proceso.
- [ ] Dimensionar disco con datos medidos: base, media/data, temporales, crecimiento,
  backup, copia de restauración y directorios `before-restore`. Acordar umbrales
  de alerta con infraestructura; una cifra fija no vale para todos los inventarios.

## 4. Permisos, servicio y tareas

| Identidad / recurso | Permiso que debe revisarse |
|---|---|
| Servicio web y worker | Lectura/ejecución del código y lectura del `.env`; escritura en data, media, logs y temporal. El perfil actual crea también las raíces configuradas al arrancar: precrearlas y comprobar el arranque con la cuenta real. |
| Mantenimiento/despliegue | Crear versión/venv y estáticos; ejecutar migraciones durante ventana autorizada. |
| Backup | Lectura de base y data/media; escritura en backup y logs; ejecutar pg_dump. No requiere administrar Windows para generar el dump. |
| Restore | Permisos sobre la base alternativa y para renombrar/reemplazar directorios durante la recuperación. No usarlo como tarea diaria. |
| PostgreSQL | Rol de aplicación no superusuario. Acordar rol de migraciones/restore y propietarios de objetos con BHP. |
| IIS | Acceso a estáticos autorizados; nunca publicar `.env`, backups, data ni media privados. |

- [ ] Servicio supervisado con `scripts/run_production.py`, inicio automático y
  recuperación tras caída. Python y directorio de trabajo apuntan a la versión correcta.
- [ ] IIS local reescribe cabeceras de proxy, exige HTTPS y aplica límites de subida.
  Comprobar IP real de cliente, cierre de sesión y bloqueo de accesos fallidos.
- [ ] Registrar tareas con la cuenta técnica y permiso de ejecución por lotes.
  Los scripts actuales solicitan `RunLevel Highest`; infraestructura debe revisar
  su política y token efectivo. Eso no justifica otorgar administrador a la cuenta.
- [ ] No ejecutar los instaladores de tareas contra nombres existentes sin revisar:
  usan `-Force` y reemplazan la definición. Después de un upgrade, comprobar
  que sus acciones apuntan a la nueva versión, no a una carpeta retirada.
- [ ] Worker: `COTENER Fiber Genius Importaciones`, por defecto cada minuto.
  Límite actual 4 horas, sin instancias simultáneas; revisar historial y último resultado.
- [ ] Backup: `COTENER Fiber Genius Backup`, por defecto diario 02:00, configurable
  diario/semanal, límite 6 horas. `BACKUP_COMMAND_TIMEOUT_SECONDS` limita cada
  comando de base, no toda la copia de archivos. Evitar cargas durante la copia.
- [ ] Probar ejecución sin sesión iniciada y un reinicio de Windows en staging.

## 5. Secuencia de instalación / actualización

1. Respaldar y verificar la versión anterior, base, archivos y configuración.
2. Instalar la nueva versión en carpeta independiente. Seleccionar el `.env`
   correcto en el proceso de mantenimiento, sin imprimir sus secretos.
3. Ejecutar `check --deploy` y `migrate --plan` con settings productivos.
   Resolver advertencias; el plan necesita acceso a PostgreSQL.
4. Durante la ventana autorizada, detener escrituras, web y worker. Aplicar
   migraciones ensayadas, sin `--fake`, y ejecutar `collectstatic --noinput`.
5. Ajustar servicio/tareas a la nueva versión, iniciar y comprobar `/health/`
   mediante el hostname HTTPS aprobado. Debe devolver 200 y base `ok`.
6. Verificar login, permisos, Sites, ODF, puertos, fibras, troncales, importación
   de ensayo, exportación y mapas desde una PC cliente. Comparar totales/relaciones.
7. Abrir acceso solo después de aceptación. Si falla, mantener el corte y usar
   el plan de vuelta atrás probado; volver solo de código no revierte migraciones.

## 6. Revisión operativa

Diariamente, registrar estas comprobaciones con la cuenta autorizada:

```powershell
Get-PSDrive -PSProvider FileSystem | Select-Object Name, Used, Free
Get-ScheduledTaskInfo -TaskName 'COTENER Fiber Genius Importaciones'
Get-ScheduledTaskInfo -TaskName 'COTENER Fiber Genius Backup'
```

- Espacio libre y crecimiento en todos los volúmenes usados, incluido TEMP/TMP.
- `/health/` consulta la base; NO acredita importaciones, capacidad de disco,
  disponibilidad de mapas, backups ni servicios externos.
- Fecha y resultado de tareas, lotes pendientes/fallidos y errores en LOG_ROOT:
  `django.log`, `security.log`, `audit.log`, `import.log`, `backup.log`.
  El log `integration.log` corresponde al monitoreo futuro. Revisar también
  logs propios de IIS y del supervisor, que no controla LOG_ROOT.
- Cada archivo de aplicación rota a 10 MiB con 10 copias según el perfil actual.
  Esto no es una retención por días ni centralización corporativa. Ensayar la
  rotación con web y worker reales, que pueden compartir archivos en Windows.
- Confirmar último backup completo y su antigüedad, no solo una carpeta existente.
  Revisar fallos y `.partial-*`; no borrar locks sin confirmar procesos detenidos.
- La retención actual limpia `daily` y `monthly` al crear un backup nuevo;
  `manual` y `before-restore` requieren una política de revisión independiente.
- La copia externa y sus alertas son responsabilidad operativa acordada con BHP.
  Un backup local en otro volumen no cubre la pérdida del servidor.

## 7. Restauración y aceptación pendiente

- [ ] Verificar con `verify-backup.ps1 -BackupPath <carpeta_completa>`.
  Verifica manifiesto/hashes, no prueba que PostgreSQL pueda restaurar el dump.
- [ ] Custodiar `.env`, versión y certificados aparte: el backup no los incluye.
- [ ] Crear base y directorios alternativos con configuración externa aislada.
  Restaurar con `run-restore.ps1`, web y worker detenidos y confirmación explícita.
- [ ] En PostgreSQL ensayar dump/restore real, permisos, versión de herramientas,
  migraciones y correspondencia de base/archivos. SQLite no valida ese procedimiento.
- [ ] Comparar totales, terminaciones, recorridos y archivos; probar GUI y roles.
- [ ] Registrar duración, volumen y resultado; acordar RPO/RTO y copia corporativa.
  Un backup diario no ofrece pérdida cero; esa exigencia requiere otra estrategia.

La etapa 6 local no sustituye este ensayo de infraestructura ni el cierre de la
suite de la etapa 7. No certificar la entrega mientras queden bloqueos abiertos.
