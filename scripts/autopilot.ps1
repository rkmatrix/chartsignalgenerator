$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..
if (Test-Path .\.venv\Scripts\python.exe) {
    & .\.venv\Scripts\python.exe -m pa.chart_trader.scan
} else {
    python -m pa.chart_trader.scan
}
