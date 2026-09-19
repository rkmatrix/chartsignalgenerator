# Keeps the PA desk (python -m pa.main) alive on the dashboard port.
#
# Safe to fire repeatedly: a global mutex means a second copy exits at once, so
# Task Scheduler can re-run this every few minutes and it doubles as a watchdog.

$ErrorActionPreference = "Continue"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Set-Location $ProjectRoot

$SupervisorLog = Join-Path $LogDir "desk-supervisor.log"
$RuntimeLog = Join-Path $LogDir "desk-runtime.log"
$Port = 8776

function Write-Log([string]$Message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $SupervisorLog -Value "$stamp | $Message"
}

function Test-DeskUp {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return [bool]$conn
}

function Stop-OrphanDesk {
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'pa\.main' } |
        ForEach-Object {
            Write-Log "killing orphan desk PID $($_.ProcessId)"
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Start-Sleep -Seconds 1
}

# One supervisor per machine.
$mutex = New-Object System.Threading.Mutex($false, "Global\PA_Desk_Supervisor")
if (-not $mutex.WaitOne(0)) {
    exit 0
}

try {
    if (Test-DeskUp) {
        Write-Log "port $Port already serving; nothing to do"
        exit 0
    }

    $Python = "C:\Users\rkmat\AppData\Local\Programs\Python\Python311\pythonw.exe"
    if (-not (Test-Path $Python)) {
        $cmd = Get-Command pythonw -ErrorAction SilentlyContinue
        if ($cmd) { $Python = $cmd.Source } else { $Python = "pythonw" }
    }

    Stop-OrphanDesk

    while ($true) {
        Write-Log "starting desk on port $Port ($Python)"
        $proc = Start-Process -FilePath $Python -ArgumentList @("-u", "-m", "pa.main") `
            -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -Wait `
            -RedirectStandardOutput $RuntimeLog -RedirectStandardError "$RuntimeLog.err"
        $code = $proc.ExitCode
        Write-Log "desk exited with code $code; restarting in 15 seconds"

        # Preserve the evidence. Start-Process truncates both redirect targets on
        # every launch, so each restart used to destroy the traceback of the crash
        # that caused it. Six crashes on 2026-09-14, two of them stack overflows
        # (0xC00000FD), left nothing behind to diagnose. Keep a dated copy so the
        # next one is explicable.
        #
        # -1 is what a deliberate Stop-Process looks like and happens on every
        # manual restart, so those are skipped to stop the useful copies being
        # aged out by routine ones.
        if ($code -ne 0 -and $code -ne -1) {
            $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $crashDir = Join-Path $LogDir "crashes"
            New-Item -ItemType Directory -Force -Path $crashDir | Out-Null
            foreach ($pair in @(@($RuntimeLog, "out"), @("$RuntimeLog.err", "err"))) {
                if ((Test-Path $pair[0]) -and (Get-Item $pair[0]).Length -gt 0) {
                    Copy-Item $pair[0] (Join-Path $crashDir "$stamp-$code.$($pair[1]).log") -ErrorAction SilentlyContinue
                }
            }
            Write-Log "archived crash logs to logs\crashes\$stamp-$code.*"
            Get-ChildItem $crashDir -Filter "*.log" -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending | Select-Object -Skip 40 |
                Remove-Item -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 15
    }
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
