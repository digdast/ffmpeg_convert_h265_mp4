# ==============================================================================
# GPU-Accelerated H.265 / HEVC Transcoder Universal Installer v2.0 (Windows)
# ==============================================================================
# Usage (PowerShell 5.1 or PowerShell 7+):
#   irm https://ffmpeg.ertechlab.com/install.ps1 | iex
#
# Usage (CMD / Restricted ExecutionPolicy):
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://ffmpeg.ertechlab.com/install.ps1 | iex"
# ==============================================================================

$ErrorActionPreference = "Stop"

# Enable TLS 1.2+ safely across Windows PowerShell 5.1 and PowerShell 7+
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
} catch {}

$bolt = [char]0x26A1
$check = [char]0x2714

Write-Host ""
Write-Host "  $bolt=============================================================$bolt" -ForegroundColor Cyan
Write-Host "    GPU-Accelerated H.265 (HEVC) Media Transcoder v2.0 Setup      " -ForegroundColor Cyan
Write-Host "    CLI Pipeline + Multi-GPU Studio GUI | ffmpeg.ertechlab.com    " -ForegroundColor Cyan
Write-Host "  $bolt=============================================================$bolt" -ForegroundColor Cyan
Write-Host ""

$INSTALL_DIR = if ($env:FFMPEG_TRANSCODER_DIR) { $env:FFMPEG_TRANSCODER_DIR } else { Join-Path $env:LOCALAPPDATA "ffmpeg-transcoder" }

# ------------------------------------------------------------------------------
# 1. Detect Real Python 3 Installation (ignoring Microsoft Store stub)
# ------------------------------------------------------------------------------
function Find-PythonRuntime {
    $candidates = @()
    foreach ($cmdName in @("python.exe", "python3.exe", "py.exe")) {
        $found = Get-Command $cmdName -All -ErrorAction SilentlyContinue
        foreach ($f in $found) {
            if ($f.Source -and ($f.Source -notlike "*\WindowsApps\*")) {
                $candidates += $f.Source
            }
        }
    }
    $localPyDirs = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe"),
        "C:\Python3*\python.exe",
        "C:\Program Files\Python3*\python.exe"
    )
    foreach ($pattern in $localPyDirs) {
        $items = Get-Item $pattern -ErrorAction SilentlyContinue | Sort-Object FullName -Descending
        foreach ($it in $items) {
            $candidates += $it.FullName
        }
    }

    foreach ($exe in ($candidates | Select-Object -Unique)) {
        $prevEAP = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            if ([System.IO.Path]::GetFileName($exe).ToLower() -eq "py.exe") {
                $out = (& $exe -3 --version 2>&1 | Out-String).Trim()
                if ($LASTEXITCODE -eq 0 -and ($out -match "Python 3\.")) {
                    return @{ Exe = $exe; Args = "-3"; Version = ($out -split "`r?`n")[0].Trim() }
                }
            } else {
                $out = (& $exe --version 2>&1 | Out-String).Trim()
                if ($LASTEXITCODE -eq 0 -and ($out -match "Python 3\.")) {
                    return @{ Exe = $exe; Args = ""; Version = ($out -split "`r?`n")[0].Trim() }
                }
            }
        } catch {}
        finally {
            $ErrorActionPreference = $prevEAP
        }
    }
    return $null
}

Write-Host "[*] Checking Python 3 installation..." -ForegroundColor Yellow
$pyInfo = Find-PythonRuntime
if (-not $pyInfo -and $env:FFMPEG_AUTO_DEPS -eq "1") {
    $wg = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($wg) {
        Write-Host "[*] Installing Python 3.12 via WinGet..." -ForegroundColor Yellow
        & winget.exe install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
        $pyInfo = Find-PythonRuntime
    }
}

if ($pyInfo) {
    Write-Host "[OK] Found $($pyInfo.Version) ($($pyInfo.Exe))" -ForegroundColor Green
} else {
    Write-Host "[!] Python 3 was not detected in PATH yet." -ForegroundColor Yellow
    Write-Host "    Install Python 3 with: winget install --id Python.Python.3.12 -e" -ForegroundColor Cyan
}

