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


def _connect() -> sqlite3.Connection:
    os.makedirs(_DATA_DIR, exist_ok=True)
    con = sqlite3.connect(_DB_FILE)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con


def _init_db_sync() -> None:
    with _LOCK:
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
                    day_start_equity REAL,
                    day_start_ts INTEGER,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            now_ms = int(time.time() * 1000)
            con.execute(
                """
                INSERT INTO runtime_state (
                    id, running, started_at, last_heartbeat, iterations, last_error, halted, halted_reason,
                    day_start_equity, day_start_ts, updated_at
                )
                VALUES (1, 0, NULL, NULL, 0, NULL, 0, NULL, NULL, NULL, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (now_ms,),
            )
            con.commit()
        finally:
            con.close()


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
    with _LOCK:
        con = _connect()
        try:
            row = con.execute("SELECT * FROM runtime_state WHERE id = 1").fetchone()
        finally:
            con.close()
    if not row:
        return {
            "running": False,
            "started_at": None,
            "last_heartbeat": None,
            "iterations": 0,
            "last_error": None,
            "halted": False,
            "halted_reason": None,
            "day_start_equity": None,
            "day_start_ts": None,
            "updated_at": None,
        }
    return {
        "running": bool(row["running"]),
        "started_at": int(row["started_at"]) if row["started_at"] is not None else None,
        "last_heartbeat": int(row["last_heartbeat"]) if row["last_heartbeat"] is not None else None,
        "iterations": int(row["iterations"] or 0),
        "last_error": row["last_error"],
        "halted": bool(row["halted"]),
        "halted_reason": row["halted_reason"],
        "day_start_equity": float(row["day_start_equity"]) if row["day_start_equity"] is not None else None,
        "day_start_ts": int(row["day_start_ts"]) if row["day_start_ts"] is not None else None,
        "updated_at": int(row["updated_at"]) if row["updated_at"] is not None else None,
    }


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
        "day_start_equity",
        "day_start_ts",
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


async def clear_actions() -> int:
    return await asyncio.to_thread(_clear_actions_sync)


async def get_runtime_state() -> Dict[str, Any]:
    return await asyncio.to_thread(_get_runtime_state_sync)


async def update_runtime_state(**updates: Any) -> Dict[str, Any]:
    return await asyncio.to_thread(_update_runtime_state_sync, **updates)
