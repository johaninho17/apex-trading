import os
import sys
from unittest.mock import patch


backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


from services.crypto import terminal_data


def test_read_ml_log_tail_returns_newest_first(tmp_path):
    terminal_data._VIEW_CACHE.clear()
    log_path = tmp_path / "ml_continuous_learning.log"
    log_path.write_text("oldest\nmiddle\nlatest\n", encoding="utf-8")
    log_dir = str(tmp_path)

    def _fake_open(path, mode="r", encoding=None, errors=None):
        assert str(path).endswith("ml_continuous_learning.log")
        return log_path.open(mode, encoding=encoding, errors=errors)

    with patch("services.crypto.terminal_data.os.path.exists", return_value=True), \
         patch("services.crypto.terminal_data.os.path.dirname", return_value=log_dir), \
         patch("builtins.open", _fake_open):
        lines = terminal_data._read_ml_log_tail(limit=3)

    assert lines == ["latest", "middle", "oldest"]


def test_get_equity_history_view_downsamples_and_marks_stale():
    terminal_data._VIEW_CACHE.clear()
    points = [
        {"ts": 1_700_000_000_000 + idx * 60_000, "equity": 10_000 + idx}
        for idx in range(1200)
    ]
    stale_now = points[-1]["ts"] + 40 * 60 * 1000

    with patch.object(terminal_data.store, "get_equity_history_sync", return_value=points), \
         patch("services.crypto.terminal_data.time.time", return_value=stale_now / 1000):
        payload = terminal_data.get_equity_history_view(points[0]["ts"])

    assert payload["status"] == "stale"
    assert payload["point_count_raw"] == 1200
    assert payload["point_count"] < payload["point_count_raw"]


def test_learning_overview_includes_safeguard_summary():
    terminal_data._VIEW_CACHE.clear()
    with patch.object(terminal_data.store, "get_latest_brain_self_score_sync", return_value={"ts": 1_700_000_000_000, "score": 72.0}), \
         patch.object(terminal_data.store, "list_brain_self_score_history_sync", return_value=[{"ts": 1_700_000_000_000, "score": 72.0}]), \
         patch.object(terminal_data.store, "list_symbol_policy_overlays_sync", return_value=[]), \
         patch.object(terminal_data.store, "get_decision_outcome_stats_sync", return_value={"by_horizon": {}}), \
         patch.object(terminal_data.store, "get_live_experience_stats_sync", return_value={"closed": 0}), \
         patch.object(terminal_data.store, "count_pending_decision_traces_for_outcome_sync", return_value=0), \
         patch.object(terminal_data, "_safeguard_overview", return_value={"trades_last_hour": 2, "top_block_reasons_24h": []}), \
         patch.object(terminal_data, "current_crypto_config", return_value={"decision_feedback": {"horizons_min": [30]}, "live_retrain_interval_days": 3}), \
         patch.object(terminal_data, "_read_ml_log_tail", return_value=["latest line"]):
        payload = terminal_data.get_learning_overview()

    assert payload["safeguards"]["trades_last_hour"] == 2
    assert payload["log_tail"][0] == "latest line"
