#!/usr/bin/env python3
"""
backtest_strategy.py — Apex Stocks Data Pipeline: Phase 3

Uses VectorBT to backtest the Swing strategy (15-min and 1-hour) and
Position strategy (Daily) across multiple years of engineered data.

Core optimization grid:
  - RSI Oversold Threshold (e.g., 28 to 40)
  - ATR Trailing Stop Multiplier (e.g., 1.0x to 3.0x)

Usage:
  # Run a single backtest with specific settings
  python3 scripts/stocks/backtest_strategy.py --symbol NVDA --tf 15min_5Y --rsi 35 --atr 2.0

  # Run FULL OPTIMIZATION GRID (find best settings)
  python3 scripts/stocks/backtest_strategy.py --symbol NVDA --tf 15min_5Y --optimize
"""

import os
import sys
import logging
import argparse
import numpy as np
import pandas as pd
import vectorbt as vbt

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _BACKEND_DIR)
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [BACKTEST] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(_BACKEND_DIR, "data", "stocks")

# Trading constants
INITIAL_CAPITAL = 10000.0
FEES = 0.000  # Alpaca stocks are commission-free


def load_data(symbol: str, timeframe: str) -> pd.DataFrame:
    """Load engineered parquet data for vectorbt."""
    path = os.path.join(DATA_DIR, symbol, f"{timeframe}_engineered.parquet")
    if not os.path.exists(path):
        logger.error("Engineered data not found: %s", path)
        sys.exit(1)
    
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df.set_index("timestamp", inplace=True)
    # VectorBT requires a monotonic increasing frequency or frequency removal
    if not df.index.is_monotonic_increasing:
        df = df.sort_index()
    return df


def run_single_backtest(df: pd.DataFrame, rsi_thresh: float, atr_mult: float, plot: bool = False):
    """
    Simulates a standard Swing Strategy.
    Entry: RSI crosses below threshold AND price is above 200 EMA (macro trend filter).
    Exit rules built into VectorBT's trailing stop logic via ATR.
    """
    close = df["close"]
    rsi = df["rsi_14"]
    ema200 = df["ema_200"]
    atr_pct = df["atr_pct"]

    # Entry Logic: RSI Oversold AND Macro Uptrend
    # We use vbt.crossed_below to get the exact trigger candle
    entries = (rsi < rsi_thresh) & (close > ema200)
    
    # We don't define generic exits because we are using a strict Trailing Stop
    # The trailing stop distance is dynamic per candle: atr_mult * atr_pct
    stop_distance = atr_pct * atr_mult

    # Build Portfolio
    pf = vbt.Portfolio.from_signals(
        close=close,
        entries=entries,
        exits=False,                      # Handled by trailing stops
        sl_stop=stop_distance,            # Dynamic stop loss % (ATR)
        sl_trail=True,                    # Make it a trailing stop
        init_cash=INITIAL_CAPITAL,
        fees=FEES,
        freq="15T" if "15min" in str(close.name) else "1D"  # fallback freq
    )

    stats = pf.stats()
    
    total_return = stats.get("Total Return [%]", 0.0)
    max_dd = stats.get("Max Drawdown [%]", 0.0)
    win_rate = stats.get("Win Rate [%]", 0.0)
    trades = stats.get("Total Closed Trades", 0)

    logger.info("── Backtest Results (RSI=%.1f | ATR=%.1fx) ──", rsi_thresh, atr_mult)
    logger.info("Total Return:   %8.2f %%", total_return)
    logger.info("Max Drawdown:   %8.2f %%", max_dd)
    logger.info("Win Rate:       %8.2f %%", win_rate)
    logger.info("Total Trades:   %8d", int(trades))
    
    if plot:
        pf.plot().show()

    return pf, stats


def run_optimization(df: pd.DataFrame):
    """
    Runs a 2D Optimization Grid over RSI thresholds and ATR multipliers.
    Prints the top 5 parameter combinations sorted by Return/Drawdown ratio.
    """
    logger.info("Running VectorBT Optimization Grid...")
    
    rsi_range = np.arange(25, 45, 2)       # [25, 27, ..., 43]
    atr_range = np.arange(1.0, 3.5, 0.5)   # [1.0, 1.5, ..., 3.0]

    close = df["close"]
    ema200 = df["ema_200"]
    
    # We need 2D aligned DataFrames to test combinations efficiently in vbt
    rsi_df = df["rsi_14"]
    atr_pct_df = df["atr_pct"]

    results = []
    
    # Iterate the grid (for small grids, loop is acceptable/readable; vbt supports broadcasting but it gets complex)
    total_runs = len(rsi_range) * len(atr_range)
    current = 0
    
    for r in rsi_range:
        for a in atr_range:
            current += 1
            print(f"\rOptimizing {current}/{total_runs} (RSI={r}, ATR={a})...", end="")
            
            entries = (rsi_df < r) & (close > ema200)
            stop_distance = atr_pct_df * a
            
            pf = vbt.Portfolio.from_signals(
                close=close,
                entries=entries,
                exits=False,
                sl_stop=stop_distance,
                sl_trail=True,
                init_cash=INITIAL_CAPITAL,
                fees=FEES
            )
            
            ret = pf.total_return() * 100
            md = pf.max_drawdown() * 100
            wr = pf.trades.win_rate() * 100 if pf.trades.count() > 0 else 0
            tc = pf.trades.count()
            
            # Simple score: Return divided by Drawdown (avoids dividing by zero)
            score = ret / abs(md) if md != 0 and ret > 0 else 0
            if ret < 0:
                score = -abs(md)  # heavily penalize negative returns
                
            results.append({
                "RSI": r,
                "ATR_x": a,
                "Return_%": ret,
                "Max_DD_%": md,
                "Win_Rate_%": wr,
                "Trades": tc,
                "Score": score
            })

    print("\n")
    res_df = pd.DataFrame(results).sort_values("Score", ascending=False).reset_index(drop=True)
    
    logger.info("── Top 5 Parameter Combinations ──")
    with pd.option_context('display.max_rows', None, 'display.max_columns', None, 'display.width', 1000, 'display.float_format', '{:.2f}'.format):
        print(res_df.head(5))
    
    best = res_df.iloc[0]
    logger.info("\n🏆 BEST SETTING:")
    logger.info("RSI Trigger:  %.1f", best["RSI"])
    logger.info("Trailing Stop: %.1fx ATR", best["ATR_x"])
    logger.info("Expected Net: %.1f%% return over backtest period", best["Return_%"])


def main():
    parser = argparse.ArgumentParser(description="Apex Stocks VectorBT Backtester")
    parser.add_argument("--symbol", type=str, required=True, help="Stock symbol (e.g., NVDA, SPY)")
    parser.add_argument("--tf", type=str, required=True, help="Timeframe file prefix (e.g., 15min_5Y)")
    parser.add_argument("--rsi", type=float, default=32.0, help="RSI oversold entry threshold (default 32)")
    parser.add_argument("--atr", type=float, default=2.0, help="ATR trailing stop multiplier (default 2.0x)")
    parser.add_argument("--optimize", action="store_true", help="Run full optimization grid instead of single test")
    parser.add_argument("--plot", action="store_true", help="Show HTML plot of trades (single mode only)")
    
    args = parser.parse_args()

    logger.info("Loading %s data for %s...", args.tf, args.symbol)
    df = load_data(args.symbol, args.tf)
    logger.info("Loaded %d bars.", len(df))

    if args.optimize:
        run_optimization(df)
    else:
        run_single_backtest(df, args.rsi, args.atr, plot=args.plot)


if __name__ == "__main__":
    main()
