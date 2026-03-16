#!/bin/bash
# run_train_universal.sh — Apex Stocks: Universal Fallback Model Trainer
# Trains a single PPO model across ALL 14 watchlist symbols blended together.
# The resulting model is used as a fallback for any stock not in the watchlist,
# and as a starting point for fine-tuning specialist models faster.
#
# Usage:
#   ./scripts/stocks/run_train_universal.sh             # Fresh 1M-step run
#   ./scripts/stocks/run_train_universal.sh --resume    # Continue training
#   ./scripts/stocks/run_train_universal.sh --n-envs 8  # More parallel envs
#
# Model saved to: data/models/stocks/ppo_universal.zip
#
# Fine-tuning for a new symbol (e.g., AAPL not in watchlist):
#   cp data/models/stocks/ppo_universal.zip data/models/stocks/ppo_AAPL_specialist.zip
#   ./scripts/stocks/run_train.sh AAPL --resume

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

echo "========================================================"
echo " Apex Stocks Universal Fallback Trainer (All Symbols)"
echo "========================================================"

python3 services/stocks/ml/train_universal.py "$@"
