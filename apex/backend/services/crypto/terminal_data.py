from __future__ import annotations

import asyncio
import copy
from collections import Counter, deque
from datetime import datetime, timezone
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

from core import background_services

from . import market_data, store
from .bot import current_crypto_config, get_account, get_bot_status, get_bot_status_snapshot, get_positions
from .policy_overlays import ensure_brain_self_score_snapshot, get_overlay_map, refresh_symbol_policy_overlays
from .universe import select_trade_universe

logger = logging.getLogger(__name__)
_REFRESH_LOCK = threading.Lock()
_REFRESH_IN_FLIGHT: set[str] = set()
_REFRESH_LAST_START: Dict[str, float] = {}
_DEFAULT_FEEDBACK_HORIZONS = [30, 60, 360, 1440]
_VIEW_CACHE_LOCK = threading.Lock()
_VIEW_CACHE: Dict[str, Dict[str, Any]] = {}
_SHELL_CFG_CACHE: Dict[str, Any] = {"ts_sec": 0.0, "payload": {}}
_SHELL_WARM_LOCK = threading.Lock()
_SHELL_WARM_STATE: Dict[str, Any] = {
    "phase": "cold",
    "last_started_ms": 0,
    "last_completed_ms": 0,
    "last_error": "",
    "views": {},
}


def _summary_profiler_enabled() -> bool:
    return str(os.getenv("APEX_PROFILE_TERMINAL_SUMMARY", "")).strip().lower() in {"1", "true", "yes", "on"}


def _summary_profiler_log(label: str, started: float) -> None:
    if not _summary_profiler_enabled():
        return
    elapsed = time.perf_counter() - started
    print(f"[terminal_summary] {label} {elapsed:.3f}s", flush=True)


def _normalize_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper()
    if not raw:
        return ""
    if "/" in raw:
        return raw
    if raw.endswith("USD") and len(raw) > 3:
        return f"{raw[:-3]}/USD"
    return f"{raw}/USD"


def _cache_get(key: str, max_age_sec: float) -> Optional[Dict[str, Any]]:
    now_sec = time.time()
    with _VIEW_CACHE_LOCK:
        entry = _VIEW_CACHE.get(key)
        if not entry:
            return None
        if (now_sec - float(entry.get("ts_sec", 0.0) or 0.0)) > float(max_age_sec):
            return None
        payload = entry.get("payload")
        return copy.deepcopy(payload) if isinstance(payload, dict) else None


def _cache_peek(key: str) -> Optional[Dict[str, Any]]:
    with _VIEW_CACHE_LOCK:
        entry = _VIEW_CACHE.get(key)
        if not entry:
            return None
        payload = entry.get("payload")
        return copy.deepcopy(payload) if isinstance(payload, dict) else None


def _cache_set(key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    cached = copy.deepcopy(payload)
    with _VIEW_CACHE_LOCK:
        _VIEW_CACHE[key] = {
            "ts_sec": time.time(),
            "payload": cached,
        }
    return copy.deepcopy(cached)


def _set_shell_warm_phase(
    phase: str,
    *,
    error: str = "",
    started_ms: Optional[int] = None,
    completed_ms: Optional[int] = None,
) -> None:
    with _SHELL_WARM_LOCK:
        _SHELL_WARM_STATE["phase"] = str(phase or "cold")
        if started_ms is not None:
            _SHELL_WARM_STATE["last_started_ms"] = int(started_ms)
        if completed_ms is not None:
            _SHELL_WARM_STATE["last_completed_ms"] = int(completed_ms)
        _SHELL_WARM_STATE["last_error"] = str(error or "")


def _set_shell_warm_view(name: str, status: str, snapshot_ts: int = 0) -> None:
    with _SHELL_WARM_LOCK:
        views = dict(_SHELL_WARM_STATE.get("views") or {})
        views[str(name)] = {
            "status": str(status or "partial"),
            "snapshot_ts": int(snapshot_ts or 0),
            "updated_at_ms": int(time.time() * 1000),
        }
        _SHELL_WARM_STATE["views"] = views


def get_shell_warm_status() -> Dict[str, Any]:
    with _SHELL_WARM_LOCK:
        return copy.deepcopy(_SHELL_WARM_STATE)


def _current_shell_config(max_age_sec: float = 30.0) -> Dict[str, Any]:
    now_sec = time.time()
    with _VIEW_CACHE_LOCK:
        payload = _SHELL_CFG_CACHE.get("payload") or {}
        ts_sec = float(_SHELL_CFG_CACHE.get("ts_sec", 0.0) or 0.0)
        if payload and (now_sec - ts_sec) <= float(max_age_sec):
            return dict(payload)

    cfg = current_crypto_config(resolve_symbols=False)
    with _VIEW_CACHE_LOCK:
        _SHELL_CFG_CACHE["ts_sec"] = now_sec
        _SHELL_CFG_CACHE["payload"] = dict(cfg)
    return dict(cfg)


def _timeframe_limit_from_range(range_key: str) -> tuple[str, int]:
    key = str(range_key or "1D").upper()
    if key == "1H":
        return "1Min", 60
    if key == "7D":
        return "1H", 168
    if key == "30D":
        return "4H", 180
    if key == "90D":
        return "1D", 90
    if key == "ALL":
        return "1D", 365
    return "15Min", 96


def _downsample_series(items: List[Dict[str, Any]], target_points: int) -> List[Dict[str, Any]]:
    if len(items) <= max(2, int(target_points or 0)):
        return list(items)
    stride = max(1, int(round(len(items) / max(2, int(target_points or 0)))))
    sampled = [items[idx] for idx in range(0, len(items), stride)]
    if sampled[-1] != items[-1]:
        sampled.append(items[-1])
    return sampled


def _equity_target_points(since_ms: int) -> int:
    now_ms = int(time.time() * 1000)
    if int(since_ms or 0) <= 0:
        return 420
    window_ms = max(0, now_ms - int(since_ms or 0))
    if window_ms <= 2 * 60 * 60 * 1000:
        return 240
    if window_ms <= 8 * 24 * 60 * 60 * 1000:
        return 360
    if window_ms <= 45 * 24 * 60 * 60 * 1000:
        return 420
    return 500


def get_equity_history_view(since_ms: int = 0) -> Dict[str, Any]:
    normalized_since = max(0, int(since_ms or 0))
    cache_key = f"equity-history:{normalized_since}"
    cached = _cache_get(cache_key, max_age_sec=20.0)
    if cached is not None:
        return cached

    history = store.get_equity_history_sync(normalized_since)
    latest_ts = int(history[-1].get("ts", 0) or 0) if history else 0
    now_ms = int(time.time() * 1000)
    status = "partial"
    if history:
        age_ms = max(0, now_ms - latest_ts)
        if age_ms <= 10 * 60 * 1000:
            status = "fresh"
        elif age_ms <= 3 * 60 * 60 * 1000:
            status = "stale"
        else:
            status = "partial"
    downsampled = _downsample_series(history, _equity_target_points(normalized_since))
    snapshot_ts = latest_ts or now_ms
    return _cache_set(
        cache_key,
        _with_meta(
            {
                "history": downsampled,
                "point_count": len(downsampled),
                "point_count_raw": len(history),
                "range_since_ms": normalized_since,
            },
            status=status,
            snapshot_ts=snapshot_ts,
            last_updated_ms=snapshot_ts,
        ),
    )


def _read_ml_log_tail(limit: int = 40) -> List[str]:
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data",
        "ml_continuous_learning.log",
    )
    if not os.path.exists(path):
        return []
    try:
        capped_limit = max(1, min(int(limit), 200))
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = deque((line.rstrip() for line in handle), maxlen=capped_limit)
        return list(reversed([line for line in lines if str(line).strip()]))
    except Exception:
        return []


def _action_is_trade(row: Dict[str, Any]) -> bool:
    action_type = str(row.get("action_type", "") or "").strip().lower()
    side = str(row.get("side", "") or "").strip().lower()
    status = str(row.get("status", "") or "").strip().lower()
    if action_type in {"order_submitted", "synthetic_exit", "emergency_flatten"} and status == "success":
        return True
    return side in {"buy", "sell"} and status == "success"


def _action_is_buy(row: Dict[str, Any]) -> bool:
    side = str(row.get("side", "") or "").strip().lower()
    action_type = str(row.get("action_type", "") or "").strip().lower()
    status = str(row.get("status", "") or "").strip().lower()
    if side == "buy" and status == "success":
        return True
    return action_type in {"order_submitted"} and side == "buy" and status == "success"


