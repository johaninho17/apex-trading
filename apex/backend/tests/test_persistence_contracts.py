import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ["NUMBA_DISABLE_JIT"] = "1"


def _passthrough_jit(fn=None, **_kwargs):
    if fn is not None:
        return fn

    def wrapper(f):
        return f

    return wrapper


_numba_mock = MagicMock()
_numba_mock.njit = _passthrough_jit
_numba_mock.jit = _passthrough_jit
_numba_mock.prange = range
sys.modules.setdefault("numba", _numba_mock)
sys.modules.setdefault("numba.core", MagicMock())

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.crypto import bot, store


class _DummyThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return None


class PersistenceContracts(unittest.TestCase):
    def test_store_round_trip_for_candidate_governor_and_risk_history(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "crypto_bot.db")
            with patch.object(store, "_DATA_DIR", tmpdir), patch.object(store, "_DB_FILE", db_file):
                candidate_id = store.record_candidate_trace_sync(
                    symbol="BTC/USD",
                    brain_mode="drl_event_fusion",
                    strategy="mean_reversion",
                    side="buy",
                    score=66.5,
                    candidate_class="near_miss",
                    suppressed_reason="strategy_score_gate",
                    payload={"effective_thresholds": {"min_signal_score": 68.0}},
                )
                governor_id = store.record_governor_state_history_sync(
                    mode="cold",
                    candidate_mode="cold",
                    pressure_score=-41.0,
                    payload={"reason_codes": ["no_buys_24h"]},
                )
                risk_id = store.record_risk_state_history_sync(
                    event_type="oracle_halt_entered",
                    state_key="oracle_halt",
                    state_value="true",
                    reason="panic_crash",
                    payload={"market_regime": "panic_crash"},
                )
                candidates = store.list_candidate_traces_sync(limit=5)
                governors = store.list_governor_state_history_sync(limit=5)
                risks = store.list_risk_state_history_sync(limit=5)

        self.assertGreater(candidate_id, 0)
        self.assertGreater(governor_id, 0)
        self.assertGreater(risk_id, 0)
        self.assertEqual(candidates[0]["candidate_class"], "near_miss")
        self.assertEqual(candidates[0]["payload"]["effective_thresholds"]["min_signal_score"], 68.0)
        self.assertEqual(governors[0]["mode"], "cold")
        self.assertEqual(governors[0]["payload"]["reason_codes"], ["no_buys_24h"])
        self.assertEqual(risks[0]["event_type"], "oracle_halt_entered")
        self.assertEqual(risks[0]["payload"]["market_regime"], "panic_crash")

    def test_runtime_records_governor_snapshot_on_recompute(self):
        runtime = bot.CryptoBotRuntime()
        with patch.object(bot.store, "_list_actions_sync", return_value=[]), \
             patch.object(bot.store, "list_brain_decision_traces_sync", return_value=[]), \
             patch.object(bot.store, "record_governor_state_history_sync") as mock_record, \
             patch("services.crypto.bot.compute_governor_state", return_value={
                 "mode": "cold",
                 "candidate_mode": "cold",
                 "pressure_score": -32.0,
                 "reason_codes": ["near_miss_cluster"],
                 "metrics": {"near_miss_6h": 4},
                 "effective": {"min_signal_score": 65.0},
                 "updated_at_ms": 1_000_000,
             }):
            state = runtime._get_activity_governor_state(
                {"activity_governor": {"enabled": True, "update_interval_sec": 300}},
                now_ms=2_000_000,
                macro_risk_mult=0.8,
                open_positions_count=2,
            )

        self.assertEqual(state["mode"], "cold")
        mock_record.assert_called_once()
        self.assertEqual(mock_record.call_args.kwargs["mode"], "cold")
        self.assertEqual(mock_record.call_args.kwargs["payload"]["metrics"]["near_miss_6h"], 4)

    def test_runtime_records_equity_guard_and_oracle_transitions(self):
        runtime = bot.CryptoBotRuntime()
        recorded = []

        with patch.object(runtime, "_record_risk_transition", side_effect=lambda **kwargs: recorded.append(kwargs)), \
             patch.object(bot.store, "get_equity_performance_sync", return_value={"1h": {"pct": -5.2}, "current": 90000.0}), \
             patch.object(bot, "_run_coro", lambda _coro: None), \
             patch.object(bot.store, "record_action", lambda **kwargs: None), \
             patch.object(bot.store, "update_runtime_state", lambda **kwargs: None), \
             patch("threading.Thread", _DummyThread):
            result = runtime._check_equity_guard({"equity_guard_soft_pct": 2.0, "equity_guard_hard_pct": 4.0})

        self.assertTrue(result["skip_trading"])
        self.assertEqual(recorded[0]["event_type"], "equity_guard_activated")
        self.assertEqual(recorded[0]["state_value"], "hard")

        recorded.clear()
        with patch.object(runtime, "_record_risk_transition", side_effect=lambda **kwargs: recorded.append(kwargs)), \
             patch.object(bot, "_run_coro", lambda _coro: None), \
             patch.object(bot.store, "record_action", lambda **kwargs: None), \
             patch.object(bot.store, "update_runtime_state", lambda **kwargs: None):
            runtime._sync_oracle_halt("panic_crash", {"summary": "fear spike"})
            runtime._sync_oracle_halt("bear", {"summary": "stabilizing"})

        self.assertEqual(recorded[0]["event_type"], "oracle_halt_entered")
        self.assertEqual(recorded[1]["event_type"], "oracle_halt_resumed")

    def test_oracle_telegram_updates_require_material_change(self):
        cfg = {
            "telegram_oracle_updates_enabled": True,
            "telegram_oracle_update_min_interval_sec": 7200,
            "telegram_oracle_update_score_delta": 0.12,
        }

        self.assertTrue(bot._should_send_oracle_telegram_update(
            cfg=cfg,
            now_ms=1_000_000,
            last_sent_ms=0,
            last_score=0.0,
            last_regime="",
            score=0.92,
            regime="bear",
        ))
        self.assertFalse(bot._should_send_oracle_telegram_update(
            cfg=cfg,
            now_ms=1_300_000,
            last_sent_ms=1_000_000,
            last_score=0.92,
            last_regime="bear",
            score=0.95,
            regime="bear",
        ))
        self.assertTrue(bot._should_send_oracle_telegram_update(
            cfg=cfg,
            now_ms=1_400_000,
            last_sent_ms=1_000_000,
            last_score=0.92,
            last_regime="bear",
            score=0.92,
            regime="normal",
        ))
        self.assertTrue(bot._should_send_oracle_telegram_update(
            cfg=cfg,
            now_ms=1_000_000 + (3 * 60 * 60 * 1000),
            last_sent_ms=1_000_000,
            last_score=0.92,
            last_regime="bear",
            score=1.08,
            regime="bear",
        ))


if __name__ == "__main__":
    unittest.main()
