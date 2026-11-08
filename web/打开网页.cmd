@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0open-preview.ps1"
if errorlevel 1 pause