def _safeguard_overview(limit: int = 600) -> Dict[str, Any]:
    capped_limit = max(120, min(int(limit or 600), 2000))
    cache_key = f"safeguard-overview:{capped_limit}"
    cached = _cache_get(cache_key, max_age_sec=15.0)
    if cached is not None:
        return cached

    now_ms = int(time.time() * 1000)
    one_hour_ms = now_ms - (60 * 60 * 1000)
    one_day_ms = now_ms - (24 * 60 * 60 * 1000)
    actions = store.list_actions_page_sync(limit=capped_limit)
    recent_actions = [row for row in actions if int(row.get("ts", 0) or 0) >= one_day_ms]
    trade_rows = [row for row in recent_actions if _action_is_trade(row)]
    buy_rows = [row for row in recent_actions if _action_is_buy(row)]
    trades_1h = sum(1 for row in trade_rows if int(row.get("ts", 0) or 0) >= one_hour_ms)
    buys_1h = sum(1 for row in buy_rows if int(row.get("ts", 0) or 0) >= one_hour_ms)
    buys_by_symbol = Counter(str(row.get("symbol", "") or "").upper() for row in buy_rows if str(row.get("symbol", "") or "").strip())
    same_symbol_gaps: List[float] = []
    symbol_buy_times: Dict[str, List[int]] = {}
    for row in reversed(buy_rows):
        symbol = str(row.get("symbol", "") or "").upper()
        if not symbol:
            continue
        symbol_buy_times.setdefault(symbol, []).append(int(row.get("ts", 0) or 0))
    for timestamps in symbol_buy_times.values():
        if len(timestamps) < 2:
            continue
        for idx in range(1, len(timestamps)):
            same_symbol_gaps.append(max(0.0, (timestamps[idx] - timestamps[idx - 1]) / 60000.0))

    block_reason_counts: Counter[str] = Counter()
    for row in recent_actions:
        if str(row.get("status", "") or "").strip().lower() != "blocked":
            continue
        reason = str(row.get("reason", "") or "").strip().lower() or "unknown"
        block_reason_counts[reason] += 1
    for row in store.list_brain_decision_traces_sync(limit=200):
        if int(row.get("ts", 0) or 0) < one_day_ms or bool(row.get("submitted", False)):
            continue
        reason = str(row.get("block_reason", "") or "").strip().lower() or "unknown"
        block_reason_counts[reason] += 1

    governor_history = store.list_governor_mode_history_sync(limit=24, since_ms=one_day_ms)
    latest_governor = governor_history[0] if governor_history else {}
    runtime_snapshot = get_bot_status_snapshot()
    effective_profile = dict(((runtime_snapshot.get("persisted") or {}).get("effective_risk_profile") or {}))
    snapshot_ts = _latest_ms(
        recent_actions[0].get("ts") if recent_actions else 0,
        latest_governor.get("ts") if latest_governor else 0,
    )
    payload = {
        "trades_last_hour": trades_1h,
        "buys_last_hour": buys_1h,
        "trades_last_24h": len(trade_rows),
        "avg_minutes_between_same_symbol_buys": round(sum(same_symbol_gaps) / max(1, len(same_symbol_gaps)), 2) if same_symbol_gaps else None,
        "max_same_symbol_buy_gap_min": round(max(same_symbol_gaps), 2) if same_symbol_gaps else None,
        "buys_by_symbol_24h": dict(buys_by_symbol.most_common(8)),
        "top_block_reasons_24h": [{"reason": reason, "count": count} for reason, count in block_reason_counts.most_common(8)],
        "effective_risk_profile": effective_profile,
        "governor": {
            "mode": str(latest_governor.get("mode", ((runtime_snapshot.get("runtime") or {}).get("activity_governor") or {}).get("mode", "normal")) or "normal"),
            "pressure_score": float(latest_governor.get("pressure_score", ((runtime_snapshot.get("runtime") or {}).get("activity_governor") or {}).get("pressure_score", 0.0)) or 0.0),
            "effectiveness_score": float(latest_governor.get("effectiveness_score", 0.0) or 0.0),
            "confidence_score": float(latest_governor.get("confidence_score", 0.0) or 0.0),
            "stability_score": float(latest_governor.get("stability_score", 0.0) or 0.0),
        },
        "limits": {
            "max_trades_per_hour": int(effective_profile.get("max_trades_per_hour", 0) or 0),
            "max_trades_per_coin_per_day": int(effective_profile.get("max_trades_per_coin_per_day", 0) or 0),
            "trade_spacing_min": int(effective_profile.get("trade_spacing_min", 0) or 0),
            "cooldown_sec": int((runtime_snapshot.get("runtime") or {}).get("cooldown_sec", 0) or 0),
            "anti_spam_sec": int((runtime_snapshot.get("runtime") or {}).get("anti_spam_sec", 0) or 0),
        },
    }
    return _cache_set(
        cache_key,
        _with_meta(payload, status="fresh", snapshot_ts=snapshot_ts or now_ms, last_updated_ms=snapshot_ts or now_ms),
    )


def _block_counts_last_24h(limit: int = 500) -> Dict[str, int]:
    now_ms = int(time.time() * 1000)
    day_ago = now_ms - (24 * 60 * 60 * 1000)
    cache_key = f"block-counts:{max(1, min(int(limit), 1000))}"
    cached = _cache_get(cache_key, max_age_sec=15.0)
    if cached is not None:
        return {
            str(symbol): int(count or 0)
            for symbol, count in (cached.get("counts") or {}).items()
        }
    counts = store.get_block_counts_last_24h_sync(day_ago, limit=limit)
    payload = {"counts": counts, "snapshot_ts": now_ms, "last_updated_ms": now_ms}
    _cache_set(cache_key, payload)
    return counts


def _event_state_map(limit: int = 200) -> Dict[str, Dict[str, Any]]:
    cache_key = f"event-map:{max(1, min(int(limit), 500))}"
    cached = _cache_get(cache_key, max_age_sec=15.0)
    if cached is not None:
        items = cached.get("items") or {}
        return {
            str(symbol): dict(row)
            for symbol, row in items.items()
            if isinstance(row, dict)
        }
    items = {
        str(row.get("symbol", "") or "").upper(): row
        for row in store.list_symbol_event_states_sync(limit=limit)
        if str(row.get("symbol", "") or "").strip()
    }
    _cache_set(cache_key, {"items": items})
    return items


def _tracked_symbol_map(account_mode: str, *, include_idle: bool = False, limit: int = 600) -> Dict[str, Dict[str, Any]]:
    cache_key = f"tracked-map:{account_mode}:{1 if include_idle else 0}:{max(1, min(int(limit), 2000))}"
    cached = _cache_get(cache_key, max_age_sec=10.0)
    if cached is not None:
        return {
            str(symbol): dict(row)
            for symbol, row in (cached.get("items") or {}).items()
            if isinstance(row, dict)
        }
    try:
        items = {
            str(row.get("symbol", "") or "").upper(): row
            for row in store.list_tracked_symbols_sync(account_mode, include_idle=include_idle, limit=limit)
            if str(row.get("symbol", "") or "").strip()
        }
    except Exception as exc:
        logger.debug("[terminal_data] tracked symbol map unavailable: %s", exc)
        items = {}
    _cache_set(cache_key, {"items": items})
    return items


def _payload_refresh_sec(snapshot: Dict[str, Any]) -> int:
    payload = snapshot.get("payload", {}) or {}
    universe_cfg = payload.get("universe_cfg", {}) or {}
    try:
        return max(60, int(universe_cfg.get("refresh_sec", 900) or 900))
    except Exception:
        return 900


def _status_from_age_ms(ts_ms: int, refresh_sec: int) -> str:
    age_ms = max(0, int(time.time() * 1000) - int(ts_ms or 0))
    if age_ms <= refresh_sec * 1000:
        return "fresh"
    if age_ms <= refresh_sec * 3000:
        return "stale"
    return "partial"


def _cache_state_from_status(status: str) -> str:
    normalized = str(status or "partial").strip().lower()
    if normalized == "fresh":
        return "hot"
    if normalized == "stale":
        return "stale"
    if normalized == "error":
        return "error"
    return "warming"


def _latest_ms(*values: Any) -> int:
    latest = 0
    for value in values:
        try:
            current = int(value or 0)
        except Exception:
            current = 0
        if current > latest:
            latest = current
    return latest


def _iso_to_ms(value: Any) -> int:
    raw = str(value or "").strip()
    if not raw:
        return 0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _with_meta(
    payload: Dict[str, Any],
    *,
    status: Optional[str] = None,
    snapshot_ts: int = 0,
    last_updated_ms: int = 0,
) -> Dict[str, Any]:
    resolved_status = str(status or payload.get("status") or "partial")
    now_ms = int(time.time() * 1000)
    resolved_snapshot_ts = int(snapshot_ts or payload.get("snapshot_ts") or last_updated_ms or now_ms)
    resolved_last_updated = int(last_updated_ms or payload.get("last_updated_ms") or resolved_snapshot_ts)
    result = dict(payload)
    result["status"] = resolved_status
    result.setdefault("cache_state", _cache_state_from_status(resolved_status))
    result.setdefault("snapshot_ts", resolved_snapshot_ts)
    result.setdefault("last_updated_ms", resolved_last_updated)
    return result


def _with_cached_status(payload: Dict[str, Any], status: str) -> Dict[str, Any]:
    return _with_meta(
        dict(payload or {}),
        status=status,
        snapshot_ts=int((payload or {}).get("snapshot_ts", 0) or 0),
        last_updated_ms=int((payload or {}).get("last_updated_ms", 0) or 0),
    )


def _snapshot_mid_price(row: Dict[str, Any]) -> float:
    payload = row.get("payload", {}) or {}
    nested_payload = payload.get("payload", {}) or {}
    return float(
        row.get(
            "mid_price",
            payload.get(
                "mid_price",
                nested_payload.get("mid_price", 0.0),
            ),
        )
        or 0.0
    )


def _latest_universe_snapshot(
    *,
    limit_rankings: Optional[int] = None,
    selected_only: bool = False,
) -> Optional[Dict[str, Any]]:
    snapshot = store.get_latest_universe_snapshot_sync(
        limit_rankings=limit_rankings,
        selected_only=selected_only,
    )
    if not snapshot:
        return None
    snapshot["ts_ms"] = int(snapshot.get("ts", 0) or 0)
    snapshot["status"] = _status_from_age_ms(snapshot["ts_ms"], _payload_refresh_sec(snapshot))
    return snapshot


