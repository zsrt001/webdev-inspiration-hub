param(
  [string]$Python = "python",
  [switch]$InstallOnly
)

$ErrorActionPreference = "Stop"

$BackendDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $BackendDir "..")).Path

$WheelsDir = Join-Path $BackendDir "wheels"
$ReqFile = Join-Path $BackendDir "requirements.txt"
$LockFile = Join-Path $WheelsDir "vendor_lock.json"
$VendorAnchor = Join-Path $BackendDir "app\\_vendor"
$Script = Join-Path $BackendDir "scripts\\vendor_wheelhouse.py"

Write-Host "[Vendor] RepoRoot     : $RepoRoot"
Write-Host "[Vendor] BackendDir   : $BackendDir"
Write-Host "[Vendor] Requirements : $ReqFile"
Write-Host "[Vendor] WheelsDir    : $WheelsDir"
Write-Host "[Vendor] LockFile     : $LockFile"
Write-Host "[Vendor] Python       : $Python"

# Avoid writing bytecode during bootstrap.
$env:PYTHONDONTWRITEBYTECODE = "1"

if (-not (Test-Path $WheelsDir)) {
  New-Item -ItemType Directory -Force -Path $WheelsDir | Out-Null
}

$cmd = "bootstrap"
if ($InstallOnly) {
  $cmd = "install"
}

Write-Host "[Vendor] Running: $cmd"
& $Python $Script $cmd --requirements $ReqFile --wheels $WheelsDir --vendor $VendorAnchor --lock $LockFile

Write-Host "[Vendor] Verifying imports..."
& $Python -c "import sys; sys.path.insert(0, r'$BackendDir'); import app; import fastapi, pydantic, sqlalchemy; import importlib.metadata as m; print('OK', m.version('fastapi'), m.version('pydantic'), m.version('sqlalchemy'))"

Write-Host "[Vendor] Done."

