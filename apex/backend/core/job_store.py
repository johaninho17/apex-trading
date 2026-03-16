import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BACKEND_ROOT, "data")
# Kept for compatibility with tests that monkeypatch JOBS_FILE to redirect storage.
JOBS_FILE = os.path.join(DATA_DIR, "jobs.json")

_LOCK = threading.RLock()


def _db_path() -> str:
    base = DATA_DIR
    if JOBS_FILE:
        maybe_dir = os.path.dirname(JOBS_FILE)
        if maybe_dir:
            base = maybe_dir
    return os.path.join(base, "jobs.db")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _ensure_store() -> None:
    os.makedirs(os.path.dirname(_db_path()), exist_ok=True)
    with _conn() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                domain TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                error TEXT,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs(updated_at DESC)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_domain_status ON jobs(domain, status)")
        con.commit()


def _row_to_job(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
    rec = dict(row)
    raw_meta = rec.get("metadata")
    if isinstance(raw_meta, str):
        try:
            rec["metadata"] = json.loads(raw_meta)
        except Exception:
            rec["metadata"] = {}
    elif raw_meta is None:
        rec["metadata"] = {}
    return rec


def create_job(domain: str, kind: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    with _LOCK:
        _ensure_store()
        now = _now_iso()
        job = {
            "id": str(uuid.uuid4()),
            "domain": domain,
            "kind": kind,
            "status": "queued",
            "progress": 0,
            "total": 0,
            "message": "",
            "error": None,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
        }
        with _conn() as con:
            con.execute(
                """
                INSERT INTO jobs (
                    id, domain, kind, status, progress, total, message, error, metadata,
                    created_at, updated_at, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"],
                    job["domain"],
                    job["kind"],
                    job["status"],
                    job["progress"],
                    job["total"],
                    job["message"],
                    job["error"],
                    json.dumps(job["metadata"], ensure_ascii=True),
                    job["created_at"],
                    job["updated_at"],
                    job["started_at"],
                    job["completed_at"],
                ),
            )
            con.commit()
        return dict(job)


def update_job(job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    with _LOCK:
        _ensure_store()
        if not fields:
            return get_job(job_id)
        fields = dict(fields)
        if "metadata" in fields and not isinstance(fields["metadata"], str):
            fields["metadata"] = json.dumps(fields["metadata"], ensure_ascii=True)
        fields["updated_at"] = _now_iso()
        cols = list(fields.keys())
        sql = f"UPDATE jobs SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?"
        params = [fields[c] for c in cols] + [job_id]
        with _conn() as con:
            cur = con.execute(sql, params)
            con.commit()
            if cur.rowcount <= 0:
                return None
        return get_job(job_id)


def mark_running(job_id: str, message: Optional[str] = None) -> Optional[Dict[str, Any]]:
    fields: Dict[str, Any] = {"status": "running", "started_at": _now_iso()}
    if message is not None:
        fields["message"] = message
    return update_job(job_id, **fields)


def mark_completed(job_id: str, message: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    fields: Dict[str, Any] = {"status": "completed", "completed_at": _now_iso()}
    if message is not None:
        fields["message"] = message
    if metadata is not None:
        fields["metadata"] = metadata
    return update_job(job_id, **fields)


def mark_failed(job_id: str, error: str, recovered: bool = False) -> Optional[Dict[str, Any]]:
    return update_job(
        job_id,
        status="failed_recovered" if recovered else "failed",
        error=error,
        completed_at=_now_iso(),
    )


def mark_cancelled(job_id: str, message: str = "Cancelled by user") -> Optional[Dict[str, Any]]:
    return update_job(
        job_id,
        status="cancelled",
        message=message,
        completed_at=_now_iso(),
    )


def touch(job_id: str, message: Optional[str] = None, progress: Optional[int] = None, total: Optional[int] = None) -> Optional[Dict[str, Any]]:
    fields: Dict[str, Any] = {}
    if message is not None:
        fields["message"] = message
    if progress is not None:
        fields["progress"] = int(progress)
    if total is not None:
        fields["total"] = int(total)
    if not fields:
        fields["status"] = "running"
    return update_job(job_id, **fields)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        _ensure_store()
        with _conn() as con:
            row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None


def list_jobs(domain: Optional[str] = None, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    with _LOCK:
        _ensure_store()
        lim = max(1, min(int(limit), 500))
        where = []
        params: List[Any] = []
        if domain:
            where.append("domain = ?")
            params.append(domain)
        if status:
            where.append("status = ?")
            params.append(status)
        sql = "SELECT * FROM jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(lim)
        with _conn() as con:
            rows = con.execute(sql, params).fetchall()
        return [_row_to_job(r) for r in rows]


def get_active_job(domain: str, kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
    active_states = {"queued", "running"}
    for job in list_jobs(domain=domain, limit=200):
        if job.get("status") not in active_states:
            continue
        if kind and job.get("kind") != kind:
            continue
        return job
    return None


def fail_stale_jobs(max_age_seconds: int = 3600) -> int:
    now = datetime.now(timezone.utc)
    updated = 0
    with _LOCK:
        _ensure_store()
        rows = list_jobs(limit=1000)
        for job in rows:
            if job.get("status") not in {"queued", "running"}:
                continue
            ts = job.get("updated_at") or job.get("created_at")
            try:
                dt = datetime.fromisoformat(ts)
            except Exception:
                dt = now
            age = (now - dt).total_seconds()
            if age <= max_age_seconds:
                continue
            mark_failed(job["id"], f"Recovered stale job after {int(age)}s without heartbeat", recovered=True)
            updated += 1
    return updated


_ensure_store()