def _lookup_snapshot_rank(snapshot: Optional[Dict[str, Any]], symbol: str) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    if not snapshot or not sym:
        return {}
    for row in snapshot.get("rankings", []) or []:
        if str(row.get("symbol", "")).upper() == sym:
            return row
    return {}


def _schedule_background_refresh(name: str, fn, *args: Any, min_interval_sec: int = 30, **kwargs: Any) -> None:
    now_sec = time.time()
    with _REFRESH_LOCK:
        if name in _REFRESH_IN_FLIGHT:
            return
        last_start = float(_REFRESH_LAST_START.get(name, 0.0) or 0.0)
        if (now_sec - last_start) < float(min_interval_sec):
            return
        _REFRESH_IN_FLIGHT.add(name)
        _REFRESH_LAST_START[name] = now_sec

    def _runner() -> None:
        try:
            fn(*args, **kwargs)
        except Exception as exc:
            logger.debug("[terminal_data] background refresh %s failed: %s", name, exc)
        finally:
            with _REFRESH_LOCK:
                _REFRESH_IN_FLIGHT.discard(name)

    threading.Thread(target=_runner, daemon=True, name=f"crypto-refresh-{name}").start()


def _refresh_universe_snapshot() -> None:
    cfg = current_crypto_config()
    mode = str(cfg.get("account_mode", "paper") or "paper")
    held_symbols = [str(row.get("symbol", "") or "") for row in market_data.get_cached_crypto_positions(max_age_sec=900)]
    select_trade_universe(cfg, mode, held_symbols=held_symbols)


def _refresh_learning_snapshots() -> None:
    refresh_symbol_policy_overlays(force=True)
    ensure_brain_self_score_snapshot(force=True)


def _refresh_account_summary() -> None:
    mode = str(_current_shell_config().get("account_mode", "paper") or "paper")
    market_data.get_account_summary(mode=mode)


def _positions_cache_ready(max_age_sec: float = 1800.0) -> bool:
    try:
        ts_sec = float(getattr(market_data, "_position_cache_ts", 0.0) or 0.0)
    except Exception:
        ts_sec = 0.0
    if ts_sec <= 0:
        return False
    return (time.time() - ts_sec) <= float(max_age_sec)


def _refresh_positions_cache() -> None:
    mode = str(_current_shell_config().get("account_mode", "paper") or "paper")
    market_data.get_crypto_positions(mode=mode)


def _warm_symbol_history_cache(symbol: str, timeframe: str, limit: int) -> None:
    market_data.fetch_bars(symbol, timeframe=timeframe, limit=limit)


def _activity_summary(limit: int = 120) -> Dict[str, Any]:
    capped_limit = max(1, min(int(limit), 400))
    cache_key = f"activity-summary:{capped_limit}"
    cached = _cache_get(cache_key, max_age_sec=10.0)
    if cached is not None:
        return cached

    recent_actions = store.list_actions_page_sync(limit=capped_limit)
    blocked_error_count = sum(
        1 for row in recent_actions if str(row.get("status", "")).lower() in {"blocked", "error"}
    )
    duplicate_suppression_count = sum(
        1
        for row in recent_actions
        if "duplicate" in str(row.get("reason", "")).lower()
        or "duplicate" in str(row.get("action_type", "")).lower()
    )
    cache_fallback_count = sum(
        1
        for row in recent_actions
        if any(
            token in str(row.get("reason", "")).lower()
            or token in str(row.get("action_type", "")).lower()
            for token in ("cache", "fallback", "stale", "fetch_error")
        )
    )
    latest_action = recent_actions[0] if recent_actions else None
    snapshot_ts = int(latest_action.get("ts", 0) or 0) if latest_action else int(time.time() * 1000)
    return _cache_set(
        cache_key,
        _with_meta(
            {
                "recent_count": len(recent_actions),
                "blocked_or_error_count": blocked_error_count,
                "duplicate_suppression_count": duplicate_suppression_count,
                "cache_fallback_count": cache_fallback_count,
                "latest_action": latest_action,
            },
            status="fresh",
            snapshot_ts=snapshot_ts,
            last_updated_ms=snapshot_ts,
        ),
    )


def _warm_shell_views() -> None:
    started_ms = int(time.time() * 1000)
    _set_shell_warm_phase("warming", started_ms=started_ms)
    try:
        _refresh_account_summary()
        _set_shell_warm_view("account", "fresh", started_ms)
    except Exception as exc:
        _set_shell_warm_view("account", "partial", started_ms)
        logger.debug("[terminal_data] shell warm account refresh failed: %s", exc)
    try:
        _refresh_positions_cache()
        _set_shell_warm_view("positions", "fresh", started_ms)
    except Exception as exc:
        _set_shell_warm_view("positions", "partial", started_ms)
        logger.debug("[terminal_data] shell warm positions refresh failed: %s", exc)
    try:
        _refresh_universe_snapshot()
        _set_shell_warm_view("universe-source", "fresh", started_ms)
    except Exception as exc:
        _set_shell_warm_view("universe-source", "partial", started_ms)
        logger.debug("[terminal_data] shell warm universe refresh failed: %s", exc)
    try:
        _refresh_learning_snapshots()
        _set_shell_warm_view("learning-source", "fresh", started_ms)
    except Exception as exc:
        _set_shell_warm_view("learning-source", "partial", started_ms)
        logger.debug("[terminal_data] shell warm learning refresh failed: %s", exc)

    view_errors: List[str] = []
    for name, builder in (
        ("scanner", lambda: get_scanner(10)),
        ("universe", lambda: get_universe_view(30)),
        ("learning-overview", get_learning_overview),
        ("brain-overview", get_brain_overview),
    ):
        try:
            payload = builder()
            _set_shell_warm_view(name, str((payload or {}).get("status", "fresh") or "fresh"), int((payload or {}).get("snapshot_ts", started_ms) or started_ms))
        except Exception as exc:
            view_errors.append(f"{name}: {exc}")
            _set_shell_warm_view(name, "partial", started_ms)
            logger.debug("[terminal_data] shell warm %s failed: %s", name, exc)

    completed_ms = int(time.time() * 1000)
    _set_shell_warm_phase(
        "ready" if not view_errors else "partial",
        error="; ".join(view_errors),
        completed_ms=completed_ms,
    )


def schedule_shell_warmup() -> None:
    _schedule_background_refresh("shell-warmup", _warm_shell_views, min_interval_sec=15)


async def get_crypto_health() -> Dict[str, Any]:
    service_state = background_services.service_status()
    account_mode = str(_current_shell_config().get("account_mode", "paper") or "paper")
    cached_account = market_data.get_cached_account_summary(max_age_sec=1800)
    shell_cache = get_shell_warm_status()
    status = "fresh"
    if cached_account:
        cache_age_sec = float(cached_account.get("cache_age_sec", 0.0) or 0.0)
        provider = {
            "status": "fresh" if cache_age_sec <= 120.0 else "partial",
            "account_mode": account_mode,
            "equity": float(cached_account.get("equity", 0.0) or 0.0),
            "cash": float(cached_account.get("cash", 0.0) or 0.0),
            "cached": True,
            "cache_age_sec": round(cache_age_sec, 1),
        }
        if provider["status"] != "fresh":
            status = "partial"
            _schedule_background_refresh("account-summary", _refresh_account_summary, min_interval_sec=30)
    else:
        provider = {
            "status": "partial",
            "account_mode": account_mode,
            "detail": "account cache cold",
        }
        status = "partial"
        _schedule_background_refresh("account-summary", _refresh_account_summary, min_interval_sec=10)

    if service_state.get("errors") and status == "fresh":
        status = "partial"
    if str(shell_cache.get("phase", "cold")).lower() not in {"ready", "fresh"} and status == "fresh":
        status = "partial"

    return {
        "status": status,
        "provider": "alpaca",
        "account_mode": account_mode,
        "services": service_state,
        "provider_state": provider,
        "shell_cache": shell_cache,
    }


