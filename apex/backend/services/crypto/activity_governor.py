from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List


_FAST_EXIT_ACTIONS = {
    "synthetic_exit",
    "synthetic_exit_rsi",
    "synthetic_exit_trailing",
    "synthetic_exit_tp1_full",
    "time_exit",
    "capital_rotation",
}


def default_governor_config() -> Dict[str, Any]:
    return {
        "enabled": True,
        "min_update_interval_sec": 600,
        "base_update_interval_sec": 1200,
        "max_update_interval_sec": 2400,
        "hysteresis_windows": 2,
        "macro_cold_damping_below": 0.85,
        "min_signal_floor": 62.0,
        "min_signal_ceiling": 78.0,
        "rsi_oversold_floor": 28.0,
        "rsi_oversold_ceiling": 38.0,
        "breakout_volume_floor": 1.5,
        "breakout_volume_ceiling": 2.3,
        "trade_spacing_floor_min": 15,
        "trade_spacing_ceiling_min": 60,
        "near_miss_band": 5.0,
        "signals_pressure_min": 4,
        "decision_feedback_window_hours": 24,
        "bad_block_relax_min": 2,
        "good_block_tighten_min": 2,
        "bad_block_relax_step": 6.0,
        "good_block_tighten_step": 6.0,
        "modes": {
            "cold": {
                "min_signal_score_delta": -3.0,
                "rsi_oversold_delta": 2.0,
                "breakout_volume_mult_delta": -0.2,
                "trade_spacing_min_delta": -5,
            },
            "normal": {
                "min_signal_score_delta": 0.0,
                "rsi_oversold_delta": 0.0,
                "breakout_volume_mult_delta": 0.0,
                "trade_spacing_min_delta": 0,
            },
            "warm": {
                "min_signal_score_delta": 1.0,
                "rsi_oversold_delta": 0.0,
                "breakout_volume_mult_delta": 0.0,
                "trade_spacing_min_delta": 5,
            },
            "hot": {
                "min_signal_score_delta": 4.0,
                "rsi_oversold_delta": -1.0,
                "breakout_volume_mult_delta": 0.2,
                "trade_spacing_min_delta": 10,
            },
            "overactive": {
                "min_signal_score_delta": 7.0,
                "rsi_oversold_delta": -2.0,
                "breakout_volume_mult_delta": 0.35,
                "trade_spacing_min_delta": 20,
            },
        },
    }


def default_governor_state(now_ms: int = 0) -> Dict[str, Any]:
    base_interval_sec = int(default_governor_config().get("base_update_interval_sec", 1200) or 1200)
    return {
        "initialized": False,
        "mode": "normal",
        "candidate_mode": "normal",
        "candidate_streak": 0,
        "pressure_score": 0,
        "reason_codes": [],
        "modifiers": dict(default_governor_config()["modes"]["normal"]),
        "metrics": {},
        "updated_at_ms": int(now_ms or 0),
        "next_update_interval_sec": base_interval_sec,
        "next_due_at_ms": int(now_ms or 0),
    }


def _count_since(items: Iterable[Dict[str, Any]], cutoff_ms: int, predicate) -> int:
    total = 0
    for item in items:
        ts = int(item.get("ts", 0) or 0)
        if ts >= cutoff_ms and predicate(item):
            total += 1
    return total


def _find_latest_ts(items: Iterable[Dict[str, Any]], predicate) -> int:
    latest = 0
    for item in items:
        ts = int(item.get("ts", 0) or 0)
        if ts > latest and predicate(item):
            latest = ts
    return latest


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _clamp_int(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, int(value)))


def _mode_for_pressure(score: float) -> str:
    if score <= -35:
        return "cold"
    if score >= 65:
        return "overactive"
    if score >= 40:
        return "hot"
    if score >= 20:
        return "warm"
    return "normal"


