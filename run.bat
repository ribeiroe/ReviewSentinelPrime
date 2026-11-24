@echo off
cd /d "%~dp0"

:: Development launcher: same setup as the release START.bat, so every
:: machine runs with the exact package versions in requirements.txt.

if exist "venv\Scripts\activate.bat" if not exist ".venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    goto install
)

if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Creating virtual environment .venv ^(first run only^)...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment. Is Python 3.12+ installed and on PATH?
        exit /b 1
    )
)
call .venv\Scripts\activate.bat

:install
echo [SETUP] Installing / updating dependencies from requirements.txt...
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. Check your internet connection and try again.
    exit /b 1
)

python app.py
