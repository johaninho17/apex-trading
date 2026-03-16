"""
execute_daily_ml.py — Apex DRL Continuous Learning Pipeline
============================================================
Spawned as a detached subprocess by ml_cron.py.

Phase 1: Load closed live_experience rows from SQLite, build engineered training CSV.
Phase 2: Fine-tune the PPO specialist model on the new data (resume=True).

Replaces the old 3-phase pipeline that required the deleted sync_training_data.py.
"""
import sys
import os
import json
import logging

# ── Path setup ────────────────────────────────────────────────────────────────
# Run from: /apex/backend/  via venv_wsl python3
_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("execute_daily_ml")


def run_pipeline(timesteps: int = 25_000, force: bool = False):
    print("\n=======================================================")
    print("📈 APEX DRL CONTINUOUS LEARNING PIPELINE INITIATED")
    print(f"   timesteps={timesteps}  force={force}")
    print("=======================================================\n")

    # ── Phase 1: Live Experience → Training CSV ────────────────────────────
    print("\n[PHASE 1] Building training data from live trade experience...")
    try:
        from services.crypto.ml.train_from_live_experience import run_live_retrain
        result = run_live_retrain(force=force)
        print(f"  Status : {result.get('status')}")
        print(f"  Reason : {result.get('reason', 'N/A')}")
        print(f"  Symbols: {list(result.get('symbols_retrained', {}).keys())}")

        if result.get("status") == "skipped":
            print("\n⏸  Not enough closed trades yet. Pipeline halted — no error.")
            print(f"  Need {50} closed trades, have {result.get('rows', 0)}.")
            return

    except Exception as e:
        print(f"❌ Phase 1 failed: {e}")
        import traceback
        traceback.print_exc()
        return

    # ── Phase 2: Fine-tune PPO specialist ─────────────────────────────────
    try:
        from services.crypto.ml.train_specialist import train_agent

        symbols_retrained = list(result.get("symbols_retrained", {}).keys())
        if not symbols_retrained:
            print("  No symbols had enough data — skipping PPO fine-tune.")
        else:
            for sym in symbols_retrained:
                ticker = sym.replace("/", "_")
                print(f"  → Fine-tuning {sym} ({ticker})...")
                train_agent(ticker, timesteps=timesteps, resume=True)

    except Exception as e:
        print(f"❌ Phase 2 failed: {e}")
        import traceback
        traceback.print_exc()
        return

    print("\n🎉 DRL Continuous Learning Pipeline Complete.")


if __name__ == "__main__":
    _force = "--force" in sys.argv
    _timesteps = 25_000
    for arg in sys.argv[1:]:
        if arg.startswith("--timesteps="):
            try:
                _timesteps = int(arg.split("=", 1)[1])
            except ValueError:
                pass

    run_pipeline(timesteps=_timesteps, force=_force)
