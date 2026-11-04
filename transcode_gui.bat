@echo off
title FFmpeg Multi-GPU Transcoder Studio
cd /d "%~dp0"
python transcode_gui.py %*
