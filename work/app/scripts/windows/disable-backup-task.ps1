param(
    [string]$TaskName = 'COTENER Fiber Genius Backup'
)

$ErrorActionPreference = 'Stop'
Disable-ScheduledTask -TaskName $TaskName | Out-Null
Write-Host "Tarea '$TaskName' desactivada."
