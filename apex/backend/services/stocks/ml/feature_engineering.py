#!/usr/bin/env python3
"""
feature_engineering.py — Apex Stocks Data Pipeline: Phase 2

Reads raw OHLCV Parquet files from data/stocks/{SYM}/{timeframe}.parquet,
calculates all technical indicators required by the swing and position
strategies, then saves enriched files as {timeframe}_engineered.parquet.

Features computed per symbol:
  - EMA 20 / EMA 50 / EMA 200  (trend direction and crossovers)
  - SMA 50 / SMA 200           (Golden Cross for position strategy)
  - RSI 14                     (overbought/oversold gate)
  - MACD (12/26/9)             (momentum trigger)
  - Bollinger Bands 20,2       (mean reversion entries and TP target)
  - ATR 14                     (dynamic stop-loss sizing)
  - VWAP deviation %           (intraday price anchor, SPY canary)
  - Log returns 1 / 5 bars     (AI model input feature)
  - Temporal cyclical encoding (hour sin/cos, weekday sin/cos)
  - golden_cross signal        (1 when SMA50 > SMA200, else 0)
  - spy_vwap_delta             (SPY VWAP deviation — cross-asset canary)

Usage:
  cd apex/backend
  ./scripts/stocks/run_feature_engineering.sh

  Or run directly:
  source venv_wsl/bin/activate
  python3 services/stocks/ml/feature_engineering.py [--timeframe 15min_5Y|daily_10Y|all]
"""

import os
import sys
import logging
import argparse
import numpy as np
import pandas as pd

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
))))
sys.path.insert(0, _BACKEND_DIR)
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ENGINEER] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DATA_DIR  = os.path.join(_BACKEND_DIR, "data", "stocks")
MACRO_DIR = os.path.join(_BACKEND_DIR, "data", "macro")

# Timeframes that contain intraday time (need cyclical hour encoding)
INTRADAY_TIMEFRAMES = {"1min_2Y", "15min_5Y", "1hour_7Y"}


