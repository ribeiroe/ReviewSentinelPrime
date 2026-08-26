# run_tests.ps1 – Review Sentinel Prime QA Runner
# -------------------------------------------------
# Prerequisites:
#   • Python 3 installed and on PATH
#   • Run from the repo root: .\qa\run_tests.ps1
# -------------------------------------------------

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
$REPO = Split-Path -Parent $ROOT

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Review Sentinel Prime – Automated QA Suite" -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# ---- Activate virtual environment -------------------------------------------
$venv = Join-Path $REPO "venv\Scripts\Activate.ps1"
if (Test-Path $venv) {
    Write-Host "`n[1/4] Activating virtual environment..." -ForegroundColor Yellow
    & $venv
} else {
    Write-Host "`n[WARN] venv not found at $venv — using system Python" -ForegroundColor DarkYellow
}

# ---- Install test dependencies ----------------------------------------------
Write-Host "`n[2/4] Installing test dependencies..." -ForegroundColor Yellow
pip install --quiet -r (Join-Path $ROOT "requirements-test.txt")

# ---- Ensure results directories exist --------------------------------------
$reportsDir = Join-Path $ROOT "results\reports"
$rawDir      = Join-Path $ROOT "results\raw"
New-Item -ItemType Directory -Force -Path $reportsDir | Out-Null
New-Item -ItemType Directory -Force -Path $rawDir      | Out-Null

# ---- Run pytest -------------------------------------------------------------
Write-Host "`n[3/4] Running tests..." -ForegroundColor Yellow

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$junitXml  = Join-Path $reportsDir "junit_$timestamp.xml"
$htmlReport = Join-Path $reportsDir "report_$timestamp.html"

Push-Location $ROOT
try {
    pytest `
        --rootdir=$ROOT `
        --html=$htmlReport `
        --self-contained-html `
        --junit-xml=$junitXml `
        -v `
        --tb=short `
        -r a
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

# ---- Print summary ----------------------------------------------------------
Write-Host "`n[4/4] Results written to:" -ForegroundColor Yellow
Write-Host "  HTML : $htmlReport" -ForegroundColor Green
Write-Host "  JUnit: $junitXml"  -ForegroundColor Green

if ($exitCode -eq 0) {
    Write-Host "`n  ALL TESTS PASSED" -ForegroundColor Green
} else {
    Write-Host "`n  SOME TESTS FAILED (exit code $exitCode)" -ForegroundColor Red
}

Write-Host "============================================================" -ForegroundColor Cyan
exit $exitCode
