#!/usr/bin/env python3
"""
train_universal.py — Apex Stocks ML: Universal Fallback Model

Trains a single PPO "generalist" agent on ALL watchlist symbols combined
into one shuffled dataset. The resulting model learns the universal language
of technical indicators (RSI, ATR, MACD, VWAP deviation) across many
different volatility personalities simultaneously.

Use this model as:
  - The default fallback for ANY stock not in the watchlist
  - The starting point to fine-tune into a specialist (faster convergence)
  - A "sanity check" model during early pipeline testing

Training Data:
  All 14 watchlist symbols, 15-min engineered Parquet, blended and shuffled.
  Symbols not found on disk are skipped gracefully.

Fine-tuning from universal model:
  python3 services/stocks/ml/train_specialist.py --symbol AAPL --resume
  (Copy universal model to ppo_AAPL_specialist.zip first, then resume)
"""

import os
import sys
import time
import argparse
import random
import numpy as np
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

# Full watchlist — symbols are loaded in this order, skipped if not on disk
WATCHLIST_TIER1 = ["NVDA", "AMD", "TSLA", "QQQ", "SPY"]
WATCHLIST_TIER2 = ["META", "MSFT", "GOOGL", "PLTR", "ARM", "SMCI"]
WATCHLIST_TIER3 = ["SOXL", "TQQQ", "SOXS"]

ALL_SYMBOLS = WATCHLIST_TIER1 + WATCHLIST_TIER2 + WATCHLIST_TIER3

# Per-symbol ATR stop (from Phase 3 VectorBT grid search)
ATR_STOPS = {
    "NVDA": 2.5, "AMD": 2.5, "TSLA": 2.5,
    "QQQ": 2.0,  "SPY": 2.0,
    "META": 2.5, "MSFT": 2.0, "GOOGL": 2.0,
    "PLTR": 3.0, "ARM": 3.0, "SMCI": 3.0,
    "SOXL": 3.0, "TQQQ": 3.0, "SOXS": 3.0,
}
DEFAULT_ATR_STOP = 2.5

# Universal model uses a representative ATR (median across all symbols)
UNIVERSAL_ATR_STOP = 2.5


def load_symbol(symbol: str, timeframe: str) -> pd.DataFrame | None:
    """Load engineered data for one symbol. Returns None if not found."""
    path = os.path.join(DATA_DIR, symbol, f"{timeframe}_engineered.parquet")
    if not os.path.exists(path):
        print(f"  [SKIP] {symbol}/{timeframe} — not found on disk")
        return None
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    if "symbol" not in df.columns:
        df["symbol"] = symbol
    return df.sort_values("timestamp").reset_index(drop=True)


