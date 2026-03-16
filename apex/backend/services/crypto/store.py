import asyncio
import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
_DB_FILE = os.path.join(_DATA_DIR, "crypto_bot.db")
_DB_READY_FILE: Optional[str] = None
_RUNTIME_STATE_CACHE_LOCK = threading.Lock()
_RUNTIME_STATE_CACHE: Dict[str, Any] = {}


def _normalize_symbol_key(raw: Any) -> str:
    value = str(raw or "").strip().upper()
    if not value:
        return ""
    if "/" in value:
        return value
    for quote in ("USD", "USDT", "USDC"):
        if value.endswith(quote) and len(value) > len(quote):
            return f"{value[:-len(quote)]}/{quote}"
    return value


def _normalize_account_mode_key(raw: Any) -> str:
    return "live" if str(raw or "").strip().lower() == "live" else "paper"


def _connect() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    con = sqlite3.connect(_DB_FILE)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con


def _connect_reader() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    con = sqlite3.connect(_DB_FILE)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON;")
    return con


def _default_runtime_state() -> Dict[str, Any]:
    return {
        "running": False,
        "started_at": None,
        "last_heartbeat": None,
        "iterations": 0,
        "last_error": None,
        "halted": False,
        "halted_reason": None,
        "risk_state": "normal",
        "risk_source": "",
        "risk_reason": "",
        "effective_risk_profile": {},
        "behavior_state": {},
        "component_calibration": {},
        "day_start_equity_paper": None,
        "day_start_equity_live": None,
        "day_start_ts": None,
        "updated_at": None,
    }


