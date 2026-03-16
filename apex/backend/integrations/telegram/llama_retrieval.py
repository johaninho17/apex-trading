"""LlamaIndex + SQLGlot retrieval helpers for Telegram bot questions."""

from __future__ import annotations

import json
import logging
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine
import sqlglot
from sqlglot import exp

from core.config_manager import get_config
from core.ollama import get_ollama_host
from integrations.telegram import bot_knowledge
from services.crypto import store

try:
    from llama_index.core import Settings, SimpleDirectoryReader, SimpleKeywordTableIndex, SQLDatabase
    from llama_index.core.indices.struct_store.sql_retriever import NLSQLRetriever
    from llama_index.llms.ollama import Ollama
    _HAS_LLAMA = True
except Exception:  # pragma: no cover - import guard
    Settings = None  # type: ignore[assignment]
    SimpleDirectoryReader = None  # type: ignore[assignment]
    SimpleKeywordTableIndex = None  # type: ignore[assignment]
    SQLDatabase = None  # type: ignore[assignment]
    NLSQLRetriever = None  # type: ignore[assignment]
    Ollama = None  # type: ignore[assignment]
    _HAS_LLAMA = False

logger = logging.getLogger("apex.telegram.llama_retrieval")

_SAFE_SQL_VIEWS: Dict[str, str] = {
    "trade_history_view": """
        CREATE VIEW IF NOT EXISTS trade_history_view AS
        SELECT
            id,
            entry_ts,
            exit_ts,
            symbol,
            side,
            strategy_used,
            brain_mode,
            entry_price,
            exit_price,
            hold_duration_min,
            outcome_pnl_pct,
            exit_reason,
            gemini_score,
            gemini_regime,
            event_score_at_entry,
            event_bias_at_entry,
            top_event_type_at_entry,
            session_win_rate,
            session_pnl_pct
        FROM live_experience
    """,
    "decision_trace_view": """
        CREATE VIEW IF NOT EXISTS decision_trace_view AS
        SELECT
            id,
            ts,
            symbol,
            brain_mode,
            decision,
            final_score,
            submitted,
            block_reason,
            payload_json
        FROM brain_decision_trace
    """,
    "news_event_view": """
        CREATE VIEW IF NOT EXISTS news_event_view AS
        SELECT
            id,
            source,
            symbol,
            base_asset,
            event_type,
            headline,
            sentiment,
            impact_score,
            confidence,
            urgency,
            trade_bias,
            published_at,
            expires_at,
            reason
        FROM news_events
    """,
    "macro_consensus_view": """
        CREATE VIEW IF NOT EXISTS macro_consensus_view AS
        SELECT
            id,
            ts,
            risk_multiplier,
            market_regime,
            macro_sentiment_score,
            summary
        FROM macro_consensus_history
    """,
    "report_view": """
        CREATE VIEW IF NOT EXISTS report_view AS
        SELECT
            id,
            ts,
            report_type,
            content
        FROM bot_reports
    """,
    "risk_state_view": """
        CREATE VIEW IF NOT EXISTS risk_state_view AS
        SELECT
            id,
            ts,
            symbol,
            event_type,
            state_key,
            state_value,
            reason
        FROM risk_state_history
    """,
    "governor_history_view": """
        CREATE VIEW IF NOT EXISTS governor_history_view AS
        SELECT
            id,
            ts,
            mode,
            candidate_mode,
            pressure_score,
            payload_json
        FROM governor_state_history
    """,
    "candidate_trace_view": """
        CREATE VIEW IF NOT EXISTS candidate_trace_view AS
        SELECT
            id,
            ts,
            symbol,
            brain_mode,
            strategy,
            side,
            score,
            submitted,
            candidate_class,
            suppressed_reason,
            payload_json
        FROM candidate_trace
    """,
}

