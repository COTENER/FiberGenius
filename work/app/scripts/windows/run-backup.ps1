param(
    [ValidateSet('daily', 'monthly', 'manual')]
    [string]$Kind = 'daily',
    [string]$SettingsModule = 'fibergenius.settings_production'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$ManagePath = Join-Path $ProjectRoot 'manage.py'

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "No se encontró el Python de Fiber Genius: $PythonPath"
}

$env:DJANGO_SETTINGS_MODULE = $SettingsModule
& $PythonPath $ManagePath backup_fibergenius --kind $Kind
if ($LASTEXITCODE -ne 0) {
    throw "El backup de Fiber Genius terminó con código $LASTEXITCODE."
}