def _resolve_interval_bounds(governor_cfg: Dict[str, Any]) -> tuple[int, int, int]:
    legacy_interval = max(60, int(governor_cfg.get("update_interval_sec", 1200) or 1200))
    min_sec = max(60, int(governor_cfg.get("min_update_interval_sec", legacy_interval) or legacy_interval))
    base_sec = max(min_sec, int(governor_cfg.get("base_update_interval_sec", legacy_interval) or legacy_interval))
    max_sec = max(base_sec, int(governor_cfg.get("max_update_interval_sec", base_sec * 2) or (base_sec * 2)))
    return min_sec, base_sec, max_sec


def _compute_next_update_interval_sec(
    *,
    governor_cfg: Dict[str, Any],
    pressure_score: float,
    resolved_mode: str,
    candidate_mode: str,
    candidate_streak: int,
    metrics: Dict[str, Any],
) -> int:
    min_sec, base_sec, max_sec = _resolve_interval_bounds(governor_cfg)
    interval = float(base_sec)
    pressure_abs = abs(float(pressure_score or 0.0))

    if pressure_abs >= 65:
        interval = float(min_sec)
    elif pressure_abs >= 40:
        interval = min(interval, max(float(min_sec), float(base_sec) * 0.75))
    elif pressure_abs <= 10:
        interval = max(interval, min(float(max_sec), float(base_sec) * 1.5))

    if str(candidate_mode or "normal") != str(resolved_mode or "normal"):
        interval = min(interval, float(min_sec))
    elif int(candidate_streak or 0) > 0:
        interval = min(interval, max(float(min_sec), float(base_sec) * 0.75))

    trades_1h = int(metrics.get("trades_1h", 0) or 0)
    buys_6h = int(metrics.get("buys_6h", 0) or 0)
    signals_6h = int(metrics.get("signals_6h", 0) or 0)
    blocked_6h = int(metrics.get("blocked_traces_6h", 0) or 0)
    near_miss_6h = int(metrics.get("near_miss_6h", 0) or 0)
    fast_exits_24h = int(metrics.get("fast_exits_24h", 0) or 0)
    open_positions_count = int(metrics.get("open_positions_count", 0) or 0)
    time_since_last_buy_min = float(metrics.get("time_since_last_buy_min", 0.0) or 0.0)

    if fast_exits_24h >= 2 or blocked_6h >= 8:
        interval = min(interval, float(min_sec))
    elif near_miss_6h >= 3 or (signals_6h >= 4 and buys_6h == 0):
        interval = min(interval, max(float(min_sec), float(base_sec) * 0.75))

    very_stable = (
        pressure_abs <= 10
        and trades_1h == 0
        and blocked_6h <= 1
        and near_miss_6h == 0
        and fast_exits_24h == 0
        and int(candidate_streak or 0) == 0
    )
    quiet_idle = (
        very_stable
        and signals_6h <= 1
        and open_positions_count <= 2
        and time_since_last_buy_min >= 60
    )
    if quiet_idle:
        interval = float(max_sec)
    elif very_stable:
        interval = max(interval, min(float(max_sec), float(base_sec) * 1.5))

    return int(max(min_sec, min(max_sec, round(interval))))


