@echo off
setlocal enabledelayedexpansion
title Payoff Tracker - Setup
cd /d "%~dp0"
call _findpy.bat

if not defined PY (
  echo.
  echo   Could not find Python on this computer.
  echo.
  echo   Install it from  https://www.python.org/downloads/
  echo   and tick "Add python.exe to PATH" on the first screen.
  echo.
  pause
  exit /b 1
)

echo.
echo   Using Python: "%PY%"
"%PY%" --version
echo.
echo   Installing the PDF readers...
echo.
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install pdfplumber pypdf
echo.
"%PY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
  echo   NOTE: this Python has no tkinter, so the window won't open.
  echo   REPORT.bat will still print your full plan as text.
  echo   To fix: reinstall Python from python.org with the default options.
) else (
  echo   Setup complete. Close this window and double-click RUN.bat
)
echo.
pause