# ------------------------------------------------------------------------------
# 2. Detect FFmpeg & Probe Hardware GPU Encoders
# ------------------------------------------------------------------------------
function Find-FFmpegBinary {
    $ff = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    if ($ff -and $ff.Source) { return $ff.Source }

    $fallbacks = @(
        "C:\ffmpeg\bin\ffmpeg.exe",
        "C:\ProgramData\chocolatey\bin\ffmpeg.exe",
        (Join-Path $env:ProgramFiles "ffmpeg\bin\ffmpeg.exe"),
        (Join-Path $env:USERPROFILE "scoop\shims\ffmpeg.exe")
    )
    $wgPattern = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\*\*\bin\ffmpeg.exe"
    $wgItems = Get-Item $wgPattern -ErrorAction SilentlyContinue
    foreach ($w in $wgItems) { $fallbacks += $w.FullName }

    foreach ($p in $fallbacks) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

Write-Host "[*] Checking FFmpeg installation..." -ForegroundColor Yellow
$ffExe = Find-FFmpegBinary
if (-not $ffExe -and $env:FFMPEG_AUTO_DEPS -eq "1") {
    $wg = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($wg) {
        Write-Host "[*] Installing Gyan.FFmpeg via WinGet..." -ForegroundColor Yellow
        & winget.exe install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
        $ffExe = Find-FFmpegBinary
    }
}

if ($ffExe) {
    $prevEAP = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $ffVer = (& $ffExe -version 2>&1 | Select-Object -First 1).ToString().Trim()
        Write-Host "[OK] Found FFmpeg: $ffVer" -ForegroundColor Green

        Write-Host "[*] Probing Hardware GPU Accelerators..." -ForegroundColor Yellow
        $encoders = (& $ffExe -hide_banner -encoders 2>&1 | Out-String)
    } finally {
        $ErrorActionPreference = $prevEAP
    }
    $hasNvenc = $encoders -match "hevc_nvenc"
    $hasQsv   = $encoders -match "hevc_qsv"
    $hasAmf   = $encoders -match "hevc_amf"

    if ($hasNvenc) { Write-Host "  $check NVIDIA NVENC (hevc_nvenc): Supported" -ForegroundColor Green }
    if ($hasQsv)   { Write-Host "  $check Intel QuickSync (hevc_qsv): Supported" -ForegroundColor Green }
    if ($hasAmf)   { Write-Host "  $check AMD AMF (hevc_amf): Supported" -ForegroundColor Green }
    if (-not ($hasNvenc -or $hasQsv -or $hasAmf)) {
        Write-Host "  [i] No GPU HEVC encoder detected in FFmpeg build; CPU (libx265) fallback ready." -ForegroundColor Yellow
    }
} else {
    Write-Host "[!] Warning: ffmpeg.exe was not found in PATH." -ForegroundColor Yellow
    Write-Host "    Install FFmpeg with: winget install --id Gyan.FFmpeg -e" -ForegroundColor Cyan
}

# ------------------------------------------------------------------------------
# 3. Download Transcoder v2.0 Suite (CLI + Multi-GPU GUI + Telemetry)
# ------------------------------------------------------------------------------
if (-not (Test-Path $INSTALL_DIR)) {
    New-Item -ItemType Directory -Path $INSTALL_DIR -Force | Out-Null
}

$baseSources = @(
    "https://ffmpeg.ertechlab.com",
    "https://raw.githubusercontent.com/digdast/ffmpeg_convert_h265_mp4/main"
)

$moduleFiles = @(
    @{ Name = "transcode_media.py"; Required = $true  },
    @{ Name = "gpu_detector.py";    Required = $false },
    @{ Name = "gpu_monitor.py";     Required = $false },
    @{ Name = "remote_engine.py";   Required = $false },
    @{ Name = "transcode_gui.py";   Required = $false }
)

Write-Host ""
Write-Host "[*] Downloading FFmpeg H.265 Transcoder Suite to $INSTALL_DIR..." -ForegroundColor Yellow

