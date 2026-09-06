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

## Recuperación después de reinicios

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
