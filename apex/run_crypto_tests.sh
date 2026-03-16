#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# APEX Crypto Bot — Full Test Runner
# Run from the project root: bash run_crypto_tests.sh
# ─────────────────────────────────────────────────────────────────────────────

set -e
cd "$(dirname "$0")"

echo "======================================================="
echo "  APEX Crypto Bot — Full End-to-End Test Suite"
echo "======================================================="
echo

# Activate the backend virtualenv
source backend/venv_wsl/bin/activate

# Install pytest if not available
if ! python -m pytest --version &>/dev/null; then
    echo "Installing pytest..."
    pip install pytest --quiet
fi

# Run the full test suite from the backend directory
cd backend
python -m pytest tests/test_crypto_bot_full.py -v --tb=short "$@"
