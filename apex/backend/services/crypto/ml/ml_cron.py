"""
ml_cron.py — ML Scheduling Daemon
====================================
Three-tier DRL training schedule + 14-day auto backtest trigger.

TIER 1 — Trade trigger:
  Fire immediately when ≥ 50 new closed trades since last run AND ≥ 3h gap.
  Runs a FAST 25k-step fine-tune (≈5 min).

TIER 2 — Weekly fine-tune:
  Every 7 days, run a 25k-step fine-tune regardless of trade count.
  Keeps the model from going stale even during slow trading periods.

TIER 3 — Monthly deep retrain:
  Every 30 days (or ≥ 200 cumulative closed trades since last deep run),
  run a 100k-step full retrain from the complete live_experience history.
  Takes 20-30 min, always detached subprocess.

AUTO BACKTEST:
  Every 14 days, run parallel 14-day backtests on 7 coins
  (BTC/USD + ETH/USD always, plus top 5 most-traded in the window).
"""
import asyncio
import os
import subprocess
import logging
import time

logger = logging.getLogger(__name__)

# ── File paths ────────────────────────────────────────────────────────────────
_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data"))
LAST_FINETUNE_FILE  = os.path.join(_DATA_DIR, "last_ml_finetune.txt")
LAST_DEEPTRAIN_FILE = os.path.join(_DATA_DIR, "last_ml_sync.txt")       # existing file — don't rename
LAST_BACKTEST_FILE  = os.path.join(_DATA_DIR, "last_auto_backtest.txt")
VENV_PYTHON  = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "venv_wsl", "bin", "python3"))
EXECUTE_SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts", "execute_daily_ml.py"))
LOG_FILE = os.path.join(_DATA_DIR, "ml_continuous_learning.log")

# ── Thresholds ────────────────────────────────────────────────────────────────
TRADE_TRIGGER_THRESHOLD = 50     # new closed trades since last run → immediate fine-tune
MIN_HOURS_BETWEEN_RUNS  = 3.0   # minimum gap to prevent thrashing
WEEKLY_HOURS            = 7 * 24        # 168h  — light fine-tune
MONTHLY_HOURS           = 30 * 24       # 720h  — deep retrain
MONTHLY_TRADE_THRESHOLD = 200           # also deep-retrain if 200 cumulative trades
BACKTEST_HOURS          = 14 * 24       # 336h  — auto backtest


# ── Startup pipeline validation ───────────────────────────────────────────────
def _validate_pipeline() -> bool:
    """
    Called once at server start. Attempts to import the pipeline entry point.
    Logs CRITICAL if broken — gives immediate visibility instead of silent failures.
    """
    try:
        from services.crypto.ml.train_from_live_experience import run_live_retrain  # noqa: F401
        logger.info("✅ ML pipeline validated: train_from_live_experience importable")
        return True
    except ImportError as e:
        logger.critical(
            f"🚨 ML PIPELINE BROKEN — DRL retrains will NOT work until fixed: {e}\n"
            f"   Fix: check services/crypto/ml/train_from_live_experience.py"
        )
        return False


# ── File-based timestamp helpers ──────────────────────────────────────────────
def _read_ts(path: str) -> float:
    if os.path.exists(path):
        try:
            with open(path) as f:
                return float(f.read().strip())
        except Exception:
            pass
    return 0.0


def _write_ts(path: str, ts: float) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(str(ts))


# ── Trade counting helper ─────────────────────────────────────────────────────
def _count_closed_trades_since(since_ms: float) -> int:
    """Count closed live_experience trades since a given epoch-ms timestamp."""
    try:
        from services.crypto import store as crypto_store
        rows = crypto_store.get_live_experience_sync(limit=1000, closed_only=True)
        return sum(
            1 for r in rows
            if float(r.get("closed_at_ms") or r.get("exit_ts") or r.get("ts") or 0) >= since_ms
        )
    except Exception as e:
        logger.debug(f"Trade count check failed: {e}")
        return 0


def _count_all_closed_trades() -> int:
    """Total closed trades ever."""
    try:
        from services.crypto import store as crypto_store
        rows = crypto_store.get_live_experience_sync(limit=5000, closed_only=True)
        return len(rows)
    except Exception:
        return 0


