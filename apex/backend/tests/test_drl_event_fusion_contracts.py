"""Contract tests for DRL event fusion, news state, and Telegram retrieval."""

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

class _DummyDataFrame:
    pass


_pandas_mock = MagicMock()
_pandas_mock.DataFrame = _DummyDataFrame
sys.modules.setdefault("pandas", _pandas_mock)
sys.modules.setdefault("httpx", MagicMock())

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import pandas as pd

from integrations.telegram import query_service
from services.crypto import bot, news_pipeline, strategy


class DrlEventFusionContracts(unittest.TestCase):
    @patch("services.crypto.news_pipeline.get_symbol_event_state")
    def test_apply_drl_event_fusion_preserves_opening_strategy(self, mock_event_state):
        mock_event_state.return_value = {
            "net_event_score": 18.0,
            "event_bias": "bullish",
            "hard_veto": False,
            "golden_trade_flag": False,
            "top_event_type": "listing",
            "top_event_score": 92.0,
        }
        candidates = [
            strategy._candidate(
                "breakout_momentum",
                "buy",
                70.0,
                120.0,
                100.0,
                "Breakout confirmed",
                {"rsi14": 41.0},
            )
        ]

        rescored = strategy._apply_drl_event_fusion(
            symbol="SOL/USD",
            cfg={"use_ml_model": False, "short_term": {"base_notional": 100.0}},
            bars_15m=pd.DataFrame(),
            s_15m={"close": 120.0},
            s_1m={"close": 120.0, "rsi14": 42.0},
            macro_risk_mult=1.2,
            candidates=candidates,
        )

        self.assertEqual(len(rescored), 1)
        fused = rescored[0]
        self.assertEqual(fused["meta"]["brain_mode"], "drl_event_fusion")
        self.assertEqual(fused["meta"]["opening_strategy"], "breakout_momentum")
        self.assertEqual(fused["meta"]["event_bias"], "bullish")
        self.assertGreater(fused["score"], 70.0)

    def test_exit_profile_keeps_brain_and_strategy_separate(self):
        profile = bot._get_exit_profile("swing_momentum", "drl_event_fusion")
        self.assertEqual(profile["opening_strategy"], "swing_momentum")
        self.assertEqual(profile["brain_mode"], "drl_event_fusion")
        self.assertEqual(profile["min_hold_min"], 240)
        self.assertEqual(profile["max_hold_min"], 10080)

    def test_golden_bypass_plan_requires_event_driven_signal_and_daily_room(self):
        runtime = bot.CryptoBotRuntime()
        cfg = {
            "brains": {
                "drl_event_fusion": {
                    "golden_bypass_enabled": True,
                    "golden_trade_min_score": 90.0,
                    "golden_bypass_max_per_day": 2,
                    "golden_bypass_max_per_symbol_per_day": 1,
                    "golden_bypass_event_types": [],
                }
            }
        }
        signal = {"side": "buy", "score": 96.0}
        signal_meta = {
            "golden_trade_flag": True,
            "event_bias": "bullish",
            "top_event_type": "listing",
        }

        plan = runtime._golden_bypass_plan(cfg, "SOL/USD", signal, signal_meta, "drl_event_fusion")
        self.assertTrue(plan["qualified"])

        runtime._daily_golden_bypass_count = 2
        blocked = runtime._golden_bypass_plan(cfg, "SOL/USD", signal, signal_meta, "drl_event_fusion")
        self.assertFalse(blocked["qualified"])
        self.assertEqual(blocked["reason"], "daily_limit")

        wrong_brain = runtime._golden_bypass_plan(cfg, "SOL/USD", signal, signal_meta, "drl")
        self.assertFalse(wrong_brain["qualified"])
        self.assertEqual(wrong_brain["reason"], "wrong_brain")

    @patch("services.crypto.bot._send_crypto_toast")
    @patch("services.crypto.bot._run_coro")
    def test_oracle_halt_auto_resumes_when_regime_normalizes(self, mock_run_coro, _mock_toast):
        runtime = bot.CryptoBotRuntime()
        runtime._halted = True
        runtime._halted_reason = "Gemini Macro Oracle triggered panic halt: Gemini News Oracle score: 0.40 (panic_crash)"

        runtime._sync_oracle_halt("normal", {"rationale_summary": "Gemini News Oracle score: 0.88 (normal)"})

        self.assertFalse(runtime._halted)
        self.assertIsNone(runtime._halted_reason)
        self.assertGreaterEqual(mock_run_coro.call_count, 2)

    def test_compute_symbol_event_state_sets_veto_and_golden_flags(self):
        now_ms = 1_700_000_000_000
        positive = {
            "id": "evt_positive",
            "source": "binance",
            "sentiment": "extreme_positive",
            "impact_score": 92.0,
            "ttl_minutes": 240,
            "published_at": now_ms,
            "trade_bias": "buy_bias",
            "event_type": "listing",
        }
        veto = {
            "id": "evt_negative",
            "source": "cryptopanic",
            "sentiment": "extreme_negative",
            "impact_score": 88.0,
            "ttl_minutes": 240,
            "published_at": now_ms,
            "trade_bias": "hard_veto",
            "event_type": "exploit",
        }

        positive_state = news_pipeline._compute_symbol_event_state("SOL/USD", [positive], now_ms=now_ms)
        veto_state = news_pipeline._compute_symbol_event_state("SOL/USD", [veto], now_ms=now_ms)

        self.assertTrue(positive_state["golden_trade_flag"])
        self.assertGreater(positive_state["net_event_score"], 0)
        self.assertFalse(positive_state["hard_veto"])
        self.assertTrue(veto_state["hard_veto"])
        self.assertLess(veto_state["net_event_score"], 0)


