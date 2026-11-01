@echo off
setlocal
title Stebbzor Pedal Manager v1.4 Setup
cd /d "%~dp0"
py -m pip install --upgrade pip
if errorlevel 1 goto error
py -m pip install -r requirements.txt
if errorlevel 1 goto error
echo.
echo Setup complete. Run start.bat to launch.
pause
exit /b 0
:error
echo Setup failed. Install Python 3.10+ and enable Add Python to PATH.
pause
exit /b 1