async def get_terminal_summary(prefer_cached: bool = True) -> Dict[str, Any]:
    from .oracle import get_current_oracle_state, get_narrative_status, get_oracle_status
    from .report_generator import get_cached_report

    overall_started = time.perf_counter()

    cache_key = f"terminal-summary:{1 if prefer_cached else 0}"
    if prefer_cached:
        cached = _cache_get(cache_key, max_age_sec=10.0)
        if cached is not None:
            return cached

    cached_account = market_data.get_cached_account_summary(max_age_sec=1800) if prefer_cached else None
    cached_positions = market_data.get_cached_crypto_positions(max_age_sec=1800) if prefer_cached else None
    fast_timeout = 1.5 if prefer_cached else 3.0
    account_cold = prefer_cached and not bool(cached_account)
    positions_ready = prefer_cached and _positions_cache_ready()
    positions_cold = prefer_cached and not positions_ready

    async def _load_account() -> Dict[str, Any]:
        started = time.perf_counter()
        if prefer_cached:
            if cached_account:
                _summary_profiler_log("load_account.cached", started)
                return cached_account
            _schedule_background_refresh("account-summary", _refresh_account_summary, min_interval_sec=10)
            payload = {
                "cash": 0.0,
                "equity": 0.0,
                "buying_power": 0.0,
                "portfolio_value": 0.0,
                "status": "warming",
                "cached": True,
                "cache_age_sec": None,
            }
            _summary_profiler_log("load_account.warming", started)
            return payload
        try:
            payload = await asyncio.wait_for(asyncio.to_thread(get_account), timeout=fast_timeout)
            _summary_profiler_log("load_account.live", started)
            return payload
        except Exception:
            payload = cached_account or market_data.get_cached_account_summary() or {
                "cash": 0.0,
                "equity": 0.0,
                "buying_power": 0.0,
                "portfolio_value": 0.0,
                "status": "unavailable",
                "cached": True,
            }
            _summary_profiler_log("load_account.fallback", started)
            return payload

    async def _load_positions() -> List[Dict[str, Any]]:
        started = time.perf_counter()
        if prefer_cached:
            if positions_ready:
                _summary_profiler_log("load_positions.cached", started)
                return cached_positions
            _schedule_background_refresh("positions-cache", _refresh_positions_cache, min_interval_sec=10)
            _summary_profiler_log("load_positions.warming", started)
            return []
        try:
            payload = await asyncio.wait_for(asyncio.to_thread(get_positions), timeout=fast_timeout)
            _summary_profiler_log("load_positions.live", started)
            return payload
        except Exception:
            payload = cached_positions or market_data.get_cached_crypto_positions() or []
            _summary_profiler_log("load_positions.fallback", started)
            return payload

    async def _load_bot_status() -> Dict[str, Any]:
        started = time.perf_counter()
        payload = get_bot_status_snapshot() if prefer_cached else await get_bot_status()
        _summary_profiler_log("load_bot_status", started)
        return payload

    if prefer_cached:
        account_data = await _load_account()
        position_items = await _load_positions()
        bot_status = await _load_bot_status()
    else:
        account_data, position_items, bot_status = await asyncio.gather(
            _load_account(),
            _load_positions(),
            _load_bot_status(),
        )
    _summary_profiler_log("account+positions+bot_status", overall_started)

    block_started = time.perf_counter()
    brain_score = store.get_latest_brain_self_score_sync() or {}
    latest_universe = _latest_universe_snapshot(limit_rankings=0)
    cfg = _current_shell_config()
    _summary_profiler_log("brain+universe+cfg", block_started)
    if not brain_score:
        _schedule_background_refresh("learning-snapshots", _refresh_learning_snapshots, min_interval_sec=120)
    if latest_universe is None:
        _schedule_background_refresh("universe-snapshot", _refresh_universe_snapshot, min_interval_sec=45)
        latest_universe = {
            "symbols": [],
            "rankings": [],
            "promoted_symbols": [],
            "recently_active_symbols": [],
            "ts_ms": int(time.time() * 1000),
            "status": "partial",
        }
    elif latest_universe.get("status") != "fresh":
        _schedule_background_refresh("universe-snapshot", _refresh_universe_snapshot, min_interval_sec=45)
    tracked_map = _tracked_symbol_map(str(cfg.get("account_mode", "paper") or "paper"), include_idle=False, limit=400)
    tracked_rows = list(tracked_map.values())
    tracked_counts = {
        "active": sum(1 for row in tracked_rows if str(row.get("active_state", "idle") or "idle") == "active"),
        "recently_active": sum(1 for row in tracked_rows if str(row.get("active_state", "idle") or "idle") == "recently_active"),
        "saved": sum(1 for row in tracked_rows if bool(row.get("saved_manual"))),
    }
    tracked_preview = [
        str(row.get("symbol", "") or "").upper()
        for row in tracked_rows
        if str(row.get("symbol", "") or "").strip()
    ][:12]

    block_started = time.perf_counter()
    activity_summary = _activity_summary(limit=120)
    _summary_profiler_log("activity_summary", block_started)
    latest_action = activity_summary.get("latest_action")
    net_unrealized = sum(float(row.get("unrealized_pl", 0.0) or 0.0) for row in position_items)
    total_exposure = sum(abs(float(row.get("market_value", 0.0) or 0.0)) for row in position_items)
    best_position = max(position_items, key=lambda row: float(row.get("unrealized_plpc", 0.0) or 0.0), default=None)
    worst_position = min(position_items, key=lambda row: float(row.get("unrealized_plpc", 0.0) or 0.0), default=None)
    account_cache_age_sec = float(account_data.get("cache_age_sec", 0.0) or 0.0) if bool(account_data.get("cached")) else 0.0
    status = "fresh"
    if account_cold or positions_cold or latest_universe.get("status") != "fresh":
        status = "partial"
    elif bool(account_data.get("cached")) and account_cache_age_sec > 120.0:
        status = "partial"
    elif not brain_score:
        status = "partial"
    feedback_cfg = cfg.get("decision_feedback", {}) or {}
    runtime = dict(bot_status.get("runtime", {}) or {})
    persisted = dict(bot_status.get("persisted", {}) or {})
    governor = dict(runtime.get("activity_governor", {}) or {})
    account_last_updated = 0
    if bool(account_data.get("cached")):
        cache_age_sec = float(account_data.get("cache_age_sec", 0.0) or 0.0)
        account_last_updated = int(time.time() * 1000 - (cache_age_sec * 1000))
    summary_snapshot_ts = _latest_ms(
        latest_universe.get("ts_ms"),
        brain_score.get("ts"),
        latest_action.get("ts") if isinstance(latest_action, dict) else 0,
        account_last_updated,
    )

    block_started = time.perf_counter()
    oracle_summary = {
        "oracle": get_oracle_status(),
        "derived_state": get_current_oracle_state(),
        "narratives": get_narrative_status(),
    }
    reports = {
        "hourly": get_cached_report("hourly"),
        "daily": get_cached_report("daily"),
    }
    _summary_profiler_log("oracle+reports", block_started)

    block_started = time.perf_counter()
    payload = _cache_set(cache_key, _with_meta({
        "account": account_data,
        "positions": position_items,
        "positions_summary": {
            "count": len(position_items),
            "net_unrealized": round(net_unrealized, 2),
            "total_exposure": round(total_exposure, 2),
            "best_symbol": best_position.get("symbol") if best_position else None,
            "worst_symbol": worst_position.get("symbol") if worst_position else None,
        },
        "config_summary": {
            "active_brain": cfg.get("active_brain", "drl_event_fusion"),
            "trading_mode": cfg.get("trading_mode", "offline"),
            "account_mode": cfg.get("account_mode", "paper"),
            "enabled": bool(cfg.get("enabled", True)),
        },
        "bot_config_summary": {
            "active_brain": cfg.get("active_brain", "drl_event_fusion"),
            "trading_mode": cfg.get("trading_mode", "offline"),
            "account_mode": cfg.get("account_mode", "paper"),
            "enabled": bool(cfg.get("enabled", True)),
            "runtime_running": bool(runtime.get("running", persisted.get("running", False))),
            "governor_mode": str(governor.get("mode", "normal") or "normal"),
            "risk_state": str(persisted.get("risk_state", "normal") or "normal"),
            "risk_source": str(persisted.get("risk_source", "") or ""),
            "feedback_horizons_min": list(feedback_cfg.get("horizons_min", _DEFAULT_FEEDBACK_HORIZONS)),
            "live_retrain_interval_days": int(cfg.get("live_retrain_interval_days", 3) or 3),
            "universe_refresh_sec": _payload_refresh_sec(latest_universe),
            "poll_interval_sec": int(cfg.get("poll_interval_sec", 30) or 30),
            "cooldown_sec": int(cfg.get("cooldown_sec", 90) or 90),
            "anti_spam_sec": int(cfg.get("anti_spam_sec", 30) or 30),
            "max_open_positions": int(cfg.get("max_open_positions", 10) or 10),
            "max_total_exposure": float(cfg.get("max_total_exposure", 50000.0) or 50000.0),
            "max_notional_per_trade": float(cfg.get("max_notional_per_trade", 5000.0) or 5000.0),
            "auto_discover_pairs": bool(cfg.get("auto_discover_pairs", False)),
            "tracked_symbol_count": len(tracked_rows),
            "tracked_symbols_preview": tracked_preview,
            "tracked_state_counts": tracked_counts,
        },
        "bot_status": bot_status,
        "oracle_summary": oracle_summary,
        "brain_self_score": brain_score,
        "activity_summary": activity_summary,
        "reports": reports,
        "universe": latest_universe,
        "services": background_services.service_status(),
        "shell_cache": get_shell_warm_status(),
    }, status=status, snapshot_ts=summary_snapshot_ts, last_updated_ms=summary_snapshot_ts))
    _summary_profiler_log("cache_set", block_started)
    return payload


