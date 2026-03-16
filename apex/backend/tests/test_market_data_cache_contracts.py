from datetime import datetime, timezone
from types import SimpleNamespace
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.crypto import market_data


class _MiniFrame:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    @property
    def empty(self):
        return not self.rows

    def sort_values(self, key):
        self.rows = sorted(self.rows, key=lambda row: row[key])
        return self

    def reset_index(self, drop=True):
        return self

    def copy(self):
        return _MiniFrame(list(self.rows))

    def __len__(self):
        return len(self.rows)


class MarketDataCacheContracts(unittest.TestCase):
    def setUp(self):
        with market_data._bar_cache_lock:
            market_data._bar_cache.clear()

    def _response(self):
        bars = [
            SimpleNamespace(
                timestamp=datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc),
                open=100.0,
                high=102.0,
                low=99.0,
                close=101.0,
                volume=12.5,
                trade_count=4,
                vwap=100.8,
            )
        ]
        return SimpleNamespace(data={"BTC/USD": bars})

    @patch("services.crypto.market_data.get_crypto_data_client")
    def test_fetch_bars_uses_fresh_cache_before_refetch(self, mock_client_factory):
        client = MagicMock()
        client.get_crypto_bars.return_value = self._response()
        mock_client_factory.return_value = client

        with patch.object(market_data.pd, "DataFrame", _MiniFrame):
            first = market_data.fetch_bars("BTC/USD", timeframe="15Min", limit=30, mode="paper")
            second = market_data.fetch_bars("BTC/USD", timeframe="15Min", limit=30, mode="paper")

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(client.get_crypto_bars.call_count, 1)

    @patch("services.crypto.market_data.get_crypto_data_client")
    def test_warm_bars_cache_populates_multiple_symbols_in_one_request(self, mock_client_factory):
        client = MagicMock()
        client.get_crypto_bars.return_value = SimpleNamespace(data={
            "BTC/USD": self._response().data["BTC/USD"],
            "ETH/USD": self._response().data["BTC/USD"],
        })
        mock_client_factory.return_value = client

        with patch.object(market_data.pd, "DataFrame", _MiniFrame):
            result = market_data.warm_bars_cache(["BTC/USD", "ETH/USD"], timeframe="15Min", limit=30, mode="paper", chunk_size=10)
            btc = market_data.fetch_bars("BTC/USD", timeframe="15Min", limit=30, mode="paper")
            eth = market_data.fetch_bars("ETH/USD", timeframe="15Min", limit=30, mode="paper")

        self.assertEqual(result["requested"], 2)
        self.assertEqual(result["warmed"], 2)
        self.assertEqual(client.get_crypto_bars.call_count, 1)
        self.assertEqual(len(btc), 1)
        self.assertEqual(len(eth), 1)

    @patch("services.crypto.market_data.get_crypto_data_client")
    def test_fetch_bars_falls_back_to_stale_cache_after_timeouts(self, mock_client_factory):
        client = MagicMock()
        client.get_crypto_bars.side_effect = [
            self._response(),
            RuntimeError("timeout"),
            RuntimeError("timeout"),
            RuntimeError("timeout"),
        ]
        mock_client_factory.return_value = client

        with patch.object(market_data.pd, "DataFrame", _MiniFrame):
            first = market_data.fetch_bars("BTC/USD", timeframe="15Min", limit=30, mode="paper")
            key = next(iter(market_data._bar_cache.keys()))
            market_data._bar_cache[key]["ts_sec"] = time.time() - (market_data._bar_cache_ttl_sec("15Min") + 5)

            second = market_data.fetch_bars("BTC/USD", timeframe="15Min", limit=30, mode="paper")

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(client.get_crypto_bars.call_count, 4)

    @patch("services.crypto.market_data.store.get_nearest_universe_mid_price_sync")
    @patch("services.crypto.market_data.get_crypto_data_client")
    def test_fetch_price_near_ts_falls_back_to_universe_snapshot_mid_price(self, mock_client_factory, mock_snapshot_price):
        mock_client_factory.side_effect = RuntimeError("alpaca unavailable")
        mock_snapshot_price.return_value = {"price": 101.25, "ts": 1_000_000}

        price = market_data.fetch_price_near_ts("BTC/USD", target_ts_ms=1_000_000, mode="paper", window_min=60)

        self.assertEqual(price, 101.25)
        mock_snapshot_price.assert_called_once_with("BTC/USD", 1_000_000, window_min=60)


if __name__ == "__main__":
    unittest.main()
