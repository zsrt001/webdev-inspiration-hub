param(
    [switch]$NonStrict,
    [switch]$NoProbeStorage,
    [switch]$NoArtifacts
)

$scriptArgs = @("scripts/preflight_production.py")

if (-not $NoProbeStorage) {
    $scriptArgs += "--probe-storage"
}

if ($NonStrict) {
    $scriptArgs += "--non-strict"
}

if (-not $NoArtifacts) {
    $scriptArgs += "--write-artifacts"
}

python @scriptArgs
exit $LASTEXITCODE
