import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.crypto import store
from services.crypto.news_pipeline import _news_provider_status
from services.crypto.oracle import (
    _SYSTEM_PROMPT as ORACLE_SYSTEM_PROMPT,
    _USER_PROMPT as ORACLE_USER_PROMPT,
    get_current_oracle_state,
    get_narrative_status,
    get_oracle_status,
)
from services.crypto.report_generator import _HOURLY_SYSTEM


def _connect_reader() -> sqlite3.Connection:
    store._init_db_sync()
    con = sqlite3.connect(store._DB_FILE)
    con.row_factory = sqlite3.Row
    return con


def _row_mentions(rows: Iterable[str], term: str) -> int:
    needle = str(term or "").lower()
    return sum(1 for text in rows if needle in str(text or "").lower())


def _oracle_report_bias(oracle_limit: int, report_limit: int) -> Dict[str, Any]:
    con = _connect_reader()
    try:
        oracle_rows = [str(row["summary"] or "") for row in con.execute(
            "SELECT summary FROM oracle_history WHERE trim(coalesce(summary,'')) <> '' ORDER BY id DESC LIMIT ?",
            (max(1, int(oracle_limit)),),
        ).fetchall()]
        report_rows = [str(row["content"] or "") for row in con.execute(
            "SELECT content FROM bot_reports WHERE report_type = 'hourly' ORDER BY id DESC LIMIT ?",
            (max(1, int(report_limit)),),
        ).fetchall()]
        empty_oracle = int(con.execute(
            "SELECT COUNT(*) AS count FROM oracle_history WHERE trim(coalesce(summary,'')) = ''"
        ).fetchone()["count"] or 0)
        empty_macro = int(con.execute(
            "SELECT COUNT(*) AS count FROM macro_consensus_history WHERE trim(coalesce(summary,'')) = ''"
        ).fetchone()["count"] or 0)
    finally:
        con.close()

    return {
        "oracle_rows_scanned": len(oracle_rows),
        "hourly_report_rows_scanned": len(report_rows),
        "oracle_mentions": {
            "bitcoin": _row_mentions(oracle_rows, "bitcoin"),
            "ethereum": _row_mentions(oracle_rows, "ethereum"),
            "solana": _row_mentions(oracle_rows, "solana"),
            "xrp": _row_mentions(oracle_rows, "xrp"),
            "altcoin": _row_mentions(oracle_rows, "altcoin"),
        },
        "hourly_report_mentions": {
            "bitcoin": _row_mentions(report_rows, "bitcoin"),
            "ethereum": _row_mentions(report_rows, "ethereum"),
            "solana": _row_mentions(report_rows, "solana"),
            "xrp": _row_mentions(report_rows, "xrp"),
            "breadth": _row_mentions(report_rows, "breadth"),
            "btc_led": _row_mentions(report_rows, "btc-led") + _row_mentions(report_rows, "btc led"),
        },
        "empty_summary_counts": {
            "oracle_history": empty_oracle,
            "macro_consensus_history": empty_macro,
        },
        "oracle_samples": oracle_rows[:6],
        "hourly_report_samples": report_rows[:4],
    }


def _news_coverage(limit: int) -> Dict[str, Any]:
    rows = store.get_recent_news_events_sync(limit=max(1, int(limit)), active_only=False)
    source_counts: Counter[str] = Counter()
    symbol_counts: Counter[str] = Counter()
    unattributed = 0
    for row in rows:
        source_counts[str(row.get("source", "news") or "news").lower()] += 1
        symbol = str(row.get("symbol", "") or "").upper()
        base_asset = str(row.get("base_asset", "") or "").upper()
        payload = dict(row.get("payload") or {})
        parsed = dict(payload.get("parsed") or {}) if isinstance(payload.get("parsed"), dict) else {}
        asset_scope = [str(item or "").upper() for item in (parsed.get("asset_scope") or []) if str(item or "").strip()]
        if symbol:
            symbol_counts[symbol] += 1
        elif base_asset:
            symbol_counts[base_asset] += 1
        elif asset_scope:
            for item in asset_scope:
                symbol_counts[item] += 1
        else:
            unattributed += 1
    return {
        "sample_size": len(rows),
        "source_counts": dict(source_counts.most_common()),
        "top_symbols": dict(symbol_counts.most_common(10)),
        "unattributed_count": unattributed,
        "provider_status": _news_provider_status(),
    }


def _narrative_breadth(limit: int) -> Dict[str, Any]:
    con = _connect_reader()
    try:
        rows = con.execute(
            "SELECT payload_json FROM actions WHERE action_type = 'gemini_narrative' ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    finally:
        con.close()
    counter: Counter[str] = Counter()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            for key in payload.keys():
                counter[str(key).upper()] += 1
    return {
        "sample_size": len(rows),
        "unique_symbols": len(counter),
        "top_symbols": dict(counter.most_common(20)),
        "current_narrative_status": get_narrative_status(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit oracle/report prompt breadth and Telegram-news evidence quality.")
    parser.add_argument("--oracle-limit", type=int, default=40)
    parser.add_argument("--report-limit", type=int, default=20)
    parser.add_argument("--news-limit", type=int, default=80)
    parser.add_argument("--narrative-limit", type=int, default=12)
    args = parser.parse_args()

    payload = {
        "db_file": store._DB_FILE,
        "prompt_excerpt": {
            "oracle_system": ORACLE_SYSTEM_PROMPT,
            "oracle_user": ORACLE_USER_PROMPT,
            "hourly_report_system": _HOURLY_SYSTEM,
        },
        "oracle_current": {
            "status": get_oracle_status(),
            "current_state": get_current_oracle_state(),
        },
        "bias_audit": _oracle_report_bias(args.oracle_limit, args.report_limit),
        "news_coverage": _news_coverage(args.news_limit),
        "narrative_breadth": _narrative_breadth(args.narrative_limit),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
