param(
    [string]$TaskName = 'COTENER Fiber Genius Backup'
)

$ErrorActionPreference = 'Stop'
Enable-ScheduledTask -TaskName $TaskName | Out-Null
Write-Host "Tarea '$TaskName' activada."
