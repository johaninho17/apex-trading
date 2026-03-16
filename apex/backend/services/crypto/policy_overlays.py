from __future__ import annotations

import time
from typing import Any, Dict, List

from core import background_services
from core.config_manager import get_config

from . import store

DEFAULT_POLICY_OVERLAY_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "refresh_sec": 300,
    "lookback_limit": 1500,
    "min_samples": 4,
    "score_delta_cap": 20.0,
    "size_multiplier_min": 0.75,
    "size_multiplier_max": 1.25,
    "cooldown_multiplier_min": 0.75,
    "cooldown_multiplier_max": 1.5,
    "brain_self_score_interval_sec": 3600,
}

_OVERLAY_CACHE: Dict[str, Any] = {
    "ts_sec": 0.0,
    "items": [],
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _load_cfg() -> Dict[str, Any]:
    cfg = dict(DEFAULT_POLICY_OVERLAY_CONFIG)
    raw = (
        get_config()
        .get("stocks", {})
        .get("crypto", {})
        .get("policy_overlays", {})
    )
    if isinstance(raw, dict):
        cfg.update(raw)
    return cfg


def _horizon_weight(horizon_min: int) -> float:
    if horizon_min <= 30:
        return 1.0
    if horizon_min <= 60:
        return 1.15
    if horizon_min <= 360:
        return 1.35
    return 1.5


def _label_edge(label: str) -> float:
    normalized = str(label or "").strip().lower()
    if normalized == "good_entry":
        return 1.0
    if normalized == "bad_block":
        return 0.85
    if normalized == "flat_entry":
        return 0.1
    if normalized == "flat_block":
        return -0.05
    if normalized == "good_block":
        return -0.85
    if normalized == "bad_entry":
        return -1.0
    return 0.0


def _overlay_from_outcomes(symbol: str, rows: List[Dict[str, Any]], cfg: Dict[str, Any]) -> Dict[str, Any]:
    weighted_sum = 0.0
    weight_total = 0.0
    counts = {
        "good_entry_count": 0,
        "bad_entry_count": 0,
        "good_block_count": 0,
        "bad_block_count": 0,
        "flat_count": 0,
    }

    for row in rows:
        label = str(row.get("outcome_label", "") or "")
        if label == "good_entry":
            counts["good_entry_count"] += 1
        elif label == "bad_entry":
            counts["bad_entry_count"] += 1
        elif label == "good_block":
            counts["good_block_count"] += 1
        elif label == "bad_block":
            counts["bad_block_count"] += 1
        else:
            counts["flat_count"] += 1

        weight = _horizon_weight(int(row.get("horizon_min", 0) or 0))
        weighted_sum += _label_edge(label) * weight
        weight_total += weight

    normalized_edge = weighted_sum / max(1.0, weight_total)
    samples = len(rows)
    sample_factor = min(1.0, samples / max(1, int(cfg.get("min_samples", 4) or 4)))
    normalized_edge *= sample_factor

    score_cap = float(cfg.get("score_delta_cap", 20.0) or 20.0)
    score_delta = _clamp(normalized_edge * score_cap, -score_cap, score_cap)
    size_multiplier = _clamp(
        1.0 + (normalized_edge * 0.18),
        float(cfg.get("size_multiplier_min", 0.75) or 0.75),
        float(cfg.get("size_multiplier_max", 1.25) or 1.25),
    )
    cooldown_multiplier = _clamp(
        1.0 - (normalized_edge * 0.30),
        float(cfg.get("cooldown_multiplier_min", 0.75) or 0.75),
        float(cfg.get("cooldown_multiplier_max", 1.5) or 1.5),
    )
    confidence_boost = _clamp(score_delta * 0.5, -10.0, 10.0)
    veto_tightness = _clamp(-score_delta * 0.6, -20.0, 20.0)

    return {
        "symbol": symbol,
        "score_delta": round(score_delta, 3),
        "size_multiplier": round(size_multiplier, 4),
        "cooldown_multiplier": round(cooldown_multiplier, 4),
        "veto_tightness": round(veto_tightness, 3),
        "confidence_boost": round(confidence_boost, 3),
        "decision_samples": samples,
        "updated_at": int(time.time() * 1000),
        **counts,
        "payload": {
            "normalized_edge": round(normalized_edge, 4),
            "weight_total": round(weight_total, 3),
        },
    }


def refresh_symbol_policy_overlays(force: bool = False) -> Dict[str, Any]:
    cfg = _load_cfg()
    if not bool(cfg.get("enabled", True)):
        return {"enabled": False, "count": 0, "items": []}

    now_sec = time.time()
    refresh_sec = max(30, int(cfg.get("refresh_sec", 300) or 300))
    if (
        not force
        and _OVERLAY_CACHE["items"]
        and (now_sec - float(_OVERLAY_CACHE["ts_sec"] or 0.0)) <= refresh_sec
    ):
        return {"enabled": True, "count": len(_OVERLAY_CACHE["items"]), "items": list(_OVERLAY_CACHE["items"])}

    rows = store.list_decision_outcomes_sync(limit=max(100, int(cfg.get("lookback_limit", 1500) or 1500)))
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        symbol = str(row.get("symbol", "") or "").upper()
        if not symbol:
            continue
        grouped.setdefault(symbol, []).append(row)

    items: List[Dict[str, Any]] = []
    min_samples = max(1, int(cfg.get("min_samples", 4) or 4))
    for symbol, symbol_rows in grouped.items():
        if len(symbol_rows) < min_samples:
            continue
        overlay = _overlay_from_outcomes(symbol, symbol_rows, cfg)
        store.upsert_symbol_policy_overlay_sync(symbol, overlay)
        items.append(overlay)

    items.sort(key=lambda item: abs(float(item.get("score_delta", 0.0) or 0.0)), reverse=True)
    _OVERLAY_CACHE["ts_sec"] = now_sec
    _OVERLAY_CACHE["items"] = list(items)
    return {"enabled": True, "count": len(items), "items": items}


def get_overlay_map(refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    if refresh:
        refresh_symbol_policy_overlays(force=False)
    items = _OVERLAY_CACHE["items"] or store.list_symbol_policy_overlays_sync(limit=400)
    return {
        str(item.get("symbol", "")).upper(): item
        for item in items
        if str(item.get("symbol", "")).strip()
    }


def compute_brain_self_score_snapshot() -> Dict[str, Any]:
    exp_stats = store.get_live_experience_stats_sync()
    outcome_stats = store.get_decision_outcome_stats_sync().get("by_horizon", {})
    service_state = background_services.service_status()
    overlays = store.list_symbol_policy_overlays_sync(limit=100)

    good_entries = 0
    bad_entries = 0
    good_blocks = 0
    bad_blocks = 0
    for labels in outcome_stats.values():
        good_entries += int(labels.get("good_entry", 0) or 0)
        bad_entries += int(labels.get("bad_entry", 0) or 0)
        good_blocks += int(labels.get("good_block", 0) or 0)
        bad_blocks += int(labels.get("bad_block", 0) or 0)

    closed = int(exp_stats.get("closed", 0) or 0)
    win_rate = float(exp_stats.get("win_rate_pct", 0.0) or 0.0)
    avg_pnl = float(exp_stats.get("avg_pnl", 0.0) or 0.0)
    total_pnl = float(exp_stats.get("total_pnl", 0.0) or 0.0)
    open_positions = int(exp_stats.get("open", 0) or 0)

    confidence = _clamp(50.0 + (win_rate - 50.0) * 0.9 + avg_pnl * 6.0, 0.0, 100.0)
    blocking_total = good_blocks + bad_blocks
    blocking_quality = _clamp(
        50.0 + ((bad_blocks - good_blocks) / max(1, blocking_total)) * 40.0,
        0.0,
        100.0,
    )
    pnl_quality = _clamp(50.0 + avg_pnl * 8.0 + total_pnl * 0.2, 0.0, 100.0)
    discipline = _clamp(70.0 - max(0, open_positions - 4) * 8.0, 0.0, 100.0)
    freshness = 100.0 if not service_state.get("errors") else max(35.0, 100.0 - (len(service_state["errors"]) * 18.0))

    if closed < 5:
        confidence = min(confidence, 55.0)
        pnl_quality = min(pnl_quality, 55.0)

    overlay_bonus = min(10.0, len(overlays) * 0.4)
    score = _clamp(
        (confidence * 0.30)
        + (blocking_quality * 0.20)
        + (pnl_quality * 0.25)
        + (discipline * 0.15)
        + (freshness * 0.10)
        + overlay_bonus,
        0.0,
        100.0,
    )

    return {
        "ts": int(time.time() * 1000),
        "score": round(score, 2),
        "confidence": round(confidence, 2),
        "discipline": round(discipline, 2),
        "pnl_quality": round(pnl_quality, 2),
        "blocking_quality": round(blocking_quality, 2),
        "freshness": round(freshness, 2),
        "payload": {
            "closed_trades": closed,
            "good_entries": good_entries,
            "bad_entries": bad_entries,
            "good_blocks": good_blocks,
            "bad_blocks": bad_blocks,
            "overlay_count": len(overlays),
        },
    }


def ensure_brain_self_score_snapshot(force: bool = False) -> Dict[str, Any]:
    cfg = _load_cfg()
    interval_sec = max(300, int(cfg.get("brain_self_score_interval_sec", 3600) or 3600))
    latest = store.get_latest_brain_self_score_sync()
    now_ms = int(time.time() * 1000)
    if (
        latest
        and not force
        and (now_ms - int(latest.get("ts", 0) or 0)) < interval_sec * 1000
    ):
        return latest

    snapshot = compute_brain_self_score_snapshot()
    store.append_brain_self_score_sync(
        ts=int(snapshot["ts"]),
        score=float(snapshot["score"]),
        confidence=float(snapshot["confidence"]),
        discipline=float(snapshot["discipline"]),
        pnl_quality=float(snapshot["pnl_quality"]),
        blocking_quality=float(snapshot["blocking_quality"]),
        freshness=float(snapshot["freshness"]),
        payload=dict(snapshot.get("payload") or {}),
    )
    latest = store.get_latest_brain_self_score_sync()
    return latest or snapshot
