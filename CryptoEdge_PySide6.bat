@echo off
chcp 65001 >nul
cd /d "%~dp0"
title CryptoEdge - PySide6 Fallback
python -u app.py
if errorlevel 1 py -u app.py
pause
