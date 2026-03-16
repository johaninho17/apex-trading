#!/usr/bin/env python3
"""
train_specialist.py — Apex Stocks ML: Phase 4 PPO Training

Trains a specialist PPO agent for a single stock symbol using the engineered
15-minute Parquet data. Uses the same patterns as the crypto trainer but
adapted for stock-specific environment and data formats.

Strategy parameters baked into training (from Phase 3 VectorBT optimization):
  - NVDA/Tier 1:  RSI=39 entry gate, ATR=2.5x trailing stop
  - QQQ/ETFs:     RSI=39, ATR=2.0x (lower volatility, tighter stop)

Usage:
  cd apex/backend
  source venv_wsl/bin/activate
  python3 services/stocks/ml/train_specialist.py --symbol NVDA
  python3 services/stocks/ml/train_specialist.py --symbol NVDA --resume
  python3 services/stocks/ml/train_specialist.py --symbol QQQ --timesteps 300000
"""

import os
import sys
import time
import argparse
import pandas as pd
from stable_baselines3 import PPO
from stable_baselines3.common.env_util import make_vec_env
from stable_baselines3.common.callbacks import EvalCallback, StopTrainingOnNoModelImprovement
from stable_baselines3.common.vec_env import SubprocVecEnv

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
))))
sys.path.insert(0, _BACKEND_DIR)

from services.stocks.ml.stock_trading_env import StockTradingEnv

DATA_DIR  = os.path.join(_BACKEND_DIR, "data", "stocks")
MODEL_DIR = os.path.join(_BACKEND_DIR, "data", "models", "stocks")
os.makedirs(MODEL_DIR, exist_ok=True)

# ── ATR stop overrides per symbol (from VectorBT Phase 3 optimization) ────────
ATR_STOP_OVERRIDES = {
    "NVDA": 2.5,
    "AMD":  2.5,
    "TSLA": 2.5,
    "QQQ":  2.0,  # Lower volatility — tighter stops
    "SPY":  2.0,
    "META": 2.5,
    "MSFT": 2.0,
    "GOOGL": 2.0,
    "PLTR": 3.0,  # Higher volatility — more room to breathe
    "ARM":  3.0,
    "SMCI": 3.0,
}
DEFAULT_ATR_STOP = 2.5


def load_engineered_data(symbol: str, timeframe: str = "15min_5Y") -> pd.DataFrame:
    """Load the Phase 2 engineered Parquet file for the given symbol."""
    path = os.path.join(DATA_DIR, symbol, f"{timeframe}_engineered.parquet")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Engineered data not found: {path}\n"
            f"Run Phase 2 first: ./scripts/stocks/run_feature_engineering.sh"
        )
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if "symbol" not in df.columns:
        df["symbol"] = symbol
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"  Loaded {len(df):,} bars | {len(df.columns)} features | "
          f"{df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}")
    return df


