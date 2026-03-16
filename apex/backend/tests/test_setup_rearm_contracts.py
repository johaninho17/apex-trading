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


class _DummyDataFrame:
    pass


_pandas_mock = MagicMock()
_pandas_mock.DataFrame = _DummyDataFrame
sys.modules.setdefault("pandas", _pandas_mock)

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.crypto import bot, store


class SetupRearmContracts(unittest.TestCase):
    def test_buyable_override_is_eligible_for_same_suppressed_setup(self):
        cfg = {"setup_rearm": {"buyable_override_enabled": True}}
        candidate_signal = {
            "strategy": "mean_reversion",
            "side": "buy",
            "score": 66.0,
            "close": 100.0,
            "meta": {
                "brain_mode": "drl_event_fusion",
                "opening_strategy": "mean_reversion",
                "macro_regime": "bear",
                "risk_state": "normal",
                "event_bias": "neutral",
            },
        }
        buy_signal = {
            "strategy": "mean_reversion",
            "side": "buy",
            "score": 66.0,
            "close": 100.0,
            "meta": {
                "brain_mode": "drl_event_fusion",
                "opening_strategy": "mean_reversion",
                "macro_regime": "bear",
                "risk_state": "normal",
                "event_bias": "neutral",
            },
        }
        previous_fp = bot._build_setup_fingerprint(
            "SKY/USD",
            candidate_signal,
            candidate_class="near_miss",
            suppressed_reason="strategy_score_gate",
        )
        current_fp = bot._build_setup_fingerprint("SKY/USD", buy_signal, decision_type="buy")

        result = bot._evaluate_setup_change(current_fp, previous_fp, cfg, for_buy=True)

        self.assertTrue(result["is_same_setup"])
        self.assertFalse(result["material_change_detected"])
        self.assertTrue(result["buyable_override_eligible"])

    def test_event_change_rearms_same_setup(self):
        cfg = {"setup_rearm": {"allow_event_rearm": True}}
        signal = {
            "strategy": "breakout_momentum",
            "side": "buy",
            "score": 78.0,
            "close": 105.0,
            "meta": {
                "brain_mode": "drl_event_fusion",
                "opening_strategy": "breakout_momentum",
                "macro_regime": "bull",
                "risk_state": "normal",
                "event_bias": "bullish",
                "top_event_type": "listing",
                "top_event_id": "evt_a",
            },
        }
        current = {
            **signal,
            "meta": {**signal["meta"], "top_event_id": "evt_b"},
        }
        previous_fp = bot._build_setup_fingerprint("SOL/USD", signal, decision_type="buy")
        current_fp = bot._build_setup_fingerprint("SOL/USD", current, decision_type="buy")

        result = bot._evaluate_setup_change(current_fp, previous_fp, cfg, for_buy=True)

        self.assertTrue(result["material_change_detected"])
        self.assertIn("event_changed", result["change_reasons"])

    def test_recent_submitted_buy_match_detects_duplicate_setup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "crypto_bot.db")
            with patch.object(store, "_DATA_DIR", tmpdir), patch.object(store, "_DB_FILE", db_file):
                signal = {
                    "strategy": "mean_reversion",
                    "side": "buy",
                    "score": 72.0,
                    "close": 100.0,
                    "meta": {
                        "brain_mode": "drl_event_fusion",
                        "opening_strategy": "mean_reversion",
                        "macro_regime": "bear",
                        "risk_state": "normal",
                        "event_bias": "neutral",
                    },
                }
                fp = bot._build_setup_fingerprint("SKY/USD", signal, decision_type="buy")
                store.record_brain_decision_trace_sync(
                    symbol="SKY/USD",
                    brain_mode="drl_event_fusion",
                    decision="buy",
                    final_score=72.0,
                    submitted=True,
                    payload={"context": {"setup_fingerprint": fp}},
                )
                match = bot._recent_submitted_buy_match({"setup_rearm": {"buy_window_sec": 900}}, "SKY/USD", fp)

        self.assertIsNotNone(match)
        self.assertTrue(match["comparison"]["is_same_setup"])
        self.assertFalse(match["comparison"]["material_change_detected"])

    def test_dynamic_dca_requires_new_rung(self):
        cfg = {"setup_rearm": {"dca": {"min_add_spacing_min": 30, "min_price_drop_pct": 1.0, "min_oversold_delta": 1.0}}}
        previous_fp = {
            "opening_strategy": "dynamic_dca",
            "close": 100.0,
            "dca_state": {"dip_distance_pct": 4.0, "oversold_severity": 3.0},
        }
        current_fp = {
            "opening_strategy": "dynamic_dca",
            "close": 98.6,
            "dca_state": {"dip_distance_pct": 5.3, "oversold_severity": 4.4},
        }

        previous_ts_ms = int((bot.time.time() - (31 * 60)) * 1000)
        self.assertTrue(bot._dca_rearm_reached(cfg, current_fp, previous_fp, previous_ts_ms))

        recent_ts_ms = int((bot.time.time() - (10 * 60)) * 1000)
        self.assertFalse(bot._dca_rearm_reached(cfg, current_fp, previous_fp, recent_ts_ms))


if __name__ == "__main__":
    unittest.main()
