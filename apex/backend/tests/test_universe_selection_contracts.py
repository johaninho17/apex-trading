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
sys.modules.setdefault("httpx", MagicMock())

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.crypto import bot, store, universe


class UniverseSelectionContracts(unittest.TestCase):
    def setUp(self):
        universe._UNIVERSE_CACHE["ts_sec"] = 0.0
        universe._UNIVERSE_CACHE["key"] = None
        universe._UNIVERSE_CACHE["result"] = None

    @patch("services.crypto.bot._discover_symbols", return_value=[])
    @patch("services.crypto.bot.get_config")
    def test_current_crypto_config_merges_shared_news_and_universe(self, mock_get_config, _mock_discover):
        mock_get_config.return_value = {
            "stocks": {
                "crypto": {
                    "active_exchange": "alpaca",
                    "account_mode": "paper",
                    "active_brain": "drl_event_fusion",
                    "news": {
                        "enabled": True,
                        "binance_poll_sec": 20,
                        "crypto_news_poll_sec": 300,
                    },
                    "universe": {
                        "refresh_sec": 777,
                        "core_symbols": ["BTC/USD"],
                    },
                    "alpaca-paper": {
                        "symbols": ["ETH/USD"],
                        "auto_discover_pairs": False,
                    },
                }
            }
        }

        cfg = bot.current_crypto_config()

        self.assertEqual(cfg["news"]["binance_poll_sec"], 20)
        self.assertEqual(cfg["news"]["crypto_news_poll_sec"], 300)
        self.assertEqual(cfg["universe"]["refresh_sec"], 777)
        self.assertEqual(cfg["universe"]["core_symbols"], ["BTC/USD"])
        self.assertEqual(cfg["active_brain"], "drl_event_fusion")

    @patch("services.crypto.universe._load_historical_edges", return_value={})
    @patch("services.crypto.universe._discover_raw_symbols", return_value=["DOGE/USD", "AVAX/USD", "ADA/USD"])
    @patch("services.crypto.universe._load_event_states")
    @patch("services.crypto.universe._rank_symbol")
    @patch("services.crypto.universe.store.record_universe_snapshot_sync", return_value=1)
    def test_select_trade_universe_keeps_forced_and_top_ranked_discovery(
        self,
        _mock_record,
        mock_rank,
        mock_event_states,
        _mock_discover,
        _mock_edges,
    ):
        mock_event_states.return_value = {
            "XYZ/USD": {
                "symbol": "XYZ/USD",
                "net_event_score": 18.0,
                "top_event_score": 82.0,
                "hard_veto": 0,
                "golden_trade_flag": 0,
            }
        }
        rank_map = {
            "BTC/USD": 60.0,
            "ETH/USD": 58.0,
            "LTC/USD": 54.0,
            "XYZ/USD": 93.0,
            "DOGE/USD": 88.0,
            "AVAX/USD": 79.0,
            "ADA/USD": 45.0,
        }

        def _fake_rank(symbol, mode, universe_cfg, bucket, event_states, historical_edges):
            if symbol not in rank_map:
                return None
            return {
                "symbol": symbol,
                "bucket": bucket,
                "selected": False,
                "spread_bps": 15.0,
                "liquidity_score": 70.0,
                "spread_score": 82.0,
                "volume_score": 76.0,
                "volatility_score": 63.0,
                "event_score": 91.0 if symbol == "XYZ/USD" else 20.0,
                "historical_edge_score": 55.0,
                "total_rank": rank_map[symbol],
                "passes_filters": symbol != "ADA/USD",
            }

        mock_rank.side_effect = _fake_rank
        cfg = {
            "auto_discover_pairs": True,
            "manual_symbols": ["ETH/USD"],
            "universe": {
                "core_symbols": ["BTC/USD"],
                "discovery_limit_raw": 10,
                "discovery_limit_ranked": 2,
                "event_override_limit": 4,
                "refresh_sec": 900,
            },
        }

        result = universe.select_trade_universe(cfg, mode="paper", held_symbols=["LTC/USD"])

        self.assertEqual(result["promoted_symbols"], ["XYZ/USD"])
        self.assertIn("BTC/USD", result["symbols"])
        self.assertIn("ETH/USD", result["symbols"])
        self.assertIn("LTC/USD", result["symbols"])
        self.assertIn("XYZ/USD", result["symbols"])
        self.assertIn("DOGE/USD", result["symbols"])
        self.assertIn("AVAX/USD", result["symbols"])
        self.assertNotIn("ADA/USD", result["symbols"])

        selected = {row["symbol"]: row["selected"] for row in result["rankings"]}
        self.assertTrue(selected["XYZ/USD"])
        self.assertTrue(selected["DOGE/USD"])
        self.assertTrue(selected["AVAX/USD"])
        self.assertFalse(selected["ADA/USD"])

    def test_universe_snapshot_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "crypto_bot.db")
            with patch.object(store, "_DATA_DIR", tmpdir), patch.object(store, "_DB_FILE", db_file):
                snapshot_id = store.record_universe_snapshot_sync(
                    account_mode="paper",
                    selected_symbols=["BTC/USD", "ETH/USD"],
                    promoted_symbols=["XYZ/USD"],
                    rankings=[
                        {
                            "symbol": "BTC/USD",
                            "bucket": "core",
                            "selected": True,
                            "total_rank": 88.5,
                            "spread_bps": 11.2,
                            "liquidity_score": 91.0,
                            "spread_score": 89.0,
                            "volume_score": 95.0,
                            "volatility_score": 60.0,
                            "event_score": 14.0,
                            "historical_edge_score": 58.0,
                        }
                    ],
                    payload={"selected_count": 2},
                )
                latest = store.get_latest_universe_snapshot_sync()

        self.assertGreater(snapshot_id, 0)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["account_mode"], "paper")
        self.assertEqual(latest["selected_symbols"], ["BTC/USD", "ETH/USD"])
        self.assertEqual(latest["promoted_symbols"], ["XYZ/USD"])
        self.assertEqual(latest["payload"]["selected_count"], 2)
        self.assertEqual(len(latest["rankings"]), 1)
        self.assertEqual(latest["rankings"][0]["symbol"], "BTC/USD")
        self.assertTrue(latest["rankings"][0]["selected"])


if __name__ == "__main__":
    unittest.main()
