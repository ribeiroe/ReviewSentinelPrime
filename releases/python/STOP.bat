@echo off
setlocal EnableDelayedExpansion
title Review Sentinel Prime - Stop Launcher
color 0C

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
        pause
        exit /b 1
    )
)

cd /d "%APP_ROOT%"

set "PID_FILE=.rsp.pid"
set "FORCE_RESET=0"
set "STOPPED=0"

if /I "%~1"=="/reset" set "FORCE_RESET=1"

if exist "%PID_FILE%" (
    set /p RSP_PID=<"%PID_FILE%"
    if not "!RSP_PID!"=="" (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-Process -Id !RSP_PID! -ErrorAction SilentlyContinue) { Stop-Process -Id !RSP_PID! -Force; exit 0 } else { exit 2 }"
        if %errorlevel% equ 0 (
            echo  [OK] Stopped Review Sentinel Prime process ^(PID !RSP_PID!^).
            set "STOPPED=1"
        )
    )
    del /f /q "%PID_FILE%" >nul 2>&1
)

if "%STOPPED%"=="0" (
    if "%FORCE_RESET%"=="1" (
        for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":5001 .*LISTENING"') do (
            taskkill /PID %%p /F >nul 2>&1
            echo  [OK] Forced stop of process using port 5001 ^(PID %%p^).
            set "STOPPED=1"
        )
    )
)

if "%STOPPED%"=="0" (
    echo  [INFO] No running Review Sentinel Prime process was found.
) else (
    if "%FORCE_RESET%"=="1" (
        echo  [OK] Reset complete.
    )
)

echo.
echo  You can start the app again with START.bat.
timeout /t 2 /nobreak >nul
exit /b 0