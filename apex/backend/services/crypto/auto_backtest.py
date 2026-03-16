"""
auto_backtest.py — Scheduled Multi-Symbol Backtester
=====================================================
Called by ml_cron.py every 14 days.

Runs a full 14-day vectorized backtest on:
  - BTC/USD and ETH/USD (always — benchmark anchors)
  - Top 5 most-traded symbols in the 14-day window (dynamic, from live_experience)

Results are persisted to bot_reports as type "backtest_auto" and broadcast
as a dashboard toast. The self-tuner reads these results during its next
daily run to apply strategy weight nudges.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Always include these two as benchmark anchors regardless of trade activity
ANCHOR_SYMBOLS = ["BTC/USD", "ETH/USD"]
# Top N dynamic symbols from live_experience (excluding anchors)
TOP_N_DYNAMIC = 5
# Full window
BACKTEST_DAYS = 14


def _get_top_traded_symbols(days: int = 14, limit: int = 5) -> List[str]:
    """
    Query live_experience to find the top `limit` symbols by closed-trade count
    in the last `days` days. Excludes the anchor symbols (already added separately).
    """
    try:
        from services.crypto import store as crypto_store
        since_ms = (time.time() - days * 86400) * 1000
        rows = crypto_store.get_live_experience_sync(limit=1000, closed_only=True)
        # Filter to the window
        recent = [r for r in rows if float(r.get("entry_ts") or r.get("ts") or 0) >= since_ms]
        # Count by symbol, excluding anchors
        counts: Dict[str, int] = {}
        for r in recent:
            sym = str(r.get("symbol") or "")
            if sym and sym not in ANCHOR_SYMBOLS:
                counts[sym] = counts.get(sym, 0) + 1
        # Sort by count descending, take top N with at least 3 trades
        sorted_syms = sorted(
            ((sym, cnt) for sym, cnt in counts.items() if cnt >= 3),
            key=lambda x: -x[1],
        )
        return [sym for sym, _ in sorted_syms[:limit]]
    except Exception as e:
        logger.warning(f"[auto_backtest] Could not query top traded symbols: {e}")
        return []


async def run_auto_backtest() -> Dict[str, Any]:
    """
    Main entry point. Runs 14-day backtests for up to 7 symbols (2 anchors + 5 top-traded)
    in parallel. Persists the summary to bot_reports and broadcasts a toast.

    Returns the full results dict.
    """
    from services.crypto import bot, store as crypto_store
    from services.crypto.backtest import run_backtest

    now = datetime.now(timezone.utc)
    start_dt = now - timedelta(days=BACKTEST_DAYS)
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    # Build final symbol list: anchors always + top dynamic (deduplicated)
    dynamic_syms = _get_top_traded_symbols(days=BACKTEST_DAYS, limit=TOP_N_DYNAMIC)
    all_symbols: List[str] = list(dict.fromkeys(ANCHOR_SYMBOLS + dynamic_syms))  # preserves order, deduplicates
    logger.info(f"[auto_backtest] Running 14-day backtest on {len(all_symbols)} symbols: {all_symbols}")

    # Load current bot config for strategy parameters
    try:
        full_cfg = bot.get_config().get("stocks", {}).get("crypto", {})
        active_exchange = full_cfg.get("active_exchange", "alpaca")
        account_mode = full_cfg.get("account_mode", "paper")
        profile_key = f"{active_exchange}-{account_mode}"
        from services.crypto.bot import DEFAULT_CRYPTO_CONFIG
        cfg = full_cfg.get(profile_key, DEFAULT_CRYPTO_CONFIG)
    except Exception as e:
        logger.error(f"[auto_backtest] Could not load bot config: {e}")
        cfg = {}

    # Run all backtests in parallel
    tasks = [
        run_backtest(
            symbol=sym,
            cfg=cfg,
            start_date=start_date,
            end_date=end_date,
            initial_equity=10_000.0,
        )
        for sym in all_symbols
    ]

    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Build structured summary
    symbol_results: Dict[str, Any] = {}
    for sym, res in zip(all_symbols, raw_results):
        if isinstance(res, Exception):
            symbol_results[sym] = {"status": "error", "error": str(res)}
        elif isinstance(res, dict) and "error" in res:
            symbol_results[sym] = {"status": "error", "error": res["error"]}
        else:
            stats = res.get("stats", {})
            symbol_results[sym] = {
                "status": "ok",
                "total_trades": stats.get("total_trades", 0),
                "win_rate_pct": stats.get("win_rate_pct", 0.0),
                "total_pnl_pct": stats.get("total_pnl_pct", 0.0),
                "profit_factor": stats.get("profit_factor", 0.0),
                "max_drawdown_pct": stats.get("max_drawdown_pct", 0.0),
                "avg_hold_hrs": stats.get("avg_hold_hrs", 0.0),
                "is_anchor": sym in ANCHOR_SYMBOLS,
            }

    summary = {
        "run_ts": int(time.time()),
        "run_date": now.isoformat(),
        "window_days": BACKTEST_DAYS,
        "start_date": start_date,
        "end_date": end_date,
        "symbols_tested": all_symbols,
        "results": symbol_results,
    }

    # Persist to bot_reports
    try:
        crypto_store._record_action_sync(
            action_type="gemini_report",
            symbol="AUTO BACKTEST",
            side="info",
            status="info",
            reason=f"Auto 14-day backtest complete: {len(all_symbols)} symbols tested",
            payload=summary,
        )
        logger.info(f"[auto_backtest] Persisted results for {len(all_symbols)} symbols.")
    except Exception as e:
        logger.error(f"[auto_backtest] Failed to persist results: {e}")

    # Broadcast toast
    try:
        from services.notification_manager import broadcast
        lines = [
            f"{sym}: {r.get('total_pnl_pct', 0.0):+.1f}% (PF {r.get('profit_factor', 0.0):.2f})"
            for sym, r in symbol_results.items()
            if r.get("status") == "ok"
        ]
        toast_body = " | ".join(lines[:4]) + (" ..." if len(lines) > 4 else "")
        await broadcast(
            "crypto",
            "toast",
            {
                "title": f"Auto Backtest Complete ({len(all_symbols)} coins, 14 days)",
                "message": toast_body,
                "type": "info",
                "duration": 8000,
            },
        )
    except Exception as e:
        logger.debug(f"[auto_backtest] Toast broadcast failed: {e}")

    return summary
