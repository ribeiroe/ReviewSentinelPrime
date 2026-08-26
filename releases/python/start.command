#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  Review Sentinel Prime — Start Script  (Mac)
#  Double-click this file to start the app.
# ─────────────────────────────────────────────────────────────────
set -e

# Resolve the project root:
#   - In the release ZIP, app.py sits alongside this script → use SCRIPT_DIR
#   - In the repo, this script lives at releases/python/ → go two levels up
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/app.py" ]; then
    cd "$SCRIPT_DIR"
else
    cd "$SCRIPT_DIR/../.."
fi

echo ""
echo "  ====================================================="
echo "    Review Sentinel Prime | SLR Automation Platform"
echo "  ====================================================="
echo ""

# ── 1. Check Python 3 ────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "  [ERROR] Python 3 was not found on this system."
    echo "  Please install Python 3.9+ from https://python.org"
    exit 1
fi
PYVER=$(python3 --version 2>&1)
echo "  [OK] Found $PYVER"

# ── 2. Create virtual environment if not present ─────────────────
if [ ! -f ".venv/bin/python" ]; then
    echo "  [SETUP] Creating virtual environment (first run only)..."
    python3 -m venv .venv
    echo "  [OK] Virtual environment created."
else
    echo "  [OK] Virtual environment found."
fi

# ── 3. Activate virtual environment ──────────────────────────────
# shellcheck disable=SC1091
source .venv/bin/activate

# ── 4. Install / update dependencies ─────────────────────────────
echo "  [SETUP] Installing dependencies (this may take a moment on first run)..."
pip install -r requirements.txt --quiet --disable-pip-version-check
echo "  [OK] All dependencies are ready."

# ── 5. Ensure data directories exist ─────────────────────────────
mkdir -p data/results

# ── 6. Launch Flask in background then open browser ──────────────
PID_FILE="$PWD/.rsp.pid"

# Stop any previously running instance
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "  [INFO] Stopping previous instance (PID $OLD_PID)..."
        kill "$OLD_PID"
        sleep 1
    fi
    rm -f "$PID_FILE"
fi

echo ""
echo "  [OK] Starting Review Sentinel Prime at http://127.0.0.1:5001"
echo "  [INFO] A browser window will open automatically."
echo "  [INFO] You can now close this terminal — the app keeps running."
echo "  [INFO] To stop the app, double-click stop.command (or run: kill \$(cat \"$PID_FILE\"))"
echo ""

# Launch Flask detached from the terminal
nohup python app.py > "$PWD/rsp.log" 2>&1 &
APP_PID=$!
echo $APP_PID > "$PID_FILE"
echo "  [OK] App running in background (PID $APP_PID). Logs → rsp.log"
echo ""

# Open browser after a short delay
sleep 2
open "http://127.0.0.1:5001"
