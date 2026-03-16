#!/bin/bash
# run_train.sh — Apex Stocks Phase 4: PPO Model Trainer
# Activates venv and trains a specialist PPO agent for a specific symbol.
# Usage: ./scripts/stocks/run_train.sh <symbol> [--resume] [--timesteps N]
#
# Examples:
#   ./scripts/stocks/run_train.sh NVDA                   # Fresh 500k steps
#   ./scripts/stocks/run_train.sh NVDA --resume          # Continue training
#   ./scripts/stocks/run_train.sh QQQ --timesteps 300000 # Shorter run
#   ./scripts/stocks/run_train.sh TSLA --n-envs 8        # More parallel envs

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

if [ "$#" -lt 1 ]; then
    echo "Usage: $0 <symbol> [--resume] [--timesteps N] [--n-envs N]"
    echo "Example: $0 NVDA --resume"
    exit 1
fi

SYMBOL="$1"
shift

echo "======================================================="
echo " Apex Stocks PPO Trainer — $SYMBOL"
echo "======================================================="

python3 services/stocks/ml/train_specialist.py --symbol "$SYMBOL" "$@"
