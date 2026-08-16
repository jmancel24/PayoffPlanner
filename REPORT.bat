@echo off
setlocal enabledelayedexpansion
title Payoff Tracker - Text Report
cd /d "%~dp0"
call _findpy.bat
if not defined PY (
  echo   Could not find Python. See START HERE.txt
  pause & exit /b 1
)
"%PY%" finplan.py --report > payoff_report.txt 2>&1
type payoff_report.txt
echo.
echo   (also saved as payoff_report.txt in this folder)
pause
