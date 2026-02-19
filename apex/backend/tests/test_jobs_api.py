from pathlib import Path

from fastapi.testclient import TestClient

from main import app
from core import job_store
from routers import alpaca


client = TestClient(app)


def _use_temp_job_store(tmp_path: Path):
    data_dir = tmp_path / "data"
    jobs_file = data_dir / "jobs.json"
    job_store.DATA_DIR = str(data_dir)
    job_store.JOBS_FILE = str(jobs_file)
    if jobs_file.exists():
        jobs_file.unlink()


def test_jobs_list_and_get_contract(tmp_path):
    _use_temp_job_store(tmp_path)
    job = job_store.create_job(domain="dfs", kind="smart_scan", metadata={"sport": "nba"})
    job_store.mark_running(job["id"], message="running")
    job_store.mark_completed(job["id"], message="done")

    list_resp = client.get("/api/v1/jobs")
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert "jobs" in body
    assert body["count"] >= 1

    row = next((j for j in body["jobs"] if j["id"] == job["id"]), None)
    assert row is not None
    assert row["status"] == "completed"

    get_resp = client.get(f"/api/v1/jobs/{job['id']}")
    assert get_resp.status_code == 200
    one = get_resp.json()
    assert one["id"] == job["id"]
    assert one["domain"] == "dfs"
    assert one["kind"] == "smart_scan"


def test_alpaca_scanner_start_dedup_uses_active_job(tmp_path, monkeypatch):
    _use_temp_job_store(tmp_path)

    # Avoid spawning a real scanner thread in tests.
    class _ThreadStub:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            return None

    monkeypatch.setattr(alpaca.threading, "Thread", _ThreadStub)
    alpaca._scanner_running = False
    alpaca._scanner_stop_requested = False
    alpaca._scanner_job_id = None

    first = client.post("/api/v1/alpaca/scanner/start", json={"strategy": "both"})
    assert first.status_code == 200
    first_body = first.json()
    assert first_body.get("job_id")

    second = client.post("/api/v1/alpaca/scanner/start", json={"strategy": "both"})
    assert second.status_code == 200
    second_body = second.json()
    assert second_body.get("status") == "running"
    assert second_body.get("job_id") == first_body.get("job_id")
