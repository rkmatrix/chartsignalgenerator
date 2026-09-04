$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..
if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Created .env from .env.example — set POLYGON_API_KEY if you want live data."
}
if (-not (Test-Path .venv)) {
    python -m venv .venv
}
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]" -q
& .\.venv\Scripts\python.exe -m pa.main
