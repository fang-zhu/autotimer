@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please install the project virtual environment first. See README.md.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0gui.py"
