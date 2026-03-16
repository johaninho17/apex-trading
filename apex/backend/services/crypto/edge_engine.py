from __future__ import annotations

from typing import Any, Dict


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def compute_edge_snapshot(
    signal: Dict[str, Any],
    *,
    macro_risk_mult: float,
    event_state: Dict[str, Any] | None = None,
    calibration: Dict[str, Any] | None = None,
    behavior_snapshot: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    event_state = dict(event_state or {})
    calibration = dict(calibration or {})
    behavior_snapshot = dict(behavior_snapshot or {})
    meta = dict(signal.get("meta") or {})
    components = dict(meta.get("score_components") or {})

    final_score = float(signal.get("score", 0.0) or 0.0)
    base_score = float(components.get("base_score", final_score) or final_score)
    drl_bonus = float(components.get("drl_bonus", 0.0) or 0.0)
    event_bonus = float(components.get("event_bonus", event_state.get("net_event_score", 0.0)) or 0.0)
    macro_bonus = float(components.get("macro_bonus", (float(macro_risk_mult or 1.0) - 1.0) * 20.0) or 0.0)
    micro_bonus = float(components.get("micro_bonus", 0.0) or 0.0)
    golden_bonus = float(components.get("golden_bonus", 0.0) or 0.0)

    weights = dict(calibration.get("weights") or {})
    drl_weight = float(weights.get("drl", 1.0) or 1.0)
    event_weight = float(weights.get("event", 1.0) or 1.0)
    macro_weight = float(weights.get("macro", 1.0) or 1.0)
    strategy_weight = float(weights.get("strategy", 1.0) or 1.0)
    symbol_weight = float(weights.get("symbol", 1.0) or 1.0)
    risk_weight = float(weights.get("risk", 1.0) or 1.0)

    p_model = _clamp(0.5 + ((final_score - 50.0) / 100.0), 0.01, 0.99)
    p_market = _clamp(0.5 + (((float(macro_risk_mult or 1.0) - 1.0) * 0.18) + (float(event_state.get("net_event_score", 0.0) or 0.0) / 300.0)), 0.01, 0.99)
    raw_edge = p_model - p_market

    behavior_score = float((behavior_snapshot.get("aggregate") or {}).get("aggregate_score", 0.0) or 0.0)
    behavior_modifier = _clamp(behavior_score / 100.0, -0.15, 0.15)

    weighted_bonus = (
        drl_bonus * drl_weight
        + event_bonus * event_weight
        + macro_bonus * macro_weight
        + micro_bonus * strategy_weight
        + golden_bonus * symbol_weight
    ) / max(1.0, drl_weight + event_weight + macro_weight + strategy_weight + symbol_weight)
    risk_bias = (risk_weight - 1.0) * 0.04
    calibrated_edge = _clamp(raw_edge + (weighted_bonus / 100.0) + behavior_modifier - risk_bias, -0.45, 0.45)
    edge_score = round(_clamp((calibrated_edge + 0.45) / 0.90 * 100.0, 0.0, 100.0), 2)

    confidence_parts = [
        min(1.0, abs(final_score - base_score) / 20.0),
        min(1.0, abs(float(event_state.get("top_event_score", 0.0) or 0.0)) / 100.0),
        min(1.0, abs(float(macro_risk_mult or 1.0) - 1.0) / 0.5),
        min(1.0, abs(behavior_modifier) / 0.15),
    ]
    edge_confidence = round(_clamp(sum(confidence_parts) / len(confidence_parts) * 100.0, 0.0, 100.0), 2)

    reason_codes = []
    if calibrated_edge >= 0.04:
        reason_codes.append("edge_positive")
    elif calibrated_edge <= -0.04:
        reason_codes.append("edge_negative")
    else:
        reason_codes.append("edge_marginal")
    if event_bonus > 5:
        reason_codes.append("event_support")
    if macro_bonus > 3:
        reason_codes.append("macro_support")
    if drl_bonus > 0:
        reason_codes.append("drl_alignment")
    if behavior_modifier < 0:
        reason_codes.append("behavior_headwind")
    elif behavior_modifier > 0:
        reason_codes.append("behavior_tailwind")

    return {
        "edge_score": edge_score,
        "edge_confidence": edge_confidence,
        "edge_probability_model": round(p_model, 4),
        "edge_probability_market": round(p_market, 4),
        "edge_delta": round(calibrated_edge, 4),
        "edge_reason_codes": reason_codes,
        "edge_components": {
            "base_score": round(base_score, 2),
            "final_score": round(final_score, 2),
            "weighted_bonus": round(weighted_bonus, 4),
            "behavior_modifier": round(behavior_modifier, 4),
            "risk_bias": round(risk_bias, 4),
        },
    }
