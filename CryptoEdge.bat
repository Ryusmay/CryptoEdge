@echo off
chcp 65001 >nul
cd /d "%~dp0"
title CryptoEdge - Native Trading Console

echo ========================================
echo   CryptoEdge - Native Dark Modern UI
echo ========================================
echo.
echo Uruchamianie natywnego okna...
echo Przegladarka NIE jest uzywana.
echo.

python -c "import requests, cryptography, PySide6, websocket" 2>nul
if errorlevel 1 (
  echo Instalacja zaleznosci CryptoEdge...
  python -m pip install -r requirements.txt
  if errorlevel 1 py -m pip install -r requirements.txt
)

python -u app.py
if errorlevel 1 py -u app.py

echo.
echo CryptoEdge zakonczony.
pause
