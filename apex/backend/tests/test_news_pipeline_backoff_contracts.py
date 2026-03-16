import os
import sys
import unittest
from unittest.mock import patch
import httpx

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.crypto import news_pipeline


class NewsPipelineBackoffContracts(unittest.TestCase):
    def setUp(self):
        news_pipeline._last_binance_poll_sec = 0.0
        news_pipeline._last_crypto_poll_sec = 0.0
        news_pipeline._cryptopanic_backoff_until_sec = 0.0
        news_pipeline._cryptopanic_dynamic_asset_limit = 0
        news_pipeline._news_poll_primed = False
        news_pipeline._BINANCE_POLL_META.clear()
        news_pipeline._BINANCE_POLL_META.update({"status": "idle", "info": ""})
        news_pipeline._CRYPTOPANIC_POLL_META.clear()
        news_pipeline._CRYPTOPANIC_POLL_META.update({"status": "idle", "info": ""})

    def test_select_cryptopanic_assets_prefers_forced_then_top_discovery(self):
        universe_state = {
            "forced_symbols": ["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD"],
            "rankings": [
                {"symbol": "BTC/USD", "bucket": "core", "selected": True},
                {"symbol": "DOGE/USD", "bucket": "discovery", "selected": True},
                {"symbol": "AVAX/USD", "bucket": "discovery", "selected": True},
                {"symbol": "ADA/USD", "bucket": "discovery", "selected": False},
            ],
        }
        assets = news_pipeline._select_cryptopanic_assets(
            symbols=["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "AVAX/USD", "ADA/USD"],
            universe_state=universe_state,
            news_cfg={"cryptopanic_asset_limit": 5},
        )
        self.assertEqual(assets, ["BTC", "ETH", "SOL", "LINK", "DOGE"])

    @patch("services.crypto.news_pipeline.refresh_symbol_event_state_sync", return_value={"symbol": "DOGE/USD"})
    @patch("services.crypto.news_pipeline.store.record_news_event_sync")
    def test_ingest_news_items_recovers_non_btc_symbol_from_requested_asset_context(
        self,
        mock_record_news_event,
        _mock_refresh_state,
    ):
        persisted = news_pipeline.ingest_normalized_news_items_sync(
            [
                {
                    "external_id": "cp:doge-1",
                    "published_at": 1_000_000,
                    "title": "Dogecoin momentum builds after new ETF chatter",
                    "body": "Analysts are watching Dogecoin after renewed filing speculation.",
                    "url": "https://example.test/doge",
                    "currencies": [],
                    "base_asset": "",
                    "requested_base_assets": ["DOGE", "BTC", "ETH"],
                }
            ],
            source="cryptopanic",
            tradable_symbols=["DOGE/USD", "BTC/USD", "ETH/USD"],
        )

        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["base_asset"], "DOGE")
        self.assertEqual(persisted[0]["symbol"], "DOGE/USD")
        recorded = mock_record_news_event.call_args.args[0]
        self.assertEqual(recorded["symbol"], "DOGE/USD")

    @patch("services.crypto.news_pipeline.httpx.get")
    def test_fetch_cryptopanic_posts_preserves_requested_assets_for_symbol_recovery(self, mock_get):
        with patch.dict(os.environ, {"CRYPTOPANIC_API_TOKEN": "test-token"}, clear=False):
            mock_response = unittest.mock.MagicMock()
            mock_response.json.return_value = {
                "results": [
                    {
                        "id": "post-1",
                        "title": "Solana ecosystem activity climbs after validator upgrades",
                        "url": "https://example.test/solana",
                        "metadata": {"description": "Developers highlighted Solana throughput gains."},
                        "currencies": [],
                    }
                ]
            }
            mock_response.raise_for_status.return_value = None
            mock_get.return_value = mock_response

            items = news_pipeline.fetch_cryptopanic_posts_sync(base_assets=["SOL", "BTC"], limit=5)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["base_asset"], "SOL")
        self.assertEqual(items[0]["requested_base_assets"], ["SOL", "BTC"])
        self.assertEqual(news_pipeline._CRYPTOPANIC_POLL_META["status"], "ok")

    @patch("services.crypto.news_pipeline.httpx.get")
    def test_fetch_cryptopanic_posts_raises_quota_exceeded_on_monthly_quota_message(self, mock_get):
        with patch.dict(os.environ, {"CRYPTOPANIC_API_TOKEN": "test-token"}, clear=False):
            mock_response = unittest.mock.MagicMock()
            mock_response.status_code = 429
            mock_response.headers = {}
            mock_response.json.return_value = {
                "status": False,
                "info": "API monthly quota exceeded - Upgrade your API plan: /developers/api/plans/",
            }
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "429",
                request=unittest.mock.MagicMock(),
                response=mock_response,
            )
            mock_get.return_value = mock_response

            with self.assertRaises(news_pipeline._CryptoPanicQuotaExceeded) as ctx:
                news_pipeline.fetch_cryptopanic_posts_sync(base_assets=["BTC"], limit=5)

        self.assertIn("monthly quota exceeded", str(ctx.exception.info).lower())

    @patch("services.crypto.news_pipeline.fetch_binance_announcements_sync", return_value=[])
    @patch("services.crypto.news_pipeline.ingest_normalized_news_items_sync", return_value=[])
    @patch("services.crypto.news_pipeline.fetch_cryptopanic_posts_sync")
    @patch("services.crypto.news_pipeline.select_trade_universe")
    @patch("services.crypto.news_pipeline.current_crypto_config")
    def test_run_news_poll_cycle_sets_cryptopanic_backoff_on_429(
        self,
        mock_cfg,
        mock_universe,
        mock_fetch_crypto,
        _mock_ingest,
        _mock_binance,
    ):
        mock_cfg.return_value = {
            "account_mode": "paper",
            "ollama_model": "qwen3:8b",
            "symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
                "news": {
                    "enabled": True,
                    "watchlist_only": False,
                    "binance_poll_sec": 20,
                    "crypto_news_poll_sec": 300,
                    "cryptopanic_asset_limit": 5,
                    "cryptopanic_backoff_sec": 900,
                    "sources": {"binance": True, "cryptopanic": True, "rss": False},
                },
            }
        mock_universe.return_value = {
            "symbols": ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD"],
            "forced_symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
            "rankings": [{"symbol": "DOGE/USD", "bucket": "discovery", "selected": True}],
        }
        mock_fetch_crypto.side_effect = news_pipeline._CryptoPanicRateLimited("429")
        news_pipeline._news_poll_primed = True
        news_pipeline._last_binance_poll_sec = 0.0
        news_pipeline._last_crypto_poll_sec = 0.0

        result = news_pipeline.run_news_poll_cycle_sync()

        self.assertEqual(result["cryptopanic"], 0)
        self.assertEqual(result["cryptopanic_backoff_sec"], 900)
        self.assertGreater(news_pipeline._cryptopanic_backoff_until_sec, 0.0)
        args = mock_fetch_crypto.call_args.kwargs
        self.assertEqual(args["base_assets"], ["BTC", "ETH", "SOL", "DOGE"])

        mock_fetch_crypto.reset_mock()
        result2 = news_pipeline.run_news_poll_cycle_sync()
        self.assertIn("cryptopanic_backoff_sec", result2)
        mock_fetch_crypto.assert_not_called()

    @patch("services.crypto.news_pipeline.fetch_binance_announcements_sync", return_value=[])
    @patch("services.crypto.news_pipeline.ingest_normalized_news_items_sync", return_value=[])
    @patch("services.crypto.news_pipeline.fetch_cryptopanic_posts_sync")
    @patch("services.crypto.news_pipeline.select_trade_universe")
    @patch("services.crypto.news_pipeline.current_crypto_config")
    def test_run_news_poll_cycle_surfaces_cryptopanic_quota_exhaustion(
        self,
        mock_cfg,
        mock_universe,
        mock_fetch_crypto,
        _mock_ingest,
        _mock_binance,
    ):
        mock_cfg.return_value = {
            "account_mode": "paper",
            "ollama_model": "qwen3:8b",
            "symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
                "news": {
                    "enabled": True,
                    "watchlist_only": False,
                    "binance_poll_sec": 20,
                    "crypto_news_poll_sec": 300,
                    "cryptopanic_asset_limit": 5,
                    "cryptopanic_backoff_sec": 900,
                    "sources": {"binance": True, "cryptopanic": True, "rss": False},
                },
            }
        mock_universe.return_value = {
            "symbols": ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD"],
            "forced_symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
            "rankings": [{"symbol": "DOGE/USD", "bucket": "discovery", "selected": True}],
        }
        mock_fetch_crypto.side_effect = news_pipeline._CryptoPanicQuotaExceeded(
            "429",
            info="API monthly quota exceeded - Upgrade your API plan: /developers/api/plans/",
        )
        news_pipeline._news_poll_primed = True
        news_pipeline._last_binance_poll_sec = 0.0
        news_pipeline._last_crypto_poll_sec = 0.0

        result = news_pipeline.run_news_poll_cycle_sync()

        self.assertEqual(result["cryptopanic"], 0)
        self.assertEqual(result["cryptopanic_status"], "quota_exceeded")
        self.assertIn("monthly quota exceeded", result["cryptopanic_info"].lower())
        self.assertEqual(result["cryptopanic_asset_limit"], 5)
        self.assertGreaterEqual(result["cryptopanic_backoff_sec"], 86400)
        self.assertGreater(news_pipeline._cryptopanic_backoff_until_sec, 0.0)

    @patch("services.crypto.news_pipeline._fetch_binance_exchangeinfo_updates_sync", return_value=[])
    @patch("services.crypto.news_pipeline._fetch_binance_cms_articles_sync")
    def test_fetch_binance_announcements_surfaces_cms_errors(self, mock_cms, _mock_exchangeinfo):
        mock_cms.side_effect = RuntimeError("cms unavailable")

        items = news_pipeline.fetch_binance_announcements_sync(limit=5)

        self.assertEqual(items, [])
        self.assertEqual(news_pipeline._BINANCE_POLL_META["status"], "error")
        self.assertIn("cms unavailable", news_pipeline._BINANCE_POLL_META["info"].lower())


if __name__ == "__main__":
    unittest.main()
