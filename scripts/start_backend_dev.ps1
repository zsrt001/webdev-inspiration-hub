$ErrorActionPreference = 'Stop'
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$logPath = Join-Path $repoRoot 'backend_dev_console.log'
Start-Transcript -Path $logPath -Append | Out-Null
try {
  $env:DEBUG = 'true'
  $env:RELOAD = '0'
  Set-Location (Join-Path $repoRoot 'backend')
  python run_api.py --host 127.0.0.1 --port 8001
}
finally {
  try { Stop-Transcript | Out-Null } catch {}
}
