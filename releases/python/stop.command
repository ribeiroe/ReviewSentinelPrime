#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────
#  Review Sentinel Prime — Stop Script  (Mac/Linux)
#  Double-click this file to stop the app started by start.command.
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

PID_FILE="$PWD/.rsp.pid"
FORCE_RESET=0
STOPPED=0

if [ "${1:-}" = "--reset" ]; then
    FORCE_RESET=1
fi

echo ""
echo "  ====================================================="
echo "    Review Sentinel Prime | Stop Launcher"
echo "  ====================================================="
echo ""

if [ -f "$PID_FILE" ]; then
    RSP_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$RSP_PID" ] && kill -0 "$RSP_PID" 2>/dev/null; then
        kill "$RSP_PID" 2>/dev/null || true
        sleep 1
        if kill -0 "$RSP_PID" 2>/dev/null; then
            kill -9 "$RSP_PID" 2>/dev/null || true
        fi
        echo "  [OK] Stopped Review Sentinel Prime process (PID $RSP_PID)."
        STOPPED=1
    fi
    rm -f "$PID_FILE"
fi

# If the PID file is missing or stale, try to locate RSP by the default port.
# This handles cases where the app was started from another shell/copy and the
# local .rsp.pid was not created.
if [ "$STOPPED" -eq 0 ] && command -v lsof >/dev/null 2>&1; then
    PIDS_ON_PORT="$(lsof -ti :5001 2>/dev/null || true)"
    if [ -n "$PIDS_ON_PORT" ]; then
        for p in $PIDS_ON_PORT; do
            CMDLINE="$(ps -p "$p" -o command= 2>/dev/null || true)"
            CMDLINE_LC="$(printf '%s' "$CMDLINE" | tr '[:upper:]' '[:lower:]')"
            case "$CMDLINE_LC" in
                *python*app.py*)
                    kill "$p" 2>/dev/null || true
                    sleep 1
                    if kill -0 "$p" 2>/dev/null; then
                        kill -9 "$p" 2>/dev/null || true
                    fi
                    echo "  [OK] Stopped Review Sentinel Prime process on port 5001 (PID $p)."
                    STOPPED=1
                    ;;
            esac
        done
    fi
fi

if [ "$STOPPED" -eq 0 ] && [ "$FORCE_RESET" -eq 1 ]; then
    if command -v lsof >/dev/null 2>&1; then
        PIDS="$(lsof -ti :5001 2>/dev/null || true)"
        if [ -n "$PIDS" ]; then
            for p in $PIDS; do
                kill "$p" 2>/dev/null || true
            done
            echo "  [OK] Forced stop of process(es) using port 5001: $PIDS"
            STOPPED=1
        fi
    fi
fi

if [ "$STOPPED" -eq 0 ]; then
    echo "  [INFO] No running Review Sentinel Prime process was found."
else
    if [ "$FORCE_RESET" -eq 1 ]; then
        echo "  [OK] Reset complete."
    fi
fi

echo ""
echo "  You can start the app again with start.command."
echo ""
