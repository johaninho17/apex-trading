import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.crypto import decision_feedback, market_data, policy_overlays, store


def _set_store_clock(monkeypatch, ts_ms):
    monkeypatch.setattr("services.crypto.store.time.time", lambda: ts_ms / 1000.0)


def test_compressed_feedback_flow_records_outcomes_and_updates_learning_state(monkeypatch, tmp_path):
    db_path = tmp_path / "crypto_bot.db"
    monkeypatch.setattr(store, "_DB_FILE", str(db_path))
    monkeypatch.setattr(store, "_DB_READY_FILE", None)
    monkeypatch.setattr(policy_overlays, "_OVERLAY_CACHE", {"ts_sec": 0.0, "items": []})
    monkeypatch.setattr("services.crypto.market_data.get_crypto_data_client", lambda mode=None: (_ for _ in ()).throw(RuntimeError("disabled for test")))
    monkeypatch.setattr("services.crypto.policy_overlays.background_services.service_status", lambda: {"errors": []})

    base_now_ms = 1_700_000_000_000
    symbol = "DOGE/USD"
    horizon_min = 5
    traces = [
        {"trace_ts": base_now_ms - 20 * 60 * 1000, "submitted": True, "close": 100.0, "outcome_price": 104.0, "decision": "buy"},
        {"trace_ts": base_now_ms - 19 * 60 * 1000, "submitted": False, "close": 100.0, "outcome_price": 103.0, "decision": "buy"},
        {"trace_ts": base_now_ms - 18 * 60 * 1000, "submitted": True, "close": 100.0, "outcome_price": 96.0, "decision": "buy"},
        {"trace_ts": base_now_ms - 17 * 60 * 1000, "submitted": False, "close": 100.0, "outcome_price": 97.0, "decision": "buy"},
    ]

    for index, row in enumerate(traces):
        _set_store_clock(monkeypatch, row["trace_ts"])
        store.record_brain_decision_trace_sync(
            symbol=symbol,
            brain_mode="drl_event_fusion",
            decision=row["decision"],
            final_score=82.0 - index,
            submitted=row["submitted"],
            block_reason="" if row["submitted"] else "score_floor",
            payload={
                "signal": {"close": row["close"], "strategy": "mean_reversion"},
                "meta": {"brain_mode": "drl_event_fusion", "test_case": "compressed_feedback_flow"},
            },
        )

        outcome_ts = row["trace_ts"] + horizon_min * 60 * 1000
        _set_store_clock(monkeypatch, outcome_ts)
        store.record_universe_snapshot_sync(
            account_mode="paper",
            selected_symbols=[symbol],
            promoted_symbols=[],
            rankings=[
                {
                    "symbol": symbol,
                    "bucket": "core",
                    "selected": True,
                    "total_rank": 92.0 - index,
                    "mid_price": row["outcome_price"],
                    "ts": outcome_ts,
                }
            ],
            payload={"test_case": "compressed_feedback_flow"},
        )

    result = decision_feedback.evaluate_pending_decision_outcomes(
        mode="paper",
        feedback_cfg={
            "enabled": True,
            "horizons_min": [horizon_min],
            "limit_per_pass": 20,
            "window_min": 30,
            "timeframe": "1Min",
        },
        now_ms=base_now_ms,
    )

    assert result["recorded"] == 4
    outcomes = store.list_decision_outcomes_sync(limit=20, symbol=symbol)
    assert len(outcomes) == 4

    overlays = policy_overlays.refresh_symbol_policy_overlays(force=True)
    assert overlays["count"] == 1
    overlay = store.get_symbol_policy_overlay_sync(symbol)
    assert overlay["decision_samples"] == 4

    brain_snapshot = policy_overlays.ensure_brain_self_score_snapshot(force=True)
    assert brain_snapshot["score"] >= 0.0
    assert store.get_latest_brain_self_score_sync() is not None

    labels = {row["outcome_label"] for row in outcomes}
    assert labels == {"good_entry", "bad_block", "bad_entry", "good_block"}

    fallback_quote = market_data.fetch_price_near_ts(
        symbol,
        traces[0]["trace_ts"] + horizon_min * 60 * 1000,
        mode="paper",
        window_min=30,
    )
    assert fallback_quote == traces[0]["outcome_price"]
