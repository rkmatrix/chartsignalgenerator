# Posts the end-of-day summary to the Chart Signals chat. Scheduled after the bell.

$ErrorActionPreference = "Continue"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $ProjectRoot

$Log = Join-Path $LogDir "eod.log"
$env:PYTHONUTF8 = "1"

$Python = "C:\Users\rkmat\AppData\Local\Programs\Python\Python311\python.exe"
if (-not (Test-Path $Python)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Python = $cmd.Source } else { $Python = "python" }
}

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $Log -Value ""
Add-Content -Path $Log -Value "===== $stamp EOD summary ====="
& $Python -m pa.open_session.eod *>&1 | Add-Content -Path $Log
Add-Content -Path $Log -Value "exit code $LASTEXITCODE"
