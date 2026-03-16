#!/bin/bash
# run_backtest.sh — Apex Stocks Phase 3
# Activates venv and runs the VectorBT optimizer
# Usage: ./scripts/stocks/run_backtest.sh <symbol> <timeframe> [--optimize|--plot]

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

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <symbol> <timeframe> [--optimize|--plot]"
    echo "Example: $0 NVDA 15min_5Y --optimize"
    exit 1
fi

SYMBOL="$1"
TIMEFRAME="$2"
shift 2

echo "================================================="
echo " Apex Stock Backtester — $SYMBOL / $TIMEFRAME"
echo "================================================="

export NUMBA_DISABLE_JIT=1

python3 scripts/stocks/backtest_strategy.py --symbol "$SYMBOL" --tf "$TIMEFRAME" "$@"
