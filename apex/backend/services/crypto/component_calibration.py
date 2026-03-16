from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _avg(values: Iterable[float]) -> float:
    values = list(values)
    if not values:
        return 0.0
    return sum(values) / len(values)


def _score_from_pnl(avg_pnl: float, win_rate: float, sample: int) -> float:
    evidence = min(1.0, sample / 20.0)
    pnl_term = _clamp(avg_pnl / 4.0, -1.0, 1.0)
    win_term = _clamp((win_rate - 50.0) / 30.0, -1.0, 1.0)
    return _clamp((pnl_term * 0.6 + win_term * 0.4) * evidence, -1.0, 1.0)


def compute_component_calibration(
    live_experience: List[Dict[str, Any]],
    decision_outcomes: List[Dict[str, Any]],
    candidate_traces: List[Dict[str, Any]],
    risk_events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    closed = [row for row in live_experience if row.get("outcome_pnl_pct") is not None]
    pnls = [float(row.get("outcome_pnl_pct", 0.0) or 0.0) for row in closed]
    win_rate = (sum(1 for pnl in pnls if pnl > 0) / len(pnls) * 100.0) if pnls else 0.0

    component_scores: Dict[str, float] = {}
    component_samples: Dict[str, int] = {}

    drl_rows = [row for row in closed if str(row.get("brain_mode") or "").lower() in {"drl", "drl_event_fusion"}]
    drl_pnls = [float(row.get("outcome_pnl_pct", 0.0) or 0.0) for row in drl_rows]
    component_scores["drl"] = _score_from_pnl(_avg(drl_pnls), (sum(1 for pnl in drl_pnls if pnl > 0) / len(drl_pnls) * 100.0) if drl_pnls else 0.0, len(drl_pnls))
    component_samples["drl"] = len(drl_pnls)

    event_rows = [row for row in closed if abs(float(row.get("event_score_at_entry", 0.0) or 0.0)) >= 5.0]
    event_pnls = [float(row.get("outcome_pnl_pct", 0.0) or 0.0) for row in event_rows]
    component_scores["event"] = _score_from_pnl(_avg(event_pnls), (sum(1 for pnl in event_pnls if pnl > 0) / len(event_pnls) * 100.0) if event_pnls else 0.0, len(event_pnls))
    component_samples["event"] = len(event_pnls)

    macro_rows = [row for row in closed if row.get("gemini_score") is not None]
    macro_pnls = [float(row.get("outcome_pnl_pct", 0.0) or 0.0) for row in macro_rows]
    component_scores["macro"] = _score_from_pnl(_avg(macro_pnls), (sum(1 for pnl in macro_pnls if pnl > 0) / len(macro_pnls) * 100.0) if macro_pnls else 0.0, len(macro_pnls))
    component_samples["macro"] = len(macro_pnls)

    strategy_groups: Dict[str, List[float]] = defaultdict(list)
    symbol_groups: Dict[str, List[float]] = defaultdict(list)
    for row in closed:
        strategy_groups[str(row.get("strategy_used") or "unknown")].append(float(row.get("outcome_pnl_pct", 0.0) or 0.0))
        symbol_groups[str(row.get("symbol") or "")].append(float(row.get("outcome_pnl_pct", 0.0) or 0.0))
    strategy_best = _avg(sorted((_avg(vals) for vals in strategy_groups.values()), reverse=True)[:3]) if strategy_groups else 0.0
    symbol_best = _avg(sorted((_avg(vals) for vals in symbol_groups.values()), reverse=True)[:5]) if symbol_groups else 0.0
    component_scores["strategy"] = _clamp(strategy_best / 3.0, -1.0, 1.0)
    component_scores["symbol"] = _clamp(symbol_best / 3.0, -1.0, 1.0)
    component_samples["strategy"] = sum(len(vals) for vals in strategy_groups.values())
    component_samples["symbol"] = sum(len(vals) for vals in symbol_groups.values())

    good_blocks = sum(1 for row in decision_outcomes if str(row.get("outcome_label") or "") == "good_block")
    bad_blocks = sum(1 for row in decision_outcomes if str(row.get("outcome_label") or "") == "bad_block")
    risk_penalty = max(0, sum(1 for row in risk_events if "halt" in str(row.get("event_type") or "")))
    risk_score = _clamp(((good_blocks - bad_blocks) / max(1, good_blocks + bad_blocks)) - (risk_penalty / max(1, len(risk_events) or 1) * 0.2), -1.0, 1.0)
    component_scores["risk"] = risk_score
    component_samples["risk"] = max(len(risk_events), good_blocks + bad_blocks)

    weights: Dict[str, float] = {}
    for name, score in component_scores.items():
        sample = component_samples.get(name, 0)
        evidence = min(1.0, sample / 25.0)
        weights[name] = round(_clamp(1.0 + (score * 0.35 * evidence), 0.7, 1.3), 3)

    return {
        "weights": weights,
        "scores": {key: round(value, 4) for key, value in component_scores.items()},
        "samples": component_samples,
        "summary": {
            "closed_trades": len(closed),
            "avg_closed_pnl_pct": round(_avg(pnls), 3),
            "win_rate_pct": round(win_rate, 2),
            "good_blocks": good_blocks,
            "bad_blocks": bad_blocks,
            "candidate_traces": len(candidate_traces),
            "risk_events": len(risk_events),
        },
    }
