# Install the `figaro` command and the figaro agent skill on Windows, for this
# user. Safe to run again: it only fixes what is missing or stale, and never
# overwrites a file or folder it did not make.
#
#   powershell -ExecutionPolicy Bypass -File tools\install.ps1
#       figaro.exe in %USERPROFILE%\.local\bin, which goes on your PATH, and
#       the skill in %USERPROFILE%\.claude\skills and %USERPROFILE%\.agents\skills
#   ... -Uninstall          remove them (only if they are ours)
#   ... -Skills DIR,DIR     the skill in these folders instead
#   ... -Bin DIR            the command in DIR, left off your PATH (for a test)
#
# The command is the figaro.exe that pip makes in the venv from pyproject.toml,
# copied: an .exe takes Figma links with "&" from PowerShell, cmd and Git Bash
# alike, while a .cmd file would hand them to cmd.exe, which cuts them at the
# "&". It runs this checkout's code with its venv, from any folder. The skill
# is a junction to skill\figaro, so it changes together with the code; making
# one needs no admin rights.
#
# Keep this file pure ASCII: Windows PowerShell 5.1 reads a BOM-less script as
# ANSI, so a single em dash or arrow breaks parsing of the whole file.

param(
    [string]$Bin = "",
    [string[]]$Skills = @(),
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 started from PowerShell 7 inherits its module folders
# and can't load the modules there (Get-FileHash goes missing): keep to its own.
if ($PSVersionTable.PSEdition -ne "Core") {
    $env:PSModulePath = [Environment]::GetEnvironmentVariable("PSModulePath", "Machine")
}
$Repo = Split-Path -Parent $PSScriptRoot
$AddToPath = -not $Bin
if (-not $Bin) { $Bin = Join-Path $env:USERPROFILE ".local\bin" }
# "-Skills a,b" comes as the one string "a,b" through powershell -File.
$Skills = @($Skills | ForEach-Object { $_ -split "," } | Where-Object { $_ })
if (-not $Skills) {
    $Skills = @((Join-Path $env:USERPROFILE ".claude\skills"), (Join-Path $env:USERPROFILE ".agents\skills"))
}

$Venv = Join-Path $Repo "venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$VenvExe = Join-Path $Venv "Scripts\figaro.exe"
$Cmd = Join-Path $Bin "figaro.exe"
$Skill = Join-Path $Repo "skill\figaro"

function Warn([string]$Text) { [Console]::Error.WriteLine("install.ps1: $Text") }
function Fail([string]$Text) { Warn $Text; exit 1 }

function Invoke-Native([string]$Exe, [string[]]$Arguments, [switch]$Quiet) {
    # Runs a program; $LASTEXITCODE says how it went. Windows PowerShell 5.1 may
    # turn what a program prints on stderr into errors that stop this script, so
    # here they don't.
    $ErrorActionPreference = "Continue"
    if ($Quiet) { & $Exe @Arguments 2>$null } else { & $Exe @Arguments }
}

function Get-PythonVersion([string[]]$Python) {
    # major.minor, or $null for what is not a working Python, such as the
    # Microsoft Store's stub python.exe, which only says where to get Python.
    try {
        $out = Invoke-Native $Python[0] (@($Python | Select-Object -Skip 1) +
            @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")) -Quiet
        if ($LASTEXITCODE -eq 0 -and "$out" -match "^\d+\.\d+$") { return [version]"$out" }
    } catch { }
    return $null
}

function Find-Python {
    # Python 3.9 or newer: python or python3 on PATH, else the py launcher.
    $candidates = @()
    foreach ($name in "python", "python3") {
        foreach ($c in @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)) {
            $candidates += , @($c.Source)
        }
    }
    $py = Get-Command py -CommandType Application -ErrorAction SilentlyContinue
    if ($py) { $candidates += , @($py.Source, "-3") }
    foreach ($c in $candidates) {
        $v = Get-PythonVersion $c
        if ($v -and $v -ge [version]"3.9") { return , $c }
    }
    return $null
}

function Test-Present([string]$Path) {
    # Anything at Path, a broken link included.
    return [IO.File]::Exists($Path) -or [IO.Directory]::Exists($Path) -or
        [bool](Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue)
}

function Test-OurExe([string]$Path) {
    # A figaro.exe that runs this checkout's venv: its launcher names the
    # venv's python.exe.
    if (-not [IO.File]::Exists($Path)) { return $false }
    $text = [Text.Encoding]::UTF8.GetString([IO.File]::ReadAllBytes($Path))
    return $text.IndexOf($VenvPython, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-Sha256([string]$Path) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($Path))) }
    finally { $sha.Dispose() }
}

function Test-OurLink([string]$Path) {
    # A junction or symbolic link to this checkout's skill.
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not $item -or -not $item.LinkType) { return $false }
    $target = "$(@($item.Target)[0])" -replace '^\\\\\?\\|^\\\?\?\\', ''
    return $target.TrimEnd("\") -eq $Skill.TrimEnd("\")
}

