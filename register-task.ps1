# Optional on-demand launcher. No automatic trigger and no administrator privileges requested.
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$python=Join-Path $root 'runtime\venv\Scripts\pythonw.exe'
$script=Join-Path $PSScriptRoot 'scripts\server.py'
$action=New-ScheduledTaskAction -Execute $python -Argument ('"'+$script+'"') -WorkingDirectory $PSScriptRoot
$principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'WebCollectorLocal' -Action $action -Principal $principal -Settings $settings -Description 'On-demand local WebCollector API, no automatic trigger' -Force | Out-Null
Write-Output 'Registered on-demand launcher: WebCollectorLocal'
