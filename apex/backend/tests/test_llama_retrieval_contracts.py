import os
import sys
from unittest.mock import patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from integrations.telegram import llama_retrieval, query_service


def test_validate_select_sql_allows_safe_select_and_enforces_limit():
    sql = llama_retrieval._validate_select_sql("SELECT symbol, outcome_pnl_pct FROM trade_history_view ORDER BY entry_ts DESC")
    assert "FROM trade_history_view" in sql
    assert "LIMIT 25" in sql.upper()


def test_validate_select_sql_blocks_non_select_and_disallowed_tables():
    try:
        llama_retrieval._validate_select_sql("DELETE FROM live_experience")
        assert False, "expected non-select query to be blocked"
    except ValueError as exc:
        assert "Only SELECT queries are allowed" in str(exc) or "Unsafe SQL command blocked" in str(exc)

    try:
        llama_retrieval._validate_select_sql("SELECT * FROM live_experience")
        assert False, "expected disallowed table to be blocked"
    except ValueError as exc:
        assert "Disallowed SQL tables" in str(exc)


@patch("integrations.telegram.query_service._summarize_with_ollama")
@patch("integrations.telegram.query_service.llama_retrieval.retrieve_context")
@patch("integrations.telegram.query_service._execute_plan")
@patch("integrations.telegram.query_service.build_query_plan")
def test_answer_query_includes_llama_context_in_debug_output(mock_plan, mock_execute, mock_llama_context, mock_summarize):
    mock_plan.return_value = {
        "intent": "trade_history",
        "symbol": "BTC/USD",
        "lookback_days": 7,
        "limit": 5,
        "datasets": ["live_experience"],
        "calculations": ["trade_stats"],
        "include_live_news": False,
    }
    mock_execute.return_value = {"plan": mock_plan.return_value, "live_experience": []}
    mock_llama_context.return_value = {
        "sql": {"sql_query": "SELECT * FROM trade_history_view LIMIT 5", "rows": [{"symbol": "BTC/USD", "outcome_pnl_pct": 1.2}]},
        "docs": [{"path": "bot_data_dictionary.md", "content": "trade history context"}],
    }
    mock_summarize.return_value = None

    text = query_service.answer_query("last 5 btc trades", debug=True)
    assert "LlamaIndex SQL query" in text
    assert "trade_history_view" in text
