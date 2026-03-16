#!/bin/bash
set -euo pipefail

echo "Starting Apex Trading Terminal..."
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v ss >/dev/null 2>&1; then
    if ss -ltn | awk '{print $4}' | grep -q ":8000$"; then
        echo "Port 8000 is already in use. Stop the existing backend or choose a different port."
        exit 1
    fi
    if ss -ltn | awk '{print $4}' | grep -q ":5173$"; then
        echo "Port 5173 is already in use. Stop the existing frontend or choose a different port."
        exit 1
    fi
fi

echo "Starting Backend (FastAPI)..."
cd "$SCRIPT_DIR/backend"

if [ ! -d "venv_wsl" ]; then
    echo "  Creating virtual environment (WSL compatible)..."
    python3 -m venv venv_wsl --copies
    source venv_wsl/bin/activate
    pip install -r requirements.txt
else
    source venv_wsl/bin/activate
fi

if [ "${APEX_BACKEND_RELOAD:-0}" = "1" ]; then
    uvicorn main:app --reload --reload-dir . --reload-exclude "venv_wsl/*" --host 0.0.0.0 --port 8000 &
else
    uvicorn main:app --host 0.0.0.0 --port 8000 &
fi
BACKEND_PID=$!
echo "  Backend PID: $BACKEND_PID"

echo "Starting Frontend (Vite + React)..."
cd "$SCRIPT_DIR/frontend"

if [ ! -d "node_modules" ]; then
    echo "  Installing Frontend dependencies..."
    npm install
fi

npx vite --host &
FRONTEND_PID=$!
echo "  Frontend PID: $FRONTEND_PID"

echo ""
echo "Apex Terminal is running."
echo "  Frontend: http://localhost:5173"
echo "  Backend:  http://localhost:8000"
echo "  API Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop both servers."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo 'Apex stopped.'" EXIT

wait