def get_scanner(limit: int = 10) -> Dict[str, Any]:
    capped_limit = max(1, min(int(limit), 20))
    cfg = _current_shell_config()
    account_mode = str(cfg.get("account_mode", "paper") or "paper")
    tracked_map = _tracked_symbol_map(account_mode, include_idle=False, limit=600)
    active_symbols = [
        symbol
        for symbol, row in tracked_map.items()
        if str(row.get("active_state", "idle") or "idle").strip().lower() == "active"
    ]
    cache_key = f"scanner:{capped_limit}:{account_mode}:{','.join(active_symbols[:40])}"
    cached = _cache_get(cache_key, max_age_sec=15.0)
    if cached is not None:
        return cached
    snapshot = _latest_universe_snapshot(limit_rankings=max(capped_limit * 4, 60), selected_only=False)
    if snapshot is None:
        _schedule_background_refresh("universe-snapshot", _refresh_universe_snapshot, min_interval_sec=45)
        cold_ts = int(time.time() * 1000)
        return _with_meta({"items": [], "count": 0, "ts": cold_ts}, status="partial", snapshot_ts=cold_ts, last_updated_ms=cold_ts)
    if snapshot.get("status") != "fresh":
        _schedule_background_refresh("universe-snapshot", _refresh_universe_snapshot, min_interval_sec=45)

    positions = market_data.get_cached_crypto_positions(max_age_sec=900)
    position_map = {
        str(row.get("symbol", "") or "").upper(): row
        for row in positions
        if str(row.get("symbol", "") or "").strip()
    }
    overlay_map = get_overlay_map(refresh=False)
    event_map = _event_state_map(limit=200)
    block_counts = _block_counts_last_24h(limit=600)

    snapshot_id = int(snapshot.get("id", 0) or 0)
    ranking_lookup: Dict[str, Dict[str, Any]] = {
        str(row.get("symbol", "") or "").upper(): row
        for row in (snapshot.get("rankings", []) or [])
    }
    ordered_symbols: List[str] = []
    for symbol in active_symbols:
        if symbol and symbol not in ordered_symbols:
            ordered_symbols.append(symbol)
    for symbol in snapshot.get("selected_symbols", []) or []:
        normalized = str(symbol or "").upper()
        if normalized and normalized not in ordered_symbols:
            ordered_symbols.append(normalized)
    for row in snapshot.get("rankings", []) or []:
        symbol = str(row.get("symbol", "") or "").upper()
        tracked = tracked_map.get(symbol, {})
        if str(tracked.get("active_state", "idle") or "idle").strip().lower() != "active":
            continue
        if symbol and symbol not in ordered_symbols:
            ordered_symbols.append(symbol)

    effective_limit = max(capped_limit, min(25, len(active_symbols) + max(0, capped_limit)))
    items: List[Dict[str, Any]] = []
    for symbol in ordered_symbols[:effective_limit]:
        row = ranking_lookup.get(symbol)
        if row is None and snapshot_id > 0:
            row = store.get_universe_ranking_for_symbol_sync(snapshot_id, symbol)
        tracked = tracked_map.get(symbol, {})
        row = row or {"symbol": symbol, "bucket": "tracked", "selected": False}
        overlay = overlay_map.get(symbol, {})
        event_state = event_map.get(symbol, {})
        quote = market_data.get_cached_latest_quote(symbol, mode=account_mode, allow_stale=True) or {}
        position = position_map.get(symbol)
        items.append(
            {
                "symbol": symbol,
                "price": float(quote.get("mid_price", _snapshot_mid_price(row)) or 0.0),
                "conviction_score": float(row.get("total_rank", 0.0) or 0.0),
                "edge_weight": float(row.get("historical_edge_score", 0.0) or 0.0),
                "event_bias": str(event_state.get("event_bias", "neutral") or "neutral"),
                "event_score": float(event_state.get("net_event_score", 0.0) or 0.0),
                "active_narrative": str(event_state.get("top_event_type", "") or ""),
                "blocked_trades_24h": int(block_counts.get(symbol, 0)),
                "overlay": overlay,
                "has_position": bool(position),
                "position_unrealized": float(position.get("unrealized_pl", 0.0) or 0.0) if position else 0.0,
                "saved": bool(tracked.get("saved_manual")),
                "watchlisted": bool(tracked.get("saved_manual")),
                "active_state": str(tracked.get("active_state", "idle") or "idle"),
                "active_reason": str(tracked.get("active_reason", "") or ""),
                "monitor_tier": str(tracked.get("monitor_tier", "eligible") or "eligible"),
                "news_context_state": str(event_state.get("news_context_state", "unavailable") or "unavailable"),
            }
        )

    snapshot_ts = int(snapshot.get("ts_ms", time.time() * 1000) or 0)
    return _cache_set(
        cache_key,
        _with_meta(
        {"items": items, "count": len(items), "ts": snapshot_ts},
        status=snapshot.get("status", "fresh"),
        snapshot_ts=snapshot_ts,
        last_updated_ms=snapshot_ts,
        ),
    )


def get_universe_view(limit: int = 30) -> Dict[str, Any]:
    capped_limit = max(1, min(int(limit), 50))
    cached = _cache_get(f"universe:{capped_limit}", max_age_sec=20.0)
    if cached is not None:
        return cached
    snapshot = _latest_universe_snapshot(limit_rankings=max(capped_limit, 30))
    if snapshot is None:
        _schedule_background_refresh("universe-snapshot", _refresh_universe_snapshot, min_interval_sec=45)
        cold_ts = int(time.time() * 1000)
        return _with_meta({
            "items": [],
            "selected_symbols": [],
            "promoted_symbols": [],
            "ts": cold_ts,
        }, status="partial", snapshot_ts=cold_ts, last_updated_ms=cold_ts)
    if snapshot.get("status") != "fresh":
        _schedule_background_refresh("universe-snapshot", _refresh_universe_snapshot, min_interval_sec=45)

    overlay_map = get_overlay_map(refresh=False)
    event_map = _event_state_map(limit=200)
    block_counts = _block_counts_last_24h(limit=800)
    tracked_map = _tracked_symbol_map(str(_current_shell_config().get("account_mode", "paper") or "paper"), include_idle=False, limit=600)

    rows: List[Dict[str, Any]] = []
    for row in snapshot.get("rankings", [])[:capped_limit]:
        symbol = str(row.get("symbol", "") or "").upper()
        overlay = overlay_map.get(symbol, {})
        event_state = event_map.get(symbol, {})
        tracked = tracked_map.get(symbol, {})
        rows.append(
            {
                "symbol": symbol,
                "bucket": row.get("bucket", "discovery"),
                "selected": bool(row.get("selected", False)),
                "conviction_score": float(row.get("total_rank", 0.0) or 0.0),
                "liquidity_score": float(row.get("liquidity_score", 0.0) or 0.0),
                "event_score": float(row.get("event_score", 0.0) or 0.0),
                "historical_edge_score": float(row.get("historical_edge_score", 0.0) or 0.0),
                "overlay_score": float(row.get("overlay_score", 0.0) or 0.0),
                "overlay": overlay,
                "event_bias": str(event_state.get("event_bias", "neutral") or "neutral"),
                "active_narrative": str(event_state.get("top_event_type", "") or ""),
                "blocked_trades_24h": int(block_counts.get(symbol, 0)),
                "freshness": "fresh" if snapshot.get("status") == "fresh" else "stale" if snapshot.get("status") == "stale" else "partial",
                "saved": bool(tracked.get("saved_manual")),
                "active_state": str(tracked.get("active_state", "idle") or "idle"),
                "active_reason": str(tracked.get("active_reason", "") or ""),
                "monitor_tier": str(tracked.get("monitor_tier", "eligible") or "eligible"),
                "news_context_state": str(event_state.get("news_context_state", "unavailable") or "unavailable"),
            }
        )

    snapshot_ts = int(snapshot.get("ts_ms", time.time() * 1000) or 0)
    return _cache_set(
        f"universe:{capped_limit}",
        _with_meta({
        "items": rows,
        "selected_symbols": snapshot.get("selected_symbols", snapshot.get("symbols", [])),
        "promoted_symbols": snapshot.get("promoted_symbols", []),
        "recently_active_symbols": (snapshot.get("payload", {}) or {}).get("recently_active_symbols", snapshot.get("recently_active_symbols", [])),
        "ts": snapshot_ts,
        }, status=snapshot.get("status", "fresh"), snapshot_ts=snapshot_ts, last_updated_ms=snapshot_ts),
    )


def get_tracked_coins(limit: int = 80) -> Dict[str, Any]:
    capped_limit = max(1, min(int(limit), 200))
    cfg = _current_shell_config()
    account_mode = str(cfg.get("account_mode", "paper") or "paper")
    cache_key = f"tracked-coins:{account_mode}:{capped_limit}"
    cached = _cache_get(cache_key, max_age_sec=12.0)
    if cached is not None:
        return cached
    tracked_rows = store.list_tracked_symbols_sync(account_mode, include_idle=False, limit=max(capped_limit * 4, 300))
    tracked_map = {
        str(row.get("symbol", "") or "").upper(): row
        for row in tracked_rows
        if str(row.get("symbol", "") or "").strip()
    }
    snapshot = _latest_universe_snapshot(limit_rankings=max(capped_limit * 4, 80))
    ranking_lookup = {
        str(row.get("symbol", "") or "").upper(): row
        for row in ((snapshot or {}).get("rankings", []) or [])
    }
    event_map = _event_state_map(limit=300)
    positions = {
        str(row.get("symbol", "") or "").upper(): row
        for row in market_data.get_cached_crypto_positions(max_age_sec=900)
        if str(row.get("symbol", "") or "").strip()
    }
    items: List[Dict[str, Any]] = []
    for symbol, tracked in tracked_map.items():
        row = ranking_lookup.get(symbol, {})
        event_state = event_map.get(symbol, {})
        quote = market_data.get_cached_latest_quote(symbol, mode=account_mode, allow_stale=True) or {}
        active_state = str(tracked.get("active_state", "idle") or "idle")
        items.append(
            {
                "symbol": symbol,
                "saved": bool(tracked.get("saved_manual")),
                "active_state": active_state,
                "active_reason": str(tracked.get("active_reason", "") or ""),
                "monitor_tier": str(tracked.get("monitor_tier", "eligible") or "eligible"),
                "active_since": int(tracked.get("active_since_ts", 0) or 0),
                "active_min_until": int(tracked.get("active_min_until_ts", 0) or 0),
                "recent_until": int(tracked.get("recent_until_ts", 0) or 0),
                "last_rank_score": float(tracked.get("last_rank_score", 0.0) or 0.0),
                "price": float(quote.get("mid_price", _snapshot_mid_price(row)) or 0.0),
                "conviction_score": float(row.get("total_rank", tracked.get("last_rank_score", 0.0)) or 0.0),
                "event_bias": str(event_state.get("event_bias", "neutral") or "neutral"),
                "news_context_state": str(event_state.get("news_context_state", "unavailable") or "unavailable"),
                "has_position": bool(positions.get(symbol)),
            }
        )
    items.sort(
        key=lambda item: (
            2 if item.get("active_state") == "active" else 1 if item.get("active_state") == "recently_active" else 0,
            1 if item.get("saved") else 0,
            float(item.get("conviction_score", 0.0) or 0.0),
        ),
        reverse=True,
    )
    snapshot_ts = int(snapshot.get("ts_ms", time.time() * 1000) or time.time() * 1000) if snapshot else int(time.time() * 1000)
    return _cache_set(
        cache_key,
        _with_meta(
            {
                "items": items[:capped_limit],
                "count": len(items),
                "active_count": sum(1 for item in items if item.get("active_state") == "active"),
                "recent_count": sum(1 for item in items if item.get("active_state") == "recently_active"),
                "saved_count": sum(1 for item in items if item.get("saved")),
                "ts": snapshot_ts,
            },
            status="fresh",
            snapshot_ts=snapshot_ts,
            last_updated_ms=snapshot_ts,
        ),
    )


