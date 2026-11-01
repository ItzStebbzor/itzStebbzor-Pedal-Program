@echo off
cd /d "%~dp0"
py stebbzor_pedal_manager.py
if errorlevel 1 pause
