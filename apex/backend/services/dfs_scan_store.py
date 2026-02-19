import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_MAX_RESULTS_PER_VERSION = 4000
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_DB_FILE = os.path.join(_DATA_DIR, "dfs_scan.db")
_LEGACY_JSONL_FILE = os.path.join(_DATA_DIR, "dfs_scan_versions.jsonl")


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(_DB_FILE, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def _ensure_store() -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    with _conn() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_versions (
                id TEXT PRIMARY KEY,
                ts INTEGER NOT NULL,
                sport TEXT NOT NULL,
                scan_scope TEXT NOT NULL,
                trending_players INTEGER NOT NULL DEFAULT 0,
                total_scanned INTEGER NOT NULL DEFAULT 0,
                plays_found INTEGER NOT NULL DEFAULT 0,
                games_queried INTEGER NOT NULL DEFAULT 0,
                results_count INTEGER NOT NULL DEFAULT 0,
                slip_count INTEGER NOT NULL DEFAULT 0,
                results_json TEXT NOT NULL,
                slip_json TEXT NOT NULL,
                locked_keys_json TEXT NOT NULL
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_scan_versions_ts ON scan_versions(ts DESC)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_scan_versions_sport_scope_ts ON scan_versions(sport, scan_scope, ts DESC)")
        _migrate_legacy_jsonl_if_needed(con)
        con.commit()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _normalize_results(results: Any) -> List[Dict[str, Any]]:
    if not isinstance(results, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in results[:_MAX_RESULTS_PER_VERSION]:
        if isinstance(row, dict):
            out.append(row)
    return out


def _normalize_rows(rows: Any) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
    return out


def _normalize_locked_keys(keys: Any) -> List[str]:
    if not isinstance(keys, list):
        return []
    return [str(k) for k in keys if isinstance(k, str)]


def _entry_from_payload(payload: Dict[str, Any], version_id: Optional[str] = None, ts_ms: Optional[int] = None) -> Dict[str, Any]:
    now_ms = int(time.time() * 1000)
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    results = _normalize_results(payload.get("results"))
    slip = _normalize_rows(payload.get("slip"))
    locked_keys = _normalize_locked_keys(payload.get("locked_keys"))
    return {
        "id": str(version_id or payload.get("id") or uuid.uuid4().hex),
        "ts": _as_int(ts_ms if ts_ms is not None else payload.get("ts"), now_ms),
        "sport": str(payload.get("sport", "nba") or "nba"),
        "scan_scope": str(payload.get("scan_scope", "smart") or "smart"),
        "trending_players": _as_int(stats.get("trending_players"), 0),
        "total_scanned": _as_int(stats.get("total_scanned"), len(results)),
        "plays_found": _as_int(stats.get("plays_found"), 0),
        "games_queried": _as_int(stats.get("games_queried"), 0),
        "results_count": len(results),
        "slip_count": len(slip),
        "results": results,
        "slip": slip,
        "locked_keys": locked_keys,
    }


def _insert_entry(con: sqlite3.Connection, entry: Dict[str, Any]) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO scan_versions (
            id, ts, sport, scan_scope, trending_players, total_scanned, plays_found, games_queried,
            results_count, slip_count, results_json, slip_json, locked_keys_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry["id"],
            entry["ts"],
            entry["sport"],
            entry["scan_scope"],
            entry["trending_players"],
            entry["total_scanned"],
            entry["plays_found"],
            entry["games_queried"],
            entry["results_count"],
            entry["slip_count"],
            json.dumps(entry["results"], separators=(",", ":"), ensure_ascii=True),
            json.dumps(entry["slip"], separators=(",", ":"), ensure_ascii=True),
            json.dumps(entry["locked_keys"], separators=(",", ":"), ensure_ascii=True),
        ),
    )


def _load_legacy_entries() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not os.path.exists(_LEGACY_JSONL_FILE):
        return rows
    try:
        with open(_LEGACY_JSONL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except Exception:
                    continue
                if isinstance(parsed, dict):
                    rows.append(parsed)
                elif isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict):
                            rows.append(item)
    except Exception:
        return []
    return rows


def _migrate_legacy_jsonl_if_needed(con: sqlite3.Connection) -> None:
    existing = con.execute("SELECT COUNT(1) FROM scan_versions").fetchone()
    if existing and int(existing[0]) > 0:
        return
    legacy_entries = _load_legacy_entries()
    if not legacy_entries:
        return
    for payload in legacy_entries:
        entry = _entry_from_payload(
            payload=payload,
            version_id=str(payload.get("id") or uuid.uuid4().hex),
            ts_ms=_as_int(payload.get("ts"), int(time.time() * 1000)),
        )
        _insert_entry(con, entry)


def _summary_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "ts": _as_int(row["ts"], 0),
        "sport": str(row["sport"] or "nba"),
        "scan_scope": str(row["scan_scope"] or "smart"),
        "total_scanned": _as_int(row["total_scanned"], 0),
        "plays_found": _as_int(row["plays_found"], 0),
        "games_queried": _as_int(row["games_queried"], 0),
        "results_count": _as_int(row["results_count"], 0),
        "slip_count": _as_int(row["slip_count"], 0),
    }


def _load_json_field(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed
        except Exception:
            return fallback
    return fallback


def _save_sync(payload: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        _ensure_store()
        entry = _entry_from_payload(payload=payload, version_id=uuid.uuid4().hex, ts_ms=int(time.time() * 1000))
        with _conn() as con:
            _insert_entry(con, entry)
            con.commit()
            row = con.execute("SELECT * FROM scan_versions WHERE id = ?", (entry["id"],)).fetchone()
        if row is None:
            return {
                "id": entry["id"],
                "ts": entry["ts"],
                "sport": entry["sport"],
                "scan_scope": entry["scan_scope"],
                "total_scanned": entry["total_scanned"],
                "plays_found": entry["plays_found"],
                "games_queried": entry["games_queried"],
                "results_count": entry["results_count"],
                "slip_count": entry["slip_count"],
            }
        return _summary_from_row(row)


def _list_sync(limit: int = 40) -> List[Dict[str, Any]]:
    with _LOCK:
        _ensure_store()
        take = max(1, min(int(limit), 200))
        with _conn() as con:
            rows = con.execute(
                """
                SELECT id, ts, sport, scan_scope, total_scanned, plays_found, games_queried, results_count, slip_count
                FROM scan_versions
                ORDER BY ts DESC
                LIMIT ?
                """,
                (take,),
            ).fetchall()
            con.commit()
    return [_summary_from_row(r) for r in rows]


def _detail_sync(version_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        _ensure_store()
        with _conn() as con:
            row = con.execute("SELECT * FROM scan_versions WHERE id = ?", (version_id,)).fetchone()
            con.commit()
    if row is None:
        return None
    summary = _summary_from_row(row)
    results = _normalize_results(_load_json_field(row["results_json"], []))
    slip = _normalize_rows(_load_json_field(row["slip_json"], []))
    locked_keys = _normalize_locked_keys(_load_json_field(row["locked_keys_json"], []))
    detail = dict(summary)
    detail["stats"] = {
        "trending_players": _as_int(row["trending_players"], 0),
        "total_scanned": summary["total_scanned"],
        "plays_found": summary["plays_found"],
        "games_queried": summary["games_queried"],
    }
    detail["results"] = results
    detail["slip"] = slip
    detail["locked_keys"] = locked_keys
    return detail


def _delete_sync(version_id: str) -> bool:
    with _LOCK:
        _ensure_store()
        with _conn() as con:
            cur = con.execute("DELETE FROM scan_versions WHERE id = ?", (version_id,))
            con.commit()
            return cur.rowcount > 0


async def save_scan_version(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(_save_sync, payload)


async def list_scan_versions(limit: int = 40) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_list_sync, limit)


async def get_scan_version(version_id: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_detail_sync, version_id)


async def delete_scan_version(version_id: str) -> bool:
    return await asyncio.to_thread(_delete_sync, version_id)