foreach ($mod in $moduleFiles) {
    $fileName = $mod.Name
    $destPath = Join-Path $INSTALL_DIR $fileName
    $tempPath = "$destPath.tmp"
    $downloaded = $false

    foreach ($base in $baseSources) {
        $url = "$base/$fileName"
        try {
            Invoke-WebRequest -Uri $url -OutFile $tempPath -UseBasicParsing -TimeoutSec 20
            if (Test-Path $tempPath) {
                $head = Get-Content -Path $tempPath -TotalCount 5 -ErrorAction SilentlyContinue | Out-String
                # Ensure we received valid Python source and not an HTML redirect/error page
                if (($head -notmatch "<!DOCTYPE html") -and ($head -notmatch "<html") -and ((Get-Item $tempPath).Length -gt 200)) {
                    Move-Item -Path $tempPath -Destination $destPath -Force
                    $downloaded = $true
                    break
                }
            }
        } catch {
            if (Test-Path $tempPath) { Remove-Item $tempPath -Force -ErrorAction SilentlyContinue }
        }
    }

    if ($downloaded) {
        Write-Host "  [OK] Installed $fileName" -ForegroundColor Green
    } elseif ($mod.Required) {
        Write-Host "  [ERROR] Failed to download required module: $fileName" -ForegroundColor Red
        exit 1
    } else {
        Write-Host "  [!] Optional module skipped: $fileName" -ForegroundColor DarkYellow
    }
}

# ------------------------------------------------------------------------------
# 4. Generate Global CLI & GUI Shims (CMD + PowerShell)
# ------------------------------------------------------------------------------
function New-AppShims {
    param(
        [string]$CommandName,
        [string]$PythonScript,
        [bool]$Windowed = $false
    )
    $cmdPath = Join-Path $INSTALL_DIR "$CommandName.cmd"
    $ps1Path = Join-Path $INSTALL_DIR "$CommandName.ps1"
    $scriptFull = Join-Path $INSTALL_DIR $PythonScript

    if ($Windowed) {
        $cmdBody = @"
@echo off
setlocal
if exist "%~dp0transcode_gui.py" (
    where pythonw.exe >nul 2>nul && (start "" pythonw.exe "%~dp0$PythonScript" %* & exit /b 0)
    where pyw.exe >nul 2>nul && (start "" pyw.exe -3 "%~dp0$PythonScript" %* & exit /b 0)
    where python.exe >nul 2>nul && (python.exe "%~dp0$PythonScript" %* & exit /b %ERRORLEVEL%)
    where py.exe >nul 2>nul && (py.exe -3 "%~dp0$PythonScript" %* & exit /b %ERRORLEVEL%)
)
echo [ERROR] Python 3 not found. Install via: winget install --id Python.Python.3.12 -e
exit /b 1
"@
    } else {
        $cmdBody = @"
@echo off
setlocal
where python.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python.exe "%~dp0$PythonScript" %*
    exit /b %ERRORLEVEL%
)
where py.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py.exe -3 "%~dp0$PythonScript" %*
    exit /b %ERRORLEVEL%
)
echo [ERROR] Python 3 not found. Install via: winget install --id Python.Python.3.12 -e
exit /b 1
"@
    }

    $ps1Body = @"
param([Parameter(ValueFromRemainingArguments=`$true)]`$RemainingArgs)
`$scriptPath = Join-Path `$PSScriptRoot "$PythonScript"
`$py = Get-Command python.exe -ErrorAction SilentlyContinue | Where-Object { `$_.Source -notlike "*\WindowsApps\*" } | Select-Object -First 1
if (`$py) {
    & `$py.Source `$scriptPath @RemainingArgs
    exit `$LASTEXITCODE
}
`$pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if (`$pyLauncher) {
    & `$pyLauncher.Source -3 `$scriptPath @RemainingArgs
    exit `$LASTEXITCODE
}
Write-Error "Python 3 not found. Install via: winget install --id Python.Python.3.12 -e"
exit 1
"@

    Set-Content -Path $cmdPath -Value $cmdBody -Encoding ASCII
    Set-Content -Path $ps1Path -Value $ps1Body -Encoding UTF8
}

New-AppShims -CommandName "transcode-media" -PythonScript "transcode_media.py"
New-AppShims -CommandName "ffmpeg-convert"  -PythonScript "transcode_media.py"
if (Test-Path (Join-Path $INSTALL_DIR "gpu_monitor.py")) {
    New-AppShims -CommandName "gpu-monitor" -PythonScript "gpu_monitor.py"
}
if (Test-Path (Join-Path $INSTALL_DIR "transcode_gui.py")) {
    New-AppShims -CommandName "transcode-gui" -PythonScript "transcode_gui.py" -Windowed $true

    $launcherBat = Join-Path $INSTALL_DIR "Launch_Transcoder_GUI.bat"
    Set-Content -Path $launcherBat -Value "@echo off`r`ncall `"%~dp0transcode-gui.cmd`" %*`r`n" -Encoding ASCII

    # Create Windows Start Menu Shortcut
    try {
        $startMenuDir = Join-Path ([Environment]::GetFolderPath("Programs")) "FFmpeg H.265 Transcoder"
        if (-not (Test-Path $startMenuDir)) {
            New-Item -ItemType Directory -Path $startMenuDir -Force | Out-Null
        }
        $lnkPath = Join-Path $startMenuDir "FFmpeg H.265 Transcoder Studio.lnk"
        $wsh = New-Object -ComObject WScript.Shell
        $sc = $wsh.CreateShortcut($lnkPath)
        $sc.TargetPath = (Join-Path $INSTALL_DIR "transcode-gui.cmd")
        $sc.WorkingDirectory = $INSTALL_DIR
        $sc.WindowStyle = 7
        $sc.Description = "GPU-Accelerated H.265 Media Transcoder Studio"
        $sc.Save()
    } catch {}
}

