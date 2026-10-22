@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title MedMH AI Workforce
if not exist ".venv\Scripts\python.exe" goto notinstalled
if not exist "whatsapp\node_modules\whatsapp-web.js" goto notinstalled

echo ============================================
echo    MedMH AI Workforce
echo    Open http://127.0.0.1:8502
echo    Leave this window open while using the app.
echo ============================================
echo.
".venv\Scripts\python.exe" "run_ai.py"
if errorlevel 1 (
echo.
echo Startup stopped with an error. Read the output above.
 pause
)
exit /b %errorlevel%

:notinstalled
echo Installation missing. Run INSTALL.bat once first.
pause
exit /b 1
