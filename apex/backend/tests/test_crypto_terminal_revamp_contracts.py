import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from starlette.routing import Match

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.crypto import policy_overlays, store


class CryptoOverlayContracts(unittest.TestCase):
    def setUp(self):
        policy_overlays._OVERLAY_CACHE["ts_sec"] = 0.0
        policy_overlays._OVERLAY_CACHE["items"] = []

    def test_policy_overlays_and_brain_score_persist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "crypto_bot.db")
            with patch.object(store, "_DATA_DIR", tmpdir), patch.object(store, "_DB_FILE", db_file):
                trace_buy = store.record_brain_decision_trace_sync(
                    symbol="SOL/USD",
                    brain_mode="drl_event_fusion",
                    decision="buy",
                    final_score=82.0,
                    submitted=True,
                    payload={"signal": {"close": 120.0}},
                )
                trace_block = store.record_brain_decision_trace_sync(
                    symbol="SOL/USD",
                    brain_mode="drl_event_fusion",
                    decision="buy",
                    final_score=77.0,
                    submitted=False,
                    block_reason="risk_gate",
                    payload={"signal": {"close": 118.0}},
                )
                store.record_decision_outcome_sync(
                    trace_id=trace_buy,
                    horizon_min=30,
                    decision_ts=1_700_000_000_000,
                    symbol="SOL/USD",
                    decision="buy",
                    submitted=True,
                    block_reason="",
                    ref_price=120.0,
                    outcome_price=125.0,
                    pnl_pct=4.16,
                    outcome_label="good_entry",
                    payload={},
                )
                store.record_decision_outcome_sync(
                    trace_id=trace_block,
                    horizon_min=60,
                    decision_ts=1_700_000_030_000,
                    symbol="SOL/USD",
                    decision="buy",
                    submitted=False,
                    block_reason="risk_gate",
                    ref_price=118.0,
                    outcome_price=122.0,
                    pnl_pct=3.38,
                    outcome_label="bad_block",
                    payload={},
                )
                store.record_live_experience_sync(
                    symbol="SOL/USD",
                    side="buy",
                    entry_ts=1_700_000_000_000,
                    strategy_used="breakout_momentum",
                    entry_price=120.0,
                    notional=500.0,
                    gemini_score=1.1,
                )
                exp_rows = store.get_live_experience_sync(limit=10, closed_only=False)
                store.close_live_experience_sync(
                    int(exp_rows[0]["id"]),
                    exit_ts=1_700_000_060_000,
                    exit_price=126.0,
                    outcome_pnl_pct=5.0,
                    hold_duration_min=45.0,
                    exit_reason="tp",
                )

                with patch.object(policy_overlays, "_load_cfg", return_value={
                    **policy_overlays.DEFAULT_POLICY_OVERLAY_CONFIG,
                    "min_samples": 1,
                    "brain_self_score_interval_sec": 1,
                }):
                    result = policy_overlays.refresh_symbol_policy_overlays(force=True)
                    latest_overlay = store.get_symbol_policy_overlay_sync("SOL/USD")
                    brain_score = policy_overlays.ensure_brain_self_score_snapshot(force=True)
                    brain_history = store.list_brain_self_score_history_sync(limit=5)

        self.assertTrue(result["enabled"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(latest_overlay["symbol"], "SOL/USD")
        self.assertGreater(float(latest_overlay["score_delta"]), 0.0)
        self.assertLessEqual(abs(float(latest_overlay["score_delta"])), 20.0)
        self.assertGreaterEqual(float(latest_overlay["size_multiplier"]), 0.75)
        self.assertLessEqual(float(latest_overlay["size_multiplier"]), 1.25)
        self.assertIsNotNone(brain_score)
        self.assertGreaterEqual(float(brain_score["score"]), 0.0)
        self.assertLessEqual(float(brain_score["score"]), 100.0)
        self.assertEqual(len(brain_history), 1)


class CryptoRouterContracts(unittest.TestCase):
    def test_canonical_and_compat_routes_share_crypto_contract(self):
        from routers import crypto as crypto_router

        async def _exercise():
            with patch("routers.crypto.get_crypto_health", new=AsyncMock(return_value={"status": "fresh", "provider_state": {"status": "fresh"}, "services": {}})), \
                 patch("routers.crypto.get_terminal_summary", new=AsyncMock(return_value={"status": "fresh", "account": {"equity": 1000.0}})), \
                 patch("routers.crypto.get_scanner", return_value={"status": "fresh", "items": [{"symbol": "BTC/USD"}]}), \
                 patch("routers.crypto.get_universe_view", return_value={"status": "fresh", "items": [{"symbol": "BTC/USD", "event_bias": "bullish"}]}):
                return (
                    await crypto_router.crypto_health(),
                    await crypto_router.terminal_summary(),
                    await crypto_router.scanner(),
                    await crypto_router.compat_hub(),
                )

        health, summary, scanner, hub = asyncio.run(_exercise())

        self.assertEqual(health["status"], "fresh")
        self.assertEqual(summary["status"], "fresh")
        self.assertEqual(scanner["status"], "fresh")
        self.assertEqual(hub["status"], "fresh")
        self.assertEqual(summary["account"]["equity"], 1000.0)
        self.assertEqual(scanner["items"][0]["symbol"], "BTC/USD")
        self.assertEqual(hub["top_coins"][0]["bias"], "bullish")

    def test_symbol_routes_accept_symbols_with_slashes(self):
        from routers import crypto as crypto_router

        app = FastAPI()
        app.include_router(crypto_router.router, prefix="/api/v1/crypto")
        scope = {"type": "http", "method": "GET", "path": "/api/v1/crypto/symbol/BTC/USD/overview"}

        matches = [
            route
            for route in app.router.routes
            if getattr(route, "path", "").endswith("/symbol/{symbol:path}/overview")
            and route.matches(scope)[0] == Match.FULL
        ]

        self.assertEqual(len(matches), 1)

    def test_symbol_snapshot_route_uses_canonical_contract(self):
        from routers import crypto as crypto_router

        async def _exercise():
            with patch(
                "routers.crypto.get_symbol_snapshot",
                new=AsyncMock(return_value={"status": "fresh", "symbol": "BTC/USD", "overview": {"symbol": "BTC/USD"}}),
            ):
                return await crypto_router.symbol_snapshot("BTC/USD")

        payload = asyncio.run(_exercise())
        self.assertEqual(payload["status"], "fresh")
        self.assertEqual(payload["symbol"], "BTC/USD")
        self.assertEqual(payload["overview"]["symbol"], "BTC/USD")


class CryptoSnapshotContracts(unittest.TestCase):
    def setUp(self):
        from services.crypto import terminal_data

        terminal_data._VIEW_CACHE.clear()
        terminal_data._SHELL_CFG_CACHE["ts_sec"] = 0.0
        terminal_data._SHELL_CFG_CACHE["payload"] = {}

    def test_crypto_health_prefers_cached_provider_state(self):
        from services.crypto import terminal_data

        async def _exercise():
            with patch.object(terminal_data.background_services, "service_status", return_value={"errors": []}), \
                 patch.object(terminal_data, "current_crypto_config", return_value={"account_mode": "paper"}), \
                 patch.object(terminal_data.market_data, "get_cached_account_summary", return_value={
                     "equity": 1234.5,
                     "cash": 678.9,
                     "cached": True,
                     "cache_age_sec": 5.0,
                 }), \
                 patch.object(terminal_data, "get_account", side_effect=AssertionError("should not hit live account")):
                return await terminal_data.get_crypto_health()

        payload = asyncio.run(_exercise())
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["provider_state"]["status"], "fresh")
        self.assertEqual(payload["provider_state"]["equity"], 1234.5)
        self.assertIn("shell_cache", payload)

    def test_terminal_summary_prefers_local_shell_contract_when_provider_cache_is_cold(self):
        from services.crypto import terminal_data

        async def _exercise():
            with patch.object(terminal_data.market_data, "get_cached_account_summary", return_value={}), \
                 patch.object(terminal_data.market_data, "get_cached_crypto_positions", return_value=[]), \
                 patch.object(terminal_data, "get_account", side_effect=AssertionError("summary should not hit live account")), \
                 patch.object(terminal_data, "get_positions", side_effect=AssertionError("summary should not hit live positions")), \
                 patch.object(terminal_data, "get_bot_status", new=AsyncMock(return_value={"runtime": {}, "persisted": {}})), \
                patch.object(terminal_data, "_latest_universe_snapshot", return_value={
                     "symbols": [],
                     "rankings": [],
                     "promoted_symbols": [],
                     "ts_ms": 1_700_000_000_000,
                     "status": "partial",
                 }), \
                patch.object(terminal_data, "_schedule_background_refresh") as refresh_mock, \
                patch.object(terminal_data, "schedule_shell_warmup") as warm_mock, \
                 patch.object(terminal_data.store, "get_latest_brain_self_score_sync", return_value={}), \
                 patch.object(terminal_data.store, "list_actions_page_sync", return_value=[]), \
                 patch("services.crypto.oracle.get_oracle_status", return_value={"age_sec": 0}), \
                 patch("services.crypto.oracle.get_current_oracle_state", return_value={}), \
                 patch("services.crypto.oracle.get_narrative_status", return_value={}), \
                 patch("services.crypto.report_generator.get_cached_report", return_value=None):
                payload = await terminal_data.get_terminal_summary(prefer_cached=True)
                return payload, refresh_mock.call_count, warm_mock.call_count

        payload, refresh_calls, warm_calls = asyncio.run(_exercise())
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["account"]["status"], "warming")
        self.assertEqual(payload["positions"], [])
        self.assertGreaterEqual(refresh_calls, 2)
        self.assertEqual(warm_calls, 0)

    def test_universe_and_scanner_use_latest_snapshot_without_live_recompute(self):
        from services.crypto import terminal_data

        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "crypto_bot.db")
            with patch.object(store, "_DATA_DIR", tmpdir), patch.object(store, "_DB_FILE", db_file):
                store.record_universe_snapshot_sync(
                    account_mode="paper",
                    selected_symbols=["BTC/USD", "ETH/USD"],
                    promoted_symbols=["SOL/USD"],
                    rankings=[
                        {
                            "symbol": "BTC/USD",
                            "bucket": "core",
                            "selected": True,
                            "total_rank": 91.4,
                            "spread_bps": 8.2,
                            "liquidity_score": 95.0,
                            "spread_score": 92.0,
                            "volume_score": 96.0,
                            "volatility_score": 61.0,
                            "event_score": 17.0,
                            "historical_edge_score": 54.0,
                            "payload": {"mid_price": 68123.45},
                        },
                        {
                            "symbol": "ETH/USD",
                            "bucket": "core",
                            "selected": True,
                            "total_rank": 83.7,
                            "spread_bps": 11.5,
                            "liquidity_score": 88.0,
                            "spread_score": 89.0,
                            "volume_score": 90.0,
                            "volatility_score": 58.0,
                            "event_score": 12.0,
                            "historical_edge_score": 52.0,
                            "payload": {"mid_price": 3521.2},
                        },
                    ],
                    payload={"universe_cfg": {"refresh_sec": 900}},
                )

                with patch.object(terminal_data, "select_trade_universe", side_effect=AssertionError("should not recompute live universe")), \
                     patch.object(terminal_data, "_schedule_background_refresh"), \
                     patch.object(terminal_data, "current_crypto_config", return_value={"account_mode": "paper"}), \
                     patch.object(terminal_data, "get_overlay_map", return_value={}), \
                     patch.object(terminal_data.market_data, "get_cached_crypto_positions", return_value=[]), \
                     patch.object(terminal_data.market_data, "get_cached_latest_quote", return_value={}):
                    universe = terminal_data.get_universe_view(limit=30)
                    scanner = terminal_data.get_scanner(limit=10)

        self.assertEqual(universe["status"], "fresh")
        self.assertEqual(universe["items"][0]["symbol"], "BTC/USD")
        self.assertEqual(scanner["status"], "fresh")
        self.assertEqual(scanner["items"][0]["symbol"], "BTC/USD")
        self.assertEqual(scanner["items"][0]["price"], 68123.45)

    def test_symbol_snapshot_stays_on_lightweight_local_contract(self):
        from services.crypto import terminal_data

        async def _exercise():
            terminal_data._cache_set(
                "symbol-decisions:BTC/USD:8",
                {
                    "snapshot_ts": 1_700_000_000_100,
                    "decision_traces": [{"decision": "buy"}],
                    "decision_outcomes": [{"outcome_label": "good_entry"}],
                },
            )
            terminal_data._cache_set(
                "symbol-trades:BTC/USD:8",
                {
                    "snapshot_ts": 1_700_000_000_200,
                    "items": [{"symbol": "BTC/USD"}],
                },
            )
            terminal_data._cache_set(
                "symbol-news:BTC/USD:6:0",
                {
                    "snapshot_ts": 1_700_000_000_300,
                    "items": [{"headline": "ETF inflow"}],
                },
            )
            terminal_data._cache_set(
                "symbol-reports:BTC/USD",
                {
                    "snapshot_ts": 1_700_000_000_400,
                    "items": [{"report_type": "hourly"}],
                },
            )
            terminal_data._cache_set(
                "symbol-actions:BTC/USD:25",
                {
                    "snapshot_ts": 1_700_000_000_500,
                    "items": [{"ts": 1_700_000_000_500}],
                },
            )
            with patch.object(terminal_data, "get_symbol_overview", new=AsyncMock(return_value={
                "status": "fresh",
                "snapshot_ts": 1_700_000_000_000,
                "symbol": "BTC/USD",
            })), \
                 patch.object(terminal_data, "get_symbol_decisions", side_effect=AssertionError("snapshot should use cached decisions")), \
                 patch.object(terminal_data, "get_symbol_trades", side_effect=AssertionError("snapshot should use cached trades")), \
                 patch.object(terminal_data, "get_symbol_news", side_effect=AssertionError("snapshot should use cached news")), \
                 patch.object(terminal_data, "get_symbol_reports", side_effect=AssertionError("snapshot should use cached reports")), \
                 patch.object(terminal_data, "get_symbol_raw_context", side_effect=AssertionError("snapshot should not load raw context")):
                return await terminal_data.get_symbol_snapshot("BTC/USD")

        payload = asyncio.run(_exercise())
        self.assertEqual(payload["status"], "fresh")
        self.assertEqual(payload["symbol"], "BTC/USD")
        self.assertEqual(payload["context_counts"]["actions"], 1)
        self.assertEqual(len(payload["report_preview"]), 1)

    def test_symbol_snapshot_cold_miss_returns_partial_and_warms_in_background(self):
        from services.crypto import terminal_data

        async def _exercise():
            with patch.object(terminal_data, "get_symbol_overview", new=AsyncMock(return_value={
                "status": "fresh",
                "snapshot_ts": 1_700_000_000_000,
                "symbol": "BTC/USD",
                "quote": {"mid_price": 68000.0},
            })), \
                 patch.object(terminal_data, "_schedule_background_refresh") as refresh_mock, \
                 patch.object(terminal_data, "_refresh_symbol_snapshot_cache", side_effect=AssertionError("snapshot refresh should be deferred")), \
                 patch.object(terminal_data, "get_symbol_decisions", side_effect=AssertionError("cold snapshot should not load decisions inline")), \
                 patch.object(terminal_data, "get_symbol_trades", side_effect=AssertionError("cold snapshot should not load trades inline")), \
                 patch.object(terminal_data, "get_symbol_news", side_effect=AssertionError("cold snapshot should not load news inline")), \
                 patch.object(terminal_data, "get_symbol_reports", side_effect=AssertionError("cold snapshot should not load reports inline")):
                payload = await terminal_data.get_symbol_snapshot("BTC/USD")
                return payload, refresh_mock.call_args_list

        payload, refresh_calls = asyncio.run(_exercise())
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(payload["symbol"], "BTC/USD")
        self.assertEqual(payload["overview"]["symbol"], "BTC/USD")
        self.assertEqual(payload["decision_preview"], [])
        self.assertEqual(payload["trade_preview"], [])
        self.assertEqual(payload["news_preview"], [])
        self.assertTrue(any(call.args and call.args[0] == "symbol-snapshot:BTC/USD" for call in refresh_calls))

    def test_symbol_overview_uses_cached_runtime_snapshot_contract(self):
        from services.crypto import terminal_data

        with patch.object(terminal_data, "current_crypto_config", return_value={"account_mode": "paper"}), \
             patch.object(terminal_data, "get_bot_status_snapshot", return_value={"runtime": {"running": True}, "persisted": {}}), \
             patch.object(terminal_data, "_latest_universe_snapshot", return_value=None), \
             patch.object(terminal_data.market_data, "get_cached_crypto_positions", return_value=[]), \
             patch.object(terminal_data.market_data, "get_cached_latest_quote", return_value={}), \
             patch.object(terminal_data.store, "get_tracked_symbol_sync", return_value={}), \
             patch.object(terminal_data.store, "get_universe_ranking_for_symbol_sync", return_value={}), \
             patch.object(terminal_data.store, "get_symbol_event_state_sync", return_value={}), \
             patch.object(terminal_data.store, "get_latest_macro_consensus_sync", return_value={}), \
             patch.object(terminal_data.store, "list_brain_decision_traces_sync", return_value=[]), \
             patch.object(terminal_data.store, "list_candidate_traces_sync", return_value=[]), \
             patch.object(terminal_data.store, "list_decision_outcomes_sync", return_value=[]), \
             patch.object(terminal_data.store, "get_recent_news_events_sync", return_value=[]), \
             patch.object(terminal_data.store, "get_live_experience_sync", return_value=[]), \
             patch.object(terminal_data.store, "get_symbol_policy_overlay_sync", return_value={}):
            payload = terminal_data._build_symbol_overview_fallback("BTC/USD")

        self.assertEqual(payload["runtime"]["running"], True)
        self.assertEqual(payload["status"], "partial")

    def test_scanner_uses_shell_safe_config_without_symbol_discovery(self):
        from services.crypto import terminal_data

        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "crypto_bot.db")
            with patch.object(store, "_DATA_DIR", tmpdir), patch.object(store, "_DB_FILE", db_file):
                store.record_universe_snapshot_sync(
                    account_mode="paper",
                    selected_symbols=["BTC/USD"],
                    promoted_symbols=[],
                    rankings=[{
                        "symbol": "BTC/USD",
                        "bucket": "core",
                        "selected": True,
                        "total_rank": 91.0,
                        "spread_bps": 8.0,
                        "liquidity_score": 94.0,
                        "spread_score": 91.0,
                        "volume_score": 95.0,
                        "volatility_score": 62.0,
                        "event_score": 10.0,
                        "historical_edge_score": 50.0,
                        "payload": {"mid_price": 68000.0},
                    }],
                    payload={"universe_cfg": {"refresh_sec": 900}},
                )

                with patch.object(terminal_data, "current_crypto_config", return_value={"account_mode": "paper"}) as cfg_mock, \
                     patch.object(terminal_data, "get_overlay_map", return_value={}), \
                     patch.object(terminal_data.market_data, "get_cached_crypto_positions", return_value=[]), \
                     patch.object(terminal_data.market_data, "get_cached_latest_quote", return_value={}):
                    payload = terminal_data.get_scanner(limit=10)

        self.assertEqual(payload["status"], "fresh")
        cfg_mock.assert_called_with(resolve_symbols=False)

    def test_scanner_prioritizes_active_tracked_symbols(self):
        from services.crypto import terminal_data

        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "crypto_bot.db")
            with patch.object(store, "_DATA_DIR", tmpdir), patch.object(store, "_DB_FILE", db_file):
                store.record_universe_snapshot_sync(
                    account_mode="paper",
                    selected_symbols=["BTC/USD"],
                    promoted_symbols=[],
                    rankings=[
                        {
                            "symbol": "BTC/USD",
                            "bucket": "core",
                            "selected": True,
                            "total_rank": 91.0,
                            "spread_bps": 8.0,
                            "liquidity_score": 94.0,
                            "spread_score": 91.0,
                            "volume_score": 95.0,
                            "volatility_score": 62.0,
                            "event_score": 10.0,
                            "historical_edge_score": 50.0,
                            "payload": {"mid_price": 68000.0},
                        },
                        {
                            "symbol": "SOL/USD",
                            "bucket": "discovery",
                            "selected": True,
                            "total_rank": 74.0,
                            "spread_bps": 12.0,
                            "liquidity_score": 82.0,
                            "spread_score": 80.0,
                            "volume_score": 81.0,
                            "volatility_score": 70.0,
                            "event_score": 8.0,
                            "historical_edge_score": 44.0,
                            "payload": {"mid_price": 130.5},
                        },
                    ],
                    payload={"universe_cfg": {"refresh_sec": 900}},
                )
                store.upsert_tracked_symbol_sync(
                    "SOL/USD",
                    "paper",
                    saved_manual=True,
                    active_state="active",
                    active_reason="top_ranked",
                    monitor_tier="active",
                    last_rank_score=74.0,
                )

                with patch.object(terminal_data, "current_crypto_config", return_value={"account_mode": "paper"}), \
                     patch.object(terminal_data, "get_overlay_map", return_value={}), \
                     patch.object(terminal_data.market_data, "get_cached_crypto_positions", return_value=[]), \
                     patch.object(terminal_data.market_data, "get_cached_latest_quote", return_value={}):
                    payload = terminal_data.get_scanner(limit=10)

        self.assertEqual(payload["status"], "fresh")
        self.assertEqual(payload["items"][0]["symbol"], "SOL/USD")
        self.assertEqual(payload["items"][0]["active_state"], "active")
        self.assertTrue(payload["items"][0]["saved"])

    def test_learning_overview_counts_pending_without_loading_full_trace_rows(self):
        from services.crypto import terminal_data

        with patch.object(terminal_data.store, "get_latest_brain_self_score_sync", return_value={"ts": 1_700_000_000_000, "score": 64.5}), \
             patch.object(terminal_data.store, "list_brain_self_score_history_sync", return_value=[{"ts": 1_700_000_000_000, "score": 64.5}]), \
             patch.object(terminal_data.store, "list_symbol_policy_overlays_sync", return_value=[]), \
             patch.object(terminal_data.store, "get_decision_outcome_stats_sync", return_value={"by_horizon": {}}), \
             patch.object(terminal_data.store, "get_live_experience_stats_sync", return_value={"closed": 0}), \
             patch.object(terminal_data.store, "list_pending_decision_traces_for_outcome_sync", side_effect=AssertionError("should not load full pending rows")), \
             patch.object(terminal_data.store, "count_pending_decision_traces_for_outcome_sync", side_effect=[3, 1]), \
             patch.object(terminal_data, "current_crypto_config", return_value={"decision_feedback": {"horizons_min": [30, 60]}, "live_retrain_interval_days": 3}), \
             patch.object(terminal_data, "_read_ml_log_tail", return_value=["ok"]):
            payload = terminal_data.get_learning_overview()

        self.assertEqual(payload["status"], "fresh")
        self.assertEqual(payload["pending_outcomes"]["30"], 3)
        self.assertEqual(payload["pending_outcomes"]["60"], 1)

    def test_live_experience_stats_track_open_and_closed_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = os.path.join(tmpdir, "crypto_bot.db")
            with patch.object(store, "_DATA_DIR", tmpdir), patch.object(store, "_DB_FILE", db_file):
                open_id = store.record_live_experience_sync(
                    symbol="BTC/USD",
                    side="buy",
                    entry_ts=1_700_000_000_000,
                    strategy_used="momentum",
                    entry_price=68000.0,
                    notional=500.0,
                    gemini_score=0.8,
                )
                closed_id = store.record_live_experience_sync(
                    symbol="ETH/USD",
                    side="buy",
                    entry_ts=1_700_000_060_000,
                    strategy_used="mean_reversion",
                    entry_price=3200.0,
                    notional=500.0,
                    gemini_score=0.7,
                )
                self.assertGreater(open_id, 0)
                store.close_live_experience_sync(
                    closed_id,
                    exit_ts=1_700_000_120_000,
                    exit_price=3300.0,
                    outcome_pnl_pct=3.1,
                    hold_duration_min=20.0,
                    exit_reason="tp",
                )
                stats = store.get_live_experience_stats_sync()

        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["closed"], 1)
        self.assertEqual(stats["open"], 1)


if __name__ == "__main__":
    unittest.main()
