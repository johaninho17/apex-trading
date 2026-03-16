import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from integrations.telegram import entity_resolver, nl_router, query_presenter, query_service


def test_parse_intent_fast_paths_current_brain_query():
    parsed = nl_router.parse_intent("what is my current brain?")
    assert parsed == {"action": "query", "question": "what is my current brain?"}


def test_entity_resolver_classifies_brain_as_concept_not_symbol():
    entities = entity_resolver.resolve_entities("what is my current brain?")
    assert entities["symbols"] == []
    assert "BRAIN" in entities["concepts"]


def test_entity_resolver_keeps_rsi_as_indicator_and_eth_as_symbol():
    entities = entity_resolver.resolve_entities("what is the rsi on eth")
    assert entities["symbols"] == ["ETH/USD"]
    assert "RSI" in entities["indicators"]


def test_build_query_plan_routes_current_brain_to_config_status():
    plan = query_service.build_query_plan("what is my current brain?")
    assert plan["intent"] == "config_status"
    assert plan["symbol"] == ""
    assert "runtime_status" in plan["datasets"]
    assert "runtime_snapshot" in plan["calculations"]


def test_query_presenter_renders_config_status_without_symbol_fallback():
    bundle = {
        "plan": {"intent": "config_status", "symbol": "", "brain_mode": "drl_event_fusion"},
        "runtime_status": {"running": True},
        "macro_consensus": {"market_regime": "bear", "risk_multiplier": 0.70},
        "reports": [],
        "metrics": {
            "active_brain": "drl_event_fusion",
            "trading_mode": "live",
            "account_mode": "paper",
            "risk_state": "normal",
            "risk_source": "",
            "governor_mode": "warm",
            "pressure_score": 22.0,
        },
    }
    text = query_presenter.render_natural_response("what is my current brain?", bundle)
    assert "Your current brain is `drl_event_fusion`" in text
    assert "It is running in live mode on the paper account." in text
    assert "Risk state" not in text
    assert "Macro context" not in text
    assert "IS/USD" not in text