function Test-InPathList([string]$List, [string]$Dir) {
    foreach ($p in ($List -split ";")) {
        if ($p -and [Environment]::ExpandEnvironmentVariables($p).TrimEnd("\") -eq $Dir.TrimEnd("\")) {
            return $true
        }
    }
    return $false
}

function Add-UserPath([string]$Dir) {
    # The raw value, with its type kept: [Environment]::SetEnvironmentVariable
    # would save it as REG_SZ and break entries such as %USERPROFILE%\bin.
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment")
    try {
        $raw = [string]$key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        if (Test-InPathList $raw $Dir) { return $false }
        $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString
        if ($key.GetValueNames() -contains "Path") { $kind = $key.GetValueKind("Path") }
        $new = if ($raw) { $raw.TrimEnd(";") + ";" + $Dir } else { $Dir }
        $key.SetValue("Path", $new, $kind)
    } finally {
        $key.Close()
    }
    # Setting a user variable tells Explorer, and the terminals it opens from
    # now on, that the environment changed.
    [Environment]::SetEnvironmentVariable("FIGARO_INSTALL", "1", "User")
    [Environment]::SetEnvironmentVariable("FIGARO_INSTALL", $null, "User")
    return $true
}

if ($Uninstall) {
    if (Test-OurExe $Cmd) { Remove-Item -LiteralPath $Cmd -Force; Write-Host "removed $Cmd" }
    elseif (Test-Present $Cmd) { Write-Host "left ${Cmd}: not ours" }
    foreach ($dir in $Skills) {
        $link = Join-Path $dir "figaro"
        if (Test-OurLink $link) {
            # The link only: Remove-Item -Recurse on a junction can empty the
            # folder it points to in Windows PowerShell 5.1.
            [IO.Directory]::Delete($link)
            Write-Host "removed $link"
        } elseif (Test-Present $link) {
            Write-Host "left ${link}: not ours"
        }
    }
    exit 0
}

# 1. The venv: the bridge needs aiohttp, `shot` cuts tall pictures with Pillow,
# and pip makes figaro.exe from pyproject.toml.
if (-not [IO.File]::Exists($VenvPython)) {
    $python = Find-Python
    if (-not $python) {
        Fail ("Python 3.9 or newer is needed. Get it from https://www.python.org/downloads/ " +
              "(tick 'Add python.exe to PATH'), open a new terminal and run this again")
    }
    Write-Host "making the venv: $Venv"
    Invoke-Native $python[0] (@($python | Select-Object -Skip 1) + @("-m", "venv", $Venv))
    if ($LASTEXITCODE) { Fail "could not make the venv in $Venv" }
}
Invoke-Native $VenvPython @("-c", "import aiohttp, PIL") -Quiet
if ($LASTEXITCODE) {
    Write-Host "installing requirements.txt into the venv"
    Invoke-Native $VenvPython @("-m", "pip", "install", "-q", "-r", (Join-Path $Repo "requirements.txt"))
    if ($LASTEXITCODE) { Fail "pip could not install requirements.txt" }
}
if (-not [IO.File]::Exists($VenvExe)) {
    Write-Host "making figaro.exe in the venv"
    # An editable install needs pip 21.3 or newer; an early Python 3.9 has an
    # older one.
    Invoke-Native $VenvPython @("-m", "pip", "install", "-q", "--upgrade", "pip")
    Invoke-Native $VenvPython @("-m", "pip", "install", "-q", "--no-deps", "-e", $Repo)
    if ($LASTEXITCODE -or -not [IO.File]::Exists($VenvExe)) { Fail "pip could not make $VenvExe" }
}

# 2. The command. A figaro.exe of someone else's is left alone.
New-Item -ItemType Directory -Force -Path $Bin | Out-Null
if ((Test-Present $Cmd) -and -not (Test-OurExe $Cmd)) {
    Fail "$Cmd exists and is not ours - move it away and run this again"
}
if ([IO.File]::Exists($Cmd) -and (Get-Sha256 $Cmd) -eq (Get-Sha256 $VenvExe)) {
    Write-Host "command: $Cmd (already there)"
} else {
    try {
        Copy-Item -LiteralPath $VenvExe -Destination $Cmd -Force
    } catch {
        Fail "could not write ${Cmd}: $($_.Exception.Message) - if figaro is running, let it finish and run this again"
    }
    Write-Host "command: $Cmd"
}

# 3. The skill: a junction in each folder. A folder that holds someone else's
# figaro is skipped, and the others still get theirs.
$failed = 0
foreach ($dir in $Skills) {
    $link = Join-Path $dir "figaro"
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    if (Test-OurLink $link) {
        Write-Host "skill: $link (already there)"
    } elseif (Test-Present $link) {
        Warn "$link exists and is not a link to $Skill - move it away and run this again"
        $failed = 1
    } else {
        New-Item -ItemType Junction -Path $link -Target $Skill | Out-Null
        Write-Host "skill: $link -> $Skill"
    }
}

# 4. PATH. Terminals that are open already keep their old one.
if ($AddToPath -and (Add-UserPath $Bin)) { Write-Host "PATH: added $Bin for your user" }
if (-not (Test-InPathList $env:Path $Bin)) {
    Write-Host "note: open a new terminal to run figaro, or call $Cmd by its full path"
}
exit $failed
