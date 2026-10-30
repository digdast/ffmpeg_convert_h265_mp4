# ==============================================================================
# ⚡ GPU-Accelerated H.265 / HEVC Transcoder Universal Installer (Windows)
# ==============================================================================
# Usage:
#   irm https://raw.githubusercontent.com/digdast/ffmpeg_convert_h265_mp4/main/install.ps1 | iex
#   or
#   irm https://ffmpeg.ertechlab.com/install.ps1 | iex
# ==============================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "  ⚡=============================================================⚡" -ForegroundColor Cyan
Write-Host "    GPU-Accelerated H.265 (HEVC) Media Transcoder Windows Setup  " -ForegroundColor Cyan
Write-Host "    Author: Efrain Rivas (@digdast) | Portfolio: ertechlab.com   " -ForegroundColor Cyan
Write-Host "  ⚡=============================================================⚡" -ForegroundColor Cyan
Write-Host ""

$INSTALL_DIR = Join-Path $env:LOCALAPPDATA "ffmpeg-transcoder"
$TARGET_PY = Join-Path $INSTALL_DIR "transcode_media.py"
$CMD_SHIM = Join-Path $INSTALL_DIR "transcode-media.cmd"
$ALIAS_SHIM = Join-Path $INSTALL_DIR "ffmpeg-convert.cmd"
$PS_SHIM = Join-Path $INSTALL_DIR "transcode-media.ps1"

# Check Python
Write-Host "[*] Checking Python installation..." -ForegroundColor Yellow
$py = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $py) {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
}

if ($py) {
    $pyVer = & $py.Source --version 2>&1
    Write-Host "[✓] Found Python: $pyVer" -ForegroundColor Green
} else {
    Write-Host "[✗] Python 3 was not found in your PATH." -ForegroundColor Red
    Write-Host "    Install via winget: winget install Python.Python.3.12" -ForegroundColor Yellow
}

# Check FFmpeg
Write-Host "[*] Checking FFmpeg installation..." -ForegroundColor Yellow
$ff = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if ($ff) {
    $ffVer = (& $ff.Source -version | Select-Object -First 1)
    Write-Host "[✓] Found FFmpeg: $ffVer" -ForegroundColor Green
} else {
    Write-Host "[!] Warning: ffmpeg.exe was not found in your PATH." -ForegroundColor Yellow
    Write-Host "    Install via winget: winget install Gyan.FFmpeg" -ForegroundColor Yellow
}

# Probing GPU Encoders
if ($ff) {
    Write-Host "`n[*] Probing Hardware GPU Accelerators..." -ForegroundColor Yellow
    $encoders = & $ff.Source -encoders 2>$null
    $hasNvenc = $encoders -match "hevc_nvenc"
    $hasQsv = $encoders -match "hevc_qsv"
    $hasAmf = $encoders -match "hevc_amf"

    if ($hasNvenc) {
        Write-Host "  ✔ NVIDIA NVENC (hevc_nvenc): Supported" -ForegroundColor Green
    }
    if ($hasQsv) {
        Write-Host "  ✔ Intel QuickSync (hevc_qsv): Supported" -ForegroundColor Green
    }
    if ($hasAmf) {
        Write-Host "  ✔ AMD AMF (hevc_amf): Supported" -ForegroundColor Green
    }
    if (-not ($hasNvenc -or $hasQsv -or $hasAmf)) {
        Write-Host "  ℹ No GPU encoder detected in current FFmpeg build. CPU (libx265) will be used." -ForegroundColor Yellow
    }
}

# Create installation directory
if (-not (Test-Path $INSTALL_DIR)) {
    New-Item -ItemType Directory -Path $INSTALL_DIR -Force | Out-Null
}

# Download transcode_media.py
Write-Host "`n[*] Downloading latest transcode_media.py..." -ForegroundColor Yellow
$sourceUrls = @(
    "https://raw.githubusercontent.com/digdast/ffmpeg_convert_h265_mp4/main/transcode_media.py",
    "https://ffmpeg.ertechlab.com/transcode_media.py"
)

$downloadSuccess = $false
foreach ($url in $sourceUrls) {
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13
        Invoke-WebRequest -Uri $url -OutFile $TARGET_PY -UseBasicParsing -TimeoutSec 15
        $downloadSuccess = $true
        break
    } catch {
        # Try next fallback
    }
}

if (-not $downloadSuccess) {
    Write-Host "[✗] Failed to download script from GitHub and fallback." -ForegroundColor Red
    exit 1
}

# Generate CMD shims for seamless terminal usage
$cmdContent = @"
@echo off
python "%~dp0transcode_media.py" %*
"@
Set-Content -Path $CMD_SHIM -Value $cmdContent -Encoding ASCII
Set-Content -Path $ALIAS_SHIM -Value $cmdContent -Encoding ASCII

# Generate PowerShell script wrapper
$psContent = @"
param([Parameter(ValueFromRemainingArguments=`$true)]`$args)
python "$TARGET_PY" @args
"@
Set-Content -Path $PS_SHIM -Value $psContent -Encoding UTF8

# Ensure $INSTALL_DIR is in the User PATH
$userPath = [Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::User)
if ($userPath -notlike "*$INSTALL_DIR*") {
    Write-Host "[*] Adding $INSTALL_DIR to User PATH..." -ForegroundColor Yellow
    $newPath = if ($userPath) { "$userPath;$INSTALL_DIR" } else { $INSTALL_DIR }
    [Environment]::SetEnvironmentVariable("Path", $newPath, [EnvironmentVariableTarget]::User)
}

# Update current session PATH so command works immediately
if ($env:Path -notlike "*$INSTALL_DIR*") {
    $env:Path = "$env:Path;$INSTALL_DIR"
}

Write-Host "`n[✓] Installation Complete!" -ForegroundColor Green
Write-Host "  Binary Location: $INSTALL_DIR" -ForegroundColor Cyan
Write-Host "  Commands:        transcode-media  or  ffmpeg-convert" -ForegroundColor Cyan
Write-Host "`nQuick Help:" -ForegroundColor Yellow
Write-Host "  transcode-media --help" -ForegroundColor Cyan
Write-Host "`nExample Transcoding:" -ForegroundColor Yellow
Write-Host '  transcode-media "D:\Movies" --dry-run' -ForegroundColor Cyan
Write-Host '  transcode-media "D:\Movies" --cq 24 --delete-original' -ForegroundColor Cyan
Write-Host ""

