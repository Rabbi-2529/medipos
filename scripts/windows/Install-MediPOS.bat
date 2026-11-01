@echo off
rem Installs ThingBD MediPOS on this PC and puts the ThingBD MediPOS icon on the desktop.
cd /d "%~dp0..\.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
