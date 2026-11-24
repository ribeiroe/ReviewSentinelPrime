#!/bin/bash
# Development launcher: same setup as the release start.command, so every
# machine runs with the exact package versions in requirements.txt.

cd "$(dirname "$0")" || exit 1

if [ -d "venv" ] && [ ! -d ".venv" ]; then
    source venv/bin/activate
else
    if [ ! -x ".venv/bin/python" ]; then
        echo "[SETUP] Creating virtual environment .venv (first run only)..."
        python3 -m venv .venv || {
            echo "[ERROR] Could not create the virtual environment. Is Python 3.12+ installed?"
            exit 1
        }
    fi
    source .venv/bin/activate
fi

echo "[SETUP] Installing / updating dependencies from requirements.txt..."
python -m pip install -r requirements.txt --quiet --disable-pip-version-check || {
    echo "[ERROR] Dependency installation failed. Check your internet connection and try again."
    exit 1
}

python app.py
