from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services.crypto import terminal_data  # noqa: E402


async def main() -> int:
    checks = [
        ("terminal_summary", lambda: terminal_data.get_terminal_summary(prefer_cached=True)),
        ("scanner", lambda: asyncio.to_thread(terminal_data.get_scanner, 10)),
        ("universe", lambda: asyncio.to_thread(terminal_data.get_universe_view, 30)),
        ("learning", lambda: asyncio.to_thread(terminal_data.get_learning_overview)),
        ("activity", lambda: asyncio.to_thread(terminal_data.get_activity_feed, limit=100)),
        ("symbol_snapshot", lambda: terminal_data.get_symbol_snapshot("BTC/USD")),
    ]
    failures = []
    for name, fn in checks:
        started = time.perf_counter()
        payload = await fn()
        elapsed = time.perf_counter() - started
        status = payload.get("status")
        print(f"{name} status={status} elapsed={elapsed:.3f}s", flush=True)
        if elapsed > 5.0:
            failures.append(f"{name} exceeded 5.0s at {elapsed:.3f}s")

    if failures:
        print("FAILURES:", flush=True)
        for failure in failures:
            print(f"- {failure}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
