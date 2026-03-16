from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List


DEFAULT_BEHAVIOR_STATE_WEIGHTS: Dict[str, float] = {
    "execution_quality": 0.25,
    "strategy_regime_fitness": 0.20,
    "symbol_edge": 0.20,
    "news_reliability": 0.15,
    "market_data_quality": 0.10,
    "opportunity_pressure": 0.10,
}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _avg(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _state(name: str, score: float, confidence: float, reasons: List[str], modifiers: Dict[str, float]) -> Dict[str, Any]:
    return {
        "name": name,
        "score": round(_clamp(score, -100.0, 100.0), 2),
        "confidence": round(_clamp(confidence, 0.0, 100.0), 2),
        "reason_codes": reasons[:6],
        "recommended_modifier": modifiers,
    }


def compute_behavior_state_snapshot(
    cfg: Dict[str, Any],
    *,
    symbol: str,
    actions: List[Dict[str, Any]],
    decision_traces: List[Dict[str, Any]],
    decision_outcomes: List[Dict[str, Any]],
    candidate_traces: List[Dict[str, Any]],
    live_experience: List[Dict[str, Any]],
    news_events: List[Dict[str, Any]],
    market_data_health: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    market_data_health = dict(market_data_health or {})
    weights = dict(DEFAULT_BEHAVIOR_STATE_WEIGHTS)
    weights.update((cfg.get("behavior_state_modules") or {}).get("weights") or {})

    fetch_errors = sum(1 for row in actions if str(row.get("action_type") or "") == "fetch_error")
    exit_rows = [row for row in live_experience if row.get("outcome_pnl_pct") is not None]
    exit_pnls = [float(row.get("outcome_pnl_pct", 0.0) or 0.0) for row in exit_rows]
    fast_exits = sum(1 for row in exit_rows if float(row.get("hold_duration_min", 9999.0) or 9999.0) <= 90.0)
    execution_reasons = []
    execution_score = 0.0
    if fetch_errors:
        execution_reasons.append("fetch_error_pressure")
        execution_score -= min(60.0, fetch_errors * 10.0)
    if fast_exits >= 3:
        execution_reasons.append("fast_exit_churn")
        execution_score -= min(40.0, fast_exits * 5.0)
    execution_conf = min(100.0, 20.0 + (len(exit_rows) * 5.0) + (fetch_errors * 10.0))
    execution_state = _state("execution_quality", execution_score, execution_conf, execution_reasons, {
        "notional_mult": round(_clamp(1.0 + (execution_score / 250.0), 0.75, 1.05), 3),
        "min_signal_score_delta": round(_clamp((-execution_score) / 35.0, 0.0, 4.0), 2),
    })

    strategy_groups: Dict[str, List[float]] = defaultdict(list)
    for row in exit_rows:
        strategy_groups[str(row.get("strategy_used") or "unknown")].append(float(row.get("outcome_pnl_pct", 0.0) or 0.0))
    strategy_score = _avg([_avg(vals) for vals in strategy_groups.values()]) * 12.0 if strategy_groups else 0.0
    strategy_reasons = []
    if strategy_score < -5:
        strategy_reasons.append("strategy_edge_soft")
    elif strategy_score > 5:
        strategy_reasons.append("strategy_edge_strong")
    strategy_state = _state("strategy_regime_fitness", strategy_score, min(100.0, len(exit_rows) * 6.0), strategy_reasons, {
        "strategy_bias": round(_clamp(strategy_score / 100.0, -0.15, 0.15), 3),
    })

    symbol_rows = [row for row in exit_rows if str(row.get("symbol") or "") == symbol]
    symbol_pnls = [float(row.get("outcome_pnl_pct", 0.0) or 0.0) for row in symbol_rows]
    symbol_score = _avg(symbol_pnls) * 15.0 if symbol_pnls else 0.0
    symbol_reasons = []
    if symbol_score < -4:
        symbol_reasons.append("symbol_underperforming")
    elif symbol_score > 4:
        symbol_reasons.append("symbol_followthrough")
    symbol_state = _state("symbol_edge", symbol_score, min(100.0, len(symbol_rows) * 10.0), symbol_reasons, {
        "symbol_bias": round(_clamp(symbol_score / 120.0, -0.15, 0.15), 3),
    })

    event_type_scores: Dict[str, List[float]] = defaultdict(list)
    for row in exit_rows:
        event_type_scores[str(row.get("top_event_type_at_entry") or "unknown")].append(float(row.get("outcome_pnl_pct", 0.0) or 0.0))
    matching_news = [row for row in news_events if (not symbol) or str(row.get("symbol") or "") == symbol]
    top_event = str((matching_news[0] or {}).get("event_type") if matching_news else "")
    news_score = _avg(event_type_scores.get(top_event, [])) * 10.0 if top_event else 0.0
    news_reasons = ["event_type_weighted"] if top_event else []
    news_state = _state("news_reliability", news_score, min(100.0, len(matching_news) * 8.0), news_reasons, {
        "event_bonus_mult": round(_clamp(1.0 + (news_score / 150.0), 0.85, 1.15), 3),
    })

    stale_bars = int(market_data_health.get("stale_bar_count", 0) or 0)
    cache_fallbacks = int(market_data_health.get("cache_fallback_count", 0) or 0)
    data_score = -(stale_bars * 12.0 + cache_fallbacks * 8.0)
    data_reasons = []
    if stale_bars:
        data_reasons.append("stale_bars")
    if cache_fallbacks:
        data_reasons.append("cache_fallbacks")
    data_state = _state("market_data_quality", data_score, min(100.0, 30.0 + (stale_bars + cache_fallbacks) * 10.0), data_reasons, {
        "min_signal_score_delta": round(_clamp((-data_score) / 40.0, 0.0, 4.0), 2),
        "notional_mult": round(_clamp(1.0 + (data_score / 200.0), 0.7, 1.0), 3),
    })

    near_miss = sum(1 for row in candidate_traces if str(row.get("candidate_class") or "") == "near_miss")
    blocked = sum(1 for row in decision_traces if not bool(row.get("submitted", 0)))
    bad_blocks = sum(1 for row in decision_outcomes if str(row.get("outcome_label") or "") == "bad_block")
    opp_score = min(70.0, near_miss * 4.0 + bad_blocks * 8.0 - max(0, blocked - near_miss) * 0.5)
    opp_reasons = []
    if near_miss:
        opp_reasons.append("near_miss_cluster")
    if bad_blocks:
        opp_reasons.append("bad_blocks_detected")
    opportunity_state = _state("opportunity_pressure", opp_score, min(100.0, 30.0 + (near_miss + bad_blocks) * 8.0), opp_reasons, {
        "min_signal_score_delta": round(_clamp(-(opp_score / 35.0), -5.0, 0.0), 2),
        "rsi_oversold_delta": round(_clamp(opp_score / 30.0, 0.0, 2.0), 2),
    })

    states = {
        "execution_quality": execution_state,
        "strategy_regime_fitness": strategy_state,
        "symbol_edge": symbol_state,
        "news_reliability": news_state,
        "market_data_quality": data_state,
        "opportunity_pressure": opportunity_state,
    }

    aggregate_score = 0.0
    aggregate_confidence = 0.0
    modifiers: Dict[str, float] = {
        "min_signal_score_delta": 0.0,
        "trade_spacing_min_delta": 0.0,
        "breakout_volume_mult_delta": 0.0,
        "rsi_oversold_delta": 0.0,
        "notional_mult": 1.0,
        "strategy_bias": 0.0,
        "symbol_bias": 0.0,
        "event_bonus_mult": 1.0,
    }
    for name, state in states.items():
        weight = float(weights.get(name, 0.0) or 0.0)
        aggregate_score += float(state.get("score", 0.0) or 0.0) * weight
        aggregate_confidence += float(state.get("confidence", 0.0) or 0.0) * weight
        recommended = dict(state.get("recommended_modifier") or {})
        for key, value in recommended.items():
            if key in {"notional_mult", "event_bonus_mult"}:
                modifiers[key] *= float(value or 1.0)
            else:
                modifiers[key] += float(value or 0.0)

    modifiers["notional_mult"] = round(_clamp(modifiers["notional_mult"], 0.65, 1.15), 3)
    modifiers["event_bonus_mult"] = round(_clamp(modifiers["event_bonus_mult"], 0.8, 1.2), 3)
    for key in ("min_signal_score_delta", "trade_spacing_min_delta", "breakout_volume_mult_delta", "rsi_oversold_delta", "strategy_bias", "symbol_bias"):
        modifiers[key] = round(float(modifiers[key] or 0.0), 3)

    return {
        "weights": {key: round(float(value or 0.0), 3) for key, value in weights.items()},
        "states": states,
        "aggregate": {
            "aggregate_score": round(aggregate_score, 2),
            "aggregate_confidence": round(_clamp(aggregate_confidence, 0.0, 100.0), 2),
            "recommended_modifier": modifiers,
            "reason_codes": [
                reason
                for state in states.values()
                for reason in list(state.get("reason_codes") or [])
            ][:10],
        },
    }
