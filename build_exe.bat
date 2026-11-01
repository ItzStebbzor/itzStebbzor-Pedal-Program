@echo off
setlocal
title Build Stebbzor Pedal Manager v1.4
cd /d "%~dp0"
echo Installing required build dependencies...
py -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto error
echo.
echo Building optimized onedir Windows application...
py -m PyInstaller --noconfirm --clean --onedir --windowed --noupx --name "StebbzorPedalManager" --collect-all keyboard --collect-all psutil --collect-all pystray --collect-all PIL --exclude-module matplotlib --exclude-module numpy --exclude-module scipy --exclude-module pandas --exclude-module tkinter.test stebbzor_pedal_manager.py
if errorlevel 1 goto error
echo.
echo BUILD COMPLETE
echo Run dist\StebbzorPedalManager\StebbzorPedalManager.exe
echo For publishing updates, ZIP the CONTENTS of dist\StebbzorPedalManager
echo into StebbzorPedalManager-Windows.zip and attach it to a GitHub Release.
pause
exit /b 0
:error
echo BUILD FAILED. Check Python installation and internet access.
pause
exit /b 1