def _set_runtime_state_cache(value: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(value or {})
    with _RUNTIME_STATE_CACHE_LOCK:
        _RUNTIME_STATE_CACHE.clear()
        _RUNTIME_STATE_CACHE.update(payload)
    return dict(payload)


def get_runtime_state_cached_sync() -> Dict[str, Any]:
    with _RUNTIME_STATE_CACHE_LOCK:
        return dict(_RUNTIME_STATE_CACHE)

def _ensure_column_sync(con: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Add a column if it does not already exist. Safe to call on every boot."""
    cols = {str(r['name']) for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")



def _init_db_sync() -> None:
    global _DB_READY_FILE
    if _DB_READY_FILE == _DB_FILE:
        return
    with _LOCK:
        if _DB_READY_FILE == _DB_FILE:
            return
        con = _connect()
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    action_type TEXT NOT NULL,
                    symbol TEXT,
                    side TEXT,
                    qty REAL,
                    notional REAL,
                    price REAL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    payload_json TEXT
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(ts DESC)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_actions_status_ts ON actions(status, ts DESC)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_actions_symbol_ts ON actions(symbol, ts DESC)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    running INTEGER NOT NULL DEFAULT 0,
                    started_at INTEGER,
                    last_heartbeat INTEGER,
                    iterations INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    halted INTEGER NOT NULL DEFAULT 0,
                    halted_reason TEXT,
                    day_start_equity_paper REAL,
                    day_start_equity_live REAL,
                    day_start_ts INTEGER,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            # Oracle memory: every AI sentiment score is persisted here so the
            # ML training pipeline can retroactively learn which news events
            # correlated to real price action.
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS oracle_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    score REAL NOT NULL,
                    summary TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_state (
                    key TEXT PRIMARY KEY,
                    value_ms INTEGER NOT NULL
                )
                """
            )
            # AI Bot Reports: multi-timeframe natural language reports from Gemini
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    report_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metrics_json TEXT,
                    grade_json TEXT
                )
                """
            )
            # â”€â”€ Live Experience: one row per closed trade â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            # Captures the full context at ENTRY time (oracle score, win rate,
            # session memory) plus the outcome â€” used to retrain the DRL model
            # on the bot's own real trading history instead of synthetic data.
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS live_experience (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_ts INTEGER NOT NULL,
                    exit_ts INTEGER,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    strategy_used TEXT,
                    entry_price REAL,
                    exit_price REAL,
                    notional REAL,
                    hold_duration_min REAL,
                    gemini_score REAL,
                    gemini_regime TEXT,
                    session_win_rate REAL,
                    session_pnl_pct REAL,
                    narrative_label TEXT,
                    rsi_15m_at_entry REAL,
                    rsi_1m_at_entry REAL,
                    macd_hist_at_entry REAL,
                    slm_confidence REAL,
                    outcome_pnl_pct REAL,
                    exit_reason TEXT
                )
                """
            )
            # ── Decision Outcomes: forward P&L measured after each trade closes ──
            # At 15m/30m/60m after the entry, the bot logs what the price did.
            # This tells the DRL model "was my reasoning correct?" independent of
            # the exit strategy — the bot learns from its READS not just its exits.
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    entry_ts INTEGER NOT NULL,
                    horizon_min INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    pnl_pct REAL,
                    outcome_label TEXT,
                    logged_at INTEGER NOT NULL
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_outcomes_trace ON decision_outcomes(trace_id)"
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS kv_store (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS equity_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    equity REAL NOT NULL,
                    cash REAL NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS news_events (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    external_id TEXT UNIQUE,
                    symbol TEXT,
                    base_asset TEXT,
                    event_type TEXT,
                    headline TEXT,
                    url TEXT,
                    published_at INTEGER NOT NULL,
                    ingested_at INTEGER NOT NULL,
                    sentiment TEXT,
                    impact_score REAL,
                    confidence REAL,
                    urgency TEXT,
                    ttl_minutes INTEGER,
                    expires_at INTEGER,
                    trade_bias TEXT,
                    parsed_by TEXT,
                    parser_model TEXT,
                    reason TEXT,
                    payload_json TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_event_state (
                    symbol TEXT PRIMARY KEY,
                    net_event_score REAL NOT NULL DEFAULT 0,
                    event_bias TEXT,
                    hard_veto INTEGER NOT NULL DEFAULT 0,
                    golden_trade_flag INTEGER NOT NULL DEFAULT 0,
                    top_event_id TEXT,
                    top_event_type TEXT,
                    top_event_score REAL,
                    event_count_active INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    payload_json TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS tracked_symbols (
                    symbol TEXT NOT NULL,
                    account_mode TEXT NOT NULL,
                    saved_manual INTEGER NOT NULL DEFAULT 0,
                    active_state TEXT NOT NULL DEFAULT 'idle',
                    active_reason TEXT,
                    active_since_ts INTEGER,
                    active_min_until_ts INTEGER,
                    recent_until_ts INTEGER,
                    last_decision_ts INTEGER,
                    monitor_tier TEXT NOT NULL DEFAULT 'eligible',
                    last_rank_score REAL NOT NULL DEFAULT 0,
                    metadata_json TEXT,
                    updated_ts INTEGER NOT NULL,
                    PRIMARY KEY(symbol, account_mode)
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_tracked_symbols_mode_state ON tracked_symbols(account_mode, active_state, updated_ts DESC)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_tracked_symbols_saved ON tracked_symbols(account_mode, saved_manual, updated_ts DESC)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_aliases (
                    canonical_asset_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    aliases_json TEXT,
                    websites_json TEXT,
                    platforms_json TEXT,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_asset_aliases_symbol ON asset_aliases(symbol)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_consensus_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    risk_multiplier REAL NOT NULL,
                    market_regime TEXT NOT NULL,
                    macro_sentiment_score REAL,
                    summary TEXT,
                    payload_json TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS brain_decision_trace (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    brain_mode TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    final_score REAL,
                    submitted INTEGER NOT NULL DEFAULT 0,
                    block_reason TEXT,
                    payload_json TEXT
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_brain_decision_trace_ts ON brain_decision_trace(ts DESC)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_brain_decision_trace_symbol_ts ON brain_decision_trace(symbol, ts DESC)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS decision_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id INTEGER NOT NULL,
                    horizon_min INTEGER NOT NULL,
                    evaluated_at INTEGER NOT NULL,
                    decision_ts INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    submitted INTEGER NOT NULL DEFAULT 0,
                    block_reason TEXT,
                    ref_price REAL,
                    outcome_price REAL,
                    pnl_pct REAL,
                    outcome_label TEXT,
                    payload_json TEXT,
                    UNIQUE(trace_id, horizon_min),
                    FOREIGN KEY(trace_id) REFERENCES brain_decision_trace(id)
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS governor_mode_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    pressure_score REAL,
                    effectiveness_score REAL,
                    confidence_score REAL,
                    stability_score REAL,
                    payload_json TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS candidate_trace (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    brain_mode TEXT NOT NULL,
                    strategy TEXT,
                    side TEXT NOT NULL,
                    score REAL,
                    submitted INTEGER NOT NULL DEFAULT 0,
                    candidate_class TEXT,
                    suppressed_reason TEXT,
                    payload_json TEXT
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_candidate_trace_symbol_ts ON candidate_trace(symbol, ts DESC)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS governor_state_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    candidate_mode TEXT,
                    pressure_score REAL,
                    payload_json TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS risk_state_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    symbol TEXT,
                    event_type TEXT NOT NULL,
                    state_key TEXT,
                    state_value TEXT,
                    reason TEXT,
                    payload_json TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS universe_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    account_mode TEXT,
                    selected_symbols_json TEXT,
                    promoted_symbols_json TEXT,
                    payload_json TEXT
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_universe_snapshots_ts ON universe_snapshots(ts DESC)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS universe_rankings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    bucket TEXT NOT NULL,
                    selected INTEGER NOT NULL DEFAULT 0,
                    total_rank REAL,
                    spread_bps REAL,
                    liquidity_score REAL,
                    spread_score REAL,
                    volume_score REAL,
                    volatility_score REAL,
                    event_score REAL,
                    historical_edge_score REAL,
                    payload_json TEXT,
                    FOREIGN KEY(snapshot_id) REFERENCES universe_snapshots(id)
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_universe_rankings_snapshot ON universe_rankings(snapshot_id, selected, total_rank DESC)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_universe_rankings_symbol_snapshot ON universe_rankings(symbol, snapshot_id DESC)"
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS component_calibration_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS research_scratchpad (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    question TEXT NOT NULL,
                    intent TEXT,
                    critique_verdict TEXT,
                    confidence REAL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_policy_overlays (
                    symbol TEXT PRIMARY KEY,
                    score_delta REAL NOT NULL DEFAULT 0,
                    size_multiplier REAL NOT NULL DEFAULT 1,
                    cooldown_multiplier REAL NOT NULL DEFAULT 1,
                    veto_tightness REAL NOT NULL DEFAULT 0,
                    confidence_boost REAL NOT NULL DEFAULT 0,
                    decision_samples INTEGER NOT NULL DEFAULT 0,
                    good_entry_count INTEGER NOT NULL DEFAULT 0,
                    bad_entry_count INTEGER NOT NULL DEFAULT 0,
                    good_block_count INTEGER NOT NULL DEFAULT 0,
                    bad_block_count INTEGER NOT NULL DEFAULT 0,
                    flat_count INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL,
                    payload_json TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS brain_self_score_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    score REAL NOT NULL,
                    confidence REAL,
                    discipline REAL,
                    pnl_quality REAL,
                    blocking_quality REAL,
                    freshness REAL,
                    payload_json TEXT
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_brain_self_score_ts ON brain_self_score_history(ts DESC)"
            )
            _ensure_column_sync(con, "runtime_state", "risk_state", "risk_state TEXT")
            _ensure_column_sync(con, "runtime_state", "risk_source", "risk_source TEXT")
            _ensure_column_sync(con, "runtime_state", "risk_reason", "risk_reason TEXT")
            _ensure_column_sync(con, "runtime_state", "effective_risk_profile_json", "effective_risk_profile_json TEXT")
            _ensure_column_sync(con, "runtime_state", "behavior_state_json", "behavior_state_json TEXT")
            _ensure_column_sync(con, "runtime_state", "component_calibration_json", "component_calibration_json TEXT")
            _ensure_column_sync(con, "live_experience", "brain_mode", "brain_mode TEXT")
            _ensure_column_sync(con, "live_experience", "event_score_at_entry", "event_score_at_entry REAL")
            _ensure_column_sync(con, "live_experience", "event_bias_at_entry", "event_bias_at_entry TEXT")
            _ensure_column_sync(con, "live_experience", "top_event_type_at_entry", "top_event_type_at_entry TEXT")
            _ensure_column_sync(con, "live_experience", "decision_trace_id", "decision_trace_id INTEGER")
            _ensure_column_sync(con, "live_experience", "news_context_state_at_entry", "news_context_state_at_entry TEXT")
            _ensure_column_sync(con, "live_experience", "news_context_complete_at_entry", "news_context_complete_at_entry INTEGER")
            _ensure_column_sync(con, "live_experience", "monitor_tier_at_entry", "monitor_tier_at_entry TEXT")
            _ensure_column_sync(con, "live_experience", "active_reason_at_entry", "active_reason_at_entry TEXT")
            _ensure_column_sync(con, "decision_outcomes", "evaluated_at", "evaluated_at INTEGER")
            _ensure_column_sync(con, "decision_outcomes", "decision_ts", "decision_ts INTEGER")
            _ensure_column_sync(con, "decision_outcomes", "decision", "decision TEXT")
            _ensure_column_sync(con, "decision_outcomes", "submitted", "submitted INTEGER NOT NULL DEFAULT 0")
            _ensure_column_sync(con, "decision_outcomes", "block_reason", "block_reason TEXT")
            _ensure_column_sync(con, "decision_outcomes", "ref_price", "ref_price REAL")
            _ensure_column_sync(con, "decision_outcomes", "outcome_price", "outcome_price REAL")
            _ensure_column_sync(con, "decision_outcomes", "payload_json", "payload_json TEXT")
            _ensure_column_sync(con, "news_events", "canonical_asset_id", "canonical_asset_id TEXT")
            _ensure_column_sync(con, "news_events", "event_scope", "event_scope TEXT")
            _ensure_column_sync(con, "news_events", "attribution_confidence", "attribution_confidence REAL")
            _ensure_column_sync(con, "news_events", "attribution_method", "attribution_method TEXT")
            _ensure_column_sync(con, "news_events", "source_tier", "source_tier TEXT")
            _ensure_column_sync(con, "news_events", "source_category", "source_category TEXT")
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_outcomes_symbol_ts ON decision_outcomes(symbol, decision_ts DESC)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_events_scope_conf ON news_events(event_scope, attribution_confidence DESC, published_at DESC)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_events_asset_ts ON news_events(canonical_asset_id, published_at DESC)"
            )

            now_ms = int(time.time() * 1000)
            con.execute(
                """
                INSERT INTO runtime_state (
                    id, running, started_at, last_heartbeat, iterations, last_error, halted, halted_reason,
                    day_start_equity_paper, day_start_equity_live, day_start_ts, updated_at
                )
                VALUES (1, 0, NULL, NULL, 0, NULL, 0, NULL, NULL, NULL, NULL, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (now_ms,),
            )
            con.commit()
            _DB_READY_FILE = _DB_FILE
        finally:
            con.close()


def get_signal_state_sync(prefix: str) -> Dict[str, int]:
    """Load all signal state keys matching a prefix (e.g. 'dca:') from DB."""
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT key, value_ms FROM signal_state WHERE key LIKE ?",
                (f"{prefix}%",),
            ).fetchall()
            return {row["key"]: int(row["value_ms"]) for row in rows}
        finally:
            con.close()


def save_signal_state_sync(key: str, value_ms: int) -> None:
    """Persist a signal state timestamp (e.g. last DCA time for a symbol)."""
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                "INSERT OR REPLACE INTO signal_state (key, value_ms) VALUES (?, ?)",
                (str(key), int(value_ms)),
            )
            con.commit()
        finally:
            con.close()


def _decode_tracked_symbol_row(row: Any) -> Dict[str, Any]:
    item = dict(row)
    item["saved_manual"] = bool(item.get("saved_manual"))
    active_state = str(item.get("active_state", "idle") or "idle").strip().lower()
    if active_state not in {"active", "recently_active", "idle"}:
        active_state = "idle"
    item["active_state"] = active_state
    item["monitor_tier"] = str(item.get("monitor_tier", "eligible") or "eligible").strip().lower() or "eligible"
    try:
        item["metadata"] = json.loads(item.get("metadata_json") or "{}")
    except Exception:
        item["metadata"] = {}
    return item


def list_tracked_symbols_sync(
    account_mode: str = "paper",
    *,
    include_idle: bool = False,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    _init_db_sync()
    mode = _normalize_account_mode_key(account_mode)
    capped = max(1, min(int(limit or 500), 2000))
    clauses = ["account_mode = ?"]
    params: List[Any] = [mode]
    if not include_idle:
        clauses.append("(saved_manual = 1 OR active_state != 'idle')")
    query = f"""
        SELECT * FROM tracked_symbols
        WHERE {' AND '.join(clauses)}
        ORDER BY
            CASE active_state
                WHEN 'active' THEN 0
                WHEN 'recently_active' THEN 1
                ELSE 2
            END,
            saved_manual DESC,
            updated_ts DESC,
            symbol ASC
        LIMIT ?
    """
    params.append(capped)
    con = _connect_reader()
    try:
        rows = con.execute(query, tuple(params)).fetchall()
    finally:
        con.close()
    return [_decode_tracked_symbol_row(row) for row in rows]


def get_tracked_symbol_sync(symbol: str, account_mode: str = "paper") -> Dict[str, Any]:
    _init_db_sync()
    sym = _normalize_symbol_key(symbol)
    mode = _normalize_account_mode_key(account_mode)
    if not sym:
        return {}
    con = _connect_reader()
    try:
        row = con.execute(
            """
            SELECT * FROM tracked_symbols
            WHERE symbol = ? AND account_mode = ?
            LIMIT 1
            """,
            (sym, mode),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return {}
    return _decode_tracked_symbol_row(row)


def upsert_tracked_symbol_sync(symbol: str, account_mode: str = "paper", **updates: Any) -> Dict[str, Any]:
    _init_db_sync()
    sym = _normalize_symbol_key(symbol)
    mode = _normalize_account_mode_key(account_mode)
    if not sym:
        return {}
    now_ms = int(time.time() * 1000)
    with _LOCK:
        con = _connect()
        try:
            existing_row = con.execute(
                "SELECT * FROM tracked_symbols WHERE symbol = ? AND account_mode = ? LIMIT 1",
                (sym, mode),
            ).fetchone()
            existing = _decode_tracked_symbol_row(existing_row) if existing_row else {}
            metadata = dict(existing.get("metadata") or {})
            metadata_updates = updates.pop("metadata", None)
            if isinstance(metadata_updates, dict):
                metadata.update(metadata_updates)

            active_state = str(updates.get("active_state", existing.get("active_state", "idle")) or "idle").strip().lower()
            if active_state not in {"active", "recently_active", "idle"}:
                active_state = "idle"
            monitor_tier = str(updates.get("monitor_tier", existing.get("monitor_tier", "eligible")) or "eligible").strip().lower() or "eligible"

            values = {
                "symbol": sym,
                "account_mode": mode,
                "saved_manual": 1 if bool(updates.get("saved_manual", existing.get("saved_manual", False))) else 0,
                "active_state": active_state,
                "active_reason": str(updates.get("active_reason", existing.get("active_reason", "")) or ""),
                "active_since_ts": int(updates.get("active_since_ts", existing.get("active_since_ts") or 0) or 0),
                "active_min_until_ts": int(updates.get("active_min_until_ts", existing.get("active_min_until_ts") or 0) or 0),
                "recent_until_ts": int(updates.get("recent_until_ts", existing.get("recent_until_ts") or 0) or 0),
                "last_decision_ts": int(updates.get("last_decision_ts", existing.get("last_decision_ts") or 0) or 0),
                "monitor_tier": monitor_tier,
                "last_rank_score": float(updates.get("last_rank_score", existing.get("last_rank_score", 0.0)) or 0.0),
                "metadata_json": json.dumps(metadata, separators=(",", ":"), ensure_ascii=True),
                "updated_ts": int(updates.get("updated_ts", now_ms) or now_ms),
            }
            con.execute(
                """
                INSERT INTO tracked_symbols (
                    symbol, account_mode, saved_manual, active_state, active_reason,
                    active_since_ts, active_min_until_ts, recent_until_ts, last_decision_ts,
                    monitor_tier, last_rank_score, metadata_json, updated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, account_mode) DO UPDATE SET
                    saved_manual=excluded.saved_manual,
                    active_state=excluded.active_state,
                    active_reason=excluded.active_reason,
                    active_since_ts=excluded.active_since_ts,
                    active_min_until_ts=excluded.active_min_until_ts,
                    recent_until_ts=excluded.recent_until_ts,
                    last_decision_ts=excluded.last_decision_ts,
                    monitor_tier=excluded.monitor_tier,
                    last_rank_score=excluded.last_rank_score,
                    metadata_json=excluded.metadata_json,
                    updated_ts=excluded.updated_ts
                """,
                (
                    values["symbol"],
                    values["account_mode"],
                    values["saved_manual"],
                    values["active_state"],
                    values["active_reason"],
                    values["active_since_ts"] or None,
                    values["active_min_until_ts"] or None,
                    values["recent_until_ts"] or None,
                    values["last_decision_ts"] or None,
                    values["monitor_tier"],
                    values["last_rank_score"],
                    values["metadata_json"],
                    values["updated_ts"],
                ),
            )
            con.commit()
        finally:
            con.close()
    return get_tracked_symbol_sync(sym, mode)


def set_tracked_symbol_saved_sync(symbol: str, saved: bool, account_mode: str = "paper") -> Dict[str, Any]:
    return upsert_tracked_symbol_sync(symbol, account_mode, saved_manual=bool(saved))


def replace_saved_tracked_symbols_sync(account_mode: str, symbols: List[str]) -> List[str]:
    _init_db_sync()
    mode = _normalize_account_mode_key(account_mode)
    normalized = []
    seen = set()
    for raw in symbols or []:
        sym = _normalize_symbol_key(raw)
        if not sym or sym in seen:
            continue
        seen.add(sym)
        normalized.append(sym)

    existing = {
        str(row.get("symbol") or "").upper(): row
        for row in list_tracked_symbols_sync(mode, include_idle=True, limit=2000)
    }
    for symbol, row in existing.items():
        if bool(row.get("saved_manual")) and symbol not in seen:
            upsert_tracked_symbol_sync(
                symbol,
                mode,
                saved_manual=False,
                active_state=row.get("active_state", "idle"),
                active_reason=row.get("active_reason", ""),
                active_since_ts=row.get("active_since_ts", 0),
                active_min_until_ts=row.get("active_min_until_ts", 0),
                recent_until_ts=row.get("recent_until_ts", 0),
                last_decision_ts=row.get("last_decision_ts", 0),
                monitor_tier=row.get("monitor_tier", "eligible"),
                last_rank_score=row.get("last_rank_score", 0.0),
                metadata=row.get("metadata", {}),
            )
    for symbol in normalized:
        upsert_tracked_symbol_sync(symbol, mode, saved_manual=True)
    return normalized


def upsert_asset_alias_sync(
    canonical_asset_id: str,
    *,
    symbol: str,
    name: str = "",
    aliases: Optional[List[str]] = None,
    websites: Optional[List[str]] = None,
    platforms: Optional[Dict[str, Any]] = None,
    updated_at: Optional[int] = None,
) -> Dict[str, Any]:
    _init_db_sync()
    canonical = str(canonical_asset_id or "").strip().lower()
    sym = _normalize_symbol_key(symbol)
    if not canonical or not sym:
        return {}
    now_ms = int(updated_at or time.time() * 1000)
    aliases_payload = json.dumps(sorted({str(item or "").strip() for item in (aliases or []) if str(item or "").strip()}), separators=(",", ":"), ensure_ascii=True)
    websites_payload = json.dumps(sorted({str(item or "").strip() for item in (websites or []) if str(item or "").strip()}), separators=(",", ":"), ensure_ascii=True)
    platforms_payload = json.dumps(dict(platforms or {}), separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                """
                INSERT INTO asset_aliases (
                    canonical_asset_id, symbol, name, aliases_json, websites_json, platforms_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(canonical_asset_id) DO UPDATE SET
                    symbol=excluded.symbol,
                    name=excluded.name,
                    aliases_json=excluded.aliases_json,
                    websites_json=excluded.websites_json,
                    platforms_json=excluded.platforms_json,
                    updated_at=excluded.updated_at
                """,
                (canonical, sym, str(name or ""), aliases_payload, websites_payload, platforms_payload, now_ms),
            )
            con.commit()
        finally:
            con.close()
    return get_asset_alias_sync(canonical)


def get_asset_alias_sync(canonical_asset_id: str) -> Dict[str, Any]:
    _init_db_sync()
    canonical = str(canonical_asset_id or "").strip().lower()
    if not canonical:
        return {}
    con = _connect_reader()
    try:
        row = con.execute(
            "SELECT * FROM asset_aliases WHERE canonical_asset_id = ? LIMIT 1",
            (canonical,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return {}
    return _decode_asset_alias_row(row)


def list_asset_aliases_sync(limit: int = 1000) -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit or 1000), 5000))
    con = _connect_reader()
    try:
        rows = con.execute(
            "SELECT * FROM asset_aliases ORDER BY updated_at DESC, symbol ASC LIMIT ?",
            (capped,),
        ).fetchall()
    finally:
        con.close()
    return [_decode_asset_alias_row(row) for row in rows]


def _decode_asset_alias_row(row: Any) -> Dict[str, Any]:
    item = dict(row)
    for field, key in (
        ("aliases_json", "aliases"),
        ("websites_json", "websites"),
        ("platforms_json", "platforms"),
    ):
        try:
            item[key] = json.loads(item.get(field) or ("{}" if field == "platforms_json" else "[]"))
        except Exception:
            item[key] = {} if field == "platforms_json" else []
    return item


def migrate_tracked_saved_symbols_sync(account_mode: str, symbols: List[str]) -> List[str]:
    mode = _normalize_account_mode_key(account_mode)
    existing = {
        str(row.get("symbol") or "").upper()
        for row in list_tracked_symbols_sync(mode, include_idle=True, limit=2000)
        if bool(row.get("saved_manual"))
    }
    added: List[str] = []
    for raw in symbols or []:
        symbol = _normalize_symbol_key(raw)
        if not symbol or symbol in existing:
            continue
        upsert_tracked_symbol_sync(symbol, mode, saved_manual=True)
        existing.add(symbol)
        added.append(symbol)
    return added


def touch_tracked_symbol_decision_sync(symbol: str, account_mode: str = "paper", decision_ts: int = 0) -> Dict[str, Any]:
    ts = int(decision_ts or int(time.time() * 1000))
    return upsert_tracked_symbol_sync(symbol, account_mode, last_decision_ts=ts)


def get_session_memory_sync(symbol: str, hours: float = 6.0) -> Dict[str, Any]:
    """
    Return a rolling performance summary for a specific coin over the last N hours.
    Used by the Ollama Session Memory block to give the SLM context on its own
    recent trading behaviour (win rate, realised PnL, last exit trigger).

    Returns a dict with:
      trades_count    : int   â€” total exits in the window
      win_rate_pct    : float â€” % of exits that had positive PnL
      total_pnl_pct   : float â€” sum of realised PnL% across exits
      last_exit_min_ago: int  â€” minutes since the most recent exit
      last_exit_reason : str  â€” reason string of the last exit
    """
    _init_db_sync()
    since_ms = int((time.time() - hours * 3600) * 1000)

    # Normalise symbol: accept both "BTCUSD" and "BTC/USD"
    sym_bare = symbol.replace("/", "").upper()
    sym_slash = symbol.upper()

    # Exit action types we care about (wins & losses)
    exit_types = (
        "synthetic_exit", "synthetic_exit_tp1", "synthetic_exit_tp1_full",
        "synthetic_exit_tp2", "synthetic_exit_trailing", "synthetic_exit_rsi",
    )
    placeholders = ",".join("?" * len(exit_types))

    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                f"""
                SELECT ts, reason, payload_json
                FROM actions
                WHERE ts >= ?
                  AND action_type IN ({placeholders})
                  AND (symbol = ? OR symbol = ?)
                  AND side = 'sell'
                ORDER BY ts DESC
                """,
                (since_ms, *exit_types, sym_bare, sym_slash),
            ).fetchall()
        finally:
            con.close()

    if not rows:
        return {}

    now_ms = int(time.time() * 1000)
    wins = 0
    total_pnl = 0.0

    for row in rows:
        payload_raw = row["payload_json"] or "{}"
        try:
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else {}
        except Exception:
            payload = {}
        pnl = float(payload.get("pnl_pct", 0.0))
        total_pnl += pnl
        if pnl > 0:
            wins += 1

    count = len(rows)
    last_row = rows[0]  # Most recent (ORDER BY ts DESC)
    last_min_ago = round((now_ms - last_row["ts"]) / 60000, 1)
    last_reason = str(last_row["reason"] or "")[:100]

    return {
        "trades_count": count,
        "win_rate_pct": round((wins / count) * 100.0, 1),
        "total_pnl_pct": round(total_pnl, 2),
        "last_exit_min_ago": last_min_ago,
        "last_exit_reason": last_reason,
    }




def record_oracle_score_sync(score: float, summary: str = "") -> None:
    """Persist a Gemini Oracle sentiment score to oracle_history for ML retraining."""
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                "INSERT INTO oracle_history (ts, score, summary) VALUES (?, ?, ?)",
                (now_ms, float(score), str(summary or "")),
            )
            con.commit()
        finally:
            con.close()

def get_latest_oracle_score_sync() -> Optional[Dict[str, Any]]:
    """Fetch the most-recent oracle_history row for boot-time cache restore."""
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT ts, score, summary FROM oracle_history ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    if not row:
        return None
    return {"ts": int(row["ts"]), "score": float(row["score"]), "summary": str(row["summary"] or "")}


def _get_signal_state_sync(key: str) -> Dict[str, Any]:
    """Read a free-form text value from kv_store. Returns {} if key not found."""
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT key, value FROM kv_store WHERE key LIKE ?", (f"{key}%",)
            ).fetchall()
        finally:
            con.close()
    return {r["key"]: r["value"] for r in rows}


def _save_signal_state_sync(key: str, value: str) -> None:
    """Persist a free-form text value to kv_store (upsert)."""
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                "INSERT OR REPLACE INTO kv_store (key, value) VALUES (?, ?)",
                (str(key), str(value)),
            )
            con.commit()
        finally:
            con.close()


def get_kv_values_sync(prefix: str) -> Dict[str, Any]:
    return _get_signal_state_sync(prefix)


def save_kv_value_sync(key: str, value: str) -> None:
    _save_signal_state_sync(key, value)


def _record_action_sync(
    action_type: str,
    symbol: str = "",
    side: str = "",
    qty: Optional[float] = None,
    notional: Optional[float] = None,
    price: Optional[float] = None,
    status: str = "info",
    reason: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                """
                INSERT INTO actions (
                    ts, action_type, symbol, side, qty, notional, price, status, reason, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_ms,
                    str(action_type or "event"),
                    str(symbol or ""),
                    str(side or ""),
                    float(qty) if qty is not None else None,
                    float(notional) if notional is not None else None,
                    float(price) if price is not None else None,
                    str(status or "info"),
                    str(reason or ""),
                    payload_json,
                ),
            )
            con.commit()
            row_id = int(cur.lastrowid)
        finally:
            con.close()

    # â”€â”€ Global Telegram Broadcasts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # Push ALL major events to Telegram natively so the user never misses an action.
    try:
        from integrations.telegram.telegram_client import send_message
        import threading as _tg_thread
        
        def _bg_send():
            act_lower = action_type.lower()
            if "_report" in act_lower:
                send_message(f"ðŸ“Š *Bot Report*\n{reason}")
            elif "oracle" in act_lower or "narrative" in act_lower:
                send_message(f"ðŸ§  *AI Update*\n{reason}")
            elif act_lower in ("fatal_error", "system_halt"):
                send_message(f"ðŸš¨ *SYSTEM HALT*\n{reason}")
            elif act_lower == "strategy_veto":
                pass
            elif "order" in act_lower:
                emoji = "âœ…" if status == "success" else ("âŒ" if status == "error" else "ðŸ“")
                send_message(f"{emoji} *Order {status.upper()}*\n`{symbol}` {side.upper()} | {reason}")
            elif "position_closed" in act_lower:
                send_message(f"ðŸ’° *Position Closed*\n`{symbol}` | {reason}")
            elif status == "error":
                send_message(f"âš ï¸ *System Error*\n{reason}")
                
        # Run send in background to not block db locking
        if action_type not in ("heartbeat", "metric_tick", "strategy_veto"):
            _tg_thread.Thread(target=_bg_send, daemon=True).start()
    except Exception:
        pass
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    return {
        "id": row_id,
        "ts": now_ms,
        "action_type": action_type,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "notional": notional,
        "price": price,
        "status": status,
        "reason": reason,
        "payload": payload or {},
    }


def _list_actions_sync(limit: int = 200) -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 1000))
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT id, ts, action_type, symbol, side, qty, notional, price, status, reason, payload_json
                FROM actions
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (capped,),
            ).fetchall()
        finally:
            con.close()

    out: List[Dict[str, Any]] = []
    for r in rows:
        payload: Dict[str, Any] = {}
        try:
            payload = json.loads(r["payload_json"] or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        out.append(
            {
                "id": int(r["id"]),
                "ts": int(r["ts"] or 0),
                "action_type": str(r["action_type"] or ""),
                "symbol": str(r["symbol"] or ""),
                "side": str(r["side"] or ""),
                "qty": float(r["qty"]) if r["qty"] is not None else None,
                "notional": float(r["notional"]) if r["notional"] is not None else None,
                "price": float(r["price"]) if r["price"] is not None else None,
                "status": str(r["status"] or "info"),
                "reason": str(r["reason"] or ""),
                "payload": payload,
            }
        )
    return out


def list_actions_page_sync(
    limit: int = 200,
    *,
    before_ts: Optional[int] = None,
    status: str = "",
    symbol: str = "",
    search: str = "",
) -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 1000))
    query = [
        """
        SELECT id, ts, action_type, symbol, side, qty, notional, price, status, reason, payload_json
        FROM actions
        WHERE 1 = 1
        """
    ]
    params: List[Any] = []
    if before_ts:
        query.append("AND ts < ?")
        params.append(int(before_ts))
    if status:
        query.append("AND LOWER(status) = ?")
        params.append(str(status).strip().lower())
    if symbol:
        query.append("AND UPPER(symbol) = ?")
        params.append(str(symbol).strip().upper())
    if search:
        like = f"%{str(search).strip().lower()}%"
        query.append(
            "AND (LOWER(action_type) LIKE ? OR LOWER(symbol) LIKE ? OR LOWER(side) LIKE ? OR LOWER(status) LIKE ? OR LOWER(reason) LIKE ?)"
        )
        params.extend([like, like, like, like, like])
    query.append("ORDER BY ts DESC, id DESC LIMIT ?")
    params.append(capped)

    con = _connect_reader()
    try:
        rows = con.execute(" ".join(query), tuple(params)).fetchall()
    finally:
        con.close()

    out: List[Dict[str, Any]] = []
    for r in rows:
        payload: Dict[str, Any] = {}
        try:
            payload = json.loads(r["payload_json"] or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except Exception:
            payload = {}
        out.append(
            {
                "id": int(r["id"]),
                "ts": int(r["ts"] or 0),
                "action_type": str(r["action_type"] or ""),
                "symbol": str(r["symbol"] or ""),
                "side": str(r["side"] or ""),
                "qty": float(r["qty"]) if r["qty"] is not None else None,
                "notional": float(r["notional"]) if r["notional"] is not None else None,
                "price": float(r["price"]) if r["price"] is not None else None,
                "status": str(r["status"] or "info"),
                "reason": str(r["reason"] or ""),
                "payload": payload,
            }
        )
    return out


def _clear_actions_sync() -> int:
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            before = con.execute("SELECT COUNT(*) AS c FROM actions").fetchone()
            total = int((before["c"] if before else 0) or 0)
            con.execute("DELETE FROM actions")
            con.commit()
        finally:
            con.close()
    return total


def _get_runtime_state_sync() -> Dict[str, Any]:
    _init_db_sync()
    cached = get_runtime_state_cached_sync()
    if cached:
        return cached
    con = _connect_reader()
    try:
        row = con.execute("SELECT * FROM runtime_state WHERE id = 1").fetchone()
    finally:
        con.close()
    if not row:
        return _set_runtime_state_cache(_default_runtime_state())
    effective_risk_profile = {}
    behavior_state = {}
    component_calibration = {}
    try:
        effective_risk_profile = json.loads(row["effective_risk_profile_json"] or "{}")
    except Exception:
        effective_risk_profile = {}
    try:
        behavior_state = json.loads(row["behavior_state_json"] or "{}")
    except Exception:
        behavior_state = {}
    try:
        component_calibration = json.loads(row["component_calibration_json"] or "{}")
    except Exception:
        component_calibration = {}
    return _set_runtime_state_cache({
        "running": bool(row["running"]),
        "started_at": int(row["started_at"]) if row["started_at"] is not None else None,
        "last_heartbeat": int(row["last_heartbeat"]) if row["last_heartbeat"] is not None else None,
        "iterations": int(row["iterations"] or 0),
        "last_error": row["last_error"],
        "halted": bool(row["halted"]),
        "halted_reason": row["halted_reason"],
        "risk_state": str(row["risk_state"] or "normal"),
        "risk_source": str(row["risk_source"] or ""),
        "risk_reason": str(row["risk_reason"] or ""),
        "effective_risk_profile": effective_risk_profile,
        "behavior_state": behavior_state,
        "component_calibration": component_calibration,
        "day_start_equity_paper": float(row["day_start_equity_paper"]) if row["day_start_equity_paper"] is not None else None,
        "day_start_equity_live": float(row["day_start_equity_live"]) if row["day_start_equity_live"] is not None else None,
        "day_start_ts": int(row["day_start_ts"]) if row["day_start_ts"] is not None else None,
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
    })


def _update_runtime_state_sync(**updates: Any) -> Dict[str, Any]:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    allowed = {
        "running",
        "started_at",
        "last_heartbeat",
        "iterations",
        "last_error",
        "halted",
        "halted_reason",
        "day_start_equity_paper",
        "day_start_equity_live",
        "day_start_ts",
        "risk_state",
        "risk_source",
        "risk_reason",
        "effective_risk_profile_json",
        "behavior_state_json",
        "component_calibration_json",
    }
    fields = []
    values = []
    for key, value in updates.items():
        if key not in allowed:
            continue
        fields.append(f"{key} = ?")
        if key in {"running", "halted"}:
            values.append(1 if bool(value) else 0)
        else:
            values.append(value)
    fields.append("updated_at = ?")
    values.append(now_ms)
    values.append(1)

    with _LOCK:
        con = _connect()
        try:
            con.execute(
                f"UPDATE runtime_state SET {', '.join(fields)} WHERE id = ?",
                tuple(values),
            )
            con.commit()
        finally:
            con.close()
    return _get_runtime_state_sync()


async def init_db() -> None:
    await asyncio.to_thread(_init_db_sync)

# â”€â”€ Equity Tracking â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def record_equity_snapshot_sync(equity: float, cash: float) -> None:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                "INSERT INTO equity_history (ts, equity, cash) VALUES (?, ?, ?)",
                (now_ms, float(equity), float(cash))
            )
            con.commit()
        finally:
            con.close()

def get_equity_history_sync(since_ms: int) -> List[Dict[str, Any]]:
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT ts, equity, cash FROM equity_history WHERE ts >= ? ORDER BY ts ASC",
                (since_ms,)
            ).fetchall()
        finally:
            con.close()
    return [{"ts": int(r["ts"]), "equity": float(r["equity"]), "cash": float(r["cash"])} for r in rows]

def get_equity_performance_sync() -> Dict[str, Any]:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    hour1 = now_ms - (1 * 60 * 60 * 1000)
    hour24 = now_ms - (24 * 60 * 60 * 1000)
    day7 = now_ms - (7 * 24 * 60 * 60 * 1000)

    with _LOCK:
        con = _connect()
        try:
            # Get latest equity
            latest = con.execute("SELECT equity FROM equity_history ORDER BY ts DESC LIMIT 1").fetchone()
            latest_eq = float(latest["equity"]) if latest else 0.0

            if latest_eq == 0:
                return {}

            # get nearest older row for each lookback window
            def _get_past_eq(target_ms: int) -> float:
                row = con.execute("SELECT equity FROM equity_history WHERE ts <= ? ORDER BY ts DESC LIMIT 1", (target_ms,)).fetchone()
                return float(row["equity"]) if row else latest_eq

            eq_1h = _get_past_eq(hour1)
            eq_24h = _get_past_eq(hour24)
            eq_7d = _get_past_eq(day7)
        finally:
            con.close()

    def calc_delta(past: float) -> Dict[str, float]:
        if past == 0:
            return {"usd": 0.0, "pct": 0.0}
        diff = latest_eq - past
        return {"usd": round(diff, 2), "pct": round((diff / past) * 100.0, 2)}

    return {
        "current": latest_eq,
        "1h": calc_delta(eq_1h),
        "24h": calc_delta(eq_24h),
        "7d": calc_delta(eq_7d),
    }

async def record_equity_snapshot(equity: float, cash: float) -> None:
    await asyncio.to_thread(record_equity_snapshot_sync, equity, cash)

async def get_equity_history(since_ms: int) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(get_equity_history_sync, since_ms)

async def get_equity_performance() -> Dict[str, Any]:
    return await asyncio.to_thread(get_equity_performance_sync)

# â”€â”€ Actions & State â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def record_action(
    action_type: str,
    symbol: str = "",
    side: str = "",
    qty: Optional[float] = None,
    notional: Optional[float] = None,
    price: Optional[float] = None,
    status: str = "info",
    reason: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return await asyncio.to_thread(
        _record_action_sync,
        action_type,
        symbol,
        side,
        qty,
        notional,
        price,
        status,
        reason,
        payload,
    )


async def list_actions(limit: int = 200) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_list_actions_sync, limit)


async def list_actions_page(
    limit: int = 200,
    *,
    before_ts: Optional[int] = None,
    status: str = "",
    symbol: str = "",
    search: str = "",
) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(
        list_actions_page_sync,
        limit,
        before_ts=before_ts,
        status=status,
        symbol=symbol,
        search=search,
    )


async def clear_actions() -> int:
    return await asyncio.to_thread(_clear_actions_sync)


async def get_runtime_state() -> Dict[str, Any]:
    cached = get_runtime_state_cached_sync()
    if cached:
        return cached
    return await asyncio.to_thread(_get_runtime_state_sync)


async def update_runtime_state(**updates: Any) -> Dict[str, Any]:
    return await asyncio.to_thread(_update_runtime_state_sync, **updates)


# â”€â”€ Bot Reports â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def record_bot_report_sync(
    report_type: str,
    content: str,
    metrics_json: str = "{}",
    grade_json: str = "{}",
) -> int:
    """Persist an AI-generated bot report to SQLite."""
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO bot_reports (ts, report_type, content, metrics_json, grade_json) VALUES (?, ?, ?, ?, ?)",
                (now_ms, report_type, content, metrics_json, grade_json),
            )
            con.commit()
            return cur.lastrowid
        finally:
            con.close()


