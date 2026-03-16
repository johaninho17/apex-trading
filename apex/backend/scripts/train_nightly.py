#!/usr/bin/env python3
"""
train_nightly.py — Phase 3: Nightly DRL Retraining (The Math Brain)

Reads the bot's ReplayBuffer from SQLite (every trade's market state + outcome),
trains the PPO neural network on real execution data, and hot-swaps the model
file so the bot wakes up with improved pattern recognition the next morning.

Usage:
  python3 scripts/train_nightly.py

Recommended: Run via cron at 2:00 AM every night:
  0 2 * * * cd /Users/johan/Projects/trading/apex/backend && source venv_wsl/bin/activate && python3 scripts/train_nightly.py >> logs/train_nightly.log 2>&1
"""

import os
import sys
import time
import logging
import shutil
from datetime import datetime

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [NIGHTLY_TRAIN] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def run():
    logger.info("=" * 60)
    logger.info("APEX NIGHTLY DRL RETRAINING — %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    # ── 1. Load ReplayBuffer data from SQLite ─────────────────────────────────
    from services.crypto.store import _connect, _init_db_sync, _LOCK
    _init_db_sync()

    logger.info("[1/4] Loading ReplayBuffer from SQLite...")
    since_ms = int((time.time() - 86400) * 1000)  # Last 24 hours

    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT ts, symbol, side, qty, price, status, reason, payload_json
                FROM actions
                WHERE ts >= ? AND status = 'success'
                  AND action_type IN (
                      'synthetic_exit', 'synthetic_exit_tp1', 'synthetic_exit_tp1_full',
                      'synthetic_exit_tp2', 'synthetic_exit_trailing', 'synthetic_exit_rsi'
                  )
                ORDER BY ts ASC
                """,
                (since_ms,),
            ).fetchall()
        finally:
            con.close()

    logger.info("  Found %d completed exit events for retraining.", len(rows))
    if len(rows) < 5:
        logger.warning("  Too few trades to retrain meaningfully (need >= 5). Skipping.")
        return

    # ── 2. Build observation/reward pairs ─────────────────────────────────────
    import json
    import numpy as np

    logger.info("[2/4] Building observation/reward pairs...")
    observations, rewards = [], []

    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
            pnl_pct  = float(payload.get("pnl_pct", 0.0))
            rsi_15m  = float(payload.get("rsi_15m", 50.0))
            rsi_1m   = float(payload.get("rsi_1m", 50.0))
            vwap_dev = float(payload.get("vwap_dev", 0.0))
            bb_pct   = float(payload.get("bb_pct", 0.5))
            macro    = float(payload.get("macro_score", 1.0))

            obs = np.array([rsi_15m / 100.0, rsi_1m / 100.0, vwap_dev, bb_pct, macro], dtype=np.float32)
            reward = np.clip(pnl_pct / 5.0, -1.0, 1.0)  # Normalise PnL to [-1, 1]
            observations.append(obs)
            rewards.append(reward)
        except Exception:
            continue

    if not observations:
        logger.warning("  No valid observations built. Skipping retraining.")
        return

    logger.info("  Built %d training samples. Mean reward: %.4f", len(observations), float(np.mean(rewards)))

    # ── 3. Load & retrain PPO model ───────────────────────────────────────────
    logger.info("[3/4] Loading PPO model and retraining...")
    model_path = os.path.join(_BACKEND_DIR, "services", "crypto", "ml", "ppo_model.zip")

    try:
        from stable_baselines3 import PPO
        import gymnasium as gym

        # Simple wrapper env so SB3 can call model.learn()
        class ReplayEnv(gym.Env):
            def __init__(self, obs_list, rew_list):
                super().__init__()
                self._obs = obs_list
                self._rews = rew_list
                self._idx = 0
                self.observation_space = gym.spaces.Box(low=-10.0, high=10.0, shape=(5,), dtype=np.float32)
                self.action_space = gym.spaces.Discrete(3)  # HOLD, BUY, SELL

            def reset(self, **kwargs):
                self._idx = 0
                return self._obs[0], {}

            def step(self, action):
                reward = self._rews[self._idx]
                self._idx = (self._idx + 1) % len(self._obs)
                obs = self._obs[self._idx]
                done = (self._idx == 0)
                return obs, reward, done, False, {}

        env = ReplayEnv(observations, rewards)

        if os.path.exists(model_path):
            model = PPO.load(model_path, env=env)
            logger.info("  Loaded existing model from %s", model_path)
        else:
            model = PPO("MlpPolicy", env, verbose=0)
            logger.info("  No existing model found — training from scratch.")

        # Backup current model before overwriting
        backup_path = model_path.replace(".zip", f"_backup_{int(time.time())}.zip")
        if os.path.exists(model_path):
            shutil.copy2(model_path, backup_path)
            logger.info("  Backed up existing model to %s", os.path.basename(backup_path))

        model.learn(total_timesteps=max(500, len(observations) * 10), reset_num_timesteps=False)
        model.save(model_path)
        logger.info("  ✅ Model saved to %s", model_path)

        # Clean up old backups (keep only last 3)
        backup_dir = os.path.dirname(model_path)
        backups = sorted([f for f in os.listdir(backup_dir) if "_backup_" in f and f.endswith(".zip")])
        for old in backups[:-3]:
            os.remove(os.path.join(backup_dir, old))
            logger.info("  Removed old backup: %s", old)

    except ImportError:
        logger.error("  stable_baselines3 not installed. Run: pip install stable-baselines3")
        return
    except Exception as e:
        logger.error("  Retraining failed: %s", e)
        return

    # ── 4. Done ───────────────────────────────────────────────────────────────
    logger.info("[4/4] Nightly retraining complete.")
    logger.info("=" * 60)


if __name__ == "__main__":
    run()
