#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Review Sentinel Prime — start-app.command
#  macOS: double-click to start. If asked, choose "Open with Terminal".
# ─────────────────────────────────────────────────────────────────────────────
set -e

# Move to the folder where this script lives (works even with double-click)
cd "$(dirname "$0")"

# Detect build context: in a release ZIP all app files are alongside this script;
# when running from the repo they live two levels up.
# APP_ROOT = where app.py / requirements.txt live (build context for Docker)
# SCRIPT_DIR = where Dockerfile and docker-compose.yml live (always here)
SCRIPT_DIR="$(pwd)"
if [ -f "app.py" ]; then
    export APP_ROOT="."
else
    export APP_ROOT="$(cd ../.. && pwd)"
fi
export DOCKERFILE="$SCRIPT_DIR/Dockerfile"

echo ""
echo "  ============================================================"
echo "   Review Sentinel Prime  |  Starting..."
echo "  ============================================================"
echo ""

# ── Check Docker is running ───────────────────────────────────────
if ! docker info > /dev/null 2>&1; then
    echo "  [!] Docker Desktop is not running."
    echo ""
    echo "  Please do the following:"
    echo "    1. Open Docker Desktop from your Applications folder."
    echo "    2. Wait until the whale icon in your menu bar is steady."
    echo "    3. Run this script again."
    echo ""
    read -r -p "  Press Enter to close..."
    exit 1
fi
echo "  [OK] Docker Desktop is running."
echo ""

# ── Start the app ─────────────────────────────────────────────────
echo "  [1/3] Starting Review Sentinel Prime..."
echo ""

if docker inspect reviewsentinel-app > /dev/null 2>&1; then
    echo "        Resuming existing installation — this will be fast."
    echo "        Please wait — do not close this window."
    echo ""
    docker compose start
else
    echo "        First-time setup — this can take 3-5 minutes to download."
    echo "        Please wait — do not close this window."
    echo ""
    docker compose up -d --build
fi

# ── Wait for app to be ready ──────────────────────────────────────
echo ""
echo "  [2/3] Waiting for the app to be ready..."
attempts=0
until curl -s -o /dev/null -w "%{http_code}" http://localhost:8090 2>/dev/null | grep -q "200"; do
    attempts=$((attempts + 1))
    if [ "$attempts" -gt 30 ]; then
        echo ""
        echo "  [!] The app is taking longer than expected."
        echo "  Try opening http://localhost:8090 in your browser manually."
        read -r -p "  Press Enter to close..."
        exit 1
    fi
    sleep 2
done

# ── Open browser ──────────────────────────────────────────────────
echo "  [3/3] App is ready! Opening your browser..."
echo ""
echo "  ============================================================"
echo "   Review Sentinel Prime is running at:"
echo "   http://localhost:8090"
echo ""
echo "   You can close this window."
echo "   The app keeps running in the background."
echo "   To stop it, double-click  stop-app.command"
echo "  ============================================================"
echo ""

# Open browser (macOS: open, Linux: xdg-open)
if command -v open > /dev/null 2>&1; then
    open http://localhost:8090
elif command -v xdg-open > /dev/null 2>&1; then
    xdg-open http://localhost:8090
fi
