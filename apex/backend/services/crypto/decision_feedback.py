from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import market_data, store


DEFAULT_FEEDBACK_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "eval_interval_sec": 600,
    "limit_per_pass": 20,
    "timeframe": "1Min",
    "window_min": 180,
    "horizons_min": [30, 60, 360, 1440],
    "flat_band_pct": 0.35,
}


def _ref_price_from_trace(trace: Dict[str, Any]) -> float:
    payload = dict(trace.get("payload") or {})
    signal = dict(payload.get("signal") or {})
    order = dict(payload.get("order") or {})
    for candidate in (
        signal.get("close"),
        signal.get("ref_price"),
        order.get("filled_avg_price"),
        order.get("limit_price"),
    ):
        try:
            price = float(candidate or 0.0)
        except Exception:
            price = 0.0
        if price > 0:
            return price
    return 0.0


def _signed_pnl_pct(decision: str, ref_price: float, outcome_price: float) -> float:
    if ref_price <= 0 or outcome_price <= 0:
        return 0.0
    raw = ((float(outcome_price) - float(ref_price)) / float(ref_price)) * 100.0
    if str(decision).lower() == "sell":
        raw *= -1.0
    return round(raw, 4)


def _label_outcome(submitted: bool, pnl_pct: float, flat_band_pct: float) -> str:
    if pnl_pct > flat_band_pct:
        return "good_entry" if submitted else "bad_block"
    if pnl_pct < -flat_band_pct:
        return "bad_entry" if submitted else "good_block"
    return "flat_entry" if submitted else "flat_block"


def evaluate_pending_decision_outcomes(
    *,
    mode: Optional[str],
    feedback_cfg: Optional[Dict[str, Any]] = None,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    cfg = dict(DEFAULT_FEEDBACK_CONFIG)
    cfg.update(feedback_cfg or {})
    if not bool(cfg.get("enabled", True)):
        return {"enabled": False, "evaluated": 0, "recorded": 0, "failed": 0}

    current_ms = int(now_ms or __import__("time").time() * 1000)
    horizons = [max(5, int(v)) for v in (cfg.get("horizons_min") or [30, 60, 360, 1440])]
    timeframe = str(cfg.get("timeframe", "1Min") or "1Min")
    window_min = max(30, int(cfg.get("window_min", 180) or 180))
    limit_per_pass = max(1, min(int(cfg.get("limit_per_pass", 20) or 20), 100))
    flat_band_pct = float(cfg.get("flat_band_pct", 0.35) or 0.35)

    evaluated = 0
    recorded = 0
    failed = 0
    labels: Dict[str, int] = {}

    for horizon_min in horizons:
        older_than = current_ms - (horizon_min * 60 * 1000)
        traces = store.list_pending_decision_traces_for_outcome_sync(horizon_min, older_than, limit=limit_per_pass)
        for trace in traces:
            evaluated += 1
            try:
                decision_ts = int(trace.get("ts", 0) or 0)
                ref_price = _ref_price_from_trace(trace)
                if ref_price <= 0:
                    ref_price = market_data.fetch_price_near_ts(
                        str(trace.get("symbol", "") or ""),
                        decision_ts,
                        timeframe=timeframe,
                        mode=mode,
                        window_min=window_min,
                    ) or 0.0
                if ref_price <= 0:
                    failed += 1
                    continue
                outcome_price = market_data.fetch_price_near_ts(
                    str(trace.get("symbol", "") or ""),
                    decision_ts + horizon_min * 60 * 1000,
                    timeframe=timeframe,
                    mode=mode,
                    window_min=window_min,
                )
                if not outcome_price or float(outcome_price) <= 0:
                    failed += 1
                    continue
                decision = str(trace.get("decision", "") or "")
                submitted = bool(trace.get("submitted", False))
                pnl_pct = _signed_pnl_pct(decision, ref_price, float(outcome_price))
                label = _label_outcome(submitted, pnl_pct, flat_band_pct)
                payload = dict(trace.get("payload") or {})
                store.record_decision_outcome_sync(
                    trace_id=int(trace.get("id", 0) or 0),
                    horizon_min=horizon_min,
                    decision_ts=decision_ts,
                    symbol=str(trace.get("symbol", "") or ""),
                    decision=decision,
                    submitted=submitted,
                    block_reason=str(trace.get("block_reason", "") or ""),
                    ref_price=ref_price,
                    outcome_price=float(outcome_price),
                    pnl_pct=pnl_pct,
                    outcome_label=label,
                    payload={
                        "signal": payload.get("signal", {}),
                        "meta": payload.get("meta", {}),
                        "horizon_min": horizon_min,
                    },
                )
                recorded += 1
                labels[label] = labels.get(label, 0) + 1
            except Exception:
                failed += 1
    return {
        "enabled": True,
        "evaluated": evaluated,
        "recorded": recorded,
        "failed": failed,
        "labels": labels,
    }