def get_latest_reports_sync(report_type: Optional[str] = None, limit: int = 1) -> List[Dict[str, Any]]:
    """Fetch the most recent bot reports, optionally filtered by type."""
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            if report_type:
                rows = con.execute(
                    "SELECT id, ts, report_type, content, metrics_json, grade_json FROM bot_reports WHERE report_type = ? ORDER BY ts DESC LIMIT ?",
                    (report_type, limit),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT id, ts, report_type, content, metrics_json, grade_json FROM bot_reports ORDER BY ts DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        finally:
            con.close()
    out = []
    for r in rows:
        metrics = {}
        grade = {}
        try:
            metrics = json.loads(r["metrics_json"] or "{}")
        except Exception:
            pass
        try:
            grade = json.loads(r["grade_json"] or "{}")
        except Exception:
            pass
        out.append({
            "id": int(r["id"]),
            "ts": int(r["ts"]),
            "report_type": str(r["report_type"]),
            "content": str(r["content"]),
            "metrics": metrics,
            "grade": grade,
        })
    return out


def get_reports_in_window_sync(report_type: str, since_ms: int) -> List[Dict[str, Any]]:
    """Fetch all reports of a given type since a timestamp (for hierarchical summarization)."""
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT id, ts, report_type, content, metrics_json FROM bot_reports WHERE report_type = ? AND ts >= ? ORDER BY ts ASC",
                (report_type, since_ms),
            ).fetchall()
        finally:
            con.close()
    return [{"ts": int(r["ts"]), "content": str(r["content"]), "metrics_json": r["metrics_json"] or "{}"} for r in rows]


