@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if not errorlevel 1 (
  py -3 launch.py %*
) else (
  python launch.py %*
)
set "maicon_result=%errorlevel%"
if not "%maicon_result%"=="0" pause
exit /b %maicon_result%
