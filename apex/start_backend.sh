#!/bin/bash
set -euo pipefail

echo "Starting Apex Backend (FastAPI)..."
cd backend

if [ ! -d "venv_wsl" ]; then
    echo "  Creating virtual environment (WSL compatible)..."
    python3 -m venv venv_wsl --copies
    source venv_wsl/bin/activate
    pip install -r requirements.txt
else
    source venv_wsl/bin/activate
fi

if command -v ss >/dev/null 2>&1 && ss -ltn | awk '{print $4}' | grep -q ":8000$"; then
    echo "Port 8000 is already in use. Stop the existing backend or choose a different port."
    exit 1
fi

if [ "${APEX_BACKEND_RELOAD:-0}" = "1" ]; then
    uvicorn main:app --reload --reload-dir . --reload-exclude "venv_wsl/*" --host 0.0.0.0 --port 8000
else
    uvicorn main:app --host 0.0.0.0 --port 8000
fi
