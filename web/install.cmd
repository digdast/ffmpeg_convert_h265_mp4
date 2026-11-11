@echo off
REM ============================================================================
REM GPU-Accelerated H.265 / HEVC Transcoder CMD Bootstrapper (Windows)
REM Usage:
REM   curl.exe -fsSL https://ffmpeg.ertechlab.com/install.cmd -o "%TEMP%\ff-install.cmd" && "%TEMP%\ff-install.cmd"
REM ============================================================================
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072; irm https://ffmpeg.ertechlab.com/install.ps1 | iex"
exit /b %ERRORLEVEL%