def get_all_historical_grades_sync() -> Dict[int, float]:
    """Fetch all Judge grades from bot reports. Returns dict of {timestamp_ms: grade_score}."""
    import json
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT ts, grade_json FROM bot_reports WHERE grade_json IS NOT NULL AND grade_json != '{}'"
            ).fetchall()
        finally:
            con.close()
            
    grades = {}
    for r in rows:
        try:
            grade_data = json.loads(r["grade_json"])
            if "score" in grade_data:
                grades[int(r["ts"])] = float(grade_data["score"])
        except Exception:
            continue
    return grades
def get_actions_since_sync(since_ms: int) -> Dict[str, Any]:
    """Get aggregate action stats since a timestamp for report metrics."""
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT action_type, side, COUNT(*) as cnt, COALESCE(SUM(notional), 0) as total_notional FROM actions WHERE ts >= ? GROUP BY action_type, side",
                (since_ms,),
            ).fetchall()
            total = con.execute("SELECT COUNT(*) as cnt FROM actions WHERE ts >= ?", (since_ms,)).fetchone()
        finally:
            con.close()
    breakdown = {}
    for r in rows:
        key = f"{r['action_type']}_{r['side']}".strip("_")
        breakdown[key] = {"count": r["cnt"], "total_notional": round(r["total_notional"], 2)}
    return {"total_actions": total["cnt"] if total else 0, "breakdown": breakdown}


