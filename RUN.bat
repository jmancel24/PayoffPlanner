@echo off
setlocal enabledelayedexpansion
title Payoff Tracker
cd /d "%~dp0"
call _findpy.bat

if not defined PY (
  echo.
  echo   Could not find Python. Run SETUP.bat first, or see START HERE.txt
  echo.
  pause
  exit /b 1
)

"%PY%" finplan.py
if errorlevel 1 (
  echo.
  echo   The app closed with an error. Try running SETUP.bat,
  echo   or double-click REPORT.bat to see your plan as text.
  echo.
  pause
)
