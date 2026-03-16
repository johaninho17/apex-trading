"""Contract tests for Gemini oracle parser hardening."""

import os
import sys
import types
import unittest
from unittest.mock import patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.crypto import oracle


class OracleParserContracts(unittest.TestCase):
    def setUp(self):
        oracle._cached_score = 1.0
        oracle._cached_regime = "normal"
        oracle._cached_summary = ""
        oracle._cached_breadth_state = "mixed"
        oracle._cached_drivers = []
        oracle._cached_mentioned_symbols = []
        oracle._cached_summary_source = "default"
        oracle._cached_at_ts = 0.0
        oracle._last_error = ""
        oracle._refresh_in_progress = False

    def test_extract_first_json_object_handles_fenced_json(self):
        raw = """```json
        {"score": 1.33, "market_regime": "greed", "summary": "ETF inflows support BTC."}
        ```"""
        parsed = oracle._extract_first_json_object(raw)
        self.assertEqual(parsed["score"], 1.33)
        self.assertEqual(parsed["market_regime"], "greed")

    def test_extract_first_json_object_ignores_surrounding_prose(self):
        raw = "Gemini analysis follows. {\"score\": 0.62, \"market_regime\": \"fear\", \"summary\": \"SEC pressure hit majors.\"} Grounding metadata after."
        parsed = oracle._extract_first_json_object(raw)
        self.assertEqual(parsed["score"], 0.62)
        self.assertEqual(parsed["market_regime"], "fear")

    def test_extract_first_json_object_handles_multiline_summary(self):
        raw = '{"score": 1.3, "market_regime": "bull", "summary": "Bitcoin ETF inflows remain strong.\nBlackRock led the session."}'
        parsed = oracle._extract_first_json_object(raw)
        self.assertEqual(parsed["score"], 1.3)
        self.assertEqual(parsed["market_regime"], "bull")

    def test_extract_oracle_fields_handles_json_like_text(self):
        raw = '{"score": 0.9, "market_regime": "caution", "summary": "ETF flows cooled after CPI but BTC held key support." extra metadata'
        parsed = oracle._extract_oracle_fields(raw)
        self.assertEqual(parsed["score"], 0.9)
        self.assertEqual(parsed["market_regime"], "caution")
        self.assertIn("ETF flows cooled", parsed["summary"])

    def test_normalize_market_regime_maps_aliases(self):
        self.assertEqual(oracle._normalize_market_regime("fear", 0.95), "bear")
        self.assertEqual(oracle._normalize_market_regime("greed", 1.05), "bull")
        self.assertEqual(oracle._normalize_market_regime("extreme_fear", 0.85), "panic_crash")
        self.assertEqual(oracle._normalize_market_regime("sideways", 1.4), "normal")

    def test_normalize_breadth_state_maps_aliases(self):
        self.assertEqual(oracle._normalize_breadth_state("btc-led"), "btc_led")
        self.assertEqual(oracle._normalize_breadth_state("broad_based"), "broad")
        self.assertEqual(oracle._normalize_breadth_state("risk-off"), "risk_off")
        self.assertEqual(oracle._normalize_breadth_state("majors"), "majors_led")

    @patch("services.crypto.oracle._call_gemini_api")
    def test_refresh_oracle_score_normalizes_regime_and_summary(self, mock_call):
        mock_call.return_value = {
            "score": 0.66,
            "market_regime": "fear",
            "summary": "SEC pressure remains high.\nETF flows mixed.",
            "breadth_state": "btc-led",
            "drivers": ["ETF outflows", "SEC pressure"],
            "mentioned_symbols": ["BTC", "ETH"],
        }
        fake_store = types.SimpleNamespace(
            record_oracle_score_sync=lambda **_kwargs: None,
            record_macro_consensus_sync=lambda **_kwargs: None,
            _record_action_sync=lambda **_kwargs: None,
        )
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
            with patch.dict(sys.modules, {"services.crypto.store": fake_store}):
                score = oracle.refresh_oracle_score()

        self.assertEqual(score, 0.66)
        self.assertEqual(oracle._cached_regime, "bear")
        self.assertEqual(oracle._cached_summary, "SEC pressure remains high. ETF flows mixed.")
        self.assertEqual(oracle._cached_breadth_state, "btc_led")
        self.assertEqual(oracle._cached_drivers, ["ETF outflows", "SEC pressure"])
        self.assertEqual(oracle._cached_mentioned_symbols, ["BTC", "ETH"])
        self.assertEqual(oracle._cached_summary_source, "fresh_gemini")

    @patch("services.crypto.oracle._call_gemini_api")
    def test_refresh_oracle_score_falls_back_when_summary_is_blank(self, mock_call):
        mock_call.return_value = {
            "score": 1.24,
            "market_regime": "bull",
            "summary": "",
            "breadth_state": "majors_led",
            "drivers": ["ETH staking demand improved", "SOL outperformed majors"],
            "mentioned_symbols": ["ETH", "SOL"],
        }
        fake_store = types.SimpleNamespace(
            record_oracle_score_sync=lambda **_kwargs: None,
            record_macro_consensus_sync=lambda **_kwargs: None,
            _record_action_sync=lambda **_kwargs: None,
        )
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}, clear=False):
            with patch.dict(sys.modules, {"services.crypto.store": fake_store}):
                score = oracle.refresh_oracle_score()

        self.assertEqual(score, 1.24)
        self.assertIn("ETH staking demand improved", oracle._cached_summary)
        self.assertEqual(oracle._cached_summary_source, "fallback_structured")


if __name__ == "__main__":
    unittest.main()
