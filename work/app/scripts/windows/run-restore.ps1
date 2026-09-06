param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [string]$SettingsModule = 'fibergenius.settings_production',
    [switch]$DatabaseOnly
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$ManagePath = Join-Path $ProjectRoot 'manage.py'

if (-not (Test-Path -LiteralPath $BackupPath -PathType Container)) {
    throw "No existe la carpeta de backup: $BackupPath"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "No se encontró el Python de Fiber Genius: $PythonPath"
}

$env:DJANGO_SETTINGS_MODULE = $SettingsModule

Write-Host 'Validando integridad del backup...'
& $PythonPath $ManagePath restore_fibergenius $BackupPath --verify-only
if ($LASTEXITCODE -ne 0) {
    throw 'El backup no superó la validación. No se restauró ningún dato.'
}

Write-Warning 'La restauración reemplazará la base activa y los archivos incluidos.'
Write-Warning 'Ejecute este script únicamente con Fiber Genius detenido y durante una ventana de mantenimiento.'
$Confirmation = Read-Host 'Escriba RESTORE-FIBERGENIUS para continuar'
if ($Confirmation -cne 'RESTORE-FIBERGENIUS') {
    throw 'Confirmación incorrecta. Restauración cancelada.'
}

$Arguments = @(
    $ManagePath,
    'restore_fibergenius',
    $BackupPath,
    '--execute',
    '--confirm',
    'RESTORE-FIBERGENIUS'
)
if ($DatabaseOnly) {
    $Arguments += '--database-only'
}

& $PythonPath @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "La restauración terminó con código $LASTEXITCODE. Revise LOG_ROOT\backup.log."
}

Write-Host 'Restauración completada. Ejecute migrate y check antes de iniciar Fiber Genius.'