# ── Training launcher ─────────────────────────────────────────────────────────
def _launch_training(reason: str, timestamp_file: str, now: float, timesteps: int) -> None:
    """
    Write the 'last run' timestamp and spawn the training subprocess detached.
    timesteps is passed as a CLI arg to execute_daily_ml.py.
    """
    _write_ts(timestamp_file, now)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "a") as logfile:
        logfile.write(
            f"\n\n--- ML DAEMON TRIGGERED ({reason}) AT {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"[{timesteps:,} steps] ---\n"
        )
        subprocess.Popen(
            [VENV_PYTHON, EXECUTE_SCRIPT, f"--timesteps={timesteps}"],
            stdout=logfile,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    logger.info(f"⚡ ML retrain launched ({reason}, {timesteps:,} steps). See ml_continuous_learning.log")


# ── Main cron loop ────────────────────────────────────────────────────────────
async def ml_cron_loop():
    """
    Background daemon. Wakes every 30 minutes and checks all triggers.

    TRAINING TRIGGERS (first matching wins per cycle):
      1. Trade trigger  — ≥50 new trades + ≥3h gap → fast fine-tune (25k steps)
      2. Weekly         — ≥7 days since last fine-tune → light fine-tune (25k steps)
      3. Monthly        — ≥30 days OR ≥200 total trades → deep retrain (100k steps)

    BACKTEST TRIGGER (independent):
      4. Every 14 days  → auto_backtest.run_auto_backtest() (async, not subprocess)
    """
    logger.info("🤖 Starting ML Cron Daemon (3-tier DRL + 14-day auto backtest)...")

    # Validate pipeline once on startup
    pipeline_ok = _validate_pipeline()
    if not pipeline_ok:
        logger.warning("ML cron will continue polling but training is disabled until pipeline is fixed.")

    # Initial wait — let FastAPI + bot fully start first
    await asyncio.sleep(90)

    while True:
        now = time.time()

        # ── Read stored timestamps ─────────────────────────────────────────
        last_finetune_ts   = _read_ts(LAST_FINETUNE_FILE)
        last_deeptrain_ts  = _read_ts(LAST_DEEPTRAIN_FILE)
        last_backtest_ts   = _read_ts(LAST_BACKTEST_FILE)

        hours_since_finetune  = (now - last_finetune_ts)  / 3600.0
        hours_since_deeptrain = (now - last_deeptrain_ts) / 3600.0
        hours_since_backtest  = (now - last_backtest_ts)  / 3600.0

        # ── Training triggers (evaluated in priority order) ────────────────
        if pipeline_ok:
            trained_this_cycle = False

            # TIER 3: Monthly deep retrain (highest priority if overdue)
            total_trades = _count_all_closed_trades()
            if hours_since_deeptrain >= MONTHLY_HOURS or total_trades >= MONTHLY_TRADE_THRESHOLD:
                reason = (
                    f"monthly-deep ({hours_since_deeptrain:.0f}h elapsed)"
                    if hours_since_deeptrain >= MONTHLY_HOURS
                    else f"trade-volume-deep ({total_trades} total closed trades)"
                )
                logger.info(f"🧠 TIER 3 trigger: {reason}")
                try:
                    _launch_training(reason, LAST_DEEPTRAIN_FILE, now, timesteps=100_000)
                    # Also update fine-tune timestamp so we don't immediately fire TIER 1/2
                    _write_ts(LAST_FINETUNE_FILE, now)
                    trained_this_cycle = True
                except Exception as e:
                    logger.error(f"❌ Tier 3 launch failed: {e}")

            # TIER 1: Trade trigger (fast fine-tune)
            if not trained_this_cycle and hours_since_finetune >= MIN_HOURS_BETWEEN_RUNS:
                try:
                    new_trades = _count_closed_trades_since(last_finetune_ts * 1000)
                    if new_trades >= TRADE_TRIGGER_THRESHOLD:
                        reason = f"trade-trigger ({new_trades} new closed trades)"
                        logger.info(f"⚡ TIER 1 trigger: {reason}")
                        _launch_training(reason, LAST_FINETUNE_FILE, now, timesteps=25_000)
                        trained_this_cycle = True
                except Exception as e:
                    logger.debug(f"Trade trigger check failed: {e}")

            # TIER 2: Weekly fine-tune
            if not trained_this_cycle and hours_since_finetune >= WEEKLY_HOURS:
                reason = f"weekly-finetune ({hours_since_finetune:.1f}h since last run)"
                logger.info(f"📅 TIER 2 trigger: {reason}")
                try:
                    _launch_training(reason, LAST_FINETUNE_FILE, now, timesteps=25_000)
                    trained_this_cycle = True
                except Exception as e:
                    logger.error(f"❌ Tier 2 launch failed: {e}")

        # ── Auto backtest trigger (independent of training) ────────────────
        if hours_since_backtest >= BACKTEST_HOURS:
            logger.info(
                f"📊 Auto backtest trigger (last ran {hours_since_backtest:.1f}h ago)"
            )
            try:
                from services.crypto.auto_backtest import run_auto_backtest
                _write_ts(LAST_BACKTEST_FILE, now)  # mark before running (prevent double-fire)
                asyncio.ensure_future(run_auto_backtest())
                logger.info("📊 Auto backtest task scheduled.")
            except Exception as e:
                logger.error(f"❌ Auto backtest failed to launch: {e}")

        # Check every 30 minutes
        await asyncio.sleep(1800)
