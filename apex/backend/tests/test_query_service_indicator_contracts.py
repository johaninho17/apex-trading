import os
import sys
from unittest.mock import patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from integrations.telegram import query_presenter, query_service


def test_build_query_plan_routes_indicator_question_to_indicator_status():
    plan = query_service.build_query_plan("what is the rsi on eth")
    assert plan["intent"] == "indicator_status"
    assert plan["symbol"] == "ETH/USD"
    assert "current_indicators" in plan["datasets"]
    assert "indicator_snapshot" in plan["calculations"]


@patch("integrations.telegram.query_service._indicator_snapshot")
def test_execute_plan_includes_indicator_snapshot(mock_indicator_snapshot):
    mock_indicator_snapshot.return_value = {"symbol": "ETH/USD", "rsi_15m": 31.2, "rsi_1m": 28.9, "trend_15m": "bullish"}
    plan = {
        "symbol": "ETH/USD",
        "limit": 5,
        "datasets": ["current_indicators", "macro_consensus"],
        "calculations": ["indicator_snapshot"],
        "intent": "indicator_status",
    }
    evidence = query_service._execute_plan(plan)
    metrics = query_service._calculate_metrics(plan, evidence)
    assert evidence["current_indicators"]["symbol"] == "ETH/USD"
    assert metrics["rsi_15m"] == 31.2
    assert metrics["trend_15m"] == "bullish"


def test_query_presenter_renders_indicator_response():
    bundle = {
        "plan": {"intent": "indicator_status", "symbol": "ETH/USD"},
        "current_indicators": {
            "symbol": "ETH/USD",
            "rsi_15m": 31.2,
            "rsi_1m": 28.9,
            "ema_fast_15m": 101.0,
            "ema_slow_15m": 99.0,
            "bb_upper_15m": 110.0,
            "bb_lower_15m": 90.0,
            "close_15m": 100.0,
            "trend_15m": "bullish",
        },
        "event_state": {"event_bias": "bullish", "net_event_score": 8.5},
        "macro_consensus": {"market_regime": "normal", "risk_multiplier": 0.98},
        "reports": [],
        "metrics": {},
    }
    text = query_presenter.render_natural_response("what is the rsi on eth", bundle)
    assert "15m RSI is 31.2" in text
    assert "Event state is bullish" in text
    assert "Macro context is normal" in text
