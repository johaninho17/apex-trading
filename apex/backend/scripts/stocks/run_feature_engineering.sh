#!/bin/bash
# run_feature_engineering.sh — Apex Stocks Phase 2
# Activates venv and runs feature_engineering.py
# Usage: ./scripts/stocks/run_feature_engineering.sh [all|daily_10Y|1hour_7Y|15min_5Y|1min_2Y]

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$BACKEND_DIR"

VENV="$BACKEND_DIR/venv_wsl"
if [ -f "$VENV/bin/activate" ]; then
    source "$VENV/bin/activate"
else
    echo "ERROR: venv_wsl not found at $VENV"
    exit 1
fi

TIMEFRAME="${1:-all}"
echo "=============================================="
echo " Apex Stock Feature Engineering — tf: $TIMEFRAME"
echo "=============================================="

python3 services/stocks/ml/feature_engineering.py --timeframe "$TIMEFRAME"
