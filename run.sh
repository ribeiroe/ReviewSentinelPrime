#!/bin/bash

# Navigate to the project directory
cd "$(dirname "$0")"

# Activate virtual environment (try .venv first, then venv)
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "No virtual environment found. Running with system Python."
fi

# Run the Flask app
python3 app.py
