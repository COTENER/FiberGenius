param(
    [Parameter(Mandatory = $true)] [string]$BundlePath,
    [Parameter(Mandatory = $true)] [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Bundle = (Resolve-Path -LiteralPath $BundlePath).Path
$LockFile = Join-Path $Bundle 'requirements.offline.lock'
$Wheels = Join-Path $Bundle 'wheels'
$VenvPath = Join-Path $ProjectRoot '.venv'
$VersionCheck = Join-Path $ProjectRoot 'scripts\check_dependencies.py'
$SourceLock = Join-Path $ProjectRoot 'requirements.lock'
if (-not (Test-Path -LiteralPath $VersionCheck -PathType Leaf)) { throw 'Falta check_dependencies.py en la version.' }
if (-not (Test-Path -LiteralPath $SourceLock -PathType Leaf)) { throw 'Falta requirements.lock en la version.' }
if (Test-Path -LiteralPath $VenvPath) {
    throw 'Use una copia nueva de la version: no se reemplaza el entorno existente.'
}
if (-not (Test-Path -LiteralPath $LockFile -PathType Leaf)) { throw 'Falta requirements.offline.lock.' }
if (-not (Test-Path -LiteralPath $Wheels -PathType Container)) { throw 'Falta wheels.' }
& $PythonPath -c "import sys,struct; sys.exit(0 if sys.platform == 'win32' and sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Se requiere Python 3.12 x64 en Windows.' }
& $PythonPath -m venv $VenvPath
if ($LASTEXITCODE -ne 0) { throw 'No se pudo crear el entorno.' }
$RuntimePython = Join-Path $VenvPath 'Scripts\python.exe'
& $RuntimePython -m pip --isolated install --no-index --find-links $Wheels --require-hashes -r $LockFile
if ($LASTEXITCODE -ne 0) { throw 'Instalacion incompleta; no iniciar Fiber Genius.' }
& $RuntimePython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependencias inconsistentes.' }
& $RuntimePython $VersionCheck --lock $SourceLock
if ($LASTEXITCODE -ne 0) { throw 'Las versiones instaladas no coinciden con el lock de esta version. No iniciar Fiber Genius.' }
Write-Host 'Dependencias instaladas. Pendientes: configurar .env, check, migraciones, estaticos y aceptacion.'
