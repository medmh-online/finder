@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MedMH AI Workforce - Installer

echo ============================================
echo  MedMH v3.9.1 - Windows installer
 echo ============================================
echo.
echo This window will stay open and an INSTALL_LOG.txt will be saved.
echo.

if not exist "tools\install_windows.ps1" (
    echo ERROR: tools\install_windows.ps1 is missing.
    echo Extract the full project ZIP to a folder and run this installer there.
    goto :failed
)

where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo ERROR: Windows PowerShell is missing.
    goto :failed
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_windows.ps1"
set "INSTALL_RESULT=%ERRORLEVEL%"
if not "%INSTALL_RESULT%"=="0" goto :failed

echo.
echo INSTALL COMPLETE. You can now run START.bat.
echo.
pause
exit /b 0

:failed
echo.
echo Installation stopped. Check INSTALL_LOG.txt for details.
echo If you need help, send the last 25 lines of INSTALL_LOG.txt.
echo.
pause
exit /b 1
