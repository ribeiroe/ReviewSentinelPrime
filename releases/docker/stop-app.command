#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Review Sentinel Prime — stop-app.command
#  macOS: double-click to stop.
# ─────────────────────────────────────────────────────────────────────────────

cd "$(dirname "$0")"

echo ""
echo "  ============================================================"
echo "   Review Sentinel Prime  |  Stopping..."
echo "  ============================================================"
echo ""

if ! docker info > /dev/null 2>&1; then
    echo "  [i] Docker Desktop is not running."
    echo "      The app is already stopped."
    echo ""
    read -r -p "  Press Enter to close..."
    exit 0
fi

echo "  Stopping Review Sentinel Prime..."
# Use 'stop' (not 'down') so the container stays visible in Docker Desktop
# and start-app.sh can resume it quickly without rebuilding.
docker compose stop

echo ""
echo "  ============================================================"
echo "   App stopped successfully."
echo "   Your data and settings have been saved."
echo "   Double-click start-app.command or use Docker Desktop to start it again."
echo "  ============================================================"
echo ""
