@echo off
title Review Sentinel Prime — Launcher
color 0B

set "SCRIPT_DIR=%~dp0"
set "APP_ROOT="

if exist "%SCRIPT_DIR%app.py" (
    set "APP_ROOT=%SCRIPT_DIR%"
) else (
    if exist "%SCRIPT_DIR%..\..\app.py" (
        set "APP_ROOT=%SCRIPT_DIR%..\..\"
    ) else (
        echo  [ERROR] Could not find app.py near this launcher.
        echo.
        echo  Expected either:
        echo    - app.py next to START.bat ^(release ZIP layout^), or
        echo    - app.py two folders up from START.bat ^(repository layout^).
        echo.
        pause
        exit /b 1
    )
)

cd /d "%APP_ROOT%"

if not exist "requirements.txt" (
    echo  [ERROR] requirements.txt was not found in %CD%
    echo.
    echo  This launcher must run from the app root.
    pause
    exit /b 1
)

echo.
echo  =====================================================
echo    Review Sentinel Prime ^| SLR Automation Platform
echo  =====================================================
echo.

:: ── 1. Check Python ──────────────────────────────────────────────
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python was not found on this system.
    echo.
    echo  Please install Python 3.12 or later from https://python.org
    echo  During installation, make sure to tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  [OK] Found %PYVER%

:: ── 2. Create virtual environment if not present ─────────────────
if not exist ".venv\Scripts\python.exe" (
    echo  [SETUP] Creating virtual environment ^(first run only^)...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo  [ERROR] Failed to create virtual environment.
        echo  Make sure "python -m venv" is available on your system.
        pause
        exit /b 1
    )
    echo  [OK] Virtual environment created.
) else (
    echo  [OK] Virtual environment found.
)

:: ── 3. Activate virtual environment ──────────────────────────────
call .venv\Scripts\activate.bat

:: ── 4. Install / update dependencies ─────────────────────────────
echo  [SETUP] Installing dependencies (this may take a moment on first run)...
pip install -r requirements.txt --quiet --disable-pip-version-check
if %errorlevel% neq 0 (
    echo  [ERROR] Dependency installation failed.
    echo  Check your internet connection and try again.
    pause
    exit /b 1
)
echo  [OK] All dependencies are ready.

:: ── 5. Ensure data directories exist ─────────────────────────────
if not exist "data\results" mkdir "data\results"

:: Keep track of the launched background process for STOP.bat
set "PID_FILE=.rsp.pid"

if exist "%PID_FILE%" (
    set /p OLD_RSP_PID=<"%PID_FILE%"
    if not "%OLD_RSP_PID%"=="" (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-Process -Id %OLD_RSP_PID% -ErrorAction SilentlyContinue) { Stop-Process -Id %OLD_RSP_PID% -Force }"
    )
    del /f /q "%PID_FILE%" >nul 2>&1
)

:: ── 6. Open browser then start Flask ─────────────────────────────
echo.
echo  [OK] Starting Review Sentinel Prime at http://127.0.0.1:5001
echo  [INFO] A browser window will open automatically.
echo  [INFO] The launcher window will close automatically.
echo  [INFO] To stop the app later, run STOP.bat.
echo.

:: Start Flask in the background with a hidden window
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Start-Process -FilePath '.\.venv\Scripts\python.exe' -ArgumentList 'app.py' -WorkingDirectory '%CD%' -WindowStyle Hidden -PassThru; Set-Content -Path '.rsp.pid' -Value $p.Id -Encoding ASCII"
if %errorlevel% neq 0 (
    echo  [ERROR] Failed to start the app in background mode.
    pause
    exit /b 1
)

:: Give Flask a moment to bind, then open browser and close launcher
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5001"
exit /b 0
