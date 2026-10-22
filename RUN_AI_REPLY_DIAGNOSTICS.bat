@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found. Run INSTALL.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "tools\diagnose_ai_replies.py" %*
echo.
echo Copy the diagnostic output to your ChatGPT conversation. It contains no client phone numbers or message texts.
pause