def blend_symbols(
    symbols: list[str],
    timeframe: str,
    chunk_size: int = 5000,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Load all available symbols and blend them into a single shuffled DataFrame.

    Strategy: slice each symbol's data into `chunk_size`-bar windows, then
    shuffle those windows together. This prevents the model from overfitting
    to any one symbol's market regime, while preserving short-term temporal
    context within each chunk (so MACD / EMA signals remain valid).
    """
    random.seed(seed)
    np.random.seed(seed)

    chunks = []
    loaded = []
    for sym in symbols:
        df = load_symbol(sym, timeframe)
        if df is None or len(df) < 500:
            continue

        # Slice into chunk_size windows with 50% overlap
        step = chunk_size // 2
        for start in range(0, len(df) - chunk_size, step):
            chunk = df.iloc[start : start + chunk_size].copy()
            chunk["symbol"] = sym  # Preserve symbol for debugging
            chunks.append(chunk)
        loaded.append(sym)
        print(f"  ✅ {sym}: {len(df):,} bars → {(len(df) - chunk_size) // step + 1} chunks")

    if not chunks:
        raise RuntimeError("No valid data found. Run Phase 1 + 2 first.")

    print(f"\n  Shuffling {len(chunks)} chunks from {len(loaded)} symbols...")
    random.shuffle(chunks)
    blended = pd.concat(chunks, ignore_index=True)
    print(f"  Combined dataset: {len(blended):,} rows")
    return blended


def train_universal(
    timeframe: str = "15min_5Y",
    timesteps: int = 1_000_000,
    symbols: list[str] | None = None,
    resume: bool = False,
    n_envs: int = 4,
) -> None:
    """Train a universal fallback model on all available symbols."""
    symbols = symbols or ALL_SYMBOLS

    print(f"\n{'='*60}")
    print(f" Apex Stocks Universal PPO — All Symbols Combined")
    print(f"{'='*60}\n")
    print(f"  Timeframe:     {timeframe}")
    print(f"  Timesteps:     {timesteps:,}")
    print(f"  Environments:  {n_envs}")
    print(f"  ATR Stop:      {UNIVERSAL_ATR_STOP}x (universal median)\n")

    # ── 1. Load and blend all symbols ────────────────────────────────────────
    print("📦 Loading and blending all watchlist symbols...")
    blended = blend_symbols(symbols, timeframe)

    # ── 2. Train/Eval split (80/20 — chunks are already shuffled) ────────────
    split_idx = int(len(blended) * 0.80)
    train_df = blended.iloc[:split_idx].reset_index(drop=True)
    eval_df  = blended.iloc[split_idx:].reset_index(drop=True)
    print(f"\n  Train: {len(train_df):,} rows | Eval: {len(eval_df):,} rows")

    # ── 3. Build environments ─────────────────────────────────────────────────
    def make_env():
        return StockTradingEnv(train_df, atr_stop_multiplier=UNIVERSAL_ATR_STOP)

    def make_eval_env():
        return StockTradingEnv(eval_df, atr_stop_multiplier=UNIVERSAL_ATR_STOP)

    print(f"\n  Spinning up {n_envs} parallel training environments...")
    train_env = make_vec_env(make_env, n_envs=n_envs, vec_env_cls=SubprocVecEnv)
    eval_env  = make_vec_env(make_eval_env, n_envs=1)

    # ── 4. Callbacks ──────────────────────────────────────────────────────────
    model_save_path = os.path.join(MODEL_DIR, "ppo_universal")
    stop_callback = StopTrainingOnNoModelImprovement(max_no_improvement_evals=10, min_evals=15, verbose=1)
    eval_callback = EvalCallback(
        eval_env,
        eval_freq=25_000 // n_envs,
        best_model_save_path=MODEL_DIR,
        callback_after_eval=stop_callback,
        verbose=1,
    )

    # ── 5. PPO Model ──────────────────────────────────────────────────────────
    zip_path = f"{model_save_path}.zip"
    reset_num_timesteps = True

    if resume and os.path.exists(zip_path):
        print(f"\n🧠 Resuming universal model from: {zip_path}")
        try:
            model = PPO.load(zip_path, env=train_env, device="auto",
                             custom_objects={"learning_rate": 0.0001})
            reset_num_timesteps = False
        except Exception as e:
            print(f"  ⚠️  Load failed ({e}) — starting fresh.")
            model = _build_model(train_env)
    else:
        model = _build_model(train_env)

    # ── 6. Train ──────────────────────────────────────────────────────────────
    print(f"\n🚀 Training universal model — {timesteps:,} timesteps...\n")
    t0 = time.time()
    try:
        model.learn(total_timesteps=timesteps, callback=eval_callback,
                    reset_num_timesteps=reset_num_timesteps)
    except KeyboardInterrupt:
        print("\n⚠️  Training interrupted.")

    elapsed = time.time() - t0
    print(f"\n✅ Done in {elapsed/60:.1f} minutes.")
    model.save(model_save_path)
    print(f"💾 Universal model saved: {zip_path}")
    print(
        f"\n📌 To fine-tune for a new symbol (e.g., AAPL):\n"
        f"   cp {zip_path} {MODEL_DIR}/ppo_AAPL_specialist.zip\n"
        f"   ./scripts/stocks/run_train.sh AAPL --resume"
    )

    train_env.close()
    eval_env.close()


def _build_model(env) -> PPO:
    """Universal model uses slightly higher entropy to stay generalist."""
    print("\n🧠 Building universal PPO neural network...")
    return PPO(
        "MlpPolicy",
        env,
        verbose=1,
        learning_rate=2e-4,    # Slightly lower LR — more symbols = noisier gradients
        n_steps=2048,
        batch_size=256,        # Larger batches to average across diverse symbol chunks
        n_epochs=10,
        gamma=0.99,
        gae_lambda=0.95,
        clip_range=0.2,
        ent_coef=0.01,         # Higher entropy than specialist — encourages broad exploration
        vf_coef=0.5,
        max_grad_norm=0.5,
        device="auto",
        policy_kwargs={"net_arch": [256, 256]},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Apex Stocks Universal PPO Trainer")
    parser.add_argument("--timeframe",  type=str, default="15min_5Y")
    parser.add_argument("--timesteps",  type=int, default=1_000_000,
                        help="Total timesteps (default 1M — more data = more compute needed)")
    parser.add_argument("--resume",     action="store_true")
    parser.add_argument("--n-envs",     type=int, default=4)
    parser.add_argument("--symbols",    type=str, nargs="+",
                        help="Override symbol list (default: all 14)")
    args = parser.parse_args()

    train_universal(
        timeframe=args.timeframe,
        timesteps=args.timesteps,
        symbols=args.symbols,
        resume=args.resume,
        n_envs=args.n_envs,
    )


if __name__ == "__main__":
    main()
