param(
  [string]$Version = "11.0.0",
  [string]$Python = "python",
  [switch]$UsePip
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WheelsDir = Join-Path $RepoRoot "wheels"
$AppDir = Join-Path $RepoRoot "app"
$TargetDir = Join-Path $AppDir "_vendor"
$VendorState = Join-Path $AppDir "_vendor_path.txt"

if (Test-Path $VendorState) {
  try {
    $rel = (Get-Content -Raw $VendorState).Trim()
    if ($rel) {
      $rel = $rel -replace "/", "\\"
      $cand = Join-Path $AppDir $rel
      if (Test-Path $cand) {
        $TargetDir = $cand
      }
    }
  } catch {
    # ignore
  }
}

Write-Host "[Pillow] RepoRoot  : $RepoRoot"
Write-Host "[Pillow] WheelsDir : $WheelsDir"
Write-Host "[Pillow] TargetDir : $TargetDir"
Write-Host "[Pillow] Python    : $Python"
Write-Host "[Pillow] Version   : $Version"

if (-not (Test-Path $WheelsDir)) {
  New-Item -ItemType Directory -Force -Path $WheelsDir | Out-Null
}
if (-not (Test-Path $TargetDir)) {
  New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
}

$wheel = Get-ChildItem -Path $WheelsDir -Filter "*pillow*cp313*win_amd64*.whl" -ErrorAction SilentlyContinue |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

if (-not $wheel) {
  Write-Host "[Pillow] Wheel not found, trying to download via python (no proxy)..."
  try {
    & $Python (Join-Path $RepoRoot "scripts\\download_pillow_wheel.py") --version $Version --dest $WheelsDir --timeout 30
  } catch {
    Write-Host "[Pillow] Auto-download failed."
  }
  $wheel = Get-ChildItem -Path $WheelsDir -Filter "*pillow*cp313*win_amd64*.whl" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
}

if (-not $wheel) {
  Write-Host ""
  Write-Host "[Pillow] No matching wheel found in $WheelsDir"
  Write-Host "[Pillow] Expected a file like: Pillow-$Version-cp313-cp313-win_amd64.whl"
  Write-Host ""
  Write-Host "How to download the wheel on a machine with internet (Windows + Python 3.13):"
  Write-Host "  python -m pip download pillow==$Version --only-binary=:all: --platform win_amd64 --python-version 313 --implementation cp --abi cp313 -d backend\\wheels"
  Write-Host ""
  throw "Missing Pillow wheel"
}

Write-Host "[Pillow] Using wheel: $($wheel.FullName)"

# Avoid writing bytecode during install/verify (some environments block .pyc temp renames).
$env:PYTHONDONTWRITEBYTECODE = "1"

Write-Host "[Pillow] Installing to $TargetDir ..."
if ($UsePip) {
  & $Python -m pip install --no-index --only-binary=:all: --no-deps --target $TargetDir $wheel.FullName
  if ($LASTEXITCODE -ne 0) {
    Write-Host "[Pillow] pip install failed. Falling back to wheel extraction..."
    & $Python (Join-Path $RepoRoot "scripts\\vendor_install_wheel.py") --wheel $wheel.FullName --target $TargetDir
  }
} else {
  & $Python (Join-Path $RepoRoot "scripts\\vendor_install_wheel.py") --wheel $wheel.FullName --target $TargetDir
}

Write-Host "[Pillow] Verifying import..."
& $Python -c "import sys; sys.dont_write_bytecode=True; sys.path.insert(0, r'$TargetDir'); import PIL; print(PIL.__version__)"
Write-Host "[Pillow] Done."
