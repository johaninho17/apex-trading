"""
finetune_all_agents.py — Fast PPO fine-tune for all existing specialist models.

Differences from train_all_agents.py:
  - ONLY runs on symbols that already have a trained .zip model
  - 30,000 timesteps (vs 100k full train) — runs in ~2-5 min total
  - Always uses resume=True to continue from existing weights
  - Skips symbols with no existing model (they need a full first-time train)
  - Logs start/progress/finish events to the Action Feed so the UI shows status

Run directly:
    python3 finetune_all_agents.py
"""

import os
import sys
import glob
import json
import time

# Allow imports from backend root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from services.crypto.ml.train_specialist import train_agent
from services.crypto import store

DATA_DIR  = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "ai_training"))
MODEL_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "models"))

# ── Tuneable: raise for more accuracy, lower for faster runs ─────────────────
FINETUNE_TIMESTEPS = 30_000


def _log(msg: str, payload: dict = None):
    """Write an event to the Action Feed (visible in the UI)."""
    try:
        store._record_action_sync(
            action_type="finetune",
            symbol="AI TUNER",
            side="info",
            status="info",
            reason=msg,
            payload=payload or {},
        )
    except Exception as exc:
        print(f"[finetune] store log failed (non-fatal): {exc}")


def finetune_all_agents():
    print("=" * 52)
    print("⚡ APEX FAST FINE-TUNE SUPERVISOR")
    print("=" * 52)

    # Only fine-tune models that already exist (have a .zip)
    model_files = glob.glob(os.path.join(MODEL_DIR, "ppo_*_specialist.zip"))

    if not model_files:
        msg = "⚠️ No existing model files found. Run 'Train AI Matrix' first to build base models."
        print(msg)
        _log(msg)
        return

    total = len(model_files)
    _log(
        f"⚡ Fast Fine-Tune started — {total} model(s) queued ({FINETUNE_TIMESTEPS:,} steps each, ~2-5 min)",
        {"total_models": total, "timesteps": FINETUNE_TIMESTEPS},
    )

    print(f"🎯 Found {total} trained model(s) to fine-tune.")
    print(f"📊 Using {FINETUNE_TIMESTEPS:,} timesteps per model.\n")

    success, failed = 0, 0
    start_all = time.time()

    for idx, model_path in enumerate(model_files, start=1):
        basename = os.path.basename(model_path)              # e.g. ppo_BTC_USD_specialist.zip
        ticker   = basename.replace("ppo_", "").replace("_specialist.zip", "")  # e.g. BTC_USD
        symbol   = ticker.replace("_", "/")                 # e.g. BTC/USD

        # Verify there is engineered training data for this ticker
        csv_path = os.path.join(DATA_DIR, f"{ticker}_15Min_engineered.csv")
        if not os.path.exists(csv_path):
            skip_msg = f"⚠️ Skipping {symbol} — no engineered CSV. Run 'Train AI Matrix' to generate feature data first."
            print(skip_msg)
            _log(skip_msg, {"symbol": symbol})
            continue

        progress_msg = f"[{idx}/{total}] Fine-tuning {symbol}…"
        print("\n" + "─" * 52)
        print(f"⚡ {progress_msg}")
        _log(progress_msg, {"symbol": symbol, "step": idx, "total": total})

        t0 = time.time()
        try:
            train_agent(ticker, timesteps=FINETUNE_TIMESTEPS, resume=True)
            elapsed = time.time() - t0
            done_msg = f"✅ {symbol} fine-tuned in {elapsed:.0f}s"
            print(done_msg)
            _log(done_msg, {"symbol": symbol, "elapsed_sec": round(elapsed, 1)})
            success += 1
        except Exception as exc:
            err_msg = f"❌ Fine-tune failed for {symbol}: {exc}"
            print(err_msg)
            _log(err_msg, {"symbol": symbol, "error": str(exc)})
            failed += 1

    total_elapsed = time.time() - start_all

    # ── Update the shared training timestamp so the UI banner resets ──────────
    state_file = os.path.abspath(os.path.join(DATA_DIR, "..", "runtime_state.json"))
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
    except Exception:
        state = {}

    state["last_ai_training_ms"] = int(time.time() * 1000)
    try:
        with open(state_file, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as exc:
        print(f"⚠️  Could not save training timestamp: {exc}")

    summary = (
        f"⚡ Fine-Tune complete in {total_elapsed/60:.1f} min — "
        f"{success} updated, {failed} failed. ML weights hot-reloaded automatically."
    )
    print("\n" + "=" * 52)
    print(summary)
    print("=" * 52)
    _log(summary, {"success": success, "failed": failed, "total_min": round(total_elapsed / 60, 1)})


if __name__ == "__main__":
    finetune_all_agents()