def get_recent_closed_trades_sync(since_ms: int) -> list:
    """
    Pairs recent SELLs with their corresponding BUYs to calculate realized P&L per trade.
    Uses FIFO matching. Only returns trades where the SELL occurred >= since_ms.
    """
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            # Look back an extra 7 days to find the BUYs for recent SELLs
            search_window = since_ms - (7 * 24 * 60 * 60 * 1000)
            rows = con.execute(
                "SELECT symbol, side, qty, price, ts FROM actions WHERE status = 'filled' AND ts >= ? ORDER BY ts ASC",
                (search_window,)
            ).fetchall()
        finally:
            con.close()
            
    inventory = {}
    closed_trades = []
    
    for r in rows:
        sym = r["symbol"]
        side = (r["side"] or "").lower()
        qty = float(r["qty"] or 0)
        price = float(r["price"] or 0)
        ts = int(r["ts"])
        
        if qty <= 0 or price <= 0:
            continue
            
        if side == "buy":
            inventory.setdefault(sym, []).append({"qty": qty, "price": price})
        elif side == "sell":
            sell_qty = qty
            buy_value = 0.0
            sell_value = 0.0
            
            if sym in inventory:
                while sell_qty > 0.000001 and inventory[sym]:
                    buy = inventory[sym][0]
                    matched_qty = min(sell_qty, buy["qty"])
                    
                    buy_value += matched_qty * buy["price"]
                    sell_value += matched_qty * price
                    
                    buy["qty"] -= matched_qty
                    sell_qty -= matched_qty
                    
                    if buy["qty"] <= 0.000001:
                        inventory[sym].pop(0)
                        
            if buy_value > 0 and ts >= since_ms:
                pnl = sell_value - buy_value
                pnl_pct = (pnl / buy_value) * 100
                closed_trades.append({
                    "symbol": sym,
                    "pnl_dollar": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "sell_ts": ts
                })
                    
    return sorted(closed_trades, key=lambda x: x["sell_ts"], reverse=True)[:10]


def get_oracle_history_sync(since_ms: int) -> Dict[str, Any]:
    """Get oracle score stats since a timestamp for report metrics."""
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT COUNT(*) as cnt, COALESCE(AVG(score), 1.0) as avg_score, COALESCE(MIN(score), 1.0) as min_score, COALESCE(MAX(score), 1.0) as max_score FROM oracle_history WHERE ts >= ?",
                (since_ms,),
            ).fetchone()
        finally:
            con.close()
    return {
        "count": row["cnt"] if row else 0,
        "avg": round(row["avg_score"], 2) if row else 1.0,
        "min": round(row["min_score"], 2) if row else 1.0,
        "max": round(row["max_score"], 2) if row else 1.0,
    }


async def record_bot_report(report_type: str, content: str, metrics_json: str = "{}", grade_json: str = "{}") -> int:
    return await asyncio.to_thread(record_bot_report_sync, report_type, content, metrics_json, grade_json)


async def get_latest_reports(report_type: Optional[str] = None, limit: int = 1) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(get_latest_reports_sync, report_type, limit)


# â”€â”€ Live Experience â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def record_live_experience_sync(
    symbol: str,
    side: str,
    entry_ts: int,
    entry_price: float,
    notional: float,
    gemini_score: float,
    gemini_regime: str = "normal",
    strategy_used: str = "",
    session_win_rate: float = 0.0,
    session_pnl_pct: float = 0.0,
    narrative_label: str = "neutral",
    rsi_15m_at_entry: float = 50.0,
    rsi_1m_at_entry: float = 50.0,
    macd_hist_at_entry: float = 0.0,
    slm_confidence: float = 0.0,
    brain_mode: str = "",
    event_score_at_entry: float = 0.0,
    event_bias_at_entry: str = "neutral",
    top_event_type_at_entry: str = "",
    decision_trace_id: Optional[int] = None,
    news_context_state_at_entry: str = "",
    news_context_complete_at_entry: bool = True,
    monitor_tier_at_entry: str = "",
    active_reason_at_entry: str = "",
) -> int:
    """Write a new live_experience row at entry time. Returns row id to update on close."""
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                """
                INSERT INTO live_experience (
                    entry_ts, symbol, side, strategy_used, entry_price, notional,
                    gemini_score, gemini_regime, session_win_rate, session_pnl_pct,
                    narrative_label, rsi_15m_at_entry, rsi_1m_at_entry,
                    macd_hist_at_entry, slm_confidence, brain_mode, event_score_at_entry,
                    event_bias_at_entry, top_event_type_at_entry, decision_trace_id,
                    news_context_state_at_entry, news_context_complete_at_entry,
                    monitor_tier_at_entry, active_reason_at_entry
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_ts, str(symbol), str(side), str(strategy_used),
                    float(entry_price), float(notional), float(gemini_score),
                    str(gemini_regime), float(session_win_rate), float(session_pnl_pct),
                    str(narrative_label), float(rsi_15m_at_entry), float(rsi_1m_at_entry),
                    float(macd_hist_at_entry), float(slm_confidence), str(brain_mode),
                    float(event_score_at_entry), str(event_bias_at_entry),
                    str(top_event_type_at_entry), int(decision_trace_id) if decision_trace_id is not None else None,
                    str(news_context_state_at_entry or ""),
                    1 if bool(news_context_complete_at_entry) else 0,
                    str(monitor_tier_at_entry or ""),
                    str(active_reason_at_entry or ""),
                ),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def close_live_experience_sync(
    row_id: int,
    exit_ts: int,
    exit_price: float,
    outcome_pnl_pct: float,
    hold_duration_min: float,
    exit_reason: str = "",
) -> None:
    """Update a live_experience row with the outcome when the position closes."""
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                """
                UPDATE live_experience
                SET exit_ts = ?, exit_price = ?, outcome_pnl_pct = ?,
                    hold_duration_min = ?, exit_reason = ?
                WHERE id = ?
                """,
                (exit_ts, float(exit_price), float(outcome_pnl_pct),
                 float(hold_duration_min), str(exit_reason), int(row_id)),
            )
            con.commit()
        finally:
            con.close()


def get_live_experience_sync(limit: int = 1000, closed_only: bool = True, symbol: str = "") -> List[Dict[str, Any]]:
    """Fetch live_experience rows. closed_only=True returns only trades with outcomes."""
    _init_db_sync()
    clauses: List[str] = []
    params: List[Any] = []
    if closed_only:
        clauses.append("exit_ts IS NOT NULL")
    if str(symbol or "").strip():
        clauses.append("UPPER(symbol) = ?")
        params.append(str(symbol).strip().upper())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    con = _connect_reader()
    try:
        rows = con.execute(
            f"SELECT * FROM live_experience {where} ORDER BY entry_ts DESC LIMIT ?",
            (*params, max(1, min(int(limit), 5000))),
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


# -- News, Macro, and Decision Trace Persistence ---------------------------------

def record_news_event_sync(event: Dict[str, Any]) -> str:
    """Upsert a parsed news event. Returns the persisted id."""
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    event_id = str(event.get("id") or event.get("external_id") or f"evt:{now_ms}")
    payload_json = json.dumps(event.get("payload", event), separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                """
                INSERT INTO news_events (
                    id, source, external_id, symbol, base_asset, event_type, headline, url,
                    published_at, ingested_at, sentiment, impact_score, confidence, urgency,
                    ttl_minutes, expires_at, trade_bias, parsed_by, parser_model, reason,
                    canonical_asset_id, event_scope, attribution_confidence, attribution_method,
                    source_tier, source_category, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source=excluded.source,
                    external_id=excluded.external_id,
                    symbol=excluded.symbol,
                    base_asset=excluded.base_asset,
                    event_type=excluded.event_type,
                    headline=excluded.headline,
                    url=excluded.url,
                    published_at=excluded.published_at,
                    ingested_at=excluded.ingested_at,
                    sentiment=excluded.sentiment,
                    impact_score=excluded.impact_score,
                    confidence=excluded.confidence,
                    urgency=excluded.urgency,
                    ttl_minutes=excluded.ttl_minutes,
                    expires_at=excluded.expires_at,
                    trade_bias=excluded.trade_bias,
                    parsed_by=excluded.parsed_by,
                    parser_model=excluded.parser_model,
                    reason=excluded.reason,
                    canonical_asset_id=excluded.canonical_asset_id,
                    event_scope=excluded.event_scope,
                    attribution_confidence=excluded.attribution_confidence,
                    attribution_method=excluded.attribution_method,
                    source_tier=excluded.source_tier,
                    source_category=excluded.source_category,
                    payload_json=excluded.payload_json
                """,
                (
                    event_id, str(event.get("source", "news")),
                    str(event.get("external_id") or event_id), str(event.get("symbol", "")),
                    str(event.get("base_asset", "")), str(event.get("event_type", "")),
                    str(event.get("headline", "")), str(event.get("url", "")),
                    int(event.get("published_at", now_ms)), int(event.get("ingested_at", now_ms)),
                    str(event.get("sentiment", "neutral")), float(event.get("impact_score", 0.0) or 0.0),
                    float(event.get("confidence", 0.0) or 0.0), str(event.get("urgency", "medium")),
                    int(event.get("ttl_minutes", 0) or 0), int(event.get("expires_at", 0) or 0),
                    str(event.get("trade_bias", "watch_only")), str(event.get("parsed_by", "")),
                    str(event.get("parser_model", "")), str(event.get("reason", "")),
                    str(event.get("canonical_asset_id", "")),
                    str(event.get("event_scope", "")),
                    float(event.get("attribution_confidence", 0.0) or 0.0),
                    str(event.get("attribution_method", "")),
                    str(event.get("source_tier", "")),
                    str(event.get("source_category", "")),
                    payload_json,
                ),
            )
            con.commit()
        finally:
            con.close()
    return event_id


def get_recent_news_events_sync(limit: int = 50, symbol: str = "", since_ms: int = 0, active_only: bool = False) -> List[Dict[str, Any]]:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    capped = max(1, min(int(limit), 500))
    clauses = ["published_at >= ?"]
    params: List[Any] = [int(since_ms or 0)]
    if symbol:
        clauses.append("(symbol = ? OR base_asset = ?)")
        params.extend([str(symbol), str(symbol).split('/')[0]])
    if active_only:
        clauses.append("(expires_at IS NULL OR expires_at = 0 OR expires_at >= ?)")
        params.append(now_ms)
    where = " AND ".join(clauses)
    con = _connect_reader()
    try:
        rows = con.execute(
            f"SELECT * FROM news_events WHERE {where} ORDER BY published_at DESC LIMIT ?",
            (*params, capped),
        ).fetchall()
    finally:
        con.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        payload = {}
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            pass
        item = dict(row)
        item["payload"] = payload
        out.append(item)
    return out


def upsert_symbol_event_state_sync(symbol: str, state: Dict[str, Any]) -> None:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    payload_json = json.dumps(state or {}, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                """
                INSERT INTO symbol_event_state (
                    symbol, net_event_score, event_bias, hard_veto, golden_trade_flag,
                    top_event_id, top_event_type, top_event_score, event_count_active, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    net_event_score=excluded.net_event_score,
                    event_bias=excluded.event_bias,
                    hard_veto=excluded.hard_veto,
                    golden_trade_flag=excluded.golden_trade_flag,
                    top_event_id=excluded.top_event_id,
                    top_event_type=excluded.top_event_type,
                    top_event_score=excluded.top_event_score,
                    event_count_active=excluded.event_count_active,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
                """,
                (
                    str(symbol), float(state.get("net_event_score", 0.0) or 0.0),
                    str(state.get("event_bias", "neutral")), 1 if bool(state.get("hard_veto", False)) else 0,
                    1 if bool(state.get("golden_trade_flag", False)) else 0,
                    str(state.get("top_event_id", "")), str(state.get("top_event_type", "")),
                    float(state.get("top_event_score", 0.0) or 0.0), int(state.get("event_count_active", 0) or 0),
                    int(state.get("updated_at", now_ms) or now_ms), payload_json,
                ),
            )
            con.commit()
        finally:
            con.close()


def get_symbol_event_state_sync(symbol: str) -> Dict[str, Any]:
    _init_db_sync()
    candidates = [str(symbol), str(symbol).split('/')[0]]
    con = _connect_reader()
    try:
        row = con.execute(
            "SELECT * FROM symbol_event_state WHERE symbol IN (?, ?) ORDER BY updated_at DESC LIMIT 1",
            (candidates[0], candidates[1]),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return {}
    out = dict(row)
    try:
        out["payload"] = json.loads(row["payload_json"] or "{}")
    except Exception:
        out["payload"] = {}
    for field in (
        "news_context_state",
        "news_context_complete",
        "news_last_fresh_at",
        "news_provider_status",
        "news_sources",
    ):
        if field not in out and field in out["payload"]:
            out[field] = out["payload"].get(field)
    out["hard_veto"] = bool(out.get("hard_veto"))
    out["golden_trade_flag"] = bool(out.get("golden_trade_flag"))
    if "news_context_complete" in out:
        out["news_context_complete"] = bool(out.get("news_context_complete"))
    return out


def list_symbol_event_states_sync(limit: int = 100) -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 500))
    con = _connect_reader()
    try:
        rows = con.execute(
            "SELECT * FROM symbol_event_state ORDER BY ABS(net_event_score) DESC, updated_at DESC LIMIT ?",
            (capped,),
        ).fetchall()
    finally:
        con.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {}
        item["payload"] = payload
        for field in (
            "news_context_state",
            "news_context_complete",
            "news_last_fresh_at",
            "news_provider_status",
            "news_sources",
        ):
            if field not in item and field in payload:
                item[field] = payload.get(field)
        item["hard_veto"] = bool(item.get("hard_veto"))
        item["golden_trade_flag"] = bool(item.get("golden_trade_flag"))
        if "news_context_complete" in item:
            item["news_context_complete"] = bool(item.get("news_context_complete"))
        out.append(item)
    return out


def record_macro_consensus_sync(
    risk_multiplier: float,
    market_regime: str,
    macro_sentiment_score: float = 0.0,
    summary: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO macro_consensus_history (ts, risk_multiplier, market_regime, macro_sentiment_score, summary, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                (now_ms, float(risk_multiplier), str(market_regime), float(macro_sentiment_score), str(summary), payload_json),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def get_latest_macro_consensus_sync() -> Optional[Dict[str, Any]]:
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            row = con.execute("SELECT * FROM macro_consensus_history ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
        finally:
            con.close()
    if not row:
        return None
    out = dict(row)
    try:
        out["payload"] = json.loads(row["payload_json"] or "{}")
    except Exception:
        out["payload"] = {}
    return out


def _load_json_payload(raw_value: Any) -> Dict[str, Any]:
    try:
        return json.loads(raw_value or "{}")
    except Exception:
        return {}


def _coerce_epoch_ms(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        raw = float(value)
    except Exception:
        return 0
    if raw <= 0:
        return 0
    if raw > 10_000_000_000:
        return int(raw)
    return int(raw * 1000)


def _rows_with_payload(rows: List[sqlite3.Row], bool_fields: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = _load_json_payload(row["payload_json"])
        for field in bool_fields or []:
            if field in item:
                item[field] = bool(item.get(field))
        out.append(item)
    return out


def record_brain_decision_trace_sync(
    symbol: str,
    brain_mode: str,
    decision: str,
    final_score: float = 0.0,
    submitted: bool = False,
    block_reason: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO brain_decision_trace (ts, symbol, brain_mode, decision, final_score, submitted, block_reason, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now_ms, str(symbol), str(brain_mode), str(decision), float(final_score or 0.0), 1 if submitted else 0, str(block_reason or ""), payload_json),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def list_brain_decision_traces_sync(limit: int = 100, symbol: str = "") -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 500))
    con = _connect_reader()
    try:
        if symbol:
            rows = con.execute(
                "SELECT * FROM brain_decision_trace WHERE symbol = ? ORDER BY ts DESC, id DESC LIMIT ?",
                (str(symbol), capped),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM brain_decision_trace ORDER BY ts DESC, id DESC LIMIT ?",
                (capped,),
            ).fetchall()
    finally:
        con.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(row["payload_json"] or "{}")
        except Exception:
            item["payload"] = {}
        item["submitted"] = bool(item.get("submitted"))
        out.append(item)
    return out


def list_pending_decision_traces_for_outcome_sync(horizon_min: int, older_than_ts: int, limit: int = 100) -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 500))
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT t.*
                FROM brain_decision_trace t
                WHERE t.ts <= ?
                  AND t.decision IN ('buy', 'sell')
                  AND NOT EXISTS (
                      SELECT 1 FROM decision_outcomes o
                      WHERE o.trace_id = t.id AND o.horizon_min = ?
                  )
                ORDER BY t.ts DESC, t.id DESC
                LIMIT ?
                """,
                (int(older_than_ts), int(horizon_min), capped),
            ).fetchall()
        finally:
            con.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(row["payload_json"] or "{}")
        except Exception:
            item["payload"] = {}
        item["submitted"] = bool(item.get("submitted"))
        out.append(item)
    return out


def count_pending_decision_traces_for_outcome_sync(horizon_min: int, older_than_ts: int) -> int:
    _init_db_sync()
    con = _connect_reader()
    try:
        row = con.execute(
            """
            SELECT COUNT(*) AS count
            FROM brain_decision_trace t
            WHERE t.ts <= ?
              AND t.decision IN ('buy', 'sell')
              AND NOT EXISTS (
                  SELECT 1 FROM decision_outcomes o
                  WHERE o.trace_id = t.id AND o.horizon_min = ?
              )
            """,
            (int(older_than_ts), int(horizon_min)),
        ).fetchone()
    finally:
        con.close()
    return int((row["count"] if row else 0) or 0)


def get_block_counts_last_24h_sync(since_ts: int, limit: int = 400) -> Dict[str, int]:
    _init_db_sync()
    capped = max(1, min(int(limit), 1000))
    con = _connect_reader()
    try:
        rows = con.execute(
            """
            SELECT UPPER(symbol) AS symbol, COUNT(*) AS cnt
            FROM brain_decision_trace
            WHERE ts >= ?
              AND submitted = 0
              AND TRIM(COALESCE(symbol, '')) <> ''
            GROUP BY UPPER(symbol)
            ORDER BY cnt DESC, symbol ASC
            LIMIT ?
            """,
            (int(since_ts), capped),
        ).fetchall()
    finally:
        con.close()
    return {
        str(row["symbol"] or ""): int(row["cnt"] or 0)
        for row in rows
        if str(row["symbol"] or "").strip()
    }



def list_recent_decision_symbols_sync(since_ts: int, limit: int = 500) -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit or 500), 2000))
    con = _connect_reader()
    try:
        rows = con.execute(
            """
            SELECT
                UPPER(symbol) AS symbol,
                MAX(ts) AS last_decision_ts,
                MAX(CASE WHEN submitted = 1 THEN 1 ELSE 0 END) AS has_submitted
            FROM brain_decision_trace
            WHERE ts >= ?
              AND TRIM(COALESCE(symbol, '')) <> ''
            GROUP BY UPPER(symbol)
            ORDER BY last_decision_ts DESC, symbol ASC
            LIMIT ?
            """,
            (int(since_ts or 0), capped),
        ).fetchall()
    finally:
        con.close()
    return [
        {
            "symbol": _normalize_symbol_key(row["symbol"]),
            "last_decision_ts": int(row["last_decision_ts"] or 0),
            "has_submitted": bool(row["has_submitted"]),
        }
        for row in rows
        if _normalize_symbol_key(row["symbol"])
    ]


