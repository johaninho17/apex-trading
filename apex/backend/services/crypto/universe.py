"""Ranked symbol universe selection for the crypto bot."""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from . import market_data, store
from .policy_overlays import get_overlay_map

logger = logging.getLogger(__name__)

_UNIVERSE_CACHE: Dict[str, Any] = {
    "ts_sec": 0.0,
    "key": None,
    "result": None,
}
_UNIVERSE_LOCK = threading.Lock()

_DEFAULT_UNIVERSE_CFG: Dict[str, Any] = {
    "enabled": True,
    "core_symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
    "discovery_limit_raw": 300,
    "discovery_limit_ranked": 25,
    "event_override_limit": 8,
    "refresh_sec": 900,
    "min_24h_volume_usd": 2_000_000.0,
    "max_spread_bps": 90.0,
    "min_price": 0.05,
    "rank_weights": {
        "liquidity": 0.30,
        "spread": 0.20,
        "volume": 0.20,
        "volatility_quality": 0.10,
        "event": 0.10,
        "historical_edge": 0.10,
    },
}

_ACTIVE_MIN_MS = 6 * 60 * 60 * 1000
_RECENT_ACTIVE_MS = 24 * 60 * 60 * 1000
_MAX_SWAPS_PER_REFRESH = 3


def _normalize_symbol(raw: Any) -> str:
    symbol = str(raw or "").strip().upper()
    if not symbol:
        return ""
    if "/" in symbol:
        return symbol
    for quote in ("USD", "USDT", "USDC"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return f"{symbol[:-len(quote)]}/{quote}"
    return symbol


def _merge_symbols(*groups: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for group in groups:
        for raw in group or []:
            symbol = _normalize_symbol(raw)
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            out.append(symbol)
    return out


def _load_universe_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    universe_cfg = dict(_DEFAULT_UNIVERSE_CFG)
    raw = cfg.get("universe", {}) or {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if key == "rank_weights" and isinstance(value, dict):
                merged = dict(universe_cfg["rank_weights"])
                merged.update(value)
                universe_cfg["rank_weights"] = merged
            else:
                universe_cfg[key] = value
    universe_cfg["core_symbols"] = _merge_symbols(universe_cfg.get("core_symbols", []))
    universe_cfg["discovery_limit_raw"] = max(10, min(300, int(universe_cfg.get("discovery_limit_raw", 80) or 80)))
    universe_cfg["discovery_limit_ranked"] = max(5, min(120, int(universe_cfg.get("discovery_limit_ranked", 25) or 25)))
    universe_cfg["event_override_limit"] = max(0, min(50, int(universe_cfg.get("event_override_limit", 8) or 8)))
    universe_cfg["refresh_sec"] = max(60, min(3600, int(universe_cfg.get("refresh_sec", 900) or 900)))
    universe_cfg["min_24h_volume_usd"] = max(0.0, float(universe_cfg.get("min_24h_volume_usd", 0.0) or 0.0))
    universe_cfg["max_spread_bps"] = max(1.0, float(universe_cfg.get("max_spread_bps", 90.0) or 90.0))
    universe_cfg["min_price"] = max(0.0, float(universe_cfg.get("min_price", 0.0) or 0.0))
    return universe_cfg


def _discover_raw_symbols(cfg: Dict[str, Any], mode: str, limit: int) -> List[str]:
    if not bool(cfg.get("auto_discover_pairs", False)):
        return []
    quote = str(cfg.get("auto_discover_quote", "USD") or "USD").strip().upper()
    tradable_only = bool(cfg.get("auto_discover_tradable_only", True))
    out: List[str] = []
    try:
        for asset in market_data.list_crypto_assets(limit=max(limit * 4, limit), mode=mode):
            symbol = _normalize_symbol(asset.get("symbol", ""))
            if not symbol:
                continue
            if tradable_only and not bool(asset.get("tradable", True)):
                continue
            if quote and not symbol.endswith(f"/{quote}"):
                continue
            status = str(asset.get("status", "active") or "active").lower()
            if "inactive" in status or "delisted" in status:
                continue
            out.append(symbol)
            if len(out) >= limit:
                break
    except Exception as exc:
        logger.debug("[universe] raw discovery failed: %s", exc)
    return _merge_symbols(out)


def _load_historical_edges(limit: int = 400) -> Dict[str, Dict[str, float]]:
    rows = store.get_live_experience_sync(limit=limit, closed_only=True)
    grouped: Dict[str, List[float]] = {}
    for row in rows:
        symbol = _normalize_symbol(row.get("symbol", ""))
        if not symbol:
            continue
        pnl = row.get("outcome_pnl_pct")
        if pnl is None:
            continue
        grouped.setdefault(symbol, []).append(float(pnl or 0.0))
    edges: Dict[str, Dict[str, float]] = {}
    for symbol, pnls in grouped.items():
        if not pnls:
            continue
        wins = sum(1 for value in pnls if value > 0)
        avg_pnl = sum(pnls) / len(pnls)
        edges[symbol] = {
            "count": float(len(pnls)),
            "win_rate": wins / len(pnls),
            "avg_pnl": avg_pnl,
        }
    return edges


def _load_event_states(limit: int = 200) -> Dict[str, Dict[str, Any]]:
    rows = store.list_symbol_event_states_sync(limit=limit)
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        symbol = _normalize_symbol(row.get("symbol", ""))
        if not symbol:
            continue
        out[symbol] = dict(row)
    return out


def _select_promoted_symbols(event_states: Dict[str, Dict[str, Any]], limit: int) -> List[str]:
    ranked: List[tuple[float, str]] = []
    for symbol, row in event_states.items():
        net_score = float(row.get("net_event_score", 0.0) or 0.0)
        top_score = float(row.get("top_event_score", 0.0) or 0.0)
        hard_veto = bool(row.get("hard_veto", 0))
        golden = bool(row.get("golden_trade_flag", 0))
        if not (golden or hard_veto or abs(net_score) >= 12.0 or abs(top_score) >= 70.0):
            continue
        priority = abs(net_score) + abs(top_score) + (25.0 if golden else 0.0) + (20.0 if hard_veto else 0.0)
        ranked.append((priority, symbol))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [symbol for _score, symbol in ranked[: max(0, limit)]]


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _score_from_dollar_volume(value: float) -> float:
    if value <= 0:
        return 0.0
    return _clamp((math.log10(max(value, 1.0)) - 5.5) / 2.5 * 100.0)


def _score_from_spread(spread_bps: float, max_spread_bps: float) -> float:
    if spread_bps <= 0:
        return 50.0
    return _clamp(100.0 - (spread_bps / max(max_spread_bps, 1.0)) * 100.0)


def _score_from_volatility(avg_range_pct: float) -> float:
    target = 1.5
    diff = abs(avg_range_pct - target)
    return _clamp(100.0 - (diff / 4.0) * 100.0)


def _score_from_event(row: Dict[str, Any]) -> float:
    if not row:
        return 0.0
    score = abs(float(row.get("net_event_score", 0.0) or 0.0)) * 2.5
    score += abs(float(row.get("top_event_score", 0.0) or 0.0)) * 0.4
    if bool(row.get("golden_trade_flag", 0)):
        score += 20.0
    if bool(row.get("hard_veto", 0)):
        score += 10.0
    return _clamp(score)


def _score_from_historical_edge(row: Dict[str, float]) -> float:
    if not row:
        return 50.0
    win_rate = float(row.get("win_rate", 0.5) or 0.5)
    avg_pnl = float(row.get("avg_pnl", 0.0) or 0.0)
    return _clamp(50.0 + (win_rate - 0.5) * 80.0 + avg_pnl * 4.0)


def _feedback_focus_symbols(cfg: Dict[str, Any], now_ms: int) -> Dict[str, int]:
    feedback_cfg = cfg.get("decision_feedback", {}) or {}
    horizons = [int(value or 0) for value in (feedback_cfg.get("horizons_min", []) or []) if int(value or 0) > 0]
    max_horizon_min = max(horizons) if horizons else (24 * 60)
    since_ts = now_ms - (max_horizon_min * 60 * 1000)
    try:
        rows = store.list_recent_decision_symbols_sync(since_ts, limit=800)
    except Exception as exc:
        logger.debug("[universe] recent feedback lookup failed: %s", exc)
        rows = []
    return {
        _normalize_symbol(row.get("symbol", "")): int(row.get("last_decision_ts", 0) or 0)
        for row in rows
        if _normalize_symbol(row.get("symbol", ""))
    }


def _tracked_reason(priority_map: Dict[str, str], symbol: str, fallback: str) -> str:
    reason = str(priority_map.get(symbol, "") or "").strip().lower()
    return reason or fallback


def _build_tracked_row(symbol: str, mode: str, existing: Optional[Dict[str, Any]] = None, **updates: Any) -> Dict[str, Any]:
    base = dict(existing or {})
    metadata = dict(base.get("metadata") or {})
    metadata.update(dict(updates.pop("metadata", {}) or {}))
    active_state = str(updates.get("active_state", base.get("active_state", "idle")) or "idle").strip().lower()
    if active_state not in {"active", "recently_active", "idle"}:
        active_state = "idle"
    return {
        "symbol": _normalize_symbol(symbol),
        "account_mode": str(mode or "paper").strip().lower() or "paper",
        "saved_manual": bool(updates.get("saved_manual", base.get("saved_manual", False))),
        "active_state": active_state,
        "active_reason": str(updates.get("active_reason", base.get("active_reason", "") or "") or ""),
        "active_since_ts": int(updates.get("active_since_ts", base.get("active_since_ts", 0) or 0) or 0),
        "active_min_until_ts": int(updates.get("active_min_until_ts", base.get("active_min_until_ts", 0) or 0) or 0),
        "recent_until_ts": int(updates.get("recent_until_ts", base.get("recent_until_ts", 0) or 0) or 0),
        "last_decision_ts": int(updates.get("last_decision_ts", base.get("last_decision_ts", 0) or 0) or 0),
        "monitor_tier": str(updates.get("monitor_tier", base.get("monitor_tier", "eligible") or "eligible") or "eligible"),
        "last_rank_score": float(updates.get("last_rank_score", base.get("last_rank_score", 0.0) or 0.0) or 0.0),
        "metadata": metadata,
        "updated_ts": int(updates.get("updated_ts", base.get("updated_ts", 0) or 0) or 0),
    }


def _persist_tracked_row(symbol: str, mode: str, existing: Optional[Dict[str, Any]] = None, **updates: Any) -> Dict[str, Any]:
    try:
        return store.upsert_tracked_symbol_sync(symbol, mode, **updates)
    except Exception as exc:
        logger.debug("[universe] tracked symbol persist failed for %s: %s", symbol, exc)
        return _build_tracked_row(symbol, mode, existing, **updates)


def _reconcile_tracked_states(
    mode: str,
    rankings: List[Dict[str, Any]],
    priority_map: Dict[str, str],
    recent_feedback_map: Dict[str, int],
    *,
    now_ms: int,
    active_limit: int,
    keep_limit: int,
) -> Dict[str, Dict[str, Any]]:
    try:
        tracked_rows = {
            _normalize_symbol(row.get("symbol", "")): row
            for row in store.list_tracked_symbols_sync(mode, include_idle=True, limit=2000)
            if _normalize_symbol(row.get("symbol", ""))
        }
    except Exception as exc:
        logger.debug("[universe] tracked symbol load failed: %s", exc)
        tracked_rows = {}
    ranked_rows = sorted(
        [dict(row) for row in rankings if _normalize_symbol(row.get("symbol", ""))],
        key=lambda item: float(item.get("total_rank", 0.0) or 0.0),
        reverse=True,
    )
    score_map: Dict[str, float] = {}
    for row in ranked_rows:
        symbol = _normalize_symbol(row.get("symbol", ""))
        current_score = float(row.get("total_rank", 0.0) or 0.0)
        previous_score = float((tracked_rows.get(symbol, {}) or {}).get("last_rank_score", 0.0) or 0.0)
        smoothed_score = round((current_score * 0.65) + (previous_score * 0.35), 2) if previous_score > 0 else round(current_score, 2)
        row["smoothed_rank"] = smoothed_score
        score_map[symbol] = smoothed_score

    ranked_symbols = [row["symbol"] for row in ranked_rows]
    entry_band = set(ranked_symbols[: max(0, active_limit)])
    keep_band = set(ranked_symbols[: max(active_limit, keep_limit)])
    current_active = {
        symbol
        for symbol, row in tracked_rows.items()
        if str(row.get("active_state", "idle") or "idle").strip().lower() == "active"
    }
    forced_active = set(priority_map)
    desired_active = forced_active | entry_band
    forced_promotions = [symbol for symbol in ranked_symbols if symbol in forced_active and symbol not in current_active]
    discretionary_promotions = [
        symbol
        for symbol in ranked_symbols
        if symbol in desired_active and symbol not in current_active and symbol not in forced_active
    ]
    promotions = forced_promotions + discretionary_promotions[:_MAX_SWAPS_PER_REFRESH]
    next_active = set(current_active)
    next_active.update(promotions)
    for symbol in current_active:
        row = tracked_rows.get(symbol, {}) or {}
        metadata = dict(row.get("metadata") or {})
        if symbol in forced_active:
            metadata["out_of_keep_band_count"] = 0
            row["metadata"] = metadata
            continue
        if now_ms < int(row.get("active_min_until_ts", 0) or 0):
            metadata["out_of_keep_band_count"] = 0
            row["metadata"] = metadata
            continue
        if symbol in keep_band:
            metadata["out_of_keep_band_count"] = 0
            row["metadata"] = metadata
            continue
        if recent_feedback_map.get(symbol):
            metadata["out_of_keep_band_count"] = 0
            row["metadata"] = metadata
            continue
        out_of_band_count = int(metadata.get("out_of_keep_band_count", 0) or 0) + 1
        metadata["out_of_keep_band_count"] = out_of_band_count
        row["metadata"] = metadata
        if out_of_band_count < 2:
            continue
        next_active.discard(symbol)
    active_demotions = [symbol for symbol in current_active if symbol not in next_active]
    if len(active_demotions) > _MAX_SWAPS_PER_REFRESH:
        preserve = active_demotions[_MAX_SWAPS_PER_REFRESH:]
        next_active.update(preserve)

    tracked_symbols = set(tracked_rows) | set(score_map) | set(recent_feedback_map) | set(priority_map)
    result: Dict[str, Dict[str, Any]] = {}
    for symbol in tracked_symbols:
        existing = tracked_rows.get(symbol, {}) or {}
        saved_manual = bool(existing.get("saved_manual"))
        last_decision_ts = max(
            int(existing.get("last_decision_ts", 0) or 0),
            int(recent_feedback_map.get(symbol, 0) or 0),
        )
        metadata = dict(existing.get("metadata") or {})
        metadata.update({
            "smoothed_rank": score_map.get(symbol, float(existing.get("last_rank_score", 0.0) or 0.0)),
            "last_refresh_ts": now_ms,
        })
        existing_state = str(existing.get("active_state", "idle") or "idle").strip().lower()
        if symbol in next_active:
            metadata["out_of_keep_band_count"] = 0
            active_since_ts = int(existing.get("active_since_ts", 0) or 0) if existing_state == "active" else now_ms
            row = _persist_tracked_row(
                symbol,
                mode,
                existing,
                saved_manual=saved_manual,
                active_state="active",
                active_reason=_tracked_reason(priority_map, symbol, "top_ranked"),
                active_since_ts=active_since_ts,
                active_min_until_ts=max(int(existing.get("active_min_until_ts", 0) or 0), active_since_ts + _ACTIVE_MIN_MS),
                recent_until_ts=max(int(existing.get("recent_until_ts", 0) or 0), last_decision_ts + _RECENT_ACTIVE_MS if last_decision_ts else 0),
                last_decision_ts=last_decision_ts,
                monitor_tier="active",
                last_rank_score=score_map.get(symbol, float(existing.get("last_rank_score", 0.0) or 0.0)),
                metadata=metadata,
                updated_ts=now_ms,
            )
        else:
            recent_until = int(existing.get("recent_until_ts", 0) or 0)
            if last_decision_ts > 0:
                recent_until = max(recent_until, last_decision_ts + _RECENT_ACTIVE_MS)
            if existing_state == "active":
                recent_until = max(recent_until, now_ms + _RECENT_ACTIVE_MS)
            metadata["out_of_keep_band_count"] = 0
            if recent_until > now_ms:
                row = _persist_tracked_row(
                    symbol,
                    mode,
                    existing,
                    saved_manual=saved_manual,
                    active_state="recently_active",
                    active_reason=_tracked_reason(priority_map, symbol, "recent_feedback" if last_decision_ts else "recently_active"),
                    active_since_ts=int(existing.get("active_since_ts", 0) or 0),
                    active_min_until_ts=0,
                    recent_until_ts=recent_until,
                    last_decision_ts=last_decision_ts,
                    monitor_tier="recently_active",
                    last_rank_score=score_map.get(symbol, float(existing.get("last_rank_score", 0.0) or 0.0)),
                    metadata=metadata,
                    updated_ts=now_ms,
                )
            else:
                row = _persist_tracked_row(
                    symbol,
                    mode,
                    existing,
                    saved_manual=saved_manual,
                    active_state="idle",
                    active_reason="saved" if saved_manual else "",
                    active_since_ts=0,
                    active_min_until_ts=0,
                    recent_until_ts=0,
                    last_decision_ts=last_decision_ts,
                    monitor_tier="saved" if saved_manual else "eligible",
                    last_rank_score=score_map.get(symbol, float(existing.get("last_rank_score", 0.0) or 0.0)),
                    metadata=metadata,
                    updated_ts=now_ms,
                )
        result[symbol] = row
    return result


def _rank_symbol(
    symbol: str,
    mode: str,
    universe_cfg: Dict[str, Any],
    bucket: str,
    event_states: Dict[str, Dict[str, Any]],
    historical_edges: Dict[str, Dict[str, float]],
    overlay_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    quote = market_data.get_latest_quote(symbol, mode=mode)
    if not quote:
        return None
    ask = float(quote.get("ask_price", 0.0) or 0.0)
    bid = float(quote.get("bid_price", 0.0) or 0.0)
    mid = float(quote.get("mid_price", 0.0) or 0.0)
    if mid <= 0:
        return None
    spread_bps = ((ask - bid) / mid) * 10000.0 if ask > 0 and bid > 0 else 0.0
    if mid < float(universe_cfg.get("min_price", 0.0) or 0.0):
        return None

    bars = None
    try:
        bars = market_data.fetch_bars(symbol, timeframe="15Min", limit=96, mode=mode)
    except Exception as exc:
        logger.debug("[universe] %s 15Min bars unavailable for %s: %s", bucket, symbol, exc)
    if bars is None or bars.empty or len(bars) < 24:
        if bucket == "discovery":
            return None
        dollar_volume = 0.0
        avg_range_pct = 0.0
    else:
        closes = bars["close"].astype(float)
        highs = bars["high"].astype(float)
        lows = bars["low"].astype(float)
        volumes = bars["volume"].astype(float)
        dollar_volume = float((closes * volumes).tail(96).sum())
        avg_range_pct = float((((highs - lows) / closes.replace(0, float("nan"))).tail(48)).fillna(0.0).mean() * 100.0)

    liquidity_score = _score_from_dollar_volume(dollar_volume)
    spread_score = _score_from_spread(spread_bps, float(universe_cfg.get("max_spread_bps", 90.0) or 90.0))
    volume_score = _score_from_dollar_volume(dollar_volume)
    volatility_score = _score_from_volatility(avg_range_pct)
    event_score = _score_from_event(event_states.get(symbol, {}))
    historical_edge_score = _score_from_historical_edge(historical_edges.get(symbol, {}))
    overlay = (overlay_map or {}).get(symbol, {})
    overlay_score = float(overlay.get("score_delta", 0.0) or 0.0)
    overlay_confidence = float(overlay.get("confidence_boost", 0.0) or 0.0)
    weights = universe_cfg.get("rank_weights", {}) or {}
    total_rank = (
        liquidity_score * float(weights.get("liquidity", 0.30) or 0.30)
        + spread_score * float(weights.get("spread", 0.20) or 0.20)
        + volume_score * float(weights.get("volume", 0.20) or 0.20)
        + volatility_score * float(weights.get("volatility_quality", 0.10) or 0.10)
        + event_score * float(weights.get("event", 0.10) or 0.10)
        + historical_edge_score * float(weights.get("historical_edge", 0.10) or 0.10)
        + overlay_score
    )
    passes_filters = (
        spread_bps <= float(universe_cfg.get("max_spread_bps", 90.0) or 90.0)
        and dollar_volume >= float(universe_cfg.get("min_24h_volume_usd", 0.0) or 0.0)
    )
    return {
        "symbol": symbol,
        "bucket": bucket,
        "selected": False,
        "mid_price": round(mid, 8),
        "spread_bps": round(spread_bps, 2),
        "dollar_volume_24h": round(dollar_volume, 2),
        "avg_range_pct": round(avg_range_pct, 3),
        "liquidity_score": round(liquidity_score, 2),
        "spread_score": round(spread_score, 2),
        "volume_score": round(volume_score, 2),
        "volatility_score": round(volatility_score, 2),
        "event_score": round(event_score, 2),
        "historical_edge_score": round(historical_edge_score, 2),
        "overlay_score": round(overlay_score, 2),
        "overlay_confidence_boost": round(overlay_confidence, 2),
        "total_rank": round(total_rank, 2),
        "passes_filters": bool(passes_filters),
    }


def select_trade_universe(cfg: Dict[str, Any], mode: str, held_symbols: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    universe_cfg = _load_universe_cfg(cfg)
    now_ms = int(time.time() * 1000)
    manual_symbols = _merge_symbols(cfg.get("manual_symbols", []))
    discovered_symbols = _merge_symbols(_discover_raw_symbols(cfg, mode, int(universe_cfg["discovery_limit_raw"])))
    held = _merge_symbols(held_symbols or [])
    event_states = _load_event_states(limit=200)
    promoted = _select_promoted_symbols(event_states, int(universe_cfg["event_override_limit"]))
    recent_feedback_map = _feedback_focus_symbols(cfg, now_ms)
    cache_key = json.dumps({
        "mode": mode,
        "manual": manual_symbols,
        "discovered": discovered_symbols,
        "held": held,
        "promoted": promoted,
        "recent_feedback": sorted(recent_feedback_map),
        "cfg": universe_cfg,
    }, sort_keys=True)

    with _UNIVERSE_LOCK:
        cached = _UNIVERSE_CACHE.get("result")
        if _UNIVERSE_CACHE.get("key") == cache_key and cached and (time.time() - float(_UNIVERSE_CACHE.get("ts_sec", 0.0) or 0.0)) <= float(universe_cfg["refresh_sec"]):
            return cached

    core = _merge_symbols(universe_cfg.get("core_symbols", []), manual_symbols)
    forced = _merge_symbols(core, held, promoted)
    candidate_symbols = _merge_symbols(forced, discovered_symbols)
    historical_edges = _load_historical_edges(limit=600)
    try:
        overlay_map = get_overlay_map(refresh=True)
    except Exception as exc:
        logger.debug("[universe] overlay refresh failed: %s", exc)
        overlay_map = {}
    rankings: List[Dict[str, Any]] = []
    for symbol in candidate_symbols:
        if symbol in held:
            bucket = "held"
        elif symbol in promoted:
            bucket = "event_override"
        elif symbol in core:
            bucket = "core"
        else:
            bucket = "discovery"
        try:
            if overlay_map:
                row = _rank_symbol(symbol, mode, universe_cfg, bucket, event_states, historical_edges, overlay_map)
            else:
                row = _rank_symbol(symbol, mode, universe_cfg, bucket, event_states, historical_edges)
        except Exception as exc:
            logger.debug("[universe] ranking skipped for %s: %s", symbol, exc)
            continue
        if not row:
            continue
        rankings.append(row)

    forced_rows = [row for row in rankings if row["bucket"] in {"held", "event_override", "core"}]
    discovery_rows = [row for row in rankings if row["bucket"] == "discovery" and row.get("passes_filters", True)]
    discovery_rows.sort(key=lambda item: (float(item.get("total_rank", 0.0) or 0.0), float(item.get("event_score", 0.0) or 0.0)), reverse=True)
    selected_discovery = discovery_rows[: int(universe_cfg["discovery_limit_ranked"])]
    keep_discovery = discovery_rows[: max(int(universe_cfg["discovery_limit_ranked"]) + 15, 40)]
    priority_map: Dict[str, str] = {}
    for symbol in held:
        priority_map[symbol] = "held"
    for symbol in promoted:
        priority_map[symbol] = "news_promoted"
    for symbol in [row["symbol"] for row in forced_rows]:
        priority_map.setdefault(symbol, "core")
    for symbol in recent_feedback_map:
        priority_map.setdefault(symbol, "recent_decision_feedback")
    tracked_map = _reconcile_tracked_states(
        mode,
        rankings,
        priority_map,
        recent_feedback_map,
        now_ms=now_ms,
        active_limit=len(_merge_symbols([row["symbol"] for row in forced_rows], [row["symbol"] for row in selected_discovery])),
        keep_limit=len(_merge_symbols([row["symbol"] for row in forced_rows], [row["symbol"] for row in keep_discovery])),
    )
    ranked_symbols = [str(row.get("symbol", "") or "").upper() for row in rankings if str(row.get("symbol", "") or "").strip()]
    selected_symbols = [
        symbol
        for symbol in ranked_symbols
        if str(tracked_map.get(symbol, {}).get("active_state", "idle") or "idle").strip().lower() == "active"
    ]
    recently_active_symbols = [
        symbol
        for symbol in ranked_symbols
        if str(tracked_map.get(symbol, {}).get("active_state", "idle") or "idle").strip().lower() == "recently_active"
    ]
    for row in rankings:
        symbol = str(row.get("symbol", "") or "").upper()
        tracked = tracked_map.get(symbol, {})
        row["selected"] = symbol in selected_symbols
        row["active_state"] = str(tracked.get("active_state", "idle") or "idle")
        row["active_reason"] = str(tracked.get("active_reason", "") or "")
        row["active_since_ts"] = int(tracked.get("active_since_ts", 0) or 0)
        row["active_min_until_ts"] = int(tracked.get("active_min_until_ts", 0) or 0)
        row["recent_until_ts"] = int(tracked.get("recent_until_ts", 0) or 0)
        row["monitor_tier"] = str(tracked.get("monitor_tier", "eligible") or "eligible")
        row["saved_manual"] = bool(tracked.get("saved_manual"))
        row["smoothed_rank"] = float((tracked.get("metadata", {}) or {}).get("smoothed_rank", row.get("smoothed_rank", row.get("total_rank", 0.0))) or 0.0)
    rankings.sort(
        key=lambda item: (
            2 if str(item.get("active_state", "idle")) == "active" else 1 if str(item.get("active_state", "idle")) == "recently_active" else 0,
            float(item.get("smoothed_rank", item.get("total_rank", 0.0)) or 0.0),
        ),
        reverse=True,
    )
    result = {
        "symbols": selected_symbols,
        "forced_symbols": _merge_symbols([row["symbol"] for row in forced_rows]),
        "promoted_symbols": _merge_symbols(promoted),
        "recently_active_symbols": recently_active_symbols,
        "rankings": rankings,
        "ts_ms": now_ms,
    }
    try:
        store.record_universe_snapshot_sync(
            account_mode=mode,
            selected_symbols=selected_symbols,
            promoted_symbols=promoted,
            rankings=rankings,
            payload={
                "core_symbols": core,
                "discovered_count": len(discovered_symbols),
                "selected_count": len(selected_symbols),
                "recently_active_symbols": recently_active_symbols,
                "saved_symbols": [
                    symbol
                    for symbol, row in tracked_map.items()
                    if bool(row.get("saved_manual"))
                ],
                "universe_cfg": universe_cfg,
            },
        )
    except Exception as exc:
        logger.debug("[universe] snapshot persist failed: %s", exc)

    with _UNIVERSE_LOCK:
        _UNIVERSE_CACHE["ts_sec"] = time.time()
        _UNIVERSE_CACHE["key"] = cache_key
        _UNIVERSE_CACHE["result"] = result
    return result