def train_agent(
    symbol: str = "NVDA",
    timeframe: str = "15min_5Y",
    timesteps: int = 500_000,
    resume: bool = False,
    n_envs: int = 4,
) -> None:
    """
    Train a PPO specialist agent for a single stock symbol.
    """
    print(f"\n{'='*60}")
    print(f" Apex Stocks PPO Specialist — {symbol}")
    print(f"{'='*60}\n")

    atr_stop = ATR_STOP_OVERRIDES.get(symbol, DEFAULT_ATR_STOP)
    print(f"  ATR Stop Multiplier: {atr_stop}x (from VectorBT optimization)")

    # ── 1. Load data ──────────────────────────────────────────────────────────
    print(f"\n📊 Loading {timeframe} engineered data for {symbol}...")
    df = load_engineered_data(symbol, timeframe)

    if len(df) < 500:
        print("❌ Not enough bars to train. Exiting.")
        return

    # ── 2. Train/Eval split (80/20 chronological) ────────────────────────────
    split_idx = int(len(df) * 0.80)
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    eval_df  = df.iloc[split_idx:].reset_index(drop=True)
    print(f"  Train: {len(train_df):,} bars | Eval: {len(eval_df):,} bars")

    # ── 3. Build Gymnasium environments ───────────────────────────────────────
    def make_env():
        return StockTradingEnv(train_df, atr_stop_multiplier=atr_stop)

    def make_eval_env():
        return StockTradingEnv(eval_df, atr_stop_multiplier=atr_stop)

    print(f"\n  Spinning up {n_envs} parallel training environments...")
    train_env = make_vec_env(make_env, n_envs=n_envs, vec_env_cls=SubprocVecEnv)
    eval_env  = make_vec_env(make_eval_env, n_envs=1)

    # ── 4. Callbacks ──────────────────────────────────────────────────────────
    stop_callback = StopTrainingOnNoModelImprovement(
        max_no_improvement_evals=8,
        min_evals=10,
        verbose=1,
    )
    model_save_path = os.path.join(MODEL_DIR, f"ppo_{symbol}_specialist")
    eval_callback = EvalCallback(
        eval_env,
        eval_freq=20_000 // n_envs,
        best_model_save_path=MODEL_DIR,
        callback_after_eval=stop_callback,
        verbose=1,
    )

    # ── 5. PPO Model ──────────────────────────────────────────────────────────
    zip_path = f"{model_save_path}.zip"
    reset_num_timesteps = True

    if resume and os.path.exists(zip_path):
        print(f"\n🧠 Resuming from existing model: {zip_path}")
        try:
            model = PPO.load(
                zip_path,
                env=train_env,
                device="auto",
                custom_objects={"learning_rate": 0.0001},
            )
            reset_num_timesteps = False
            print("  Loaded successfully — continuing training.")
        except (ValueError, Exception) as e:
            print(f"  ⚠️  Incompatible model ({e}) — starting fresh.")
            model = _build_fresh_model(train_env)
    else:
        model = _build_fresh_model(train_env)

    # ── 6. Train ──────────────────────────────────────────────────────────────
    print(f"\n🚀 Training — {timesteps:,} timesteps | {n_envs} envs | device: auto\n")
    t0 = time.time()
    try:
        model.learn(
            total_timesteps=timesteps,
            callback=eval_callback,
            reset_num_timesteps=reset_num_timesteps,
        )
    except KeyboardInterrupt:
        print("\n⚠️  Training interrupted by user.")

    elapsed = time.time() - t0
    print(f"\n✅ Training finished in {elapsed/60:.1f} minutes.")

    # ── 7. Save final model ───────────────────────────────────────────────────
    model.save(model_save_path)
    print(f"💾 Model saved: {zip_path}")

    train_env.close()
    eval_env.close()


def _build_fresh_model(env) -> PPO:
    """Build a fresh PPO model with tuned hyperparameters for stock trading."""
    print("\n🧠 Building fresh PPO neural network...")
    return PPO(
        "MlpPolicy",
        env,
        verbose=1,
        learning_rate=3e-4,
        n_steps=2048,
        batch_size=128,
        n_epochs=10,
        gamma=0.99,           # discount factor
        gae_lambda=0.95,      # GAE smoothing
        clip_range=0.2,       # PPO clip
        ent_coef=0.005,       # Explore (lower than crypto — stocks need more precision)
        vf_coef=0.5,
        max_grad_norm=0.5,
        device="auto",        # Uses CUDA if available, else CPU
        policy_kwargs={
            "net_arch": [256, 256],  # 2-layer 256-unit MLP
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Apex Stocks PPO Specialist Trainer")
    parser.add_argument("--symbol",     type=str,   default="NVDA",       help="Stock symbol to train (e.g., NVDA, QQQ)")
    parser.add_argument("--timeframe",  type=str,   default="15min_5Y",   help="Engineered data timeframe file prefix")
    parser.add_argument("--timesteps",  type=int,   default=500_000,      help="Total PPO training timesteps (default 500k)")
    parser.add_argument("--resume",     action="store_true",               help="Resume from existing saved model")
    parser.add_argument("--n-envs",     type=int,   default=4,            help="Number of parallel training environments (default 4)")
    args = parser.parse_args()

    train_agent(
        symbol=args.symbol,
        timeframe=args.timeframe,
        timesteps=args.timesteps,
        resume=args.resume,
        n_envs=args.n_envs,
    )


if __name__ == "__main__":
    main()
