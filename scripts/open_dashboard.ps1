# Puts the PA dashboard on screen after a logon.
#
# start_desk.ps1 keeps the server alive but never opens anything, so after a
# reboot the desk is healthy and the screen is blank -- which looks exactly like
# an outage. Verified on 2026-09-21: the machine booted 08:36:50, the desk was
# serving by 08:37:56, and it was still reported as down at 08:48 because no
# window was showing it.
#
# Waits for /health before opening, so the browser never lands on a connection
# error while the server is still starting.

$ErrorActionPreference = "Continue"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Log = Join-Path $LogDir "dashboard-open.log"
$Port = 8776
$Url = "http://127.0.0.1:$Port/"
$WaitSeconds = 180

function Write-Log([string]$Message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $Log -Value "$stamp | $Message"
}

# Open once per boot. The task is logon-triggered, but a lock/unlock or a second
# session would otherwise stack up tabs; keying the marker to boot time means a
# genuine restart opens a window and nothing else does.
$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString("yyyyMMdd-HHmmss")
$marker = Join-Path $LogDir ".dashboard-opened-$boot"
if (Test-Path $marker) {
    Write-Log "already opened for the boot at $boot; leaving the existing window alone"
    exit 0
}

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$Port/health" -TimeoutSec 5 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $healthy = $true; break }
    } catch {
        Start-Sleep -Seconds 3
    }
}

if (-not $healthy) {
    # Deliberately still opens. If the desk is genuinely broken, a browser tab
    # showing the failure is far more likely to be noticed than a silent absence,
    # which is the failure mode this whole script exists to prevent.
    Write-Log "desk did not answer within ${WaitSeconds}s; opening anyway so the problem is visible"
} else {
    Write-Log "desk healthy; opening $Url"
}

try {
    Start-Process $Url
    New-Item -ItemType File -Force -Path $marker | Out-Null
    Get-ChildItem $LogDir -Filter ".dashboard-opened-*" -Force -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 5 |
        Remove-Item -Force -ErrorAction SilentlyContinue
} catch {
    Write-Log "could not open a browser: $($_.Exception.Message)"
}