def record_decision_outcome_sync(
    trace_id: int,
    horizon_min: int,
    decision_ts: int,
    symbol: str,
    decision: str,
    submitted: bool,
    block_reason: str,
    ref_price: float,
    outcome_price: float,
    pnl_pct: float,
    outcome_label: str,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            cols = {str(r["name"]) for r in con.execute("PRAGMA table_info(decision_outcomes)").fetchall()}
            values_by_col = {
                "trace_id": int(trace_id),
                "horizon_min": int(horizon_min),
                "evaluated_at": now_ms,
                "decision_ts": int(decision_ts),
                "symbol": str(symbol),
                "decision": str(decision),
                "submitted": 1 if submitted else 0,
                "block_reason": str(block_reason or ""),
                "ref_price": float(ref_price or 0.0),
                "outcome_price": float(outcome_price or 0.0),
                "pnl_pct": float(pnl_pct or 0.0),
                "outcome_label": str(outcome_label or ""),
                "payload_json": payload_json,
                "entry_ts": int(decision_ts),
                "entry_price": float(ref_price or 0.0),
                "exit_price": float(outcome_price or 0.0),
                "logged_at": now_ms,
            }
            ordered_cols = [
                "trace_id",
                "symbol",
                "entry_ts",
                "horizon_min",
                "entry_price",
                "exit_price",
                "pnl_pct",
                "outcome_label",
                "logged_at",
                "evaluated_at",
                "decision_ts",
                "decision",
                "submitted",
                "block_reason",
                "ref_price",
                "outcome_price",
                "payload_json",
            ]
            insert_cols = [name for name in ordered_cols if name in cols]
            cur = con.execute(
                f"""
                INSERT OR REPLACE INTO decision_outcomes (
                    {", ".join(insert_cols)}
                ) VALUES ({", ".join(["?"] * len(insert_cols))})
                """,
                tuple(values_by_col[name] for name in insert_cols),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()



def list_decision_outcomes_sync(limit: int = 200, symbol: str = "") -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 1000))
    con = _connect_reader()
    try:
        if symbol:
            rows = con.execute(
                "SELECT * FROM decision_outcomes WHERE symbol = ? ORDER BY decision_ts DESC, id DESC LIMIT ?",
                (str(symbol), capped),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM decision_outcomes ORDER BY decision_ts DESC, id DESC LIMIT ?",
                (capped,),
            ).fetchall()
    finally:
        con.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(row["payload_json"] or "{}")
        except Exception:
            item["payload"] = {}
        item["submitted"] = bool(item.get("submitted"))
        out.append(item)
    return out



def record_candidate_trace_sync(
    symbol: str,
    brain_mode: str,
    strategy: str,
    side: str,
    score: float = 0.0,
    submitted: bool = False,
    candidate_class: str = "",
    suppressed_reason: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO candidate_trace (ts, symbol, brain_mode, strategy, side, score, submitted, candidate_class, suppressed_reason, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now_ms,
                    str(symbol),
                    str(brain_mode),
                    str(strategy or ""),
                    str(side or "info"),
                    float(score or 0.0),
                    1 if submitted else 0,
                    str(candidate_class or ""),
                    str(suppressed_reason or ""),
                    payload_json,
                ),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def has_recent_candidate_trace_sync(
    symbol: str,
    strategy: str,
    candidate_class: str,
    suppressed_reason: str,
    score: float,
    *,
    within_sec: int = 180,
    score_epsilon: float = 0.5,
) -> bool:
    _init_db_sync()
    cutoff_ms = int(time.time() * 1000) - max(1, int(within_sec)) * 1000
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                """
                SELECT 1
                FROM candidate_trace
                WHERE symbol = ?
                  AND strategy = ?
                  AND candidate_class = ?
                  AND suppressed_reason = ?
                  AND ts >= ?
                  AND ABS(COALESCE(score, 0) - ?) <= ?
                ORDER BY ts DESC, id DESC
                LIMIT 1
                """,
                (
                    str(symbol),
                    str(strategy or ""),
                    str(candidate_class or ""),
                    str(suppressed_reason or ""),
                    int(cutoff_ms),
                    float(score or 0.0),
                    float(score_epsilon or 0.0),
                ),
            ).fetchone()
        finally:
            con.close()
    return row is not None


