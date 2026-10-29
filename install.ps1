param([Parameter(Mandatory=$true)][string]$PythonExe)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
foreach ($folder in @('runtime','data','logs','profiles')) { New-Item -ItemType Directory -Path (Join-Path $root $folder) -Force | Out-Null }
$venv=Join-Path $root 'runtime\venv'
& $PythonExe -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
$python=Join-Path $venv 'Scripts\python.exe'
& $python -m pip install -r (Join-Path $PSScriptRoot 'requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
$env:PLAYWRIGHT_BROWSERS_PATH=Join-Path $root 'runtime\browsers'
& $python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed' }
Write-Output 'Install complete. Ensure FFmpeg is installed before media capture. Run start.cmd.'
