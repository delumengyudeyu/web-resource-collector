$installRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $installRoot 'logs\server.pid'
try {
  $health = Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 2
  if ($health.data_dir -eq (Join-Path $installRoot 'data')) {
    Invoke-RestMethod 'http://127.0.0.1:8765/api/shutdown' -Method Post -TimeoutSec 3 | Out-Null
    for ($attempt=0; $attempt -lt 40; $attempt++) {
      Start-Sleep -Milliseconds 250
      try { Invoke-RestMethod 'http://127.0.0.1:8765/health' -TimeoutSec 1 | Out-Null } catch { Write-Output 'WebCollector stopped.'; exit 0 }
    }
    throw 'Shutdown is still in progress; inspect logs before restarting.'
  }
} catch {}
if (Test-Path -LiteralPath $pidFile) {
  $serverPid = [int](Get-Content -LiteralPath $pidFile)
  $server = Get-CimInstance Win32_Process -Filter "ProcessId=$serverPid" -ErrorAction SilentlyContinue
  if ($server -and ($server.CommandLine -match 'collector.api:app' -or $server.CommandLine -match 'scripts\\server.py')) {
    & (Join-Path $installRoot 'runtime\venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'scripts\stop.py') $serverPid
  }
  Remove-Item -LiteralPath $pidFile
}
