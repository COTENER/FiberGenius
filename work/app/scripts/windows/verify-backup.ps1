param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [string]$SettingsModule = 'fibergenius.settings_production'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$ManagePath = Join-Path $ProjectRoot 'manage.py'
$env:DJANGO_SETTINGS_MODULE = $SettingsModule

& $PythonPath $ManagePath restore_fibergenius $BackupPath --verify-only
if ($LASTEXITCODE -ne 0) {
    throw "La verificación del backup terminó con código $LASTEXITCODE."
}