def list_candidate_traces_sync(limit: int = 200, symbol: str = "") -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 1000))
    con = _connect_reader()
    try:
        if symbol:
            rows = con.execute(
                "SELECT * FROM candidate_trace WHERE symbol = ? ORDER BY ts DESC, id DESC LIMIT ?",
                (str(symbol), capped),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM candidate_trace ORDER BY ts DESC, id DESC LIMIT ?",
                (capped,),
            ).fetchall()
    finally:
        con.close()
    return _rows_with_payload(rows, bool_fields=["submitted"])


def record_governor_state_history_sync(
    mode: str,
    candidate_mode: str,
    pressure_score: float,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO governor_state_history (ts, mode, candidate_mode, pressure_score, payload_json) VALUES (?, ?, ?, ?, ?)",
                (now_ms, str(mode or "normal"), str(candidate_mode or "normal"), float(pressure_score or 0.0), payload_json),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def list_governor_state_history_sync(limit: int = 200, since_ms: int = 0) -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 5000))
    with _LOCK:
        con = _connect()
        try:
            if int(since_ms or 0) > 0:
                rows = con.execute(
                    "SELECT * FROM governor_state_history WHERE ts >= ? ORDER BY ts DESC, id DESC LIMIT ?",
                    (int(since_ms), capped),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM governor_state_history ORDER BY ts DESC, id DESC LIMIT ?",
                    (capped,),
                ).fetchall()
        finally:
            con.close()
    return _rows_with_payload(rows)


def record_risk_state_history_sync(
    event_type: str,
    state_key: str = "",
    state_value: str = "",
    symbol: str = "",
    reason: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO risk_state_history (ts, symbol, event_type, state_key, state_value, reason, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now_ms, str(symbol or ""), str(event_type or ""), str(state_key or ""), str(state_value or ""), str(reason or ""), payload_json),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def list_risk_state_history_sync(limit: int = 200, symbol: str = "") -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 1000))
    with _LOCK:
        con = _connect()
        try:
            if symbol:
                rows = con.execute(
                    "SELECT * FROM risk_state_history WHERE symbol = ? OR symbol = '' ORDER BY ts DESC, id DESC LIMIT ?",
                    (str(symbol), capped),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM risk_state_history ORDER BY ts DESC, id DESC LIMIT ?",
                    (capped,),
                ).fetchall()
        finally:
            con.close()
    return _rows_with_payload(rows)



def record_component_calibration_sync(payload: Optional[Dict[str, Any]] = None) -> int:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO component_calibration_history (ts, payload_json) VALUES (?, ?)",
                (now_ms, payload_json),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def get_latest_component_calibration_sync() -> Dict[str, Any]:
    _init_db_sync()
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT * FROM component_calibration_history ORDER BY ts DESC, id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    if not row:
        return {}
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        payload = {}
    payload["ts"] = int(row["ts"] or 0)
    return payload


def record_research_scratchpad_sync(
    channel: str,
    question: str,
    intent: str = "",
    critique_verdict: str = "",
    confidence: float = 0.0,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO research_scratchpad (ts, channel, question, intent, critique_verdict, confidence, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now_ms, str(channel or "telegram"), str(question or ""), str(intent or ""), str(critique_verdict or ""), float(confidence or 0.0), payload_json),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def list_research_scratchpads_sync(limit: int = 100, channel: str = "") -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 1000))
    with _LOCK:
        con = _connect()
        try:
            if channel:
                rows = con.execute(
                    "SELECT * FROM research_scratchpad WHERE channel = ? ORDER BY ts DESC, id DESC LIMIT ?",
                    (str(channel), capped),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM research_scratchpad ORDER BY ts DESC, id DESC LIMIT ?",
                    (capped,),
                ).fetchall()
        finally:
            con.close()
    return _rows_with_payload(rows)
def record_governor_mode_history_sync(
    mode: str,
    pressure_score: float,
    effectiveness_score: float,
    confidence_score: float,
    stability_score: float,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO governor_mode_history (ts, mode, pressure_score, effectiveness_score, confidence_score, stability_score, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now_ms, str(mode or "normal"), float(pressure_score or 0.0), float(effectiveness_score or 0.0), float(confidence_score or 0.0), float(stability_score or 0.0), payload_json),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def list_governor_mode_history_sync(limit: int = 200, since_ms: int = 0) -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 5000))
    with _LOCK:
        con = _connect()
        try:
            if int(since_ms or 0) > 0:
                rows = con.execute(
                    "SELECT * FROM governor_mode_history WHERE ts >= ? ORDER BY ts DESC, id DESC LIMIT ?",
                    (int(since_ms), capped),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM governor_mode_history ORDER BY ts DESC, id DESC LIMIT ?",
                    (capped,),
                ).fetchall()
        finally:
            con.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(row["payload_json"] or "{}")
        except Exception:
            item["payload"] = {}
        out.append(item)
    return out


def get_activity_history_sync(since_ms: int = 0, limit: int = 1000) -> List[Dict[str, Any]]:
    capped = max(1, min(int(limit), 5000))
    state_rows = list(reversed(list_governor_state_history_sync(limit=max(capped * 2, 200), since_ms=since_ms)))
    mode_rows = list(reversed(list_governor_mode_history_sync(limit=max(capped * 2, 200), since_ms=since_ms)))
    out: List[Dict[str, Any]] = []
    mode_idx = 0
    current_mode: Dict[str, Any] = {}
    for row in state_rows:
        ts = int(row.get("ts", 0) or 0)
        while mode_idx < len(mode_rows) and int(mode_rows[mode_idx].get("ts", 0) or 0) <= ts:
            current_mode = dict(mode_rows[mode_idx])
            mode_idx += 1
        payload = dict(row.get("payload") or {})
        metrics = dict(payload.get("metrics") or {})
        effective = dict(payload.get("effective") or {})
        out.append({
            "ts": ts,
            "mode": str(row.get("mode", "normal") or "normal"),
            "candidate_mode": str(row.get("candidate_mode", "normal") or "normal"),
            "pressure_score": float(row.get("pressure_score", 0.0) or 0.0),
            "reason_codes": list(payload.get("reason_codes") or []),
            "metrics": metrics,
            "effective": effective,
            "effectiveness_score": float(current_mode.get("effectiveness_score", 0.0) or 0.0),
            "confidence_score": float(current_mode.get("confidence_score", 0.0) or 0.0),
            "stability_score": float(current_mode.get("stability_score", 0.0) or 0.0),
            "verdict": str((current_mode.get("payload") or {}).get("verdict", "") or current_mode.get("payload", {}).get("verdict", "") or current_mode.get("verdict", "")),
        })
    if len(out) > capped:
        out = out[-capped:]
    return out


async def get_activity_history(since_ms: int = 0, limit: int = 1000) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(get_activity_history_sync, since_ms, limit)


