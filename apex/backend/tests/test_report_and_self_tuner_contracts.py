
"""
Baseline contract tests for report data lineage and safe self-tuning.

These tests pin down current behavior before the DRL event fusion redesign.
"""

import os
import sys
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

from services.crypto import report_generator, self_tuner


class ReportAndSelfTunerContracts(unittest.TestCase):
    @patch("services.crypto.store._get_runtime_state_sync")
    @patch("services.crypto.store.get_equity_performance_sync")
    @patch("services.crypto.store._list_actions_sync")
    @patch("services.crypto.store.list_risk_state_history_sync")
    @patch("services.crypto.store.list_candidate_traces_sync")
    @patch("services.crypto.store.list_decision_outcomes_sync")
    @patch("services.crypto.store.get_activity_history_sync")
    @patch("services.crypto.oracle.get_current_oracle_state")
    @patch("services.crypto.oracle.get_oracle_status")
    @patch("services.crypto.market_data.get_crypto_positions")
    def test_gather_snapshot_uses_local_runtime_sources(
        self,
        mock_positions,
        mock_oracle_status,
        mock_oracle_state,
        mock_activity_history,
        mock_outcomes,
        mock_candidates,
        mock_risk,
        mock_actions,
        mock_equity,
        mock_runtime,
    ):
        mock_runtime.return_value = {
            "running": 1,
            "halted": 0,
            "halted_reason": "",
            "iterations": 42,
            "last_error": "",
        }
        mock_equity.return_value = {
            "current": 12500.0,
            "1h": {"usd": 150.0, "pct": 1.2},
            "24h": {"usd": 300.0, "pct": 2.4},
            "7d": {"usd": 900.0, "pct": 7.8},
        }
        mock_actions.return_value = [
            {
                "action_type": "order_submitted",
                "symbol": "BTC/USD",
                "side": "buy",
                "notional": 500.0,
                "reason": "Breakout confirmed",
            }
        ]
        mock_outcomes.return_value = [{"symbol": "BTC/USD", "submitted": True, "outcome_label": "good_entry", "block_reason": "", "pnl_pct": 1.4}]
        mock_candidates.return_value = [{"symbol": "ETH/USD", "strategy": "mean_reversion", "side": "buy", "score": 66.5, "candidate_class": "near_miss", "suppressed_reason": "strategy_score_gate"}]
        mock_risk.return_value = [{"event_type": "oracle_halt_resumed", "symbol": "", "state_value": "false", "reason": "Macro normalized"}]
        mock_activity_history.return_value = [
            {
                "ts": 123,
                "mode": "cold",
                "candidate_mode": "cold",
                "pressure_score": -28.0,
                "effectiveness_score": 12.0,
                "confidence_score": 70.0,
                "verdict": "helping",
                "reason_codes": ["near_miss_cluster"],
                "effective": {"min_signal_score": 65.0},
            }
        ]
        mock_oracle_state.return_value = {
            "risk_multiplier": 1.15,
            "market_regime": "bull",
            "rationale_summary": "ETF flows remain supportive.",
        }
        mock_oracle_status.return_value = {"age_sec": 300, "last_error": ""}
        mock_positions.return_value = [
            {
                "symbol": "BTC/USD",
                "qty": 0.01,
                "avg_entry_price": 50000.0,
                "market_value": 515.0,
                "unrealized_pl": 15.0,
                "unrealized_plpc": 0.03,
            }
        ]

        snapshot = report_generator._gather_snapshot()

        self.assertTrue(snapshot["bot"]["running"])
        self.assertEqual(snapshot["oracle"]["market_regime"], "bull")
        self.assertEqual(snapshot["oracle"]["score"], 1.15)
        self.assertEqual(snapshot["equity_perf"]["current"], 12500.0)
        self.assertEqual(len(snapshot["open_positions"]), 1)
        self.assertEqual(snapshot["open_positions"][0]["symbol"], "BTC/USD")
        self.assertEqual(snapshot["recent_actions"][0]["type"], "order_submitted")
        self.assertEqual(snapshot["recent_actions"][0]["reason"], "Breakout confirmed")
        self.assertEqual(snapshot["activity_governor"]["mode"], "cold")
        self.assertEqual(snapshot["activity_governor"]["verdict"], "helping")
        self.assertEqual(snapshot["recent_decision_outcomes"][0]["outcome_label"], "good_entry")
        self.assertEqual(snapshot["recent_candidate_traces"][0]["candidate_class"], "near_miss")
        self.assertEqual(snapshot["recent_risk_transitions"][0]["event_type"], "oracle_halt_resumed")

    @patch("services.crypto.self_tuner.store._record_action_sync")
    @patch("services.crypto.self_tuner.store._list_actions_sync")
    @patch("services.crypto.self_tuner.store.list_brain_decision_traces_sync")
    @patch("services.crypto.self_tuner.store.list_decision_outcomes_sync")
    @patch("services.crypto.self_tuner.store.get_activity_history_sync")
    @patch("services.crypto.self_tuner.bot.update_config")
    @patch("services.crypto.self_tuner.bot.get_config")
    @patch("services.crypto.self_tuner.report_generator._call_gemini")
    @patch("services.crypto.self_tuner.report_generator.get_all_latest_reports")
    def test_self_tuner_clamps_patch_and_updates_supported_fields(
        self,
        mock_reports,
        mock_call_gemini,
        mock_get_config,
        mock_update_config,
        mock_activity_history,
        mock_outcomes,
        mock_traces,
        mock_actions,
        mock_record_action,
    ):
        mock_reports.return_value = {
            "daily": {
                "content": "Equity fell and exits were noisy.",
                "grade": {
                    "grade": "C",
                    "score": 61,
                    "strengths": "Kept running safely.",
                    "weaknesses": "Stops were too loose.",
                    "suggestion": "Tighten exits carefully.",
                },
            }
        }
        mock_call_gemini.return_value = (
            '{"take_profit_pct": 99, "stop_loss_pct": -4, '
            '"rsi_1m_sniper_dip": 999, "min_signal_score": 20, '
            '"rsi_oversold": 999, "breakout_volume_mult": 99, '
            '"reasoning": "Stress test bounds."}'
        )
        mock_get_config.return_value = {
            "stocks": {
                "crypto": {
                    "active_exchange": "alpaca",
                    "account_mode": "paper",
                    "alpaca-paper": {
                        "min_signal_score": 68.0,
                        "synthetic_exits": {
                            "take_profit_pct": 3.0,
                            "stop_loss_pct": 2.0,
                        },
                        "short_term": {
                            "rsi_1m_sniper_dip": 48.0,
                            "rsi_oversold": 32.0,
                            "breakout_volume_mult": 1.9,
                        },
                    },
                }
            }
        }
        mock_activity_history.return_value = [
            {
                "mode": "cold",
                "pressure_score": -18.0,
                "effectiveness_score": 9.0,
                "confidence_score": 66.0,
                "verdict": "helping",
                "reason_codes": ["near_miss_cluster"],
            }
        ]
        mock_outcomes.return_value = [{"outcome_label": "bad_block", "block_reason": "strategy_score_gate"}]
        mock_traces.return_value = [{"payload": {"signal": {"meta": {"candidate_only": True}}}, "block_reason": "strategy_score_gate"}]
        mock_actions.return_value = [{"action_type": "candidate_detected"}]

        result = self_tuner.generate_and_apply_tuning_patch()

        tuner_prompt = mock_call_gemini.call_args.kwargs["prompt"]
        self.assertIn("ACTIVITY GOVERNOR SUMMARY", tuner_prompt)
        self.assertIn("current_mode", tuner_prompt)

        self.assertEqual(result["take_profit_pct"], 10.0)
        self.assertEqual(result["stop_loss_pct"], 0.5)
        self.assertEqual(result["rsi_1m_sniper_dip"], 65.0)
        self.assertEqual(result["min_signal_score"], 62.0)
        self.assertEqual(result["rsi_oversold"], 38.0)
        self.assertEqual(result["breakout_volume_mult"], 2.3)
        self.assertEqual(result["reasoning"], "Stress test bounds.")
        mock_update_config.assert_called_once()
        persisted = mock_update_config.call_args.args[0]
        profile = persisted["stocks"]["crypto"]["alpaca-paper"]
        self.assertEqual(profile["synthetic_exits"]["take_profit_pct"], 10.0)
        self.assertEqual(profile["synthetic_exits"]["stop_loss_pct"], 0.5)
        self.assertEqual(profile["short_term"]["rsi_1m_sniper_dip"], 65.0)
        self.assertEqual(profile["short_term"]["rsi_oversold"], 38.0)
        self.assertEqual(profile["short_term"]["breakout_volume_mult"], 2.3)
        self.assertEqual(profile["min_signal_score"], 62.0)
        mock_record_action.assert_called_once()

    @patch("services.crypto.self_tuner.report_generator.get_all_latest_reports")
    def test_self_tuner_skips_without_daily_report(self, mock_reports):
        mock_reports.return_value = {"daily": None}
        self.assertIsNone(self_tuner.generate_and_apply_tuning_patch())


if __name__ == "__main__":
    unittest.main()