# ── Core indicator engine ─────────────────────────────────────────────────────
def engineer_features(df: pd.DataFrame, timeframe: str, spy_vwap_delta: pd.Series = None) -> pd.DataFrame:
    """
    Injects the full indicator suite into a raw OHLCV DataFrame.

    Args:
        df: Raw OHLCV bars (must have: timestamp, open, high, low, close, volume, vwap)
        timeframe: Label string — used to skip intraday-only features on daily data
        spy_vwap_delta: Optional series aligned by timestamp — SPY canary signal

    Returns:
        Engineered DataFrame with NaN rows dropped
    """
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    c = df["close"]
    h = df["high"]
    l = df["low"]
    vol = df["volume"]

    # ── 1. Trend — Exponential Moving Averages ────────────────────────────────
    df["ema_20"]  = c.ewm(span=20,  adjust=False).mean()
    df["ema_50"]  = c.ewm(span=50,  adjust=False).mean()
    df["ema_200"] = c.ewm(span=200, adjust=False).mean()

    # ── 2. Simple Moving Averages (for Golden Cross — position strategy) ──────
    df["sma_50"]  = c.rolling(50).mean()
    df["sma_200"] = c.rolling(200).mean()

    # Golden Cross: 1 when SMA50 > SMA200 (macro bull), 0 otherwise
    df["golden_cross"] = (df["sma_50"] > df["sma_200"]).astype(int)

    # ── 3. RSI 14 ─────────────────────────────────────────────────────────────
    delta = c.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    # ── 4. MACD 12/26/9 ───────────────────────────────────────────────────────
    ema_fast = c.ewm(span=12, adjust=False).mean()
    ema_slow = c.ewm(span=26, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    df["macd"]          = macd_line
    df["macd_signal"]   = signal_line
    df["macd_hist"]     = macd_line - signal_line
    # Crossover flag: 1 when MACD just crossed above signal (entry trigger)
    df["macd_crossover"] = (
        (df["macd"] > df["macd_signal"]) & (df["macd"].shift(1) <= df["macd_signal"].shift(1))
    ).astype(int)

    # ── 5. Bollinger Bands 20,2 ───────────────────────────────────────────────
    bb_mid = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    df["bb_upper"] = bb_mid + 2 * bb_std
    df["bb_lower"] = bb_mid - 2 * bb_std
    df["bb_mid"]   = bb_mid
    # BB %B: 0 = at lower band, 1 = at upper band (normalized position)
    bb_range = df["bb_upper"] - df["bb_lower"]
    df["bb_pct"] = (c - df["bb_lower"]) / bb_range.replace(0, np.nan)

    # ── 6. ATR 14 (True Range) ────────────────────────────────────────────────
    prev_close = c.shift(1)
    tr = pd.concat([
        h - l,
        (h - prev_close).abs(),
        (l - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr_14"] = tr.ewm(com=13, adjust=False).mean()
    # ATR as % of price — useful for comparing stops across different-priced stocks
    df["atr_pct"] = df["atr_14"] / c

    # ── 7. VWAP deviation ─────────────────────────────────────────────────────
    if "vwap" in df.columns and df["vwap"].notna().any():
        vwap_col = df["vwap"]
    else:
        # Approximate VWAP using rolling typical price weighted by volume
        typical_price = (h + l + c) / 3
        vwap_col = (typical_price * vol).rolling(14).sum() / vol.rolling(14).sum()

    df["vwap_dev"] = (c - vwap_col) / vwap_col.replace(0, np.nan)

    # ── 8. Log returns ────────────────────────────────────────────────────────
    df["log_return_1"] = np.log(c / c.shift(1))
    df["log_return_5"] = np.log(c / c.shift(5))

    # ── 9. Temporal cyclical encoding (intraday timeframes only) ──────────────
    if timeframe in INTRADAY_TIMEFRAMES:
        df["hour_of_day"] = df["timestamp"].dt.hour
        df["day_of_week"] = df["timestamp"].dt.dayofweek
        df["hour_sin"] = np.sin(df["hour_of_day"] * (2.0 * np.pi / 24))
        df["hour_cos"] = np.cos(df["hour_of_day"] * (2.0 * np.pi / 24))
        df["day_sin"]  = np.sin(df["day_of_week"] * (2.0 * np.pi / 7))
        df["day_cos"]  = np.cos(df["day_of_week"] * (2.0 * np.pi / 7))
        df = df.drop(columns=["hour_of_day", "day_of_week"])

    # ── 10. SPY canary — cross-asset macro signal ─────────────────────────────
    if spy_vwap_delta is not None:
        # spy_vwap_delta is a DataFrame with columns [timestamp, spy_vwap_delta]
        df = df.merge(spy_vwap_delta[["timestamp", "spy_vwap_delta"]], on="timestamp", how="left")
        df["spy_vwap_delta"] = df["spy_vwap_delta"].fillna(0.0)
    else:
        df["spy_vwap_delta"] = 0.0  # Neutral placeholder if SPY data unavailable

    # ── 11. Gemini macro oracle placeholder ───────────────────────────────────
    # Will be overwritten at runtime by the live oracle; 1.0 = neutral baseline
    df["gemini_macro"] = 1.0

    # ── Clean up ──────────────────────────────────────────────────────────────
    df = df.dropna().reset_index(drop=True)
    return df


# ── SPY canary pre-computation ────────────────────────────────────────────────
def compute_spy_vwap_delta(timeframe: str) -> pd.Series:
    """
    Loads SPY bars for the given timeframe and computes VWAP deviation.
    Returns a Series indexed by timestamp for joining onto other symbols.
    """
    spy_path = os.path.join(DATA_DIR, "SPY", f"{timeframe}.parquet")
    if not os.path.exists(spy_path):
        logger.warning("  SPY/%s not found — spy_vwap_delta will be 0.0 for all rows", timeframe)
        return None

    spy = pd.read_parquet(spy_path)
    spy["timestamp"] = pd.to_datetime(spy["timestamp"], utc=True)

    c   = spy["close"]
    h   = spy["high"]
    l_  = spy["low"]
    vol = spy["volume"]

    if "vwap" in spy.columns and spy["vwap"].notna().any():
        vwap = spy["vwap"]
    else:
        tp   = (h + l_ + c) / 3
        vwap = (tp * vol).rolling(14).sum() / vol.rolling(14).sum()

    delta = (c - vwap) / vwap.replace(0, np.nan)
    return pd.Series(delta.values, index=spy["timestamp"], name="spy_vwap_delta").dropna()


# ── Orchestration ─────────────────────────────────────────────────────────────
def process_timeframe(timeframe: str) -> None:
    """
    Process all symbols for a given timeframe label (e.g., '15min_5Y').
    SPY is processed first so its VWAP delta can be injected into all others.
    """
    logger.info("\n── Processing timeframe: %s ─────────────────────────", timeframe)

    # Pre-compute SPY canary signal for this timeframe
    spy_delta = compute_spy_vwap_delta(timeframe)

    # Collect all symbols that have this timeframe file
    sym_dirs = sorted(d for d in os.listdir(DATA_DIR)
                      if os.path.isdir(os.path.join(DATA_DIR, d)))

    processed = 0
    for sym in sym_dirs:
        raw_path = os.path.join(DATA_DIR, sym, f"{timeframe}.parquet")
        out_path = os.path.join(DATA_DIR, sym, f"{timeframe}_engineered.parquet")

        if not os.path.exists(raw_path):
            continue

        if os.path.exists(out_path):
            logger.info("  [SKIP] %s/%s — already engineered", sym, timeframe)
            processed += 1
            continue

        try:
            df = pd.read_parquet(raw_path)
            if df.empty or len(df) < 200:
                logger.warning("  %s/%s — too few rows (%d), skipping", sym, timeframe, len(df))
                continue

            # Build per-symbol SPY canary (aligned by timestamp)
            spy_series = None
            if spy_delta is not None and sym != "SPY":
                spy_series = spy_delta.reset_index()
                spy_series.columns = ["timestamp", "spy_vwap_delta"]

            df_eng = engineer_features(df, timeframe, spy_series)

            df_eng.to_parquet(out_path, compression="snappy", index=False)
            n_features = len(df_eng.columns)
            logger.info(
                "  ✅ %s/%s → %d rows | %d features | %.1f MB",
                sym, timeframe, len(df_eng), n_features,
                os.path.getsize(out_path) / 1e6,
            )
            processed += 1
        except Exception as e:
            logger.error("  ❌ %s/%s failed: %s", sym, timeframe, e)

    logger.info("  Done: %d symbols processed for %s", processed, timeframe)


TIMEFRAME_ORDER = ["daily_10Y", "1hour_7Y", "15min_5Y", "1min_2Y"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Apex Stocks Feature Engineering")
    parser.add_argument(
        "--timeframe",
        choices=TIMEFRAME_ORDER + ["all"],
        default="all",
        help="Which timeframe to process (default: all in recommended order)",
    )
    args = parser.parse_args()

    if not os.path.exists(DATA_DIR):
        logger.error("data/stocks/ directory not found — run fetch_historical_data.py first")
        sys.exit(1)

    targets = TIMEFRAME_ORDER if args.timeframe == "all" else [args.timeframe]

    for tf in targets:
        process_timeframe(tf)

    logger.info("\n✅ Feature engineering complete.")


if __name__ == "__main__":
    main()