class TelegramQueryContracts(unittest.TestCase):
    @patch("integrations.telegram.query_service._summarize_with_ollama", return_value=None)
    @patch("integrations.telegram.query_service._current_positions")
    @patch("integrations.telegram.query_service._account_summary")
    @patch("integrations.telegram.query_service.store.get_latest_reports_sync")
    @patch("integrations.telegram.query_service.store.get_latest_macro_consensus_sync")
    @patch("integrations.telegram.query_service.store.get_symbol_event_state_sync")
    @patch("integrations.telegram.query_service.store.list_brain_decision_traces_sync")
    @patch("integrations.telegram.query_service.store.get_recent_news_events_sync")
    @patch("integrations.telegram.query_service.store.get_live_experience_sync")
    @patch("integrations.telegram.query_service.store._list_actions_sync")
    @patch("integrations.telegram.query_service.get_config")
    def test_query_service_answers_from_local_evidence(
        self,
        mock_get_config,
        mock_actions,
        mock_experience,
        mock_news,
        mock_traces,
        mock_event_state,
        mock_macro,
        mock_reports,
        mock_account,
        mock_positions,
        _mock_summary,
    ):
        mock_get_config.return_value = {"stocks": {"crypto": {"symbols": ["SOL/USD", "ETH/USD"]}}}
        mock_account.return_value = {"equity": 1000.0, "cash": 250.0}
        mock_positions.return_value = [
            {"symbol": "SOL/USD", "qty": 1.25, "avg_entry_price": 120.0, "current_price": 126.0, "unrealized_pl": 7.5}
        ]
        mock_actions.return_value = [
            {"action_type": "order_submitted", "side": "buy", "symbol": "SOL/USD", "price": 120.0, "reason": "Breakout confirmed"}
        ]
        mock_experience.return_value = [
            {"strategy_used": "breakout_momentum", "side": "buy", "symbol": "SOL/USD", "entry_price": 120.0, "outcome_pnl_pct": 3.5, "event_score_at_entry": 18.0}
        ]
        mock_news.return_value = [
            {"source": "binance", "event_type": "listing", "sentiment": "extreme_positive", "impact_score": 92.0, "headline": "Binance will list SOL"}
        ]
        mock_traces.return_value = [
            {"decision": "buy", "final_score": 88.0, "submitted": 1, "block_reason": ""}
        ]
        mock_event_state.return_value = {"net_event_score": 18.0, "event_bias": "bullish", "hard_veto": 0, "top_event_type": "listing"}
        mock_macro.return_value = {"risk_multiplier": 1.18, "market_regime": "bull", "summary": "Macro supportive."}
        mock_reports.return_value = [{"report_type": "daily", "content": "Daily report summary."}]

        answer = query_service.answer_query("why did we buy SOL", use_llm=False, debug=True)

        self.assertIn("SOL/USD", answer)
        self.assertIn("Account snapshot", answer)
        self.assertIn("Current positions", answer)
        self.assertIn("Recent actions", answer)
        self.assertIn("Recent news", answer)
        self.assertIn("Recent decision traces", answer)
        self.assertIn("Macro consensus", answer)

    @patch("integrations.telegram.query_service._summarize_with_ollama", return_value=None)
    @patch("integrations.telegram.query_service._current_positions")
    @patch("integrations.telegram.query_service._account_summary")
    @patch("integrations.telegram.query_service.store.get_latest_reports_sync")
    @patch("integrations.telegram.query_service.store.get_latest_macro_consensus_sync")
    @patch("integrations.telegram.query_service.store.get_symbol_event_state_sync")
    @patch("integrations.telegram.query_service.store.get_live_experience_sync")
    @patch("integrations.telegram.query_service.get_config")
    def test_query_service_defaults_to_natural_language(
        self,
        mock_get_config,
        mock_experience,
        mock_event_state,
        mock_macro,
        mock_reports,
        mock_account,
        mock_positions,
        _mock_summary,
    ):
        mock_get_config.return_value = {"stocks": {"crypto": {"symbols": ["BTC/USD", "ETH/USD"], "active_brain": "drl_event_fusion"}}}
        mock_account.return_value = {"equity": 89027.12, "cash": 51876.02}
        mock_positions.return_value = [
            {"symbol": "BTC/USD", "qty": 0.51342, "avg_entry_price": 70967.089279229, "current_price": 68464.6, "unrealized_pl": -1284.828196, "unrealized_plpc": -0.0353}
        ]
        mock_experience.return_value = [
            {"strategy_used": "position_golden_cross", "side": "buy", "symbol": "BTC/USD", "entry_price": 67014.196, "outcome_pnl_pct": None, "event_score_at_entry": None}
        ]
        mock_event_state.return_value = {"net_event_score": -4.0, "event_bias": "bearish", "hard_veto": 0, "top_event_type": "regulation"}
        mock_macro.return_value = {"risk_multiplier": 0.66, "market_regime": "bear", "summary": "SEC pressure remains high. ETF flows mixed."}
        mock_reports.return_value = [{"report_type": "hourly", "content": "Hourly report summary."}]

        answer = query_service.answer_query("what is my btc", use_llm=False)

        self.assertIn("You currently hold BTC/USD.", answer)
        self.assertIn("Macro context is bear with a 0.66 risk multiplier.", answer)
        self.assertIn("Ask next:", answer)
        self.assertNotIn("Query plan:", answer)