def get_asset_catalog(query: str = "", limit: int = 40, tradable_only: bool = True) -> Dict[str, Any]:
    capped_limit = max(1, min(int(limit), 150))
    cfg = _current_shell_config()
    account_mode = str(cfg.get("account_mode", "paper") or "paper")
    raw_query = str(query or "").strip().upper()
    cache_key = f"asset-catalog:{account_mode}:{raw_query}:{1 if tradable_only else 0}:{capped_limit}"
    cached = _cache_get(cache_key, max_age_sec=10.0)
    if cached is not None:
        return cached
    tracked_map = _tracked_symbol_map(account_mode, include_idle=False, limit=1000)
    event_map = _event_state_map(limit=500)
    assets = market_data.list_crypto_assets(limit=300, mode=account_mode)
    items: List[Dict[str, Any]] = []
    for asset in assets:
        symbol = _normalize_symbol(asset.get("symbol", ""))
        if not symbol:
            continue
        if tradable_only and not bool(asset.get("tradable", True)):
            continue
        name = str(asset.get("name", "") or "")
        haystack = f"{symbol} {name}".upper()
        if raw_query and raw_query not in haystack:
            continue
        tracked = tracked_map.get(symbol, {})
        event_state = event_map.get(symbol, {})
        items.append(
            {
                "symbol": symbol,
                "name": name or symbol,
                "tradable": bool(asset.get("tradable", True)),
                "status": str(asset.get("status", "active") or "active"),
                "saved": bool(tracked.get("saved_manual")),
                "active_state": str(tracked.get("active_state", "idle") or "idle"),
                "active_reason": str(tracked.get("active_reason", "") or ""),
                "monitor_tier": str(tracked.get("monitor_tier", "eligible") or "eligible"),
                "news_context_state": str(event_state.get("news_context_state", "unavailable") or "unavailable"),
            }
        )
    items.sort(
        key=lambda item: (
            2 if item.get("active_state") == "active" else 1 if item.get("active_state") == "recently_active" else 0,
            1 if item.get("saved") else 0,
            item.get("symbol", ""),
        ),
        reverse=True,
    )
    snapshot_ts = int(time.time() * 1000)
    return _cache_set(
        cache_key,
        _with_meta(
            {
                "items": items[:capped_limit],
                "count": len(items),
                "query": raw_query,
                "ts": snapshot_ts,
            },
            status="fresh",
            snapshot_ts=snapshot_ts,
            last_updated_ms=snapshot_ts,
        ),
    )


def _cached_symbol_action_preview(symbol: str, limit: int = 25) -> List[Dict[str, Any]]:
    sym = _normalize_symbol(symbol)
    capped = max(1, min(int(limit), 100))
    cache_key = f"symbol-actions:{sym}:{capped}"
    cached = _cache_get(cache_key, max_age_sec=15.0)
    if cached is not None:
        return list(cached.get("items") or [])
    items = store.list_actions_page_sync(limit=capped, symbol=sym)
    snapshot_ts = _latest_ms(items[0].get("ts") if items else 0)
    _cache_set(
        cache_key,
        _with_meta({"items": items}, status="fresh", snapshot_ts=snapshot_ts, last_updated_ms=snapshot_ts),
    )
    return items