def record_universe_snapshot_sync(
    account_mode: str,
    selected_symbols: List[str],
    promoted_symbols: List[str],
    rankings: List[Dict[str, Any]],
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    selected_json = json.dumps(list(selected_symbols or []), separators=(",", ":"), ensure_ascii=True)
    promoted_json = json.dumps(list(promoted_symbols or []), separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                "INSERT INTO universe_snapshots (ts, account_mode, selected_symbols_json, promoted_symbols_json, payload_json) VALUES (?, ?, ?, ?, ?)",
                (now_ms, str(account_mode or ""), selected_json, promoted_json, payload_json),
            )
            snapshot_id = int(cur.lastrowid)
            for row in rankings or []:
                row_payload = json.dumps(row, separators=(",", ":"), ensure_ascii=True)
                con.execute(
                    """
                    INSERT INTO universe_rankings (
                        snapshot_id, symbol, bucket, selected, total_rank, spread_bps,
                        liquidity_score, spread_score, volume_score, volatility_score,
                        event_score, historical_edge_score, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        str(row.get("symbol", "")),
                        str(row.get("bucket", "discovery")),
                        1 if bool(row.get("selected", False)) else 0,
                        float(row.get("total_rank", 0.0) or 0.0),
                        float(row.get("spread_bps", 0.0) or 0.0),
                        float(row.get("liquidity_score", 0.0) or 0.0),
                        float(row.get("spread_score", 0.0) or 0.0),
                        float(row.get("volume_score", 0.0) or 0.0),
                        float(row.get("volatility_score", 0.0) or 0.0),
                        float(row.get("event_score", 0.0) or 0.0),
                        float(row.get("historical_edge_score", 0.0) or 0.0),
                        row_payload,
                    ),
                )
            con.commit()
            return snapshot_id
        finally:
            con.close()


def _decode_universe_snapshot_row(row: Any) -> Dict[str, Any]:
    out = dict(row)
    try:
        out["selected_symbols"] = json.loads(row["selected_symbols_json"] or "[]")
    except Exception:
        out["selected_symbols"] = []
    try:
        out["promoted_symbols"] = json.loads(row["promoted_symbols_json"] or "[]")
    except Exception:
        out["promoted_symbols"] = []
    try:
        out["payload"] = json.loads(row["payload_json"] or "{}")
    except Exception:
        out["payload"] = {}
    return out


def _decode_universe_ranking_row(rank_row: Any) -> Dict[str, Any]:
    item = dict(rank_row)
    item["selected"] = bool(item.get("selected"))
    try:
        item["payload"] = json.loads(rank_row["payload_json"] or "{}")
    except Exception:
        item["payload"] = {}
    return item


def get_latest_universe_snapshot_meta_sync() -> Optional[Dict[str, Any]]:
    _init_db_sync()
    con = _connect_reader()
    try:
        row = con.execute("SELECT * FROM universe_snapshots ORDER BY ts DESC, id DESC LIMIT 1").fetchone()
    finally:
        con.close()
    if not row:
        return None
    return _decode_universe_snapshot_row(row)


def list_universe_rankings_for_snapshot_sync(
    snapshot_id: int,
    *,
    limit: Optional[int] = None,
    selected_only: bool = False,
) -> List[Dict[str, Any]]:
    _init_db_sync()
    query = [
        "SELECT * FROM universe_rankings WHERE snapshot_id = ?",
    ]
    params: List[Any] = [int(snapshot_id)]
    if selected_only:
        query.append("AND selected = 1")
    query.append("ORDER BY selected DESC, total_rank DESC, id ASC")
    if limit is not None and int(limit) > 0:
        query.append("LIMIT ?")
        params.append(int(limit))

    con = _connect_reader()
    try:
        ranking_rows = con.execute(" ".join(query), tuple(params)).fetchall()
    finally:
        con.close()

    return [_decode_universe_ranking_row(rank_row) for rank_row in ranking_rows]


def get_universe_ranking_for_symbol_sync(snapshot_id: int, symbol: str) -> Dict[str, Any]:
    _init_db_sync()
    sym = str(symbol or "").strip().upper()
    if not sym:
        return {}
    con = _connect_reader()
    try:
        row = con.execute(
            """
            SELECT * FROM universe_rankings
            WHERE snapshot_id = ? AND UPPER(symbol) = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(snapshot_id), sym),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return {}
    return _decode_universe_ranking_row(row)


def get_nearest_universe_mid_price_sync(
    symbol: str,
    target_ts_ms: int,
    *,
    window_min: int = 180,
    limit: int = 240,
) -> Dict[str, Any]:
    _init_db_sync()
    sym = str(symbol or "").strip().upper()
    target_ms = int(target_ts_ms or 0)
    if not sym or target_ms <= 0:
        return {}

    window_ms = max(1, int(window_min or 180)) * 60 * 1000
    capped = max(10, min(int(limit or 240), 1000))
    query = """
        SELECT
            r.id,
            r.snapshot_id,
            r.symbol,
            r.payload_json,
            s.ts AS snapshot_ts
        FROM universe_rankings r
        JOIN universe_snapshots s
          ON s.id = r.snapshot_id
        WHERE UPPER(r.symbol) = ?
          AND s.ts BETWEEN ? AND ?
        ORDER BY ABS(s.ts - ?) ASC, r.id DESC
        LIMIT ?
    """
    params = (
        sym,
        target_ms - window_ms,
        target_ms + window_ms,
        target_ms,
        capped,
    )

    con = _connect_reader()
    try:
        rows = con.execute(query, params).fetchall()
    finally:
        con.close()

    best: Dict[str, Any] = {}
    best_delta_ms: Optional[int] = None
    for row in rows:
        payload = _load_json_payload(row["payload_json"])
        nested_payload = payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {}
        price = 0.0
        for candidate in (
            payload.get("mid_price"),
            nested_payload.get("mid_price"),
            payload.get("close"),
            nested_payload.get("close"),
            payload.get("price"),
            nested_payload.get("price"),
            payload.get("last_price"),
            nested_payload.get("last_price"),
        ):
            try:
                price = float(candidate or 0.0)
            except Exception:
                price = 0.0
            if price > 0:
                break
        if price <= 0:
            continue

        candidate_ts = _coerce_epoch_ms(payload.get("ts"))
        if candidate_ts <= 0:
            candidate_ts = _coerce_epoch_ms(row["snapshot_ts"])
        if candidate_ts <= 0:
            continue
        delta_ms = abs(candidate_ts - target_ms)
        if best_delta_ms is not None and delta_ms >= best_delta_ms:
            continue
        best_delta_ms = delta_ms
        best = {
            "snapshot_id": int(row["snapshot_id"] or 0),
            "symbol": sym,
            "price": round(price, 8),
            "ts": candidate_ts,
            "delta_ms": delta_ms,
            "payload": payload,
        }
    return best


def get_latest_universe_snapshot_sync(
    *,
    limit_rankings: Optional[int] = None,
    selected_only: bool = False,
) -> Optional[Dict[str, Any]]:
    out = get_latest_universe_snapshot_meta_sync()
    if not out:
        return None
    if limit_rankings == 0:
        out["rankings"] = []
        return out

    out["rankings"] = list_universe_rankings_for_snapshot_sync(
        int(out["id"]),
        limit=limit_rankings,
        selected_only=selected_only,
    )
    return out


def get_strategy_performance_sync() -> List[Dict[str, Any]]:
    """
    Return P&L attribution grouped by strategy from live_experience.
    Used by the Brain/Backtest tab strategy leaderboard.
    """
    _init_db_sync()
    con = _connect_reader()
    try:
        rows = con.execute(
            """
            SELECT
                COALESCE(strategy_used, 'unknown') AS strategy,
                COUNT(*) AS trades,
                SUM(CASE WHEN outcome_pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN outcome_pnl_pct <= 0 THEN 1 ELSE 0 END) AS losses,
                ROUND(AVG(outcome_pnl_pct), 3) AS avg_pnl_pct,
                ROUND(SUM(outcome_pnl_pct), 3) AS total_pnl_pct,
                ROUND(AVG(hold_duration_min), 1) AS avg_hold_min
            FROM live_experience
            WHERE exit_ts IS NOT NULL
            GROUP BY strategy_used
            ORDER BY total_pnl_pct DESC
            """
        ).fetchall()
    finally:
        con.close()

    out = []
    for r in rows:
        trades = int(r["trades"] or 0)
        wins = int(r["wins"] or 0)
        out.append({
            "strategy": str(r["strategy"] or "unknown"),
            "trades": trades,
            "wins": wins,
            "losses": int(r["losses"] or 0),
            "win_rate_pct": round(wins / max(1, trades) * 100, 1),
            "avg_pnl_pct": float(r["avg_pnl_pct"] or 0),
            "total_pnl_pct": float(r["total_pnl_pct"] or 0),
            "avg_hold_min": float(r["avg_hold_min"] or 0),
        })
    return out


def get_live_experience_stats_sync() -> Dict[str, Any]:
    """
    Return high-level stats from live_experience table.
    Used by the Brain tab model status card.
    """
    _init_db_sync()
    con = _connect_reader()
    try:
        row = con.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN exit_ts IS NOT NULL THEN 1 ELSE 0 END) AS closed,
                SUM(CASE WHEN exit_ts IS NULL THEN 1 ELSE 0 END) AS open,
                SUM(CASE WHEN exit_ts IS NOT NULL AND outcome_pnl_pct > 0 THEN 1 ELSE 0 END) AS wins,
                ROUND(AVG(CASE WHEN exit_ts IS NOT NULL THEN outcome_pnl_pct END), 3) AS avg_pnl,
                ROUND(SUM(CASE WHEN exit_ts IS NOT NULL THEN outcome_pnl_pct ELSE 0 END), 3) AS total_pnl,
                MIN(entry_ts) AS first_ts,
                MAX(COALESCE(exit_ts, entry_ts)) AS last_ts
            FROM live_experience
            """
        ).fetchone()
    finally:
        con.close()

    if not row or not row["total"]:
        return {"total": 0, "closed": 0, "open": 0, "wins": 0, "win_rate_pct": 0.0, "avg_pnl": 0.0, "total_pnl": 0.0}

    closed = int(row["closed"] or 0)
    wins = int(row["wins"] or 0)
    return {
        "total": int(row["total"] or 0),
        "closed": closed,
        "open": int(row["open"] or 0),
        "wins": wins,
        "win_rate_pct": round(wins / max(1, closed) * 100, 1),
        "avg_pnl": float(row["avg_pnl"] or 0),
        "total_pnl": float(row["total_pnl"] or 0),
        "first_ts": int(row["first_ts"] or 0),
        "last_ts": int(row["last_ts"] or 0),
    }


def log_decision_outcome_sync(
    trace_id: int,
    symbol: str,
    entry_ts: int,
    horizon_min: int,
    entry_price: float,
    exit_price: float,
    pnl_pct: float,
    outcome_label: str,
) -> int:
    """
    Write one forward P&L measurement to decision_outcomes.
    Called by the background outcome writer in bot.py at 15m/30m/60m after entry.
    """
    _init_db_sync()
    now_ms = int(time.time() * 1000)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                """
                INSERT INTO decision_outcomes
                    (trace_id, symbol, entry_ts, horizon_min, entry_price, exit_price, pnl_pct, outcome_label, logged_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(trace_id),
                    str(symbol),
                    int(entry_ts),
                    int(horizon_min),
                    float(entry_price),
                    float(exit_price),
                    float(pnl_pct),
                    str(outcome_label),
                    now_ms,
                ),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def get_decision_outcome_stats_sync() -> Dict[str, Any]:
    """
    Aggregate decision_outcomes labels for brain feedback display.
    Returns counts of good_entry/bad_entry/flat_entry/good_block/bad_block by horizon.
    """
    _init_db_sync()
    con = _connect_reader()
    try:
        rows = con.execute(
            """
            SELECT horizon_min, outcome_label, COUNT(*) AS cnt
            FROM decision_outcomes
            GROUP BY horizon_min, outcome_label
            ORDER BY horizon_min, outcome_label
            """
        ).fetchall()
    finally:
        con.close()

    by_horizon: Dict[str, Dict[str, int]] = {}
    for r in rows:
        h = str(r["horizon_min"])
        if h not in by_horizon:
            by_horizon[h] = {}
        by_horizon[h][str(r["outcome_label"] or "unknown")] = int(r["cnt"] or 0)

    return {"by_horizon": by_horizon}


def upsert_symbol_policy_overlay_sync(symbol: str, overlay: Dict[str, Any]) -> None:
    _init_db_sync()
    sym = str(symbol or "").upper()
    if not sym:
        return
    now_ms = int(overlay.get("updated_at") or time.time() * 1000)
    payload_json = json.dumps(dict(overlay or {}), separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                """
                INSERT INTO symbol_policy_overlays (
                    symbol, score_delta, size_multiplier, cooldown_multiplier, veto_tightness,
                    confidence_boost, decision_samples, good_entry_count, bad_entry_count,
                    good_block_count, bad_block_count, flat_count, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    score_delta = excluded.score_delta,
                    size_multiplier = excluded.size_multiplier,
                    cooldown_multiplier = excluded.cooldown_multiplier,
                    veto_tightness = excluded.veto_tightness,
                    confidence_boost = excluded.confidence_boost,
                    decision_samples = excluded.decision_samples,
                    good_entry_count = excluded.good_entry_count,
                    bad_entry_count = excluded.bad_entry_count,
                    good_block_count = excluded.good_block_count,
                    bad_block_count = excluded.bad_block_count,
                    flat_count = excluded.flat_count,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    sym,
                    float(overlay.get("score_delta", 0.0) or 0.0),
                    float(overlay.get("size_multiplier", 1.0) or 1.0),
                    float(overlay.get("cooldown_multiplier", 1.0) or 1.0),
                    float(overlay.get("veto_tightness", 0.0) or 0.0),
                    float(overlay.get("confidence_boost", 0.0) or 0.0),
                    int(overlay.get("decision_samples", 0) or 0),
                    int(overlay.get("good_entry_count", 0) or 0),
                    int(overlay.get("bad_entry_count", 0) or 0),
                    int(overlay.get("good_block_count", 0) or 0),
                    int(overlay.get("bad_block_count", 0) or 0),
                    int(overlay.get("flat_count", 0) or 0),
                    now_ms,
                    payload_json,
                ),
            )
            con.commit()
        finally:
            con.close()


def get_symbol_policy_overlay_sync(symbol: str) -> Dict[str, Any]:
    _init_db_sync()
    sym = str(symbol or "").upper()
    if not sym:
        return {}
    con = _connect_reader()
    try:
        row = con.execute(
            "SELECT * FROM symbol_policy_overlays WHERE symbol = ? LIMIT 1",
            (sym,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return {}
    item = dict(row)
    try:
        item["payload"] = json.loads(row["payload_json"] or "{}")
    except Exception:
        item["payload"] = {}
    return item


def list_symbol_policy_overlays_sync(limit: int = 100) -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 1000))
    con = _connect_reader()
    try:
        rows = con.execute(
            "SELECT * FROM symbol_policy_overlays ORDER BY ABS(score_delta) DESC, updated_at DESC LIMIT ?",
            (capped,),
        ).fetchall()
    finally:
        con.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(row["payload_json"] or "{}")
        except Exception:
            item["payload"] = {}
        out.append(item)
    return out


def append_brain_self_score_sync(
    *,
    ts: int,
    score: float,
    confidence: float = 0.0,
    discipline: float = 0.0,
    pnl_quality: float = 0.0,
    blocking_quality: float = 0.0,
    freshness: float = 0.0,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    _init_db_sync()
    payload_json = json.dumps(payload or {}, separators=(",", ":"), ensure_ascii=True)
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                """
                INSERT INTO brain_self_score_history (
                    ts, score, confidence, discipline, pnl_quality, blocking_quality, freshness, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(ts),
                    float(score or 0.0),
                    float(confidence or 0.0),
                    float(discipline or 0.0),
                    float(pnl_quality or 0.0),
                    float(blocking_quality or 0.0),
                    float(freshness or 0.0),
                    payload_json,
                ),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()


def get_latest_brain_self_score_sync() -> Optional[Dict[str, Any]]:
    items = list_brain_self_score_history_sync(limit=1)
    return items[0] if items else None


def list_brain_self_score_history_sync(limit: int = 200) -> List[Dict[str, Any]]:
    _init_db_sync()
    capped = max(1, min(int(limit), 2000))
    con = _connect_reader()
    try:
        rows = con.execute(
            "SELECT * FROM brain_self_score_history ORDER BY ts DESC, id DESC LIMIT ?",
            (capped,),
        ).fetchall()
    finally:
        con.close()
    out: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(row["payload_json"] or "{}")
        except Exception:
            item["payload"] = {}
        out.append(item)
    return out
