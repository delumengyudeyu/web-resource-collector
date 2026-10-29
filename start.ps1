$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$installRoot = Split-Path -Parent $projectRoot
$pythonExe = Join-Path $installRoot 'runtime\venv\Scripts\python.exe'
$env:COLLECTOR_HOME = $installRoot
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $installRoot 'runtime\browsers'
$localConfig = Join-Path $installRoot 'local-config.json'
if (Test-Path -LiteralPath $localConfig) {
  $config = Get-Content -LiteralPath $localConfig -Raw | ConvertFrom-Json
  if ($config.allow_fake_ip) { $env:COLLECTOR_ALLOW_FAKE_IP = '1' }
}
$pidFile = Join-Path $installRoot 'logs\server.pid'
try { $health = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2; if ($health.version -and $health.data_dir -eq (Join-Path $installRoot 'data')) { Write-Output 'Already running: http://127.0.0.1:8765'; exit 0 } } catch {}
$task = Get-ScheduledTask -TaskName 'WebCollectorLocal' -ErrorAction SilentlyContinue
if ($task) {
  Start-ScheduledTask -TaskName 'WebCollectorLocal'
} else {
  $serverScript = Join-Path $projectRoot 'scripts\server.py'
  Start-Process -FilePath $pythonExe -ArgumentList ('"' + $serverScript + '"') -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
}
for ($attempt = 0; $attempt -lt 30; $attempt++) {
  try { $health = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 1; if ($health.status -eq 'ok') { Write-Output 'WebCollector ready: http://127.0.0.1:8765'; exit 0 } } catch {}
  Start-Sleep -Milliseconds 300
}
throw 'Service did not become ready. See logs/server.err.log.'
