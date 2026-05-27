@echo off
title ZiSi Bot - All Services

echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║        ZiSi Bot - Single Terminal Mode       ║
echo  ╚══════════════════════════════════════════════╝
echo.
echo  This window runs EVERYTHING:
echo    - Python bot (main.py)  - trading cycles
echo    - Dashboard backend     - API on :5000
echo    - Dashboard frontend    - served on :5000 (built)
echo.
echo  Press Ctrl+C once to stop all services cleanly.
echo.

cd /d "%~dp0presentation\dashboard\backend"

REM Check if frontend has been built; if not, build it first
if not exist "..\frontend\dist\index.html" (
    echo [ZiSi] Frontend not built yet - building now...
    cd /d "%~dp0presentation\dashboard\frontend"
    call npm run build
    cd /d "%~dp0presentation\dashboard\backend"
)

REM Start everything (server.js spawns the bot automatically)
npm start

pause
