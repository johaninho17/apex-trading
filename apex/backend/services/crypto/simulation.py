from __future__ import annotations

import asyncio
import copy
import math
import time
from typing import Any, Dict, Iterable, List

from . import current_crypto_config, store
from .backtest import run_backtest


def _normalize_symbol(raw: Any) -> str:
    value = str(raw or "").strip().upper()
    if not value:
        return ""
    if "/" in value:
        return value
    if value.endswith("USD") and len(value) > 3:
        return f"{value[:-3]}/USD"
    return f"{value}/USD"


def _unique_symbols(values: Iterable[Any]) -> List[str]:
    items: List[str] = []
    for value in values:
        symbol = _normalize_symbol(value)
        if symbol and symbol not in items:
            items.append(symbol)
    return items


def _latest_universe_symbols(account_mode: str, *, limit: int = 40) -> List[str]:
    snapshot = store.get_latest_universe_snapshot_sync(limit_rankings=max(limit, 80), selected_only=False) or {}
    rows = list(snapshot.get("rankings", []) or [])
    selected = [
        _normalize_symbol(row.get("symbol"))
        for row in rows
        if bool(row.get("selected", False))
    ]
    if selected:
        return _unique_symbols(selected[:limit])
    return _unique_symbols((snapshot.get("selected_symbols") or [])[:limit])


def resolve_simulation_symbols(
    scope: str,
    *,
    symbols: Iterable[Any] | None = None,
    account_mode: str = "paper",
    limit: int = 12,
) -> List[str]:
    normalized_scope = str(scope or "single").strip().lower()
    requested = _unique_symbols(symbols or [])
    if normalized_scope == "single":
        return requested[:1]
    if normalized_scope == "custom":
        return requested[: max(1, min(int(limit or 12), 24))]
    if normalized_scope == "tracked":
        tracked = store.list_tracked_symbols_sync(account_mode, include_idle=False, limit=max(60, limit * 4))
        return _unique_symbols(row.get("symbol") for row in tracked)[: max(1, min(int(limit or 12), 24))]
    if normalized_scope == "active":
        tracked = store.list_tracked_symbols_sync(account_mode, include_idle=False, limit=max(80, limit * 4))
        active = [
            row.get("symbol")
            for row in tracked
            if str(row.get("active_state", "idle") or "idle") == "active"
        ]
        if active:
            return _unique_symbols(active)[: max(1, min(int(limit or 12), 24))]
    return _latest_universe_symbols(account_mode, limit=max(1, min(int(limit or 12), 24)))


def _merge_equity_curves(results: List[Dict[str, Any]], initial_equity: float) -> List[Dict[str, Any]]:
    buckets: Dict[int, float] = {}
    for result in results:
        for point in list(result.get("equity_curve") or []):
            ts_ms = int(point.get("ts_ms", 0) or 0)
            if ts_ms <= 0:
                continue
            buckets[ts_ms] = buckets.get(ts_ms, 0.0) + float(point.get("equity", 0.0) or 0.0)
    if not buckets:
        return [{"ts_ms": int(time.time() * 1000), "equity": round(float(initial_equity or 0.0), 2)}]
    items = [{"ts_ms": ts, "equity": round(eq, 2)} for ts, eq in sorted(buckets.items())]
    if len(items) <= 500:
        return items
    stride = max(1, math.ceil(len(items) / 500))
    sampled = items[::stride]
    if sampled[-1]["ts_ms"] != items[-1]["ts_ms"]:
        sampled.append(items[-1])
    return sampled


def _summarize_backtest_results(results: List[Dict[str, Any]], *, initial_equity: float, scope: str) -> Dict[str, Any]:
    trades = []
    errors = []
    final_equity = 0.0
    for result in results:
        if result.get("error"):
            errors.append({"symbol": result.get("symbol"), "error": result.get("error")})
        trades.extend(list(result.get("trades") or []))
        final_equity += float(result.get("final_equity", 0.0) or 0.0)

    wins = [trade for trade in trades if float(trade.get("pnl_pct", 0.0) or 0.0) > 0]
    losses = [trade for trade in trades if float(trade.get("pnl_pct", 0.0) or 0.0) <= 0]
    win_rate = len(wins) / max(1, len(trades)) * 100.0
    avg_hold = sum(float(trade.get("hold_hrs", 0.0) or 0.0) for trade in trades) / max(1, len(trades))
    pnl_total = sum(float(trade.get("pnl_usd", 0.0) or 0.0) for trade in trades)
    per_symbol: Dict[str, Dict[str, Any]] = {}
    for trade in trades:
        symbol = _normalize_symbol(trade.get("symbol"))
        if not symbol:
            continue
        bucket = per_symbol.setdefault(symbol, {"symbol": symbol, "trades": 0, "pnl_usd": 0.0, "wins": 0})
        bucket["trades"] += 1
        bucket["pnl_usd"] += float(trade.get("pnl_usd", 0.0) or 0.0)
        bucket["wins"] += 1 if float(trade.get("pnl_pct", 0.0) or 0.0) > 0 else 0
    per_symbol_rows = []
    for row in per_symbol.values():
        trades_count = max(1, int(row["trades"]))
        per_symbol_rows.append(
            {
                "symbol": row["symbol"],
                "trades": int(row["trades"]),
                "pnl_usd": round(float(row["pnl_usd"]), 2),
                "win_rate_pct": round(float(row["wins"]) / trades_count * 100.0, 1),
            }
        )
    per_symbol_rows.sort(key=lambda row: (row["pnl_usd"], row["trades"]), reverse=True)
    blocked_reason_counts: Dict[str, int] = {}
    for result in results:
        diagnostics = dict(result.get("diagnostics", {}) or {})
        for reason, count in (diagnostics.get("blocked_reasons") or {}).items():
            blocked_reason_counts[str(reason)] = blocked_reason_counts.get(str(reason), 0) + int(count or 0)
    return {
        "scope": scope,
        "total_trades": len(trades),
        "win_rate_pct": round(win_rate, 1),
        "total_pnl_usd": round(pnl_total, 2),
        "total_pnl_pct": round(((final_equity - initial_equity) / max(1.0, initial_equity)) * 100.0, 2) if initial_equity else 0.0,
        "avg_hold_hrs": round(avg_hold, 2),
        "profit_factor": round(abs(sum(float(t.get("pnl_pct", 0.0) or 0.0) for t in wins)) / max(0.01, abs(sum(float(t.get("pnl_pct", 0.0) or 0.0) for t in losses))), 2),
        "errors": errors,
        "blocked_reasons": blocked_reason_counts,
        "per_symbol": per_symbol_rows[:12],
    }


