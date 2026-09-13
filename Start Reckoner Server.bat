@echo off
cd /d "%~dp0"
echo Starting Pharmacy Ready Reckoner server...
echo.
"%~dp0venv\Scripts\python.exe" server.py
pause
