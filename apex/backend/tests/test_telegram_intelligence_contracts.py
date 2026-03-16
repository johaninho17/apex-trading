import os
import sys
import time
from unittest.mock import patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from integrations.telegram import query_presenter, query_service


@patch("integrations.telegram.query_service._plan_with_ollama", return_value=None)
def test_build_query_plan_routes_new_intents(_mock_plan):
    assert query_service.build_query_plan("show me ADA card")["intent"] == "coin_card"
    assert query_service.build_query_plan("how is LINK learning going")["intent"] == "learning_card"
    assert query_service.build_query_plan("why is TRUMP blocked")["intent"] == "blocked_review"
    assert query_service.build_query_plan("show tracked coins")["intent"] == "tracked_status"
    assert query_service.build_query_plan("show oracle details")["intent"] == "oracle_detail"
    assert query_service.build_query_plan("show market breadth in news")["intent"] == "news_breadth"


def test_query_presenter_renders_oracle_detail_and_news_breadth():
    oracle_text = query_presenter.render_natural_response(
        "show oracle details",
        {
            "plan": {"intent": "oracle_detail", "symbol": ""},
            "metrics": {
                "oracle_score": 1.24,
                "oracle_regime": "bull",
                "oracle_breadth_state": "btc_led",
                "oracle_age_sec": 120,
                "oracle_drivers": ["ETF inflows stayed positive", "BTC held higher lows"],
                "oracle_mentioned_symbols": ["BTC", "ETH"],
                "oracle_summary_source": "fresh_gemini",
                "oracle_summary": "BTC led the move while ETH participation remained smaller.",
            },
            "reports": [],
            "macro_consensus": {},
        },
    )
    assert "breadth `btc_led`" in oracle_text
    assert "Main drivers" in oracle_text
    assert "Summary source is `fresh_gemini`." in oracle_text

    breadth_text = query_presenter.render_natural_response(
        "show market breadth in news",
        {
            "plan": {"intent": "news_breadth", "symbol": ""},
            "metrics": {
                "news_event_count": 12,
                "symbol_tagged_news_count": 4,
                "unattributed_news_count": 8,
                "news_breadth_top_symbols": [("BTC", 5), ("ETH", 2), ("SOL", 1)],
                "news_provider_status": {"binance": "blocked", "cryptopanic": "quota_exceeded"},
                "oracle_breadth_state": "btc_led",
                "oracle_regime": "bull",
            },
            "reports": [],
            "macro_consensus": {},
        },
    )
    assert "12 recent stored news item" in breadth_text
    assert "Top mentioned symbols: BTC=5, ETH=2, SOL=1" in breadth_text
    assert "Provider health: binance=blocked, cryptopanic=quota_exceeded" in breadth_text


@patch("services.crypto.news_pipeline._news_provider_status", return_value={"binance": {"status": "blocked"}, "cryptopanic": {"status": "quota_exceeded"}})
@patch("services.crypto.news_pipeline.fetch_cryptopanic_posts_sync")
@patch("services.crypto.news_pipeline.fetch_binance_announcements_sync")
def test_live_news_respects_provider_limits(mock_binance, mock_cryptopanic, _mock_status):
    query_service._LIVE_NEWS_CACHE.clear()
    items, meta = query_service._live_news("BTC/USD", 5)
    assert items == []
    assert meta["status"] == "provider_limited"
    mock_binance.assert_not_called()
    mock_cryptopanic.assert_not_called()


@patch("services.crypto.news_pipeline._news_provider_status", return_value={"binance": {"status": "ok"}, "cryptopanic": {"status": "ok"}})
def test_live_news_uses_cooldown_cache(_mock_status):
    cache_key = query_service._live_news_cache_key("BTC/USD", 5)
    query_service._LIVE_NEWS_CACHE[cache_key] = {
        "ts_sec": time.time(),
        "items": [{"title": "cached headline", "source": "cryptopanic_live"}],
    }
    items, meta = query_service._live_news("BTC/USD", 5)
    assert items[0]["title"] == "cached headline"
    assert meta["status"] == "cooldown_cached"
    assert meta["from_cache"] is True