class TelegramQueryPlannerContracts(unittest.TestCase):
    @patch("integrations.telegram.query_service._plan_with_ollama", return_value=None)
    @patch("integrations.telegram.query_service.get_config")
    def test_build_query_plan_for_live_news_question(self, mock_get_config, _mock_plan):
        mock_get_config.return_value = {"stocks": {"crypto": {"symbols": ["SOL/USD", "ETH/USD"]}}}
        plan = query_service.build_query_plan("what news affected SOL today")
        self.assertEqual(plan["intent"], "news_analysis")
        self.assertEqual(plan["symbol"], "SOL/USD")
        self.assertTrue(plan["include_live_news"])
        self.assertIn("live_news", plan["datasets"])

    @patch("integrations.telegram.query_service._plan_with_ollama", return_value=None)
    @patch("integrations.telegram.query_service.get_config")
    def test_build_query_plan_for_event_edge_question(self, mock_get_config, _mock_plan):
        mock_get_config.return_value = {"stocks": {"crypto": {"symbols": ["SOL/USD", "ETH/USD"]}}}
        plan = query_service.build_query_plan("which event types lose the most money")
        self.assertEqual(plan["intent"], "event_edge_analysis")
        self.assertEqual(plan["symbol"], "")
        self.assertIn("event_edge_stats", plan["calculations"])

    @patch("integrations.telegram.query_service._plan_with_ollama", return_value=None)
    @patch("integrations.telegram.query_service.get_config")
    def test_build_query_plan_for_historical_news_question(self, mock_get_config, _mock_plan):
        mock_get_config.return_value = {"stocks": {"crypto": {"symbols": ["SOL/USD", "ETH/USD"]}}}
        plan = query_service.build_query_plan("what news affected SOL yesterday")
        self.assertEqual(plan["intent"], "news_analysis")
        self.assertEqual(plan["symbol"], "SOL/USD")
        self.assertFalse(plan["include_live_news"])
        self.assertNotIn("live_news", plan["datasets"])

    @patch("integrations.telegram.query_service.httpx.post")
    @patch("integrations.telegram.query_service.ollama_url", return_value="http://ollama.local/api/chat")
    @patch("integrations.telegram.query_service.get_config")
    def test_plan_with_ollama_includes_bot_knowledge(self, mock_get_config, _mock_ollama_url, mock_post):
        class _Response:
            def raise_for_status(self):
                return None
            def json(self):
                return {"message": {"content": '{"intent":"position_status","symbol":"ETH/USD","lookback_days":7,"limit":5,"datasets":["account_summary"],"calculations":["position_snapshot"],"include_live_news":false}'}}

        mock_get_config.return_value = {"stocks": {"crypto": {"symbols": ["ETH/USD"], "telegram_ollama_model": "qwen3:8b", "active_brain": "drl_event_fusion"}}}
        mock_post.return_value = _Response()

        plan = query_service._plan_with_ollama("what is my eth")

        self.assertEqual(plan["symbol"], "ETH/USD")
        system_prompt = mock_post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertIn("live_experience stores trade memory", system_prompt)
        self.assertIn("drl_event_fusion", system_prompt)

    @patch("integrations.telegram.query_service._summarize_with_ollama", return_value=None)
    @patch("integrations.telegram.query_service._recent_reports", return_value=[])
    @patch("integrations.telegram.query_service.store.get_live_experience_sync")
    def test_answer_query_event_edge_analysis(self, mock_experience, _mock_reports, _mock_summary):
        mock_experience.return_value = [
            {"symbol": "SOL/USD", "top_event_type_at_entry": "listing", "outcome_pnl_pct": -4.0},
            {"symbol": "ETH/USD", "top_event_type_at_entry": "listing", "outcome_pnl_pct": -2.0},
            {"symbol": "BTC/USD", "top_event_type_at_entry": "etf", "outcome_pnl_pct": 3.0},
        ]
        answer = query_service.answer_query("which event types lose the most money", use_llm=False, debug=True)
        self.assertIn("event_edge_ranking", answer)
        self.assertIn("listing", answer)
        self.assertIn("etf", answer)

    @patch("integrations.telegram.query_service._summarize_with_ollama", return_value=None)
    @patch("integrations.telegram.query_service._live_news")
    @patch("integrations.telegram.query_service.store.get_recent_news_events_sync")
    @patch("integrations.telegram.query_service.store.get_latest_reports_sync", return_value=[])
    @patch("integrations.telegram.query_service.store.get_latest_macro_consensus_sync", return_value={})
    @patch("integrations.telegram.query_service.get_config")
    def test_answer_query_news_uses_stored_and_live_sources(
        self,
        mock_get_config,
        _mock_macro,
        _mock_reports,
        mock_news_events,
        mock_live_news,
        _mock_summary,
    ):
        mock_get_config.return_value = {"stocks": {"crypto": {"symbols": ["SOL/USD", "ETH/USD"]}}}
        mock_news_events.return_value = [
            {"source": "cryptopanic", "headline": "Stored SOL headline", "impact_score": 61.0, "sentiment": "positive", "event_type": "upgrade"}
        ]
        mock_live_news.return_value = [
            {"source": "binance_live", "title": "Live Binance SOL listing rumor"}
        ]
        answer = query_service.answer_query("what news affected SOL today", use_llm=False, debug=True)
        self.assertIn("Recent news", answer)
        self.assertIn("Live news", answer)
        self.assertIn("Stored SOL headline", answer)
        self.assertIn("Live Binance SOL listing rumor", answer)


if __name__ == "__main__":
    unittest.main()
