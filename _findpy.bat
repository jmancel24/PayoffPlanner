@echo off
rem ── Finds a working Python and sets PY to the full path of python.exe ──
rem     Call sites must quote it:   "%PY%" finplan.py
set "PY="

rem 1) the py launcher (ships with the Python 3.14 install manager).
rem    Ask it for the real interpreter path rather than assuming one.
for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"

rem 2) python on PATH. This also filters out the fake python.exe Windows
rem    ships that only opens the Microsoft Store -- it can't run -c.
if not defined PY for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"

rem 3) Python 3.14+ install-manager location
if not defined PY for /d %%D in ("%LocalAppData%\Python\pythoncore-*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"

rem 4) classic python.org per-user install
if not defined PY for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"

rem 5) classic python.org all-users install
if not defined PY for /d %%D in ("%ProgramFiles%\Python3*") do if exist "%%D\python.exe" set "PY=%%D\python.exe"
