@echo off
title Review Sentinel Prime - Stopping
color 0C
cd /d "%~dp0"

echo.
echo  ============================================================
echo   Review Sentinel Prime  ^|  Stopping...
echo  ============================================================
echo.

docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo  [i] Docker Desktop is not running.
    echo      The app is already stopped.
    echo.
    pause
    exit /b 0
)

echo  Stopping Review Sentinel Prime...
:: Use 'stop' (not 'down') so the container stays visible in Docker Desktop
:: and start-app.bat can resume it quickly without rebuilding.
docker compose stop
if %errorlevel% neq 0 (
    echo.
    echo  [!] Something went wrong.
    echo  Please take a screenshot of this window and contact support.
    echo.
    pause
    exit /b 1
)

echo.
echo  ============================================================
echo   App stopped successfully.
echo   Your data and settings have been saved.
echo   Double-click start-app.bat  or use Docker Desktop to start it again.
echo  ============================================================
echo.
timeout /t 4 /nobreak >nul
exit /b 0
