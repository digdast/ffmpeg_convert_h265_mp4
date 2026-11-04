@echo off
title FFmpeg Multi-GPU Transcoder Studio
cd /d "%~dp0"
python transcode_gui.py %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Transcoder closed with exit code %ERRORLEVEL%.
    pause
)
