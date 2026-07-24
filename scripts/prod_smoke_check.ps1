param(
    [string]$BaseUrl = "https://www.vowpic.com",
    [int]$ReadinessTimeoutSec = 180,
    [switch]$SkipGenerationBackendProbe
)

$ErrorActionPreference = "Stop"
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    throw "Node.js is required. Run: node scripts/prod_smoke_check.mjs --base-url $BaseUrl"
}

$nodeArgs = @(
    "scripts/prod_smoke_check.mjs",
    "--base-url",
    $BaseUrl,
    "--readiness-timeout-sec",
    [string]$ReadinessTimeoutSec
)
if ($SkipGenerationBackendProbe) {
    $nodeArgs += "--skip-generation-backend-probe"
}

& $node.Source @nodeArgs
exit $LASTEXITCODE
