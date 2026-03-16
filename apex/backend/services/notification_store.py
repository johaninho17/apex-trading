import asyncio
import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_SEVEN_DAYS_MS = 7 * 24 * 60 * 60 * 1000
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_DATA_FILE = os.path.join(_DATA_DIR, "notifications.jsonl")


def _ensure_store() -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    if not os.path.exists(_DATA_FILE):
        with open(_DATA_FILE, "w", encoding="utf-8"):
            pass


def _load_entries() -> List[Dict[str, Any]]:
    _ensure_store()
    entries: List[Dict[str, Any]] = []
    with open(_DATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    entries.append(item)
            except Exception:
                continue
    return entries


def _write_entries(entries: List[Dict[str, Any]]) -> None:
    _ensure_store()
    with open(_DATA_FILE, "w", encoding="utf-8") as f:
        for item in entries:
            f.write(json.dumps(item, separators=(",", ":"), ensure_ascii=True))
            f.write("\n")


def _prune(entries: List[Dict[str, Any]], now_ms: int) -> List[Dict[str, Any]]:
    cutoff = now_ms - _SEVEN_DAYS_MS
    kept = [e for e in entries if int(e.get("ts", 0)) >= cutoff]
    kept.sort(key=lambda x: int(x.get("ts", 0)), reverse=True)
    return kept


def _build_title(channel: str, event_type: str, payload: Dict[str, Any]) -> str:
    title = payload.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()[:120]
    return f"{channel.upper()} {event_type}"


def _build_severity(payload: Dict[str, Any], event_type: str) -> str:
    t = payload.get("type")
    if isinstance(t, str) and t in {"success", "error", "warning", "info", "signal"}:
        return t
    if event_type in {"error", "warning"}:
        return event_type
    return "info"


def _group_for_channel(channel: str) -> str:
    if channel == "crypto":
        return "crypto"
    if channel == "alpaca":
        return "stocks"
    if channel == "dfs":
        return "dfs"
    if channel in {"kalshi", "polymarket"}:
        return "events"
    return "system"


def _record_sync(channel: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    now_ms = int(time.time() * 1000)
    item_id = uuid.uuid4().hex
    title = _build_title(channel, event_type, payload)
    severity = _build_severity(payload, event_type)
    group = _group_for_channel(channel)

    entry: Dict[str, Any] = {
        "id": item_id,
        "ts": now_ms,
        "title": title,
        "channel": channel,
        "group": group,
        "event_type": event_type,
        "severity": severity,
        "payload": payload,
    }
    if isinstance(payload.get("message"), str):
        entry["message"] = payload.get("message")

    with _LOCK:
        entries = _load_entries()
        entries.append(entry)
        pruned = _prune(entries, now_ms)
        _write_entries(pruned)
    return {
        "id": item_id,
        "ts": now_ms,
        "title": title,
        "channel": channel,
        "group": group,
    }


def _group_matches(entry_group: str, query_group: str) -> bool:
    q = (query_group or "all").lower()
    if q == "all":
        return True
    return entry_group == q


def _list_sync(limit: int = 200, days: int = 7, group: str = "all") -> List[Dict[str, Any]]:
    now_ms = int(time.time() * 1000)
    days_ms = max(1, min(days, 7)) * 24 * 60 * 60 * 1000
    cutoff = now_ms - days_ms

    with _LOCK:
        entries = _prune(_load_entries(), now_ms)
        _write_entries(entries)

    rows = []
    for entry in entries:
        ts = int(entry.get("ts", 0))
        if ts < cutoff:
            continue
        entry_group = str(entry.get("group") or _group_for_channel(str(entry.get("channel", ""))))
        if not _group_matches(entry_group, group):
            continue
        rows.append(
            {
                "id": entry.get("id", ""),
                "ts": ts,
                "title": entry.get("title", "Notification"),
                "channel": entry.get("channel", "system"),
                "group": entry_group,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _detail_sync(notification_id: str) -> Optional[Dict[str, Any]]:
    now_ms = int(time.time() * 1000)
    with _LOCK:
        entries = _prune(_load_entries(), now_ms)
        _write_entries(entries)
    for entry in entries:
        if entry.get("id") == notification_id:
            if "group" not in entry:
                entry["group"] = _group_for_channel(str(entry.get("channel", "")))
            return entry
    return None


def _delete_by_id_sync(notification_id: str) -> bool:
    now_ms = int(time.time() * 1000)
    with _LOCK:
        entries = _prune(_load_entries(), now_ms)
        before = len(entries)
        kept = [e for e in entries if e.get("id") != notification_id]
        _write_entries(kept)
        return len(kept) < before


def _delete_by_group_sync(group: str = "all") -> int:
    now_ms = int(time.time() * 1000)
    with _LOCK:
        entries = _prune(_load_entries(), now_ms)
        before = len(entries)
        if (group or "all").lower() == "all":
            _write_entries([])
            return before
        kept = []
        for e in entries:
            entry_group = str(e.get("group") or _group_for_channel(str(e.get("channel", ""))))
            if not _group_matches(entry_group, group):
                kept.append(e)
        _write_entries(kept)
        return before - len(kept)


async def record_notification(channel: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(_record_sync, channel, event_type, payload)


async def list_notification_summaries(limit: int = 200, days: int = 7, group: str = "all") -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_list_sync, limit, days, group)


async def get_notification_detail(notification_id: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_detail_sync, notification_id)


async def delete_notification_by_id(notification_id: str) -> bool:
    return await asyncio.to_thread(_delete_by_id_sync, notification_id)


async def delete_notifications_by_group(group: str = "all") -> int:
    return await asyncio.to_thread(_delete_by_group_sync, group)
