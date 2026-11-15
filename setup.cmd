@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if not errorlevel 1 (
  py -3 launch.py --setup %*
) else (
  python launch.py --setup %*
)
set "maicon_result=%errorlevel%"
if not "%maicon_result%"=="0" (
  echo Setup failed. Read the error above. Python 3.12 must be installed with "Add python.exe to PATH" enabled.
  pause
)
exit /b %maicon_result%
