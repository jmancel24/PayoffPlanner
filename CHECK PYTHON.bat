@echo off
setlocal enabledelayedexpansion
title Payoff Tracker - Python check
cd /d "%~dp0"
call _findpy.bat
echo.
if not defined PY (
  echo   RESULT: no working Python found.
  echo.
  echo   Looked in:
  echo     - the "py" launcher
  echo     - "python" on your PATH
  echo     - %%LocalAppData%%\Python\pythoncore-*
  echo     - %%LocalAppData%%\Programs\Python\Python3*
  echo     - C:\Program Files\Python3*
  echo.
  echo   Install from https://www.python.org/downloads/
) else (
  echo   Python found:  "%PY%"
  "%PY%" --version
  echo.
  "%PY%" -c "import tkinter; print('   tkinter:     yes - the app window will open')" 2>nul || echo    tkinter:     NO - use REPORT.bat instead of RUN.bat
  "%PY%" -c "import pdfplumber; print('   pdfplumber:  yes - PDF import ready')" 2>nul || echo    pdfplumber:  no - run SETUP.bat
  "%PY%" -c "import pypdf; print('   pypdf:       yes - backup PDF reader ready')" 2>nul || echo    pypdf:       no - run SETUP.bat
)
echo.
pause
