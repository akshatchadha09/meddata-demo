@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set PORT=8000

echo.
echo ================================================
echo        MedData ^| PHASE 8.1 DEMO
echo ================================================
echo.

REM If another program is already using port 8000, identify it.
set "PIDS="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr LISTENING ^| findstr :%PORT%') do set "PIDS=!PIDS! %%P"
if defined PIDS (
  echo Port %PORT% is already in use by PID(s):!PIDS!
  echo.
  choice /C YN /M "Stop those local processes so Phase 8.1 can start"
  if errorlevel 2 (
    echo.
    echo Please close the program using port %PORT% and run this launcher again.
    pause
    exit /b 1
  )
  for %%P in (!PIDS!) do taskkill /PID %%P /F >nul 2>nul
  timeout /t 1 /nobreak >nul
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create the Python environment. Make sure Python is installed and in PATH.
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"
python -m pip install -r backend\requirements.txt
if errorlevel 1 (
  echo.
  echo Dependency installation failed.
  pause
  exit /b 1
)

echo.
echo Starting MedData on http://127.0.0.1:%PORT%
echo Do not open the HTML files directly.
echo.
python -m uvicorn backend.main:app --host 127.0.0.1 --port %PORT%
pause
