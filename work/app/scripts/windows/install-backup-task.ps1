param(
    [string]$TaskName = 'COTENER Fiber Genius Backup',
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$DailyAt = '02:00',
    [ValidateSet('Daily', 'Weekly')]
    [string]$Frequency = 'Daily',
    [ValidateSet('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')]
    [string]$DayOfWeek = 'Sunday',
    [string]$TaskUser = "$env:USERDOMAIN\$env:USERNAME"
)

$ErrorActionPreference = 'Stop'
$RunScript = (Resolve-Path (Join-Path $PSScriptRoot 'run-backup.ps1')).Path
$PowerShellExe = (Get-Command powershell.exe).Source
$Credential = Get-Credential -UserName $TaskUser -Message 'Cuenta técnica para ejecutar backups de Fiber Genius'
$Password = $Credential.GetNetworkCredential().Password
$Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$RunScript`" -Kind daily"
$Action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument $Arguments
if ($Frequency -eq 'Weekly') {
    $Trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $DayOfWeek -At $DailyAt
} else {
    $Trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
}
$Settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -User $Credential.UserName `
    -Password $Password `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "Tarea '$TaskName' instalada. Frecuencia: $Frequency, hora: $DailyAt"
