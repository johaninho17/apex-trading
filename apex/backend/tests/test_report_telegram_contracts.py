import os
import sys
import unittest
from unittest.mock import patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.crypto import report_generator


class ReportTelegramContracts(unittest.TestCase):
    @patch('services.crypto.store._record_action_sync')
    @patch('services.crypto.store.record_bot_report_sync')
    @patch('integrations.telegram.telegram_client.send_report')
    @patch('services.crypto.report_generator._judge_decisions')
    @patch('services.crypto.report_generator._call_gemini')
    @patch('services.crypto.report_generator._build_prompt')
    @patch('services.crypto.report_generator._gather_coin_prices')
    @patch('services.crypto.report_generator._build_metrics')
    @patch('services.crypto.report_generator._gather_snapshot')
    def test_generate_report_sends_full_report_to_telegram(
        self,
        mock_snapshot,
        mock_metrics,
        mock_prices,
        mock_prompt,
        mock_call_gemini,
        mock_judge,
        mock_send_report,
        mock_record_report,
        mock_record_action,
    ):
        mock_snapshot.return_value = {'bot': {'running': True}}
        mock_metrics.return_value = {'equity_change_pct': -2.59}
        mock_prices.return_value = 'BTC 68000'
        mock_prompt.return_value = 'prompt'
        mock_call_gemini.return_value = 'Full Gemini daily report text.'
        mock_judge.return_value = {'grade': 'B', 'score': 82}

        result = report_generator.generate_report('daily')

        self.assertEqual(result['content'], 'Full Gemini daily report text.')
        mock_record_report.assert_called_once()
        mock_send_report.assert_called_once_with('daily', 'Full Gemini daily report text.', grade={'grade': 'B', 'score': 82})
        mock_record_action.assert_called_once()

    def test_build_prompt_includes_breadth_and_news_coverage_context(self):
        snapshot = {
            "bot": {"running": True, "iterations": 12},
            "oracle": {
                "score": 1.22,
                "market_regime": "bull",
                "summary": "ETH and SOL outperformed while BTC held support.",
                "breadth_state": "majors_led",
                "drivers": ["ETH staking demand improved", "SOL led exchange volume"],
                "mentioned_symbols": ["ETH", "SOL", "BTC"],
                "summary_source": "fresh_gemini",
                "age_min": 10,
            },
            "macro_consensus": {"risk_multiplier": 1.22, "market_regime": "bull", "macro_sentiment_score": 61, "summary": "Broadly constructive."},
            "tracked_symbol_context": [
                {"symbol": "SOL/USD", "active_state": "active", "active_reason": "top_rank", "saved_manual": False, "monitor_tier": "active"},
            ],
            "narrative_leaders": {
                "SOL": {"label": "positive", "reason": "SOL led exchange volumes after a positive product update."},
            },
            "news_provider_health": {
                "binance": {"status": "blocked", "info": "waf"},
                "cryptopanic": {"status": "quota_exceeded", "info": "monthly quota"},
            },
            "news_symbol_coverage": {
                "recent_total": 8,
                "symbol_tagged": 0,
                "unattributed": 8,
                "source_counts": {"cryptopanic": 8},
                "context_counts": {"unavailable": 12},
            },
            "recent_decision_outcomes": [],
            "recent_candidate_traces": [],
            "recent_risk_transitions": [],
            "recent_news_events": [],
            "recent_actions": [],
            "open_positions": [],
            "equity_perf": {},
            "activity_governor": {},
        }
        metrics = {"news_event_count": 8, "positive_news_events": 3, "negative_news_events": 1, "hard_veto_events": 0}
        prompt = report_generator._build_prompt("hourly", snapshot, metrics, "SOL: $120.00, ETH: $3,200.00")
        self.assertIn("Oracle Breadth: majors_led", prompt)
        self.assertIn("Oracle Drivers: ETH staking demand improved, SOL led exchange volume", prompt)
        self.assertIn("Tracked Symbol Context:", prompt)
        self.assertIn("News Provider Health: binance=blocked, cryptopanic=quota_exceeded", prompt)
        self.assertIn("News Coverage Warning: Recent news exists but symbol attribution is limited.", prompt)


if __name__ == '__main__':
    unittest.main()
