[CmdletBinding()]
param(
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
$expectedPythonVersion = "3.11.15"
$expectedNodeVersion = "24.17.0"
$expectedPlatformSystem = "Linux"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$bootstrapPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $bootstrapPython)) {
    $bootstrapPython = "python"
}
$backendLock = Join-Path $root "backend\requirements.lock.txt"
if ($env:OS -eq "Windows_NT") {
    $backendLock = Join-Path $root "backend\requirements.windows.lock.txt"
}
$temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$lockedEnvironmentRoot = $null

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

$startedAt = [DateTimeOffset]::UtcNow
Push-Location $root
try {
    $lockedEnvironmentRoot = Join-Path $temporaryRoot ("vowpic-baseline-" + [Guid]::NewGuid().ToString("N"))
    Invoke-Checked "python -m venv" { & $bootstrapPython -m venv $lockedEnvironmentRoot }
    $lockedPython = Join-Path $lockedEnvironmentRoot "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $lockedPython)) {
        $lockedPython = Join-Path $lockedEnvironmentRoot "bin/python"
    }
    if (-not (Test-Path -LiteralPath $lockedPython)) {
        throw "locked Python environment did not provide an interpreter"
    }
    Invoke-Checked "backend hash-locked install" {
        & $lockedPython -m pip install --disable-pip-version-check --require-hashes -r $backendLock
    }
    Invoke-Checked "python dependency check" { & $lockedPython -m pip check }
    $pythonVersion = (& $lockedPython -c "import platform; print(platform.python_version())").Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "locked Python version probe failed with exit code $LASTEXITCODE"
    }
    $platformSystem = (& $lockedPython -c "import platform; print(platform.system())").Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "locked platform probe failed with exit code $LASTEXITCODE"
    }
    $nodeVersion = (node --version).Trim().TrimStart("v")
    if ($LASTEXITCODE -ne 0) {
        throw "Node version probe failed with exit code $LASTEXITCODE"
    }
    $backendLockSha256 = (Get-FileHash -LiteralPath $backendLock -Algorithm SHA256).Hash.ToLowerInvariant()
    $rootPrefix = $root.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $backendLockFullPath = [System.IO.Path]::GetFullPath($backendLock)
    if (-not $backendLockFullPath.StartsWith(
        $rootPrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "backend lock must remain inside the verified repository"
    }
    $backendLockRelativePath = $backendLockFullPath.Substring($rootPrefix.Length).Replace('\', '/')
    $runtimeAlignment = if (
        $pythonVersion -eq $expectedPythonVersion -and
        $nodeVersion -eq $expectedNodeVersion -and
        $platformSystem -eq $expectedPlatformSystem
    ) { "PASS" } else { "NOT_RUN" }

    $backendTestProgram = @'
import sys
import unittest

suite = unittest.defaultTestLoader.discover("backend/tests", top_level_dir=".")
count = suite.countTestCases()
print(f"collected_backend_tests={count}")
if count == 0:
    raise SystemExit("backend test discovery collected zero tests")
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
'@
    $backendTestProgram | & $lockedPython -
    if ($LASTEXITCODE -ne 0) {
        throw "backend tests failed with exit code $LASTEXITCODE"
    }

    Push-Location (Join-Path $root "frontend")
    try {
        Invoke-Checked "frontend locked install" { npm ci --ignore-scripts }
        Invoke-Checked "frontend typecheck" { npm run typecheck }
        Invoke-Checked "frontend web build" { npm run build:web }
    }
    finally {
        Pop-Location
    }

    $fingerprintScript = Join-Path $root "scripts\release\fingerprint_worktree.py"
    $sourceIdentityJson = & $lockedPython $fingerprintScript --root $root
    if ($LASTEXITCODE -ne 0) {
        throw "worktree fingerprint failed with exit code $LASTEXITCODE"
    }
    $sourceIdentity = $sourceIdentityJson | ConvertFrom-Json
    $report = [ordered]@{
        schema_version = "safe-baseline.local.v3"
        scope = "TASKS_1_4_LOCAL_ENGINEERING_BASELINE"
        generated_at = [DateTimeOffset]::UtcNow.ToString("o")
        started_at = $startedAt.ToString("o")
        base_sha = $sourceIdentity.base_sha
        source_sha = $sourceIdentity.source_sha
        code_identity = $sourceIdentity.code_identity
        working_tree_clean = $sourceIdentity.working_tree_clean
        working_tree_sha256 = $sourceIdentity.working_tree_sha256
        release_eligible = [bool]$sourceIdentity.release_eligible -and $runtimeAlignment -eq "PASS"
        python_version = $pythonVersion
        expected_python_version = $expectedPythonVersion
        node_version = $nodeVersion
        expected_node_version = $expectedNodeVersion
        platform_system = $platformSystem
        expected_platform_system = $expectedPlatformSystem
        backend_lock_path = $backendLockRelativePath
        backend_lock_sha256 = $backendLockSha256
        runtime_alignment = $runtimeAlignment
        runtime_alignment_reason = if ($runtimeAlignment -eq "PASS") {
            $null
        } else {
            "Local runtime differs from the exact GitHub release runtime; engineering checks are not release-bindable"
        }
        backend_lock_install = "PASS"
        backend_tests = "PASS"
        frontend_typecheck = "PASS"
        frontend_build = "PASS"
        frontend_unit = "NOT_RUN"
        frontend_unit_reason = "Task 22 has not installed Vitest or committed the first real unit suite"
        overall = "TASKS_1_4_BASELINE_PASS_WITH_NOT_RUN"
    }
    $json = $report | ConvertTo-Json -Depth 5
    if ($Output) {
        $outputPath = [System.IO.Path]::GetFullPath((Join-Path $root $Output))
        $outputDirectory = Split-Path -Parent $outputPath
        [System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($json + "`n")
        $stream = [System.IO.File]::Open($outputPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
        try {
            $stream.Write($bytes, 0, $bytes.Length)
        }
        finally {
            $stream.Dispose()
        }
    }
    Write-Output $json
}
finally {
    if ($lockedEnvironmentRoot -and (Test-Path -LiteralPath $lockedEnvironmentRoot)) {
        $resolvedLockedEnvironment = [System.IO.Path]::GetFullPath($lockedEnvironmentRoot)
        $temporaryPrefix = $temporaryRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
        if (-not $resolvedLockedEnvironment.StartsWith(
            $temporaryPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            throw "refusing to remove a locked environment outside the system temporary directory"
        }
        Remove-Item -LiteralPath $resolvedLockedEnvironment -Recurse -Force
    }
    Pop-Location
}
