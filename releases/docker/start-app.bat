@echo off
title Review Sentinel Prime
color 0B
cd /d "%~dp0"

echo.
echo  ============================================================
echo   Review Sentinel Prime  ^|  Starting...
echo  ============================================================
echo.

:: -- Check Docker is running --------------------------------------------------
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo  [!] Docker Desktop is not running.
    echo.
    echo  Please do the following:
    echo    1. Find "Docker Desktop" in your Start Menu and open it.
    echo    2. Wait until the whale icon in your taskbar turns green/steady.
    echo    3. Double-click this file again.
    echo.
    pause
    exit /b 1
)
echo  [OK] Docker Desktop is running.
echo.

:: -- Start the app ------------------------------------------------------------
echo  [1/3] Starting Review Sentinel Prime...
echo.

:: Check if the container already exists (created on a previous run)
docker inspect reviewsentinel-app >nul 2>&1
if %errorlevel% equ 0 (
    echo        Resuming existing installation - this will be fast.
    echo        Please wait - do not close this window.
    echo.
    docker compose start
) else (
    echo        First-time setup - this can take 3-5 minutes to download.
    echo        Please wait - do not close this window.
    echo.
    docker compose up -d --build
)
if %errorlevel% neq 0 (
    echo.
    echo  [!] Something went wrong.
    echo  Please take a screenshot of this window and contact support.
    echo.
    pause
    exit /b 1
)
echo.

:: -- Wait for app to be ready -------------------------------------------------
echo  [2/3] Waiting for the app to be ready...
set /a attempts=0
:wait_loop
    set /a attempts+=1
    if %attempts% gtr 30 (
        echo.
        echo  [!] The app is taking longer than expected.
        echo  Try opening http://localhost:8090 in your browser manually.
        echo  If it does not work after 1 minute, please contact support.
        echo.
        pause
        exit /b 1
    )
    timeout /t 2 /nobreak >nul
    powershell -Command "try { $null = Invoke-WebRequest -Uri 'http://localhost:8090' -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
    if %errorlevel% neq 0 goto wait_loop

:: -- Open browser -------------------------------------------------------------
echo  [3/3] App is ready! Opening your browser...
echo.
echo  ============================================================
echo   Review Sentinel Prime is running at:
echo   http://localhost:8090
echo.
echo   You can close this window.
echo   The app keeps running in the background.
echo   To stop it, double-click  stop-app.bat
echo  ============================================================
echo.
start http://localhost:8090
exit /b 0
