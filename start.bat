@echo off
rem LAK Style Generator - first run creates the Python environment, then starts the app.
setlocal
cd /d "%~dp0backend"

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  py -3.13 -m venv .venv || python -m venv .venv || goto :nopython
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)

echo Starting on http://127.0.0.1:8765
start "" http://127.0.0.1:8765
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8765
goto :eof

:nopython
echo Python 3.11+ is required. Install it from https://www.python.org/downloads/
pause & exit /b 1
:fail
echo Dependency installation failed. Check your internet connection and try again.
pause & exit /b 1
