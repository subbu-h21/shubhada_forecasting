@echo off
cd /d "%~dp0"

echo ============================================================
echo Pharmacy Ready Reckoner - Clean Start
echo ============================================================
echo.

echo [1/3] Stopping any old server still running...
set FOUND=0
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8420 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)
if "%FOUND%"=="1" (
    echo       Old server stopped.
) else (
    echo       Nothing was running - clean already.
)
echo.

echo [2/3] Refreshing data and reports (run_reckoner.py)...
python run_reckoner.py
if errorlevel 1 (
    echo.
    echo       Something went wrong above - check the message before continuing.
    pause
    exit /b 1
)
echo.

echo [3/3] Starting the mobile server...
echo ============================================================
echo.
python server.py

pause
