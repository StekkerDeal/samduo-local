# Local CI gate: runs the same checks as .github/workflows (except HACS
# validation, which needs the GitHub API). Requires Docker Desktop.
#
#   .\scripts\check.ps1          # everything
#   .\scripts\check.ps1 -Fast    # ruff + pytest only, skip hassfest

param(
    [switch]$Fast
)

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent

Write-Host "== Ruff + pytest (python:3.13 container) ==" -ForegroundColor Cyan
docker run --rm -v "${repo}:/app" -w /app -v samduo-pip-cache:/root/.cache/pip python:3.13 sh -c @"
pip install -q ruff -r requirements_test.txt 2>&1 | tail -1
ruff check custom_components/samduo_battery tests || exit 1
ruff format --check custom_components/samduo_battery tests || exit 1
pytest tests/ -q --tb=short
"@
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: ruff/pytest" -ForegroundColor Red; exit 1 }

if (-not $Fast) {
    Write-Host "== Hassfest (ghcr.io/home-assistant/hassfest) ==" -ForegroundColor Cyan
    docker run --rm -v "${repo}:/github/workspace" ghcr.io/home-assistant/hassfest
    if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: hassfest" -ForegroundColor Red; exit 1 }
}

Write-Host "All checks passed." -ForegroundColor Green
