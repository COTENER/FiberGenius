param(
    [string]$TaskName = 'COTENER Fiber Genius Importaciones',
    [ValidateRange(1, 60)]
    [int]$EveryMinutes = 1,
    [string]$TaskUser = "$env:USERDOMAIN\$env:USERNAME"
)

$ErrorActionPreference = 'Stop'
$RunScript = (Resolve-Path (Join-Path $PSScriptRoot 'run-import-worker.ps1')).Path
$PowerShellExe = (Get-Command powershell.exe).Source
$Credential = Get-Credential -UserName $TaskUser -Message 'Cuenta técnica de Fiber Genius'
$Password = $Credential.GetNetworkCredential().Password
$Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$RunScript`""
$Action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $Arguments
$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -User $Credential.UserName `
    -Password $Password `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "Tarea '$TaskName' instalada. Revisión cada $EveryMinutes minuto(s)."