def _resolve_symbol_quote(
    symbol: str,
    *,
    latest_universe: Optional[Dict[str, Any]] = None,
    positions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    quote = market_data.get_cached_latest_quote(sym, allow_stale=True) or {}
    if quote:
        return quote
    position_items = list(positions or market_data.get_cached_crypto_positions(max_age_sec=900))
    position = next((p for p in position_items if str(p.get("symbol", "")).upper() == sym), None)
    if position and float(position.get("current_price", 0.0) or 0.0) > 0:
        return {
            "symbol": sym,
            "mid_price": float(position.get("current_price", 0.0) or 0.0),
            "cached": True,
        }
    snapshot_rank = {}
    if latest_universe:
        snapshot_rank = store.get_universe_ranking_for_symbol_sync(int(latest_universe.get("id", 0) or 0), sym)
    snapshot_mid = _snapshot_mid_price(snapshot_rank)
    if snapshot_mid > 0:
        return {
            "symbol": sym,
            "mid_price": snapshot_mid,
            "cached": True,
        }
    return {}


def _build_symbol_overview_local(symbol: str) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    latest_universe = _latest_universe_snapshot(limit_rankings=0)
    cfg = _current_shell_config()
    tracked = store.get_tracked_symbol_sync(sym, str(cfg.get("account_mode", "paper") or "paper"))
    positions = market_data.get_cached_crypto_positions(max_age_sec=900)
    position = next((p for p in positions if str(p.get("symbol", "")).upper() == sym), None)
    event_state = store.get_symbol_event_state_sync(sym)
    macro = store.get_latest_macro_consensus_sync() or {}
    traces = store.list_brain_decision_traces_sync(limit=12, symbol=sym)
    candidates = store.list_candidate_traces_sync(limit=12, symbol=sym)
    outcomes = store.list_decision_outcomes_sync(limit=24, symbol=sym)
    recent_news = store.get_recent_news_events_sync(limit=12, symbol=sym, active_only=False)
    live_exp = store.get_live_experience_sync(limit=20, closed_only=False, symbol=sym)
    quote = _resolve_symbol_quote(sym, latest_universe=latest_universe, positions=positions)
    overlay = store.get_symbol_policy_overlay_sync(sym)
    runtime = get_bot_status_snapshot()
    snapshot_ts = _latest_ms(
        latest_universe.get("ts_ms") if latest_universe else 0,
        overlay.get("updated_at") if overlay else 0,
        traces[0].get("ts") if traces else 0,
        candidates[0].get("ts") if candidates else 0,
        outcomes[0].get("decision_ts") if outcomes else 0,
        recent_news[0].get("published_at") if recent_news else 0,
    )
    return _with_meta(
        {
            "symbol": sym,
            "position": position,
            "quote": quote,
            "overlay": overlay,
            "tracked": tracked,
            "event_state": event_state,
            "macro_consensus": macro,
            "runtime": runtime.get("runtime", {}),
            "latest_trace": traces[0] if traces else None,
            "latest_candidate": candidates[0] if candidates else None,
            "latest_outcome": outcomes[0] if outcomes else None,
            "counts": {
                "trades": len(live_exp),
                "candidates": len(candidates),
                "blocked": sum(1 for row in traces if not bool(row.get("submitted", False))),
                "news": len(recent_news),
            },
        },
        status="fresh" if quote else "partial",
        snapshot_ts=snapshot_ts,
        last_updated_ms=snapshot_ts,
    )


def _build_symbol_overview_fallback(symbol: str) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    latest_universe = _latest_universe_snapshot(limit_rankings=0)
    cfg = _current_shell_config()
    tracked = store.get_tracked_symbol_sync(sym, str(cfg.get("account_mode", "paper") or "paper"))
    positions = market_data.get_cached_crypto_positions(max_age_sec=900)
    position = next((p for p in positions if str(p.get("symbol", "")).upper() == sym), None)
    quote = _resolve_symbol_quote(sym, latest_universe=latest_universe, positions=positions)
    runtime = get_bot_status_snapshot()
    snapshot_ts = _latest_ms(
        latest_universe.get("ts_ms") if latest_universe else 0,
    )
    return _with_meta(
        {
            "symbol": sym,
            "position": position,
            "quote": quote,
            "overlay": {},
            "tracked": tracked,
            "event_state": {},
            "macro_consensus": {},
            "runtime": runtime.get("runtime", {}),
            "latest_trace": None,
            "latest_candidate": None,
            "latest_outcome": None,
            "counts": {
                "trades": 0,
                "candidates": 0,
                "blocked": 0,
                "news": 0,
            },
        },
        status="partial",
        snapshot_ts=snapshot_ts,
        last_updated_ms=snapshot_ts,
    )


def _refresh_symbol_overview_cache(symbol: str) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    return _cache_set(f"symbol-overview:{sym}", _build_symbol_overview_local(sym))


def _build_symbol_snapshot_partial(symbol: str, overview: Dict[str, Any]) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    decisions = _cache_peek(f"symbol-decisions:{sym}:8") or {}
    trades = _cache_peek(f"symbol-trades:{sym}:8") or {}
    news = _cache_peek(f"symbol-news:{sym}:6:0") or {}
    reports = _cache_peek(f"symbol-reports:{sym}") or {}
    action_preview = _cache_peek(f"symbol-actions:{sym}:25") or {}
    snapshot_ts = _latest_ms(
        overview.get("snapshot_ts"),
        decisions.get("snapshot_ts"),
        trades.get("snapshot_ts"),
        news.get("snapshot_ts"),
        reports.get("snapshot_ts"),
        action_preview.get("snapshot_ts"),
    )
    preview_ready = any(
        (
            decisions.get("decision_traces"),
            decisions.get("decision_outcomes"),
            trades.get("items"),
            news.get("items"),
            reports.get("items"),
            action_preview.get("items"),
        )
    )
    status = str(overview.get("status", "partial") or "partial")
    if not preview_ready:
        status = "partial"
    return _with_meta(
        {
            "symbol": sym,
            "overview": overview,
            "decision_preview": list((decisions.get("decision_traces") or [])[:6]),
            "outcome_preview": list((decisions.get("decision_outcomes") or [])[:6]),
            "trade_preview": list((trades.get("items") or [])[:6]),
            "news_preview": list((news.get("items") or [])[:4]),
            "report_preview": list((reports.get("items") or [])[:4]),
            "context_counts": {
                "actions": len(action_preview.get("items") or []),
                "reports": len(reports.get("items") or []),
                "news_events": len(news.get("items") or []),
            },
        },
        status=status,
        snapshot_ts=snapshot_ts,
        last_updated_ms=snapshot_ts,
    )


def _build_symbol_snapshot_local(symbol: str) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    overview = _cache_peek(f"symbol-overview:{sym}") or _build_symbol_overview_local(sym)
    decisions = get_symbol_decisions(sym, limit=8)
    trades = get_symbol_trades(sym, limit=8)
    news = get_symbol_news(sym, limit=6)
    reports = get_symbol_reports(sym)
    action_preview = _cached_symbol_action_preview(sym, limit=25)
    snapshot_ts = _latest_ms(
        overview.get("snapshot_ts"),
        decisions.get("snapshot_ts"),
        trades.get("snapshot_ts"),
        news.get("snapshot_ts"),
        reports.get("snapshot_ts"),
        action_preview[0].get("ts") if action_preview else 0,
    )
    return _with_meta(
        {
            "symbol": sym,
            "overview": overview,
            "decision_preview": (decisions.get("decision_traces") or [])[:6],
            "outcome_preview": (decisions.get("decision_outcomes") or [])[:6],
            "trade_preview": (trades.get("items") or [])[:6],
            "news_preview": (news.get("items") or [])[:4],
            "report_preview": (reports.get("items") or [])[:4],
            "context_counts": {
                "actions": len(action_preview),
                "reports": len(reports.get("items") or []),
                "news_events": len(news.get("items") or []),
            },
        },
        status=str(overview.get("status", "partial") or "partial"),
        snapshot_ts=snapshot_ts,
        last_updated_ms=snapshot_ts,
    )


def _refresh_symbol_snapshot_cache(symbol: str) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    return _cache_set(f"symbol-snapshot:{sym}", _build_symbol_snapshot_local(sym))


async def get_symbol_overview(symbol: str) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    if not sym:
        raise ValueError("symbol is required")
    cache_key = f"symbol-overview:{sym}"
    cached = _cache_get(cache_key, max_age_sec=12.0)
    if cached is not None:
        return cached
    stale = _cache_peek(cache_key)
    if stale is not None:
        _schedule_background_refresh(
            f"symbol-overview:{sym}",
            _refresh_symbol_overview_cache,
            sym,
            min_interval_sec=12,
        )
        return _with_cached_status(stale, "stale")
    try:
        return await asyncio.wait_for(asyncio.to_thread(_refresh_symbol_overview_cache, sym), timeout=1.5)
    except Exception as exc:
        logger.debug("[terminal_data] symbol overview fallback for %s: %s", sym, exc)
        return _build_symbol_overview_fallback(sym)


async def get_symbol_snapshot(symbol: str) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    cache_key = f"symbol-snapshot:{sym}"
    cached = _cache_get(cache_key, max_age_sec=15.0)
    if cached is not None:
        return cached
    overview = await get_symbol_overview(sym)
    stale = _cache_peek(cache_key)
    if stale is not None:
        _schedule_background_refresh(
            f"symbol-snapshot:{sym}",
            _refresh_symbol_snapshot_cache,
            sym,
            min_interval_sec=15,
        )
        return _with_cached_status(stale, "stale")
    _schedule_background_refresh(
        f"symbol-snapshot:{sym}",
        _refresh_symbol_snapshot_cache,
        sym,
        min_interval_sec=15,
    )
    return _build_symbol_snapshot_partial(sym, overview)


def get_symbol_history(symbol: str, range_key: str = "1D") -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    timeframe, limit = _timeframe_limit_from_range(range_key)
    df = market_data.get_cached_bars_frame(sym, timeframe=timeframe, limit=limit, allow_stale=True)
    status = "fresh"
    if df is None:
        _schedule_background_refresh(
            f"history:{sym}:{timeframe}:{limit}",
            _warm_symbol_history_cache,
            sym,
            timeframe,
            limit,
            min_interval_sec=30,
        )
        items = []
        status = "partial"
    else:
        items = [] if df.empty else df.tail(limit).to_dict("records")
    last_updated_ms = _iso_to_ms(items[-1].get("timestamp")) if items else 0
    return _with_meta({
        "symbol": sym,
        "range_key": str(range_key or "1D").upper(),
        "timeframe": timeframe,
        "items": items,
        "count": len(items),
    }, status=status, snapshot_ts=last_updated_ms, last_updated_ms=last_updated_ms)


def get_symbol_trades(symbol: str, limit: int = 25) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    capped = max(1, min(int(limit), 100))
    cache_key = f"symbol-trades:{sym}:{capped}"
    cached = _cache_get(cache_key, max_age_sec=15.0)
    if cached is not None:
        return cached
    items = [
        r for r in store.get_live_experience_sync(limit=max(50, limit * 4), closed_only=False)
        if str(r.get("symbol", "")).upper() == sym
    ][: capped]
    snapshot_ts = _latest_ms(
        items[0].get("entry_ts") if items else 0,
        items[0].get("exit_ts") if items else 0,
    )
    return _cache_set(
        cache_key,
        _with_meta({"symbol": sym, "items": items, "count": len(items)}, status="fresh", snapshot_ts=snapshot_ts, last_updated_ms=snapshot_ts),
    )


def get_symbol_decisions(symbol: str, limit: int = 25) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    capped = max(1, min(int(limit), 100))
    cache_key = f"symbol-decisions:{sym}:{capped}"
    cached = _cache_get(cache_key, max_age_sec=15.0)
    if cached is not None:
        return cached
    decision_traces = store.list_brain_decision_traces_sync(limit=capped, symbol=sym)
    candidate_traces = store.list_candidate_traces_sync(limit=capped, symbol=sym)
    decision_outcomes = store.list_decision_outcomes_sync(limit=capped, symbol=sym)
    snapshot_ts = _latest_ms(
        decision_traces[0].get("ts") if decision_traces else 0,
        candidate_traces[0].get("ts") if candidate_traces else 0,
        decision_outcomes[0].get("decision_ts") if decision_outcomes else 0,
    )
    return _cache_set(
        cache_key,
        _with_meta({
            "symbol": sym,
            "decision_traces": decision_traces,
            "candidate_traces": candidate_traces,
            "decision_outcomes": decision_outcomes,
            "component_calibration": store.get_latest_component_calibration_sync(),
        }, status="fresh", snapshot_ts=snapshot_ts, last_updated_ms=snapshot_ts),
    )


def get_symbol_learning(symbol: str) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    cache_key = f"symbol-learning:{sym}"
    cached = _cache_get(cache_key, max_age_sec=15.0)
    if cached is not None:
        return cached
    outcomes = store.list_decision_outcomes_sync(limit=120, symbol=sym)
    overlay = store.get_symbol_policy_overlay_sync(sym)
    labels: Dict[str, int] = {}
    for row in outcomes:
        label = str(row.get("outcome_label", "unknown") or "unknown")
        labels[label] = labels.get(label, 0) + 1
    snapshot_ts = _latest_ms(
        outcomes[0].get("decision_ts") if outcomes else 0,
        overlay.get("updated_at") if overlay else 0,
    )
    return _cache_set(
        cache_key,
        _with_meta({
            "symbol": sym,
            "overlay": overlay,
            "outcomes": outcomes[:40],
            "label_counts": labels,
        }, status="fresh", snapshot_ts=snapshot_ts, last_updated_ms=snapshot_ts),
    )


def get_symbol_brain(symbol: str) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    cache_key = f"symbol-brain:{sym}"
    cached = _cache_get(cache_key, max_age_sec=15.0)
    if cached is not None:
        return cached
    overlay = store.get_symbol_policy_overlay_sync(sym)
    traces = store.list_brain_decision_traces_sync(limit=40, symbol=sym)
    candidates = store.list_candidate_traces_sync(limit=40, symbol=sym)
    snapshot_ts = _latest_ms(
        overlay.get("updated_at") if overlay else 0,
        traces[0].get("ts") if traces else 0,
        candidates[0].get("ts") if candidates else 0,
    )
    return _cache_set(
        cache_key,
        _with_meta({
            "symbol": sym,
            "overlay": overlay,
            "decision_traces": traces,
            "candidate_traces": candidates,
        }, status="fresh", snapshot_ts=snapshot_ts, last_updated_ms=snapshot_ts),
    )


def get_symbol_news(symbol: str, limit: int = 20, since_ms: int = 0) -> Dict[str, Any]:
    from .report_generator import get_all_latest_reports

    sym = _normalize_symbol(symbol)
    capped = max(1, min(int(limit), 100))
    cache_key = f"symbol-news:{sym}:{capped}:{int(since_ms or 0)}"
    cached = _cache_get(cache_key, max_age_sec=15.0)
    if cached is not None:
        return cached
    rows = store.get_recent_news_events_sync(
        limit=capped,
        symbol=sym,
        since_ms=int(since_ms or 0),
        active_only=False,
    )
    event_state = store.get_symbol_event_state_sync(sym)
    base = str(sym).split("/")[0]
    pattern = re.compile(rf"\b{re.escape(base)}\b", re.IGNORECASE)
    related_reports: List[Dict[str, Any]] = []
    for report_type, report in (get_all_latest_reports() or {}).items():
        content = str((report or {}).get("content") or "")
        match = pattern.search(content)
        if not match:
            continue
        start = max(0, match.start() - 80)
        related_reports.append(
            {
                "report_type": report_type,
                "ts": report.get("ts"),
                "snippet": content[start : start + 180].replace("\n", " ").strip(),
            }
        )
    snapshot_ts = _latest_ms(
        rows[0].get("published_at") if rows else 0,
        event_state.get("updated_at") if event_state else 0,
        related_reports[0].get("ts") if related_reports else 0,
    )
    return _cache_set(
        cache_key,
        _with_meta({
            "symbol": sym,
            "items": rows,
            "count": len(rows),
            "event_state": event_state,
            "related_reports": related_reports[:4],
        }, status="fresh", snapshot_ts=snapshot_ts, last_updated_ms=snapshot_ts),
    )


def get_symbol_reports(symbol: str) -> Dict[str, Any]:
    from .report_generator import get_all_latest_reports

    sym = _normalize_symbol(symbol)
    cache_key = f"symbol-reports:{sym}"
    cached = _cache_get(cache_key, max_age_sec=20.0)
    if cached is not None:
        return cached
    base = str(sym).split("/")[0]
    pattern = re.compile(rf"\b{re.escape(base)}\b", re.IGNORECASE)
    items: List[Dict[str, Any]] = []
    for report_type, report in (get_all_latest_reports() or {}).items():
        content = str((report or {}).get("content") or "")
        if not pattern.search(content):
            continue
        items.append({"report_type": report_type, **dict(report or {})})
    snapshot_ts = _latest_ms(items[0].get("ts") if items else 0)
    return _cache_set(
        cache_key,
        _with_meta({"symbol": sym, "items": items}, status="fresh", snapshot_ts=snapshot_ts, last_updated_ms=snapshot_ts),
    )


def get_symbol_raw_context(symbol: str, ts: int = 0, window_ms: int = 3600000) -> Dict[str, Any]:
    sym = _normalize_symbol(symbol)
    center = int(ts or time.time() * 1000)
    radius = max(60000, int(window_ms or 3600000))
    cache_key = f"symbol-raw:{sym}:{center}:{radius}"
    cached = _cache_get(cache_key, max_age_sec=15.0)
    if cached is not None:
        return cached
    start = center - radius
    end = center + radius
    actions = [
        row
        for row in store.list_actions_page_sync(limit=300)
        if start <= int(row.get("ts", 0) or 0) <= end
        and (not sym or str(row.get("symbol", "")).upper() in {"", sym})
    ]
    reports = []
    from .report_generator import get_historical_reports
    for items in get_historical_reports(40).values():
        for row in items:
            row_ts = int(row.get("ts", 0) or 0)
            if start <= row_ts <= end:
                reports.append(row)
    news = [
        row
        for row in store.get_recent_news_events_sync(limit=100, symbol=sym, since_ms=max(0, start), active_only=False)
        if int(row.get("published_at", 0) or 0) <= end
    ]
    snapshot_ts = _latest_ms(
        actions[0].get("ts") if actions else 0,
        reports[0].get("ts") if reports else 0,
        news[0].get("published_at") if news else 0,
    )
    return _cache_set(
        cache_key,
        _with_meta({
            "symbol": sym,
            "ts": center,
            "window_ms": radius,
            "actions": actions,
            "reports": reports,
            "news_events": news,
        }, status="fresh", snapshot_ts=snapshot_ts, last_updated_ms=snapshot_ts),
    )


def get_activity_feed(
    *,
    limit: int = 100,
    before_ts: Optional[int] = None,
    status: str = "",
    symbol: str = "",
    search: str = "",
) -> Dict[str, Any]:
    items = store.list_actions_page_sync(
        limit=max(1, min(int(limit), 200)),
        before_ts=before_ts,
        status=status,
        symbol=_normalize_symbol(symbol) if symbol else "",
        search=search,
    )
    next_before_ts = items[-1]["ts"] if items else None
    snapshot_ts = _latest_ms(items[0].get("ts") if items else 0)
    return _with_meta({
        "items": items,
        "count": len(items),
        "next_before_ts": next_before_ts,
    }, status="fresh", snapshot_ts=snapshot_ts, last_updated_ms=snapshot_ts)


def get_reports_overview(limit_per_type: int = 25) -> Dict[str, Any]:
    from .report_generator import get_all_latest_reports, get_historical_reports

    reports = get_all_latest_reports()
    history = get_historical_reports(limit_per_type)
    latest_report_ts = 0
    for report in reports.values():
        if isinstance(report, dict):
            latest_report_ts = max(latest_report_ts, int(report.get("ts", 0) or 0))
    return _with_meta({"reports": reports, "history": history}, status="fresh", snapshot_ts=latest_report_ts, last_updated_ms=latest_report_ts)


def get_learning_overview() -> Dict[str, Any]:
    cached = _cache_get("learning-overview", max_age_sec=15.0)
    if cached is not None:
        return cached
    latest_score = store.get_latest_brain_self_score_sync()
    score_history = list(reversed(store.list_brain_self_score_history_sync(limit=72)))
    overlays = store.list_symbol_policy_overlays_sync(limit=30)
    outcome_stats = store.get_decision_outcome_stats_sync()
    exp_stats = store.get_live_experience_stats_sync()
    safeguard_overview = _safeguard_overview(limit=600)
    if (not latest_score) or (not overlays and outcome_stats.get("by_horizon")):
        _schedule_background_refresh("learning-snapshots", _refresh_learning_snapshots, min_interval_sec=120)
    cfg = _current_shell_config()
    feedback_cfg = cfg.get("decision_feedback", {}) or {}
    pending = {}
    for horizon in feedback_cfg.get("horizons_min", [30, 60, 360, 1440]):
        older_than = int(time.time() * 1000) - int(horizon) * 60 * 1000
        pending[str(horizon)] = store.count_pending_decision_traces_for_outcome_sync(int(horizon), older_than)

    latest_updated = _latest_ms(
        latest_score.get("ts") if latest_score else 0,
        overlays[0].get("updated_at") if overlays else 0,
    )
    return _cache_set(
        "learning-overview",
        _with_meta({
        "brain_self_score": latest_score,
        "brain_self_score_history": score_history,
        "overlays": overlays,
        "outcome_stats": outcome_stats,
        "live_experience": exp_stats,
        "safeguards": safeguard_overview,
        "pending_outcomes": pending,
        "training_schedule": {
            "live_retrain_interval_days": int(cfg.get("live_retrain_interval_days", 3) or 3),
            "feedback_horizons_min": feedback_cfg.get("horizons_min", _DEFAULT_FEEDBACK_HORIZONS),
        },
        "log_tail": _read_ml_log_tail(limit=50),
        }, status="fresh" if latest_score else "partial", snapshot_ts=latest_updated, last_updated_ms=latest_updated),
    )


def get_brain_overview() -> Dict[str, Any]:
    import glob

    cached = _cache_get("brain-overview", max_age_sec=20.0)
    if cached is not None:
        return cached

    models_dir = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "models")
    )
    model_files: List[Dict[str, Any]] = []
    if os.path.isdir(models_dir):
        for path in glob.glob(os.path.join(models_dir, "ppo_*.zip")):
            mtime = os.path.getmtime(path)
            model_files.append(
                {
                    "name": os.path.basename(path),
                    "size_kb": round(os.path.getsize(path) / 1024, 1),
                    "last_modified_ms": int(mtime * 1000),
                }
            )

    latest_score = store.get_latest_brain_self_score_sync()
    overlays = store.list_symbol_policy_overlays_sync(limit=30)
    decision_outcomes = store.get_decision_outcome_stats_sync()
    if (not latest_score) or (not overlays and decision_outcomes.get("by_horizon")):
        _schedule_background_refresh("learning-snapshots", _refresh_learning_snapshots, min_interval_sec=120)
    latest_updated = _latest_ms(
        latest_score.get("ts") if latest_score else 0,
        overlays[0].get("updated_at") if overlays else 0,
        model_files[0].get("last_modified_ms") if model_files else 0,
    )
    return _cache_set(
        "brain-overview",
        _with_meta({
        "models": model_files,
        "strategy_performance": store.get_strategy_performance_sync(),
        "brain_self_score": latest_score,
        "brain_self_score_history": list(reversed(store.list_brain_self_score_history_sync(limit=72))),
        "overlays": overlays,
        "decision_outcomes": decision_outcomes,
        "services": background_services.service_status(),
        }, status="fresh" if latest_score else "partial", snapshot_ts=latest_updated, last_updated_ms=latest_updated),
    )
