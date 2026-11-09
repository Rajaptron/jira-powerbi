@echo off
REM Windows launcher (the PC where Power BI Desktop runs). Double-click to start.
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto deps
echo First run: setting up, this takes about 30 seconds...
py -3 -m venv .venv 2>nul || python -m venv .venv
if not exist .venv\Scripts\python.exe (
  echo Python 3 was not found. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^) and try again.
  pause
  exit /b 1
)
:deps
REM Install / update add-ons whenever requirements.txt changes (e.g. after copying in a new version).
fc /b requirements.txt .venv\requirements.installed >nul 2>&1
if errorlevel 1 (
  echo Installing add-ons...
  .venv\Scripts\python -m pip install -q -r requirements.txt && copy /y requirements.txt .venv\requirements.installed >nul
)
.venv\Scripts\python -m app.server %*
if errorlevel 1 pause