async def run_simulation(
    *,
    scope: str,
    symbols: Iterable[Any] | None = None,
    account_mode: str = "paper",
    start_date: str | None = None,
    end_date: str | None = None,
    initial_equity: float = 10_000.0,
    macro_score: float = 1.0,
    slippage_pct: float = 0.05,
    commission_pct: float = 0.0,
    max_position_pct: float = 0.10,
    limit: int = 8,
) -> Dict[str, Any]:
    cfg = copy.deepcopy(current_crypto_config(resolve_symbols=False))
    selected_symbols = resolve_simulation_symbols(scope, symbols=symbols, account_mode=account_mode, limit=limit)
    if not selected_symbols:
        return {
            "status": "partial",
            "controls": {
                "scope": scope,
                "symbols": [],
                "start_date": start_date,
                "end_date": end_date,
                "initial_equity": initial_equity,
                "macro_score": macro_score,
                "slippage_pct": slippage_pct,
                "commission_pct": commission_pct,
                "max_position_pct": max_position_pct,
            },
            "message": "No symbols resolved for simulation.",
            "results": [],
            "equity_curve": [],
        }

    per_symbol_equity = float(initial_equity or 0.0) / max(1, len(selected_symbols))
    results = []
    for symbol in selected_symbols:
        result = await run_backtest(
            symbol=symbol,
            cfg=cfg,
            start_date=start_date,
            end_date=end_date,
            initial_equity=per_symbol_equity,
            macro_score=macro_score,
            slippage_pct=slippage_pct,
            commission_pct=commission_pct,
            max_position_pct=max_position_pct,
        )
        results.append(result)

    combined_equity = _merge_equity_curves(results, initial_equity)
    final_equity = sum(float(result.get("final_equity", 0.0) or 0.0) for result in results)
    stats = _summarize_backtest_results(results, initial_equity=initial_equity, scope=scope)
    safeguard_diagnostics = {
        "trades_per_hour_proxy": round(float(stats.get("total_trades", 0) or 0) / max(1.0, max(1, len(combined_equity)) / 4.0), 2),
        "same_symbol_trade_counts": {row["symbol"]: row["trades"] for row in stats.get("per_symbol", [])},
        "blocked_reasons": stats.get("blocked_reasons", {}),
        "max_position_pct": float(max_position_pct or 0.10),
    }
    return {
        "status": "fresh",
        "controls": {
            "scope": scope,
            "symbols": selected_symbols,
            "start_date": start_date,
            "end_date": end_date,
            "initial_equity": float(initial_equity or 0.0),
            "macro_score": float(macro_score or 1.0),
            "slippage_pct": float(slippage_pct or 0.0),
            "commission_pct": float(commission_pct or 0.0),
            "max_position_pct": float(max_position_pct or 0.10),
        },
        "results": results,
        "equity_curve": combined_equity,
        "trade_log": [trade for result in results for trade in list(result.get("trades") or [])][-300:],
        "stats": {
            **stats,
            "symbol_count": len(selected_symbols),
            "final_equity": round(final_equity, 2),
        },
        "safeguard_diagnostics": safeguard_diagnostics,
        "ts": int(time.time() * 1000),
    }


def simulation_defaults(account_mode: str = "paper") -> Dict[str, Any]:
    tracked_symbols = resolve_simulation_symbols("tracked", account_mode=account_mode, limit=12)
    active_symbols = resolve_simulation_symbols("active", account_mode=account_mode, limit=12)
    return {
        "status": "fresh",
        "defaults": {
            "scope": "single",
            "initial_equity": 10_000.0,
            "macro_score": 1.0,
            "slippage_pct": 0.05,
            "commission_pct": 0.0,
            "max_position_pct": 0.10,
            "start_date": "",
            "end_date": "",
        },
        "scopes": [
            {"id": "single", "label": "Single Coin"},
            {"id": "tracked", "label": "Tracked Coins"},
            {"id": "active", "label": "Active Universe"},
            {"id": "custom", "label": "Custom Basket"},
        ],
        "tracked_preview": tracked_symbols[:8],
        "active_preview": active_symbols[:8],
        "ts": int(time.time() * 1000),
    }
