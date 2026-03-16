import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.crypto.decision_feedback import evaluate_pending_decision_outcomes
from services.crypto.news_pipeline import get_recent_news_summary
from services.crypto.policy_overlays import ensure_brain_self_score_snapshot, refresh_symbol_policy_overlays
from services.crypto import store


LEARNING_TABLES = [
    "candidate_trace",
    "brain_decision_trace",
    "live_experience",
    "decision_outcomes",
    "symbol_policy_overlays",
    "brain_self_score_history",
    "news_events",
    "symbol_event_state",
]


def _connect_reader() -> sqlite3.Connection:
    store._init_db_sync()
    con = sqlite3.connect(store._DB_FILE)
    con.row_factory = sqlite3.Row
    return con


def _table_counts() -> Dict[str, int]:
    counts: Dict[str, int] = {}
    con = _connect_reader()
    try:
        for table in LEARNING_TABLES:
            row = con.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
            counts[table] = int((row["count"] if row else 0) or 0)
    finally:
        con.close()
    return counts


def _pending_by_horizon(horizons: List[int], now_ms: int) -> Dict[str, int]:
    pending: Dict[str, int] = {}
    for horizon_min in horizons:
        older_than = now_ms - int(horizon_min) * 60 * 1000
        pending[str(horizon_min)] = store.count_pending_decision_traces_for_outcome_sync(int(horizon_min), older_than)
    return pending


def _news_coverage(limit: int) -> Dict[str, Any]:
    events = get_recent_news_summary(limit=max(1, int(limit)), since_ms=0)
    by_source = Counter()
    by_symbol = Counter()
    blank_symbol = 0
    for event in events:
        source = str(event.get("source", "") or "unknown").lower()
        symbol = str(event.get("symbol", "") or "").upper()
        by_source[source] += 1
        if symbol:
            by_symbol[symbol] += 1
        else:
            blank_symbol += 1
    return {
        "sample_size": len(events),
        "blank_symbol_count": blank_symbol,
        "by_source": dict(by_source.most_common()),
        "top_symbols": dict(by_symbol.most_common(10)),
    }


def _latest_learning_state() -> Dict[str, Any]:
    latest_score = store.get_latest_brain_self_score_sync() or {}
    overlays = store.list_symbol_policy_overlays_sync(limit=10)
    return {
        "latest_brain_self_score": {
            "ts": latest_score.get("ts"),
            "score": latest_score.get("score"),
            "payload": latest_score.get("payload", {}),
        },
        "top_overlays": [
            {
                "symbol": item.get("symbol"),
                "score_delta": item.get("score_delta"),
                "decision_samples": item.get("decision_samples"),
            }
            for item in overlays
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit crypto learning writes and news symbol coverage.")
    parser.add_argument("--mode", default="paper")
    parser.add_argument("--run-feedback", action="store_true", help="Run one decision-feedback pass against the current DB.")
    parser.add_argument("--horizons", nargs="*", type=int, default=[30, 60, 360, 1440])
    parser.add_argument("--window-min", type=int, default=180)
    parser.add_argument("--limit-per-pass", type=int, default=20)
    parser.add_argument("--news-limit", type=int, default=250)
    args = parser.parse_args()

    now_ms = int(time.time() * 1000)
    before_counts = _table_counts()
    before_pending = _pending_by_horizon(args.horizons, now_ms)
    before_news = _news_coverage(args.news_limit)

    feedback_result: Dict[str, Any] = {"ran": False}
    overlay_result: Dict[str, Any] = {"count": 0}
    brain_result: Dict[str, Any] = {}
    if args.run_feedback:
        feedback_result = evaluate_pending_decision_outcomes(
            mode=args.mode,
            feedback_cfg={
                "enabled": True,
                "horizons_min": list(args.horizons),
                "window_min": int(args.window_min),
                "limit_per_pass": int(args.limit_per_pass),
            },
            now_ms=now_ms,
        )
        feedback_result["ran"] = True
        if int(feedback_result.get("recorded", 0) or 0) > 0:
            overlay_result = refresh_symbol_policy_overlays(force=True)
            brain_result = ensure_brain_self_score_snapshot(force=True)

    after_counts = _table_counts()
    after_pending = _pending_by_horizon(args.horizons, now_ms)
    after_news = _news_coverage(args.news_limit)

    payload = {
        "db_file": store._DB_FILE,
        "now_ms": now_ms,
        "before": {
            "counts": before_counts,
            "pending_by_horizon": before_pending,
            "news_coverage": before_news,
        },
        "feedback": feedback_result,
        "overlay_refresh": {
            "count": int(overlay_result.get("count", 0) or 0),
        },
        "brain_self_score": {
            "ts": brain_result.get("ts"),
            "score": brain_result.get("score"),
        },
        "after": {
            "counts": after_counts,
            "pending_by_horizon": after_pending,
            "news_coverage": after_news,
            "learning_state": _latest_learning_state(),
        },
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
