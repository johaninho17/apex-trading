from __future__ import annotations

from typing import Any, Dict, List

from . import store


DEFAULT_MODE_EFFECTIVENESS_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "eval_interval_sec": 900,
    "window_hours": 24,
    "min_samples": 8,
    "history_limit": 48,
}


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def evaluate_mode_effectiveness(*, current_mode: str, pressure_score: float, cfg: Dict[str, Any] | None = None, now_ms: int | None = None) -> Dict[str, Any]:
    settings = dict(DEFAULT_MODE_EFFECTIVENESS_CONFIG)
    settings.update(cfg or {})
    if not bool(settings.get("enabled", True)):
        return {"enabled": False}

    current_ms = int(now_ms or __import__("time").time() * 1000)
    cutoff_ms = current_ms - (max(1, int(settings.get("window_hours", 24) or 24)) * 3600 * 1000)
    min_samples = max(1, int(settings.get("min_samples", 8) or 8))
    history_limit = max(4, int(settings.get("history_limit", 48) or 48))

    outcomes = store.list_decision_outcomes_sync(limit=1000)
    relevant: List[Dict[str, Any]] = []
    for row in outcomes:
        if int(row.get("decision_ts", 0) or 0) < cutoff_ms:
            continue
        payload = dict(row.get("payload") or {})
        meta = dict(payload.get("meta") or {})
        mode = str(meta.get("activity_governor_mode", "") or "")
        if mode == str(current_mode or "normal"):
            relevant.append(row)

    label_counts: Dict[str, int] = {}
    submitted_pnls: List[float] = []
    for row in relevant:
        label = str(row.get("outcome_label", "") or "")
        if label:
            label_counts[label] = label_counts.get(label, 0) + 1
        if bool(row.get("submitted", False)):
            try:
                submitted_pnls.append(float(row.get("pnl_pct", 0.0) or 0.0))
            except Exception:
                pass

    good_entry = label_counts.get("good_entry", 0)
    bad_entry = label_counts.get("bad_entry", 0)
    good_block = label_counts.get("good_block", 0)
    bad_block = label_counts.get("bad_block", 0)
    flat_total = label_counts.get("flat_entry", 0) + label_counts.get("flat_block", 0)
    sample_size = len(relevant)

    entry_quality = ((good_entry - bad_entry) / max(1, good_entry + bad_entry)) * 35.0 if (good_entry + bad_entry) else 0.0
    block_quality = ((good_block - bad_block) / max(1, good_block + bad_block)) * 30.0 if (good_block + bad_block) else 0.0
    avg_pnl = sum(submitted_pnls) / len(submitted_pnls) if submitted_pnls else 0.0
    pnl_component = _clamp(avg_pnl * 8.0, -25.0, 25.0)
    flat_penalty = min(10.0, flat_total * 1.5)
    effectiveness_score = round(_clamp(entry_quality + block_quality + pnl_component - flat_penalty, -100.0, 100.0), 2)

    confidence_score = round(_clamp((sample_size / float(min_samples)) * 60.0, 0.0, 100.0), 2)
    history = store.list_governor_mode_history_sync(limit=history_limit)
    stability_hits = 0
    stability_total = 0
    for row in history:
        payload = dict(row.get("payload") or {})
        if str(row.get("mode", "") or "") != str(current_mode or "normal"):
            continue
        stability_total += 1
        verdict = str(payload.get("verdict", "") or "")
        if verdict in ("helping", "neutral"):
            stability_hits += 1
    stability_score = round((stability_hits / max(1, stability_total)) * 100.0, 2) if stability_total else 50.0

    if sample_size < min_samples:
        verdict = "insufficient"
    elif effectiveness_score >= 15.0:
        verdict = "helping"
    elif effectiveness_score <= -15.0:
        verdict = "hurting"
    else:
        verdict = "neutral"

    reason_codes: List[str] = []
    if good_entry > bad_entry:
        reason_codes.append("good_entry_rate_high")
    if good_block > bad_block:
        reason_codes.append("good_block_rate_high")
    if bad_entry > good_entry:
        reason_codes.append("bad_entry_rate_high")
    if bad_block > good_block:
        reason_codes.append("bad_block_rate_high")
    if avg_pnl > 0.4:
        reason_codes.append("avg_pnl_positive")
    elif avg_pnl < -0.4:
        reason_codes.append("avg_pnl_negative")
    if sample_size < min_samples:
        reason_codes.append("sample_low")

    payload = {
        "window_hours": int(settings.get("window_hours", 24) or 24),
        "sample_size": sample_size,
        "label_counts": label_counts,
        "avg_pnl_pct": round(avg_pnl, 4),
        "verdict": verdict,
        "reason_codes": reason_codes,
    }
    store.record_governor_mode_history_sync(
        mode=str(current_mode or "normal"),
        pressure_score=float(pressure_score or 0.0),
        effectiveness_score=effectiveness_score,
        confidence_score=confidence_score,
        stability_score=stability_score,
        payload=payload,
    )

    return {
        "enabled": True,
        "mode": str(current_mode or "normal"),
        "pressure_score": round(float(pressure_score or 0.0), 2),
        "effectiveness_score": effectiveness_score,
        "confidence_score": confidence_score,
        "stability_score": stability_score,
        "sample_size": sample_size,
        "avg_pnl_pct": round(avg_pnl, 4),
        "verdict": verdict,
        "reason_codes": reason_codes,
        "label_counts": label_counts,
    }
