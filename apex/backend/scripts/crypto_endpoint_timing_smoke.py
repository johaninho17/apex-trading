from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from main import app  # noqa: E402


ENDPOINTS = [
    ("/api/v1/crypto/terminal/summary?prefer_cached=1", 5.0),
    ("/api/v1/crypto/scanner?limit=10", 5.0),
    ("/api/v1/crypto/universe?limit=30", 5.0),
    ("/api/v1/crypto/learning/overview", 5.0),
    ("/api/v1/crypto/activity/feed?limit=100", 5.0),
    ("/api/v1/crypto/symbol/BTC/USD/snapshot", 5.0),
]


def main() -> int:
    failures = []
    with TestClient(app) as client:
        for path, threshold_sec in ENDPOINTS:
            started = time.perf_counter()
            response = client.get(path)
            elapsed = time.perf_counter() - started
            status_code = response.status_code
            print(f"{path} status={status_code} elapsed={elapsed:.3f}s")
            if status_code >= 400:
                failures.append(f"{path} returned {status_code}")
                continue
            if elapsed > threshold_sec:
                failures.append(
                    f"{path} exceeded {threshold_sec:.1f}s threshold at {elapsed:.3f}s"
                )

    if failures:
        print("FAILURES:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
