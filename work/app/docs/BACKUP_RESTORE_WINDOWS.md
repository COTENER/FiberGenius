# Backup y restauración de Fiber Genius en Windows

## Configuración

Defina en el `.env` productivo:

```env
BACKUP_ROOT=E:\COTENER\FiberGenius\backups
BACKUP_RETENTION_DAYS=30
BACKUP_PG_DUMP_PATH=C:\Program Files\PostgreSQL\17\bin\pg_dump.exe
BACKUP_PG_RESTORE_PATH=C:\Program Files\PostgreSQL\17\bin\pg_restore.exe
```

La cuenta de la tarea debe poder leer `DATA_ROOT` y `MEDIA_ROOT`, escribir en
`BACKUP_ROOT` y conectarse a PostgreSQL. Si el destino es un NAS, use una cuenta
de dominio con permiso explícito sobre el recurso compartido y NTFS.

Cada ejecución se registra en `LOG_ROOT\backup.log`.

El `.env` no se incluye en el paquete porque contiene secretos. Debe respaldarse
por el mecanismo seguro de infraestructura de BHP.

## Crear un backup manual

```powershell
.\scripts\windows\run-backup.ps1 -Kind manual
```

## Instalar la recurrencia diaria

Ejecute PowerShell como administrador:

```powershell
.\scripts\windows\install-backup-task.ps1 -DailyAt 02:00 -TaskUser DOMINIO\CuentaFiberGeniusBackup
```

La contraseña se solicita de forma interactiva y la conserva el Programador de
tareas de Windows; no se escribe en scripts ni logs.

## Activar o desactivar

```powershell
.\scripts\windows\disable-backup-task.ps1
.\scripts\windows\enable-backup-task.ps1
```

## Verificar un paquete

```powershell
.\scripts\windows\verify-backup.ps1 -BackupPath E:\COTENER\FiberGenius\backups\daily\FiberGenius_2026-08-07_020000
```

## Restauración

La restauración debe realizarse en una ventana de mantenimiento, con respaldo
previo y el servicio web detenido. Primero valide el paquete:

Después de instalar Fiber Genius y PostgreSQL en el servidor de destino,
detenga Fiber Genius y ejecute el lanzador interactivo:

```powershell
.\scripts\windows\run-restore.ps1 -BackupPath E:\COTENER\FiberGenius\backups\daily\FiberGenius_2026-08-07_020000
```

El script valida hashes y tamaños antes de solicitar la frase de confirmación.
Para recuperar únicamente PostgreSQL, agregue `-DatabaseOnly`.

La alternativa directa por consola es:

```powershell
.\.venv\Scripts\python.exe manage.py restore_fibergenius RUTA_BACKUP --verify-only --settings=fibergenius.settings_production
```

La ejecución destructiva exige dos confirmaciones explícitas:

```powershell
.\.venv\Scripts\python.exe manage.py restore_fibergenius RUTA_BACKUP --execute --confirm RESTORE-FIBERGENIUS --settings=fibergenius.settings_production
```

Después de restaurar PostgreSQL, ejecute `migrate` y `check`, inicie Fiber Genius
y valide inventario, usuarios, archivos SOR y permisos.
