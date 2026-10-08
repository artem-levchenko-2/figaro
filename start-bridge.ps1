# Start the Figaro bridge on native Windows, detached.
#
# start-bridge.sh needs bash and tmux, which a plain Windows box has neither of;
# without this the only option is keeping a terminal window open forever.
#
#   .\start-bridge.ps1            # start (no-op if already running)
#   .\start-bridge.ps1 -Restart   # stop the bridge on the port, then start
#   .\start-bridge.ps1 -Stop      # just stop
#
# If PowerShell says running scripts is disabled, run it as
#   powershell -ExecutionPolicy Bypass -File start-bridge.ps1
#
# Logs: %TEMP%\figaro-bridge-<port>.log and .err.log. The script finds the
# bridge by its port, so it never touches a bridge on another port, and it
# stops nothing but a bridge. The bridge exits after $env:FIGARO_IDLE_EXIT
# (default 3h) with no plugin and no requests; the figaro command starts it
# again by running this script.
#
# Keep this file pure ASCII: Windows PowerShell 5.1 reads a BOM-less script as
# ANSI, so a single em dash or arrow breaks parsing of the whole file.

param(
    [int]$Port = 8788,
    [switch]$Restart,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 started from PowerShell 7 inherits its module folders
# and can't load the modules there (Get-FileHash goes missing): keep to its own.
if ($PSVersionTable.PSEdition -ne "Core") {
    $env:PSModulePath = [Environment]::GetEnvironmentVariable("PSModulePath", "Machine")
}
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $env:TEMP "figaro-bridge-$Port.log"
$errLog = Join-Path $env:TEMP "figaro-bridge-$Port.err.log"
$idle = if ($env:FIGARO_IDLE_EXIT) { $env:FIGARO_IDLE_EXIT } else { "3h" }

# Prefer the venv interpreter, fall back to whatever python is on PATH.
$python = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    # WindowsApps\python.exe is the Microsoft Store stub: it opens the Store
    # instead of running anything, so treat it as "no Python".
    if ($cmd -and $cmd.Source -like "*\WindowsApps\*") { $cmd = $null }
    if (-not $cmd) {
        Write-Error "No Python found. Install it from python.org (tick 'Add python.exe to PATH'), then run tools\install.ps1"
    }
    $python = $cmd.Source
    Write-Host "venv not found, using $python" -ForegroundColor Yellow
}

function Get-BridgePid {
    # Get-NetTCPConnection reports the state as an enum, so it works on any
    # Windows display language; netstat prints "LISTENING" translated.
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($conn) { return $conn.OwningProcess }
    $line = netstat -ano | Select-String ":$Port\s.*\s0\.0\.0\.0:0\s|:$Port\s.*\s\[::\]:0\s" | Select-Object -First 1
    if (-not $line) { return $null }
    return ($line.ToString() -split '\s+' | Where-Object { $_ } | Select-Object -Last 1)
}

function Stop-Bridge {
    $existing = Get-BridgePid
    if (-not $existing) { Write-Host "port $Port is free"; return }
    # Only a bridge: whatever else listens there is not ours to stop.
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$existing" -ErrorAction SilentlyContinue
    if (-not $proc -or $proc.CommandLine -notlike "*bridge.py*") {
        $name = if ($proc) { $proc.Name } else { "an unknown program" }
        Write-Error "port $Port is held by $name (pid $existing), not a Figaro bridge - stop it yourself or use another port"
    }
    try {
        Stop-Process -Id $existing -Force -ErrorAction Stop
        Write-Host "stopped pid $existing" -ForegroundColor Green
        Start-Sleep -Milliseconds 600
    } catch {
        Write-Error "could not stop pid ${existing}: $($_.Exception.Message). It may be running as another user - try an elevated shell."
    }
}

if ($Stop -or $Restart) { Stop-Bridge }
if ($Stop) { return }

$existing = Get-BridgePid
if ($existing) {
    Write-Host "bridge already listening on $Port (pid $existing) - use -Restart to replace it" -ForegroundColor Yellow
    return
}

# One pre-quoted string, not an array: PS 5.1 does not quote array elements,
# so a path with a space ("F:\00 Projects\...") splits into two arguments.
Start-Process -FilePath $python `
    -ArgumentList "-u `"$(Join-Path $root 'bridge.py')`" --port $Port --idle-exit $idle" `
    -WorkingDirectory $root `
    -RedirectStandardOutput $log `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden | Out-Null

Start-Sleep -Seconds 2
$now = Get-BridgePid
if ($now) {
    Write-Host "bridge listening on http://127.0.0.1:$Port (pid $now)" -ForegroundColor Green
    Write-Host "log: $log"
    Write-Host "now run the plugin: Figma > Plugins > Development > Figaro"
} else {
    # Python puts the actual reason (a missing aiohttp, a taken port) on stderr.
    Write-Host "bridge did not come up - last lines of ${errLog}:" -ForegroundColor Red
    if (Test-Path $errLog) { Get-Content $errLog -Tail 20 }
    if ((Get-Content $errLog -Raw -ErrorAction SilentlyContinue) -match "No module named 'aiohttp'") {
        Write-Host "fix: run tools\install.ps1, which installs requirements.txt into the venv" -ForegroundColor Yellow
    }
    exit 1
}
