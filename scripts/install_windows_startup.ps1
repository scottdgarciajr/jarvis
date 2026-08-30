$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$StartupScript = Join-Path $PSScriptRoot "start_jarvis_windows.ps1"
$TaskName = "Jarvis Home Server"
$PowerShell = Join-Path $PSHOME "powershell.exe"
if (-not (Test-Path $PowerShell)) {
    $PowerShell = "powershell.exe"
}

if (-not (Test-Path $StartupScript)) {
    throw "Could not find $StartupScript"
}

$Action = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartupScript`"" `
    -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Task = New-ScheduledTask -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal

Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null
Write-Host "Installed or updated scheduled task: $TaskName"
Write-Host "Jarvis will start automatically when $env:USERNAME logs in."
Write-Host "Startup logs: $(Join-Path $ProjectDir 'data\jarvis-startup.log')"