_SQL_TABLE_CONTEXT: Dict[str, str] = {
    "trade_history_view": "Closed and open trade memory. Use this for last trades, win rate, hold time, entry/exit context, and strategy_used by symbol.",
    "decision_trace_view": "Decision audit trail. Use this for submitted vs blocked trades, final_score, brain_mode, decision, and block_reason.",
    "news_event_view": "Persisted parsed news events. Use this for catalysts, headline history, sentiment, impact_score, confidence, event_type, and trade_bias.",
    "macro_consensus_view": "Macro regime history. Use this for Gemini-style macro regime, risk_multiplier, and summary over time.",
    "report_view": "Bot reports. Use this for hourly or daily narrative summaries when the user asks about latest reports or report context.",
    "risk_state_view": "Risk-state transition history. Use this for halted, restricted, cautious, resume, oracle, and drawdown state questions.",
    "governor_history_view": "Activity governor history. Use this for mode changes, pressure, and governor timeline questions.",
    "candidate_trace_view": "Suppressed or near-miss candidates. Use this when the user asks why trades were almost taken or what was blocked early.",
}

_ALLOWED_SQL_TABLES = tuple(_SAFE_SQL_VIEWS.keys())
_DOC_FILES = [str(doc["path"]) for doc in getattr(bot_knowledge, "_DOCS", [])]


def _db_path() -> str:
    return str(Path(store._DB_FILE).resolve())


def _ensure_safe_views() -> None:
    db = _db_path()
    con = sqlite3.connect(db)
    try:
        for ddl in _SAFE_SQL_VIEWS.values():
            con.execute(ddl)
        con.commit()
    finally:
        con.close()


@lru_cache(maxsize=1)
def _sql_database() -> SQLDatabase | None:
    if not _HAS_LLAMA:
        return None
    _ensure_safe_views()
    engine = create_engine(f"sqlite:///{_db_path()}")
    return SQLDatabase(engine, include_tables=list(_ALLOWED_SQL_TABLES), sample_rows_in_table_info=2)


@lru_cache(maxsize=1)
def _ollama_llm() -> Ollama | None:
    if not _HAS_LLAMA:
        return None
    cfg = get_config().get("stocks", {}).get("crypto", {})
    model_name = str(cfg.get("telegram_ollama_model") or "qwen3:8b")
    base_url = get_ollama_host()
    llm = Ollama(model=model_name, base_url=base_url, request_timeout=20.0, temperature=0.0)
    Settings.llm = llm
    return llm


@lru_cache(maxsize=1)
def _doc_index() -> Any:
    if not _HAS_LLAMA:
        return None
    paths = [path for path in _DOC_FILES if Path(path).exists()]
    if not paths:
        return None
    docs = SimpleDirectoryReader(input_files=paths).load_data()
    return SimpleKeywordTableIndex.from_documents(docs)


def _validate_select_sql(sql_query: str) -> str:
    raw = str(sql_query or "").strip()
    if not raw:
        raise ValueError("Empty SQL query")
    if len(raw) > 4000:
        raise ValueError("SQL query too long")

    expr = sqlglot.parse_one(raw, read="sqlite")
    if not isinstance(expr, exp.Select):
        raise ValueError("Only SELECT queries are allowed")

    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Pragma)
    for node in expr.walk():
        if isinstance(node, forbidden):
            raise ValueError("Unsafe SQL command blocked")

    table_names = {table.name for table in expr.find_all(exp.Table)}
    if not table_names:
        raise ValueError("SQL query did not reference an allowed view")
    disallowed = sorted(name for name in table_names if name not in _ALLOWED_SQL_TABLES)
    if disallowed:
        raise ValueError(f"Disallowed SQL tables: {', '.join(disallowed)}")

    limit_expr = expr.args.get("limit")
    if limit_expr is None:
        expr = expr.limit(25)
    else:
        limit_value: Optional[int] = None
        limit_node = getattr(limit_expr, "expression", None)
        if isinstance(limit_node, exp.Literal) and limit_node.is_number:
            try:
                limit_value = int(limit_node.this)
            except Exception:
                limit_value = None
        if limit_value is None or limit_value > 50:
            expr = expr.limit(50)

    return expr.sql(dialect="sqlite")