# Create Uninstaller script
$uninstallPath = Join-Path $INSTALL_DIR "uninstall.ps1"
$uninstallBody = @"
`$installDir = '$INSTALL_DIR'
`$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (`$userPath) {
    `$clean = (`$userPath -split ';' | Where-Object { `$_ -and (`$_.TrimEnd('\') -ne `$installDir.TrimEnd('\')) }) -join ';'
    [Environment]::SetEnvironmentVariable('Path', `$clean, 'User')
}
`$sm = Join-Path ([Environment]::GetFolderPath('Programs')) 'FFmpeg H.265 Transcoder'
if (Test-Path `$sm) { Remove-Item `$sm -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host '[OK] Removed FFmpeg H.265 Transcoder from PATH and Start Menu.' -ForegroundColor Green
Write-Host "You can now delete: `$installDir" -ForegroundColor Cyan
"@
Set-Content -Path $uninstallPath -Value $uninstallBody -Encoding UTF8

# ------------------------------------------------------------------------------
# 5. Register User PATH & Current Session PATH
# ------------------------------------------------------------------------------
$userPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)
$pathEntries = if ($userPath) { $userPath -split ";" } else { @() }
$alreadyInUserPath = $false
foreach ($entry in $pathEntries) {
    if ($entry.TrimEnd("\") -ieq $INSTALL_DIR.TrimEnd("\")) {
        $alreadyInUserPath = $true
        break
    }
}

if (-not $alreadyInUserPath) {
    Write-Host "[*] Adding $INSTALL_DIR to Windows User PATH..." -ForegroundColor Yellow
    $newPath = if ($userPath) { "$($userPath.TrimEnd(';'));$INSTALL_DIR" } else { $INSTALL_DIR }
    [Environment]::SetEnvironmentVariable("Path", $newPath, [EnvironmentVariableTarget]::User)
}

if (($env:Path -split ";") -notcontains $INSTALL_DIR) {
    $env:Path = "$($env:Path.TrimEnd(';'));$INSTALL_DIR"
}

Write-Host ""
Write-Host "[OK] Installation Complete!" -ForegroundColor Green
Write-Host "  Install Location: $INSTALL_DIR" -ForegroundColor Cyan
Write-Host "  CLI Commands:     transcode-media   |   ffmpeg-convert   |   gpu-monitor" -ForegroundColor Cyan
Write-Host "  Studio GUI:       transcode-gui     (also added to Windows Start Menu)" -ForegroundColor Cyan
Write-Host ""
Write-Host "Quick Start:" -ForegroundColor Yellow
Write-Host "  transcode-media --help" -ForegroundColor Cyan
Write-Host "  transcode-gui" -ForegroundColor Cyan
Write-Host '  transcode-media "D:\Movies" --dry-run' -ForegroundColor Cyan
Write-Host '  transcode-media "D:\Movies" --both --cq 24 --delete-original' -ForegroundColor Cyan
Write-Host ""
