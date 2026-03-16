#!/bin/bash
# run_fetch.sh — Apex Stocks Data Pipeline launcher
# Handles venv activation automatically.
# Usage: ./scripts/stocks/run_fetch.sh [daily|1h|15m|1m|macro|all]

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$BACKEND_DIR"

# Activate WSL venv
VENV="$BACKEND_DIR/venv_wsl"
if [ -f "$VENV/bin/activate" ]; then
    source "$VENV/bin/activate"
else
    echo "ERROR: venv_wsl not found at $VENV"
    exit 1
fi

TIER="${1:-all}"
echo "=============================================="
echo " Apex Stock Data Fetcher — tier: $TIER"
echo "=============================================="

python3 scripts/stocks/fetch_historical_data.py --tier "$TIER"