def _rows_from_metadata(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = metadata.get("result") or []
    columns = metadata.get("col_keys") or []
    result: List[Dict[str, Any]] = []
    for row in rows[:50]:
        if isinstance(row, dict):
            result.append(dict(row))
        elif isinstance(row, (list, tuple)):
            result.append({str(columns[idx]): row[idx] for idx in range(min(len(columns), len(row)))})
    return result


def retrieve_sql(question: str) -> Dict[str, Any]:
    if not _HAS_LLAMA:
        return {}
    sql_database = _sql_database()
    llm = _ollama_llm()
    if sql_database is None or llm is None:
        return {}

    retriever = NLSQLRetriever(
        sql_database,
        llm=llm,
        tables=list(_ALLOWED_SQL_TABLES),
        context_query_kwargs=_SQL_TABLE_CONTEXT,
        sql_only=True,
        handle_sql_errors=False,
    )
    nodes, metadata = retriever.retrieve_with_metadata(question)
    sql_query = str((metadata or {}).get("sql_query") or (metadata or {}).get("result") or "").strip()
    if not sql_query and nodes:
        sql_query = str(nodes[0].node.get_content() or "").strip()
    validated_sql = _validate_select_sql(sql_query)
    raw_response, run_meta = sql_database.run_sql(validated_sql)
    return {
        "sql_query": validated_sql,
        "raw_response": raw_response,
        "rows": _rows_from_metadata(run_meta),
        "col_keys": list(run_meta.get("col_keys") or []),
    }


def retrieve_docs(question: str, max_nodes: int = 4) -> List[Dict[str, str]]:
    if not _HAS_LLAMA:
        return []
    index = _doc_index()
    if index is None:
        return []
    retriever = index.as_retriever()
    nodes = retriever.retrieve(question)
    results: List[Dict[str, str]] = []
    for node in nodes[:max_nodes]:
        text = node.node.get_content().strip()
        if len(text) > 1200:
            text = text[:1200].rsplit("\n", 1)[0]
        metadata = dict(getattr(node.node, "metadata", {}) or {})
        results.append({
            "path": str(metadata.get("file_path") or metadata.get("filename") or ""),
            "content": text,
        })
    return results


def retrieve_context(question: str) -> Dict[str, Any]:
    if not _HAS_LLAMA:
        return {}
    context: Dict[str, Any] = {}
    try:
        context["sql"] = retrieve_sql(question)
    except Exception as exc:
        logger.debug("[llama_retrieval] SQL retrieval failed: %s", exc)
    try:
        docs = retrieve_docs(question)
        if docs:
            context["docs"] = docs
    except Exception as exc:
        logger.debug("[llama_retrieval] doc retrieval failed: %s", exc)
    return context


def format_context(context: Dict[str, Any]) -> str:
    if not context:
        return ""
    lines: List[str] = []
    sql_ctx = context.get("sql") or {}
    if sql_ctx:
        lines.append(f"LlamaIndex SQL query: {sql_ctx.get('sql_query', '')}")
        rows = sql_ctx.get("rows") or []
        if rows:
            lines.append("LlamaIndex SQL rows:")
            for row in rows[:10]:
                lines.append("- " + json.dumps(row, ensure_ascii=True, default=str))
    docs = context.get("docs") or []
    if docs:
        lines.append("LlamaIndex bot-doc context:")
        for doc in docs[:4]:
            label = Path(str(doc.get("path") or "doc")).name or "doc"
            lines.append(f"[{label}] {str(doc.get('content') or '')[:600]}")
    return "\n".join(lines).strip()


__all__ = [
    "retrieve_context",
    "format_context",
    "retrieve_sql",
    "retrieve_docs",
    "_validate_select_sql",
]
