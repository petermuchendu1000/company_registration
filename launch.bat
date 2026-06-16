@echo off
title Company App Factory
color 1F

echo.
echo  ==============================================
echo    Company App Factory
echo  ==============================================
echo.

REM Create logs dir if it doesn't exist
if not exist "pipeline_output\logs" mkdir "pipeline_output\logs"

REM Check if server is already running
powershell -Command "try{Invoke-WebRequest http://localhost:5000/control -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0}catch{exit 1}" >nul 2>&1
if %errorlevel% equ 0 (
    echo  Server already running.
    echo  Opening control panel...
    start "" http://localhost:5000/control
    exit /b 0
)

echo  Starting server...
start /B "" python -u app.py > pipeline_output\logs\server.log 2>&1

echo  Waiting for server to be ready...
:WAIT
timeout /t 2 /nobreak > nul
powershell -Command "try{Invoke-WebRequest http://localhost:5000/control -UseBasicParsing -TimeoutSec 1 | Out-Null; exit 0}catch{exit 1}" >nul 2>&1
if %errorlevel% neq 0 goto WAIT

echo  Opening control panel...
start "" http://localhost:5000/control

echo.
echo  -----------------------------------------------
echo   Control panel: http://localhost:5000/control
echo  -----------------------------------------------
echo.
echo  Keep this window open while working.
echo  Close it (or press Ctrl+C) to stop the server.
echo.
pause
