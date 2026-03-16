#!/usr/bin/env python3
"""
fetch_historical_data.py — Apex Stocks Data Pipeline: Phase 1

Pulls historical OHLCV bars for the stocks watchlist from Alpaca and
macro data (VIX, earnings) from yfinance. Saves everything as compressed
Parquet files for fast VectorBT backtesting and PPO model training.

Pull sequence (do in order — fastest first):
  1. Daily bars   — all 14 symbols, 10 years
  2. 1-Hour bars  — Tier 1+2, 7 years
  3. 15-Min bars  — all 14 symbols, 5 years
  4. 1-Min bars   — Tier 1 only, 2 years  (large — do last)
  5. Macro data   — VIX + earnings via yfinance

Usage:
  cd apex/backend
  source venv_wsl/bin/activate
  python3 scripts/stocks/fetch_historical_data.py [--tier daily|1h|15m|1m|macro|all]

Output structure:
  data/stocks/NVDA/daily_10Y.parquet
  data/stocks/NVDA/1hour_7Y.parquet
  data/stocks/NVDA/15min_5Y.parquet
  data/stocks/NVDA/1min_2Y.parquet
  data/macro/VIX_daily_10Y.parquet
  data/macro/earnings_calendar.parquet
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime, timezone, date, timedelta
from typing import List, Optional

# ── Path setup ────────────────────────────────────────────────────────────────
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _BACKEND_DIR)
os.environ.setdefault("NUMBA_DISABLE_JIT", "1")

from dotenv import load_dotenv
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [FETCH] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Output paths ──────────────────────────────────────────────────────────────
DATA_DIR    = os.path.join(_BACKEND_DIR, "data", "stocks")
MACRO_DIR   = os.path.join(_BACKEND_DIR, "data", "macro")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(MACRO_DIR, exist_ok=True)

# ── Watchlist definition ──────────────────────────────────────────────────────
# Tier 1: All 4 timeframes, deepest history
TIER_1 = ["NVDA", "AMD", "TSLA", "QQQ", "SPY"]

# Tier 2: 15min + Daily only
TIER_2 = ["META", "MSFT", "GOOGL", "PLTR", "ARM"]

# Tier 3: Daily only (leveraged ETFs — leverage decay corrupts intraday data)
TIER_3 = ["SOXL", "TQQQ", "SOXS"]

# Special case: SMCI only from 2023-01-01 onward
SMCI_START = date(2023, 1, 1)

ALL_SYMBOLS = TIER_1 + TIER_2 + TIER_3 + ["SMCI"]

# ── Date range config ─────────────────────────────────────────────────────────
TODAY = date.today()

RANGES = {
    "daily_10Y":  date(TODAY.year - 10, TODAY.month, TODAY.day),
    "1hour_7Y":   date(TODAY.year - 7,  TODAY.month, TODAY.day),
    "15min_5Y":   date(TODAY.year - 5,  TODAY.month, TODAY.day),
    "1min_2Y":    date(TODAY.year - 2,  TODAY.month, TODAY.day),
}

# Pre-2018 NVDA/AMD data corrupts ATR tuning (different company era)
NVDA_AMD_START = date(2018, 1, 1)
# SOXL/TQQQ/SOXS before 2015: low volume, unrepresentative
LEVERAGED_ETF_START = date(2015, 1, 1)


def _effective_start(symbol: str, range_start: date) -> date:
    """Apply symbol-specific start date overrides."""
    if symbol == "SMCI":
        return max(range_start, SMCI_START)
    if symbol in {"NVDA", "AMD"}:
        return max(range_start, NVDA_AMD_START)
    if symbol in {"SOXL", "TQQQ", "SOXS"}:
        return max(range_start, LEVERAGED_ETF_START)
    return range_start


def _parquet_path(symbol: str, label: str) -> str:
    sym_dir = os.path.join(DATA_DIR, symbol)
    os.makedirs(sym_dir, exist_ok=True)
    return os.path.join(sym_dir, f"{label}.parquet")


# ── Alpaca fetch ──────────────────────────────────────────────────────────────
def fetch_alpaca_bars(
    symbol: str,
    timeframe_str: str,
    start: date,
    label: str,
    mode: str = "paper",
    chunk_days: int = 365,
) -> Optional[pd.DataFrame]:
    """
    Fetch historical bars from Alpaca in yearly chunks to avoid API limits.
    Returns a combined DataFrame or None on failure.
    """
    out_path = _parquet_path(symbol, label)
    if os.path.exists(out_path):
        existing = pd.read_parquet(out_path)
        logger.info("  [SKIP] %s/%s — already on disk (%d rows)", symbol, label, len(existing))
        return existing

    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        from integrations.alpaca.runtime_config import get_alpaca_credentials
    except ImportError as e:
        logger.error("Import failed: %s", e)
        return None

    # Parse timeframe
    tf_map = {
        "1min":  TimeFrame(1,  TimeFrameUnit.Minute),
        "15min": TimeFrame(15, TimeFrameUnit.Minute),
        "1hour": TimeFrame(1,  TimeFrameUnit.Hour),
        "daily": TimeFrame(1,  TimeFrameUnit.Day),
    }
    tf = tf_map.get(timeframe_str)
    if tf is None:
        logger.error("Unknown timeframe: %s", timeframe_str)
        return None

    api_key, secret_key, _ = get_alpaca_credentials(mode=mode)
    client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)

    all_rows: List[dict] = []
    chunk_start = start
    end_date = TODAY

    while chunk_start < end_date:
        chunk_end = min(chunk_start + timedelta(days=chunk_days), end_date)

        try:
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=datetime(chunk_start.year, chunk_start.month, chunk_start.day, tzinfo=timezone.utc),
                end=datetime(chunk_end.year, chunk_end.month, chunk_end.day, tzinfo=timezone.utc),
            )
            resp = client.get_stock_bars(req)
            data = getattr(resp, "data", None) or resp
            bars = (data.get(symbol) or [] if isinstance(data, dict) else [])

            for b in bars:
                ts = getattr(b, "timestamp", None)
                all_rows.append({
                    "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "open":   float(getattr(b, "open",   0.0) or 0.0),
                    "high":   float(getattr(b, "high",   0.0) or 0.0),
                    "low":    float(getattr(b, "low",    0.0) or 0.0),
                    "close":  float(getattr(b, "close",  0.0) or 0.0),
                    "volume": float(getattr(b, "volume", 0.0) or 0.0),
                    "vwap":   float(getattr(b, "vwap",   0.0) or 0.0),
                    "trade_count": int(getattr(b, "trade_count", 0) or 0),
                    "symbol": symbol,
                })
            logger.info(
                "  %s/%s chunk %s→%s: %d bars (total so far: %d)",
                symbol, label, chunk_start, chunk_end, len(bars), len(all_rows)
            )
        except Exception as e:
            logger.warning("  %s/%s chunk %s→%s failed: %s", symbol, label, chunk_start, chunk_end, e)

        chunk_start = chunk_end
        time.sleep(0.35)  # Respect Alpaca rate limits (200 req/min)

    if not all_rows:
        logger.warning("  %s/%s — no data returned", symbol, label)
        return None

    df = pd.DataFrame(all_rows)
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df.to_parquet(out_path, compression="snappy", index=False)
    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    logger.info("  ✅ Saved %s/%s → %d rows | %.1f MB", symbol, label, len(df), size_mb)
    return df


# ── yfinance macro fetch ──────────────────────────────────────────────────────
def fetch_vix(years_back: int = 10) -> None:
    out_path = os.path.join(MACRO_DIR, "VIX_daily_10Y.parquet")
    if os.path.exists(out_path):
        logger.info("  [SKIP] VIX — already on disk")
        return
    try:
        import yfinance as yf
        start_str = str(date(TODAY.year - years_back, TODAY.month, TODAY.day))
        df = yf.download("^VIX", start=start_str, progress=False)
        if df.empty:
            logger.error("  VIX download returned empty DataFrame")
            return
        df = df.reset_index()
        df.columns = [c.lower().replace(" ", "_") for c in df.columns]
        df.to_parquet(out_path, compression="snappy", index=False)
        logger.info("  ✅ VIX saved → %d rows | %.1f MB", len(df), os.path.getsize(out_path) / 1e6)
    except ImportError:
        logger.error("  yfinance not installed. Run: pip install yfinance")
    except Exception as e:
        logger.error("  VIX fetch failed: %s", e)


def fetch_earnings(symbols: List[str], years_back: int = 5) -> None:
    out_path = os.path.join(MACRO_DIR, "earnings_calendar.parquet")
    if os.path.exists(out_path):
        logger.info("  [SKIP] Earnings calendar — already on disk")
        return
    try:
        import yfinance as yf
        all_rows = []
        for sym in symbols:
            if sym in {"SPY", "QQQ", "SOXL", "TQQQ", "SOXS"}:
                continue  # ETFs have no earnings
            try:
                ticker = yf.Ticker(sym)
                cal = ticker.calendar
                if cal is not None and not (isinstance(cal, dict) and not cal):
                    # Normalize calendar formats across yfinance versions
                    if isinstance(cal, pd.DataFrame):
                        row = {"symbol": sym}
                        for col in cal.columns:
                            vals = cal[col].dropna().tolist()
                            row[str(col).lower()] = vals[0] if vals else None
                        all_rows.append(row)
                    elif isinstance(cal, dict):
                        row = {"symbol": sym}
                        row.update({k.lower(): v for k, v in cal.items()})
                        all_rows.append(row)
                logger.info("  Earnings: %s ✅", sym)
                time.sleep(0.5)
            except Exception as e:
                logger.warning("  Earnings: %s failed — %s", sym, e)

        if all_rows:
            df = pd.DataFrame(all_rows)
            df.to_parquet(out_path, compression="snappy", index=False)
            logger.info("  ✅ Earnings calendar saved → %d rows", len(df))
        else:
            logger.warning("  No earnings data collected")
    except ImportError:
        logger.error("  yfinance not installed. Run: pip install yfinance")
    except Exception as e:
        logger.error("  Earnings fetch failed: %s", e)


# ── Orchestration ─────────────────────────────────────────────────────────────
def run_daily(mode: str = "paper") -> None:
    """Step 1: Daily bars — all 14 symbols, 10 years. Fastest, do first."""
    logger.info("\n── STEP 1: DAILY BARS (all 14 symbols, 10Y) ──────────────────")
    for sym in ALL_SYMBOLS:
        start = _effective_start(sym, RANGES["daily_10Y"])
        fetch_alpaca_bars(sym, "daily", start, "daily_10Y", mode=mode, chunk_days=365)


def run_1hour(mode: str = "paper") -> None:
    """Step 2: 1-Hour bars — Tier 1 + Tier 2, 7 years."""
    logger.info("\n── STEP 2: 1-HOUR BARS (Tier 1+2, 7Y) ──────────────────────")
    for sym in TIER_1 + TIER_2 + ["SMCI"]:
        start = _effective_start(sym, RANGES["1hour_7Y"])
        fetch_alpaca_bars(sym, "1hour", start, "1hour_7Y", mode=mode, chunk_days=365)


def run_15min(mode: str = "paper") -> None:
    """Step 3: 15-Min bars — all 14 symbols, 5 years."""
    logger.info("\n── STEP 3: 15-MIN BARS (all 14 symbols, 5Y) ─────────────────")
    for sym in ALL_SYMBOLS:
        start = _effective_start(sym, RANGES["15min_5Y"])
        fetch_alpaca_bars(sym, "15min", start, "15min_5Y", mode=mode, chunk_days=180)


def run_1min(mode: str = "paper") -> None:
    """Step 4: 1-Min bars — Tier 1 only, 2 years. Largest — do last."""
    logger.info("\n── STEP 4: 1-MIN BARS (Tier 1 only, 2Y) ────────────────────")
    for sym in TIER_1:  # No SMCI at 1m — too volatile for ATR pre-2023
        start = _effective_start(sym, RANGES["1min_2Y"])
        fetch_alpaca_bars(sym, "1min", start, "1min_2Y", mode=mode, chunk_days=60)


def run_macro() -> None:
    """Step 5+6: VIX + earnings via yfinance. Takes minutes."""
    logger.info("\n── STEP 5: VIX (10Y) ────────────────────────────────────────")
    fetch_vix(years_back=10)
    logger.info("\n── STEP 6: EARNINGS CALENDAR ────────────────────────────────")
    fetch_earnings(ALL_SYMBOLS, years_back=5)


# ── Entrypoint ────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Apex Stocks Historical Data Fetcher")
    parser.add_argument(
        "--tier",
        choices=["daily", "1h", "15m", "1m", "macro", "all"],
        default="all",
        help="Which tier to pull (default: all, in recommended sequence)",
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default="paper",
        help="Alpaca credentials mode (default: paper — same data, different keys)",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("APEX STOCKS HISTORICAL DATA PIPELINE")
    logger.info("Symbols: %s", ", ".join(ALL_SYMBOLS))
    logger.info("Output:  %s", DATA_DIR)
    logger.info("=" * 60)

    t0 = time.time()

    if args.tier in ("daily", "all"):
        run_daily(mode=args.mode)
    if args.tier in ("1h", "all"):
        run_1hour(mode=args.mode)
    if args.tier in ("15m", "all"):
        run_15min(mode=args.mode)
    if args.tier in ("1m", "all"):
        run_1min(mode=args.mode)
    if args.tier in ("macro", "all"):
        run_macro()

    elapsed = time.time() - t0
    logger.info("\n✅ DONE — Total time: %.1f minutes", elapsed / 60)
    logger.info("Data directory: %s", DATA_DIR)

    # Print size summary
    total_mb = 0.0
    for root, _, files in os.walk(DATA_DIR):
        for f in files:
            if f.endswith(".parquet"):
                total_mb += os.path.getsize(os.path.join(root, f)) / 1e6
    for f in os.listdir(MACRO_DIR):
        if f.endswith(".parquet"):
            total_mb += os.path.getsize(os.path.join(MACRO_DIR, f)) / 1e6
    logger.info("Total disk usage: %.1f MB", total_mb)


if __name__ == "__main__":
    main()