def compute_governor_state(
    cfg: Dict[str, Any],
    *,
    now_ms: int,
    actions: List[Dict[str, Any]] | None,
    decision_traces: List[Dict[str, Any]] | None,
    decision_outcomes: List[Dict[str, Any]] | None = None,
    open_positions_count: int,
    macro_risk_mult: float,
    previous_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    governor_cfg = copy.deepcopy(default_governor_config())
    governor_cfg.update((cfg.get("activity_governor") or {}))
    if not bool(governor_cfg.get("enabled", True)):
        disabled = default_governor_state(now_ms)
        disabled["mode"] = "disabled"
        disabled["candidate_mode"] = "disabled"
        disabled["updated_at_ms"] = int(now_ms)
        return disabled

    prev = previous_state or default_governor_state(now_ms)
    actions = list(actions or [])
    traces = list(decision_traces or [])
    outcomes = list(decision_outcomes or [])

    hour_ms = 3600 * 1000
    six_hour_ms = 6 * hour_ms
    day_ms = 24 * hour_ms

    cutoff_1h = now_ms - hour_ms
    cutoff_6h = now_ms - six_hour_ms
    cutoff_24h = now_ms - day_ms
    feedback_window_hours = max(1, int(governor_cfg.get("decision_feedback_window_hours", 24) or 24))
    cutoff_feedback = now_ms - (feedback_window_hours * hour_ms)

    trade_actions = [
        item for item in actions
        if str(item.get("action_type", "")) == "order_submitted"
        and str(item.get("status", "")).lower() == "success"
    ]
    buy_actions = [item for item in trade_actions if str(item.get("side", "")).lower() == "buy"]
    signal_actions = [
        item for item in actions
        if str(item.get("action_type", "")) == "signal_detected"
    ]
    exit_actions = [
        item for item in actions
        if str(item.get("action_type", "")) in _FAST_EXIT_ACTIONS
        and str(item.get("status", "")).lower() == "success"
    ]
    blocked_traces = [item for item in traces if not bool(item.get("submitted", False))]
    recent_outcomes = [item for item in outcomes if int(item.get("decision_ts", 0) or 0) >= cutoff_feedback]
    bad_block_24h = sum(1 for item in recent_outcomes if str(item.get("outcome_label", "") or "") == "bad_block")
    good_block_24h = sum(1 for item in recent_outcomes if str(item.get("outcome_label", "") or "") == "good_block")

    min_signal_score = float(cfg.get("min_signal_score", 68.0) or 68.0)
    near_miss_band = float(governor_cfg.get("near_miss_band", 5.0) or 5.0)
    near_miss_6h = 0
    for trace in blocked_traces:
        ts = int(trace.get("ts", 0) or 0)
        if ts < cutoff_6h:
            continue
        score = float(trace.get("final_score", 0.0) or 0.0)
        if abs(min_signal_score - score) <= near_miss_band:
            near_miss_6h += 1

    trades_1h = _count_since(trade_actions, cutoff_1h, lambda _: True)
    buys_6h = _count_since(buy_actions, cutoff_6h, lambda _: True)
    buys_24h = _count_since(buy_actions, cutoff_24h, lambda _: True)
    signals_6h = _count_since(signal_actions, cutoff_6h, lambda _: True)
    blocked_6h = _count_since(blocked_traces, cutoff_6h, lambda _: True)
    fast_exits_24h = _count_since(exit_actions, cutoff_24h, lambda _: True)

    last_buy_ts = _find_latest_ts(buy_actions, lambda _: True)
    time_since_last_buy_min = (
        round(max(0, now_ms - last_buy_ts) / 60000.0, 1) if last_buy_ts > 0 else 99999.0
    )

    pressure_score = 0.0
    reason_codes: List[str] = []

    max_trades_per_hour = max(1, int(cfg.get("max_trades_per_hour", 8) or 8))
    max_open_positions = max(1, int(cfg.get("max_open_positions", 8) or 8))

    if time_since_last_buy_min >= 48 * 60:
        pressure_score -= 45
        reason_codes.append("no_buys_48h")
    elif time_since_last_buy_min >= 24 * 60:
        pressure_score -= 32
        reason_codes.append("no_buys_24h")
    elif time_since_last_buy_min >= 12 * 60:
        pressure_score -= 18
        reason_codes.append("no_buys_12h")

    if buys_24h <= 1:
        pressure_score -= 10
        reason_codes.append("low_trade_count_24h")

    signals_pressure_min = max(1, int(governor_cfg.get("signals_pressure_min", 4) or 4))
    if signals_6h >= signals_pressure_min and buys_6h == 0:
        pressure_score -= 15
        reason_codes.append("signals_without_entries")

    if near_miss_6h >= 3:
        pressure_score -= 10
        reason_codes.append("near_miss_cluster")

    bad_block_relax_min = max(1, int(governor_cfg.get("bad_block_relax_min", 2) or 2))
    good_block_tighten_min = max(1, int(governor_cfg.get("good_block_tighten_min", 2) or 2))
    bad_block_relax_step = float(governor_cfg.get("bad_block_relax_step", 6.0) or 6.0)
    good_block_tighten_step = float(governor_cfg.get("good_block_tighten_step", 6.0) or 6.0)
    if bad_block_24h >= bad_block_relax_min and bad_block_24h > good_block_24h:
        pressure_score -= min(18.0, (bad_block_24h - good_block_24h) * bad_block_relax_step)
        reason_codes.append("bad_block_cluster")
    elif good_block_24h >= good_block_tighten_min and good_block_24h > bad_block_24h:
        pressure_score += min(18.0, (good_block_24h - bad_block_24h) * good_block_tighten_step)
        reason_codes.append("good_block_cluster")

    if trades_1h >= max(1, int(round(max_trades_per_hour * 0.75))):
        pressure_score += 22
        reason_codes.append("hourly_trade_pressure")

    if buys_6h >= max(4, max_trades_per_hour):
        pressure_score += 18
        reason_codes.append("dense_buy_cluster")

    if fast_exits_24h >= 2:
        pressure_score += 24
        reason_codes.append("fast_exit_churn")

    if blocked_6h >= 8 and buys_6h >= 2:
        pressure_score += 8
        reason_codes.append("gates_under_pressure")

    if open_positions_count >= max(1, int(round(max_open_positions * 0.75))):
        pressure_score += 10
        reason_codes.append("positions_near_capacity")

    macro_damping_below = float(governor_cfg.get("macro_cold_damping_below", 0.85) or 0.85)
    if macro_risk_mult < macro_damping_below and pressure_score < 0:
        pressure_score *= 0.5
        reason_codes.append("bear_macro_damping")

    pressure_score = round(_clamp(pressure_score, -100.0, 100.0), 2)
    candidate_mode = _mode_for_pressure(pressure_score)

    current_mode = str(prev.get("mode", "normal") or "normal")
    candidate_streak = int(prev.get("candidate_streak", 0) or 0)
    prev_candidate_mode = str(prev.get("candidate_mode", current_mode) or current_mode)
    hysteresis_windows = max(1, int(governor_cfg.get("hysteresis_windows", 2) or 2))

    if candidate_mode == current_mode:
        candidate_streak = 0
        prev_candidate_mode = candidate_mode
        resolved_mode = current_mode
    else:
        if candidate_mode == prev_candidate_mode:
            candidate_streak += 1
        else:
            prev_candidate_mode = candidate_mode
            candidate_streak = 1
        resolved_mode = candidate_mode if candidate_streak >= hysteresis_windows else current_mode

    mode_modifiers = dict((governor_cfg.get("modes") or {}).get(resolved_mode, {}))
    if resolved_mode == "cold" and macro_risk_mult < macro_damping_below:
        mode_modifiers["min_signal_score_delta"] = round(float(mode_modifiers.get("min_signal_score_delta", 0.0)) * 0.5, 2)
        mode_modifiers["rsi_oversold_delta"] = round(float(mode_modifiers.get("rsi_oversold_delta", 0.0)) * 0.5, 2)
        mode_modifiers["breakout_volume_mult_delta"] = round(float(mode_modifiers.get("breakout_volume_mult_delta", 0.0)) * 0.5, 2)
        mode_modifiers["trade_spacing_min_delta"] = int(round(float(mode_modifiers.get("trade_spacing_min_delta", 0.0)) * 0.5))

    metrics = {
        "trades_1h": trades_1h,
        "buys_6h": buys_6h,
        "buys_24h": buys_24h,
        "signals_6h": signals_6h,
        "blocked_traces_6h": blocked_6h,
        "near_miss_6h": near_miss_6h,
        "bad_block_24h": bad_block_24h,
        "good_block_24h": good_block_24h,
        "fast_exits_24h": fast_exits_24h,
        "time_since_last_buy_min": time_since_last_buy_min,
        "open_positions_count": open_positions_count,
        "macro_risk_mult": round(float(macro_risk_mult or 1.0), 3),
    }
    next_update_interval_sec = _compute_next_update_interval_sec(
        governor_cfg=governor_cfg,
        pressure_score=pressure_score,
        resolved_mode=resolved_mode,
        candidate_mode=prev_candidate_mode,
        candidate_streak=candidate_streak,
        metrics=metrics,
    )

    return {
        "initialized": True,
        "mode": resolved_mode,
        "candidate_mode": prev_candidate_mode,
        "candidate_streak": candidate_streak,
        "pressure_score": pressure_score,
        "reason_codes": reason_codes,
        "modifiers": mode_modifiers,
        "metrics": metrics,
        "updated_at_ms": int(now_ms),
        "next_update_interval_sec": next_update_interval_sec,
        "next_due_at_ms": int(now_ms) + (next_update_interval_sec * 1000),
    }


def apply_governor_to_config(cfg: Dict[str, Any], governor_state: Dict[str, Any]) -> Dict[str, Any]:
    governor_cfg = copy.deepcopy(default_governor_config())
    governor_cfg.update((cfg.get("activity_governor") or {}))
    if not bool(governor_cfg.get("enabled", True)):
        return copy.deepcopy(cfg)

    next_cfg = copy.deepcopy(cfg)
    modifiers = dict(governor_state.get("modifiers") or {})
    short_term = dict(next_cfg.get("short_term") or {})

    min_signal = float(next_cfg.get("min_signal_score", 68.0) or 68.0) + float(modifiers.get("min_signal_score_delta", 0.0) or 0.0)
    next_cfg["min_signal_score"] = round(_clamp(min_signal, float(governor_cfg.get("min_signal_floor", 62.0) or 62.0), float(governor_cfg.get("min_signal_ceiling", 78.0) or 78.0)), 2)

    oversold = float(short_term.get("rsi_oversold", 32.0) or 32.0) + float(modifiers.get("rsi_oversold_delta", 0.0) or 0.0)
    short_term["rsi_oversold"] = round(_clamp(oversold, float(governor_cfg.get("rsi_oversold_floor", 28.0) or 28.0), float(governor_cfg.get("rsi_oversold_ceiling", 38.0) or 38.0)), 2)

    breakout_mult = float(short_term.get("breakout_volume_mult", 1.9) or 1.9) + float(modifiers.get("breakout_volume_mult_delta", 0.0) or 0.0)
    short_term["breakout_volume_mult"] = round(_clamp(breakout_mult, float(governor_cfg.get("breakout_volume_floor", 1.5) or 1.5), float(governor_cfg.get("breakout_volume_ceiling", 2.3) or 2.3)), 3)
    next_cfg["short_term"] = short_term

    trade_spacing = int(round(float(next_cfg.get("trade_spacing_min", 30) or 30) + float(modifiers.get("trade_spacing_min_delta", 0.0) or 0.0)))
    next_cfg["trade_spacing_min"] = _clamp_int(trade_spacing, int(governor_cfg.get("trade_spacing_floor_min", 15) or 15), int(governor_cfg.get("trade_spacing_ceiling_min", 60) or 60))

    next_cfg["_activity_governor"] = {
        "mode": governor_state.get("mode", "normal"),
        "pressure_score": governor_state.get("pressure_score", 0),
        "reason_codes": list(governor_state.get("reason_codes") or []),
        "modifiers": modifiers,
        "next_update_interval_sec": int(governor_state.get("next_update_interval_sec", 0) or 0),
        "next_due_at_ms": int(governor_state.get("next_due_at_ms", 0) or 0),
        "effective": {
            "min_signal_score": next_cfg["min_signal_score"],
            "trade_spacing_min": next_cfg["trade_spacing_min"],
            "rsi_oversold": short_term.get("rsi_oversold"),
            "breakout_volume_mult": short_term.get("breakout_volume_mult"),
        },
    }
    return next_cfg
