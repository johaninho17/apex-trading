"""
backtest.py — Vectorized Strategy Backtester
=============================================
Replays evaluate_symbol() chronologically over historical bars.
Simulates fills with transaction costs and generates an equity curve.

Usage:
    POST /api/v1/alpaca/crypto/analytics/backtest
    Body: {"symbol": "BTC/USD", "timeframe": "1D", "initial_equity": 10000, "start_date": "2024-01-01"}
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Slippage assumption: 0.05% per fill (realistic for crypto)
SLIPPAGE_PCT = 0.05
# Commission: 0% (Alpaca crypto has no commission)
COMMISSION_PCT = 0.0


async def run_backtest(
    symbol: str,
    cfg: Dict[str, Any],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    initial_equity: float = 10_000.0,
    macro_score: float = 1.0,
    slippage_pct: float = SLIPPAGE_PCT,
    commission_pct: float = COMMISSION_PCT,
    max_position_pct: float = 0.10,
) -> Dict[str, Any]:
    """
    Run a vectorized backtest for a single symbol.

    Returns:
        {
          "symbol": str,
          "trades": [...],          # each trade with entry/exit/pnl
          "equity_curve": [...],    # [{ts_ms, equity}, ...]
          "stats": {                # summary stats
            "total_trades", "win_rate", "total_pnl_pct",
            "max_drawdown_pct", "sharpe_ratio", "avg_hold_hrs"
          }
        }
    """
    t_start = time.monotonic()

    try:
        from .market_data import fetch_bars
        from .strategy import evaluate_symbol
        from .indicators import enrich_indicators, snapshot
    except ImportError as e:
        return {"error": f"Import failed: {e}", "symbol": symbol}

    # ── Load historical bars ─────────────────────────────────────────────
    days_back = 365
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
            days_back = max(30, (datetime.now(timezone.utc) - start_dt).days)
        except Exception:
            pass

    try:
        bars_15m = fetch_bars(symbol, timeframe="15Min", limit=min(4000, days_back * 96))
        bars_1m  = fetch_bars(symbol, timeframe="1Min",  limit=min(40000, days_back * 960))
    except Exception as e:
        return {"error": f"Failed to fetch bars: {e}", "symbol": symbol}

    if bars_15m is None or bars_15m.empty or len(bars_15m) < 60:
        return {"error": "Insufficient 15m bar data", "symbol": symbol}

    if bars_1m is None or bars_1m.empty or len(bars_1m) < 14:
        bars_1m = bars_15m.copy()  # fallback

    try:
        import pandas as pd

        if start_date:
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
            bars_15m = bars_15m[pd.to_datetime(bars_15m["timestamp"], utc=True) >= start_dt]
            bars_1m = bars_1m[pd.to_datetime(bars_1m["timestamp"], utc=True) >= start_dt]
        if end_date:
            end_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc) + timedelta(days=1)
            bars_15m = bars_15m[pd.to_datetime(bars_15m["timestamp"], utc=True) < end_dt]
            bars_1m = bars_1m[pd.to_datetime(bars_1m["timestamp"], utc=True) < end_dt]
    except Exception:
        pass

    # ── Simulation loop ──────────────────────────────────────────────────
    equity = initial_equity
    position: Optional[Dict[str, Any]] = None
    equity_curve: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []

    MIN_WINDOW = 60  # minimum bars before we start evaluating

    total_15m = len(bars_15m)
    for i in range(MIN_WINDOW, total_15m):
        window_15m = bars_15m.iloc[:i]
        bar_ts = bars_15m.iloc[i - 1].get("timestamp", None)
        ts_ms = int(bar_ts.timestamp() * 1000) if hasattr(bar_ts, "timestamp") else int(time.time() * 1000)

        # Match 1m bars up to this timestamp
        if hasattr(bar_ts, "timestamp"):
            cutoff = bar_ts.timestamp()
            import pandas as pd
            bars_1m_window = bars_1m[pd.to_datetime(bars_1m["timestamp"]).apply(
                lambda x: x.timestamp() if hasattr(x, "timestamp") else 0
            ) <= cutoff].tail(200)
        else:
            bars_1m_window = bars_1m.iloc[:i * 15].tail(200)

        if len(bars_1m_window) < 14:
            continue

        current_price = float(bars_15m.iloc[i - 1].get("close", 0) or 0)
        if current_price <= 0:
            continue

        # ── Exit check ────────────────────────────────────────────────
        if position:
            entry = position["entry"]
            pnl_pct = (current_price - entry) / entry * 100.0
            sl_pct = float(cfg.get("synthetic_exits", {}).get("stop_loss_pct", 3.5))
            tp_pct = float(cfg.get("synthetic_exits", {}).get("take_profit_pct", 3.0))

            exit_reason = None
            if pnl_pct <= -sl_pct:
                exit_reason = "stop_loss"
            elif pnl_pct >= tp_pct:
                exit_reason = "take_profit"
            elif macro_score < 0.5:
                exit_reason = "oracle_crash"

            if exit_reason:
                fill_price = current_price * (1 - float(slippage_pct or 0.0) / 100)
                realized_pnl_pct = (fill_price - entry) / entry * 100.0
                trade_notional = position["notional"]
                exit_commission = trade_notional * (float(commission_pct or 0.0) / 100.0)
                pnl_dollars = trade_notional * realized_pnl_pct / 100.0 - exit_commission
                equity += pnl_dollars
                hold_hrs = (ts_ms - position["entry_ts_ms"]) / 3_600_000

                trades.append({
                    "symbol": symbol,
                    "entry": round(entry, 4),
                    "exit": round(fill_price, 4),
                    "entry_ts_ms": position["entry_ts_ms"],
                    "exit_ts_ms": ts_ms,
                    "pnl_pct": round(realized_pnl_pct, 2),
                    "pnl_usd": round(pnl_dollars, 2),
                    "notional": round(trade_notional, 2),
                    "hold_hrs": round(hold_hrs, 2),
                    "reason": exit_reason,
                    "strategy": position.get("strategy", "unknown"),
                })
                position = None

        equity_curve.append({"ts_ms": ts_ms, "equity": round(equity, 2)})

        # ── Entry check (only when flat) ──────────────────────────────
        if position:
            continue

        try:
            sig = await evaluate_symbol(
                symbol=symbol,
                bars_15m=window_15m,
                bars_1m=bars_1m_window,
                cfg=cfg,
                now_ms=ts_ms,
                macro_risk_mult=macro_score,
            )
        except Exception:
            continue

        if not sig or sig.get("side") not in ("buy",):
            continue

        # Simulate fill with slippage
        fill_price = current_price * (1 + float(slippage_pct or 0.0) / 100)
        notional = min(
            float(cfg.get("short_term", {}).get("base_notional", 500.0)),
            equity * max(0.01, float(max_position_pct or 0.10))
        )

        if notional < 50:
            continue  # too small

        entry_commission = notional * (float(commission_pct or 0.0) / 100.0)
        equity -= entry_commission

        position = {
            "entry": fill_price,
            "notional": notional,
            "entry_ts_ms": ts_ms,
            "strategy": sig.get("strategy", "unknown"),
            "entry_commission": entry_commission,
        }

    # Close any open position at last bar
    if position and len(bars_15m) > 0:
        last_price = float(bars_15m.iloc[-1].get("close", 0) or 0)
        if last_price > 0:
            entry = position["entry"]
            pnl_pct = (last_price - entry) / entry * 100.0
            exit_commission = position["notional"] * (float(commission_pct or 0.0) / 100.0)
            pnl_dollars = position["notional"] * pnl_pct / 100.0 - exit_commission
            equity += pnl_dollars
            trades.append({
                "symbol": symbol, "entry": round(entry, 4), "exit": round(last_price, 4),
                "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_dollars, 2),
                "notional": round(position["notional"], 2), "reason": "sim_end",
                "strategy": position.get("strategy", "unknown"),
            })

    # ── Statistics ────────────────────────────────────────────────────────
    total_trades = len(trades)
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate = len(wins) / max(1, total_trades) * 100
    total_pnl_pct = (equity - initial_equity) / initial_equity * 100

    # Max drawdown
    peak_eq = initial_equity
    max_dd = 0.0
    for point in equity_curve:
        eq = point["equity"]
        if eq > peak_eq:
            peak_eq = eq
        dd = (peak_eq - eq) / peak_eq * 100 if peak_eq > 0 else 0
        if dd > max_dd:
            max_dd = dd

    avg_hold = sum(t.get("hold_hrs", 0) for t in trades) / max(1, total_trades)
    avg_win = sum(t["pnl_pct"] for t in wins) / max(1, len(wins))
    avg_loss = sum(t["pnl_pct"] for t in losses) / max(1, len(losses))

    elapsed = round(time.monotonic() - t_start, 2)

    return {
        "symbol": symbol,
        "initial_equity": initial_equity,
        "final_equity": round(equity, 2),
        "equity_curve": equity_curve[-500:],  # cap to last 500 points for response size
        "trades": trades[-200:],
        "stats": {
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate, 1),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "avg_hold_hrs": round(avg_hold, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "profit_factor": round(
                abs(sum(t["pnl_pct"] for t in wins)) / max(0.01, abs(sum(t["pnl_pct"] for t in losses))),
                2
            ),
        },
        "meta": {
            "bars_15m": total_15m,
            "elapsed_sec": elapsed,
            "macro_score_used": macro_score,
            "slippage_pct": float(slippage_pct or 0.0),
            "commission_pct": float(commission_pct or 0.0),
            "max_position_pct": float(max_position_pct or 0.10),
        }
    }
