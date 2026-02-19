from fastapi.testclient import TestClient

import main
from main import app


client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert body["status"].get("apex") == "healthy"


def test_import_health_endpoint():
    resp = client.get("/api/v1/health/imports")
    assert resp.status_code == 200
    modules = resp.json().get("modules", {})
    assert "api_client" in modules
    assert "risk_manager" in modules
    # Apex should resolve Kalshi runtime modules for these legacy imports.
    assert "kalshi/" in modules["api_client"]
    assert "kalshi/" in modules["risk_manager"]


def test_scalper_tick_shape():
    resp = client.post("/api/v1/kalshi/scalper/tick")
    assert resp.status_code == 200
    body = resp.json()
    assert "dashboard" in body
    assert "signals" in body
    assert "contracts_loaded" in body


def test_write_auth_guard_optional(monkeypatch):
    monkeypatch.setattr(main, "_WRITE_API_KEY", "unit-test-key")
    payload = {"odds": -110, "probability": 0.55, "stake": 100}

    blocked = client.post("/api/v1/dfs/ev-calculator", json=payload)
    assert blocked.status_code == 401

    allowed = client.post(
        "/api/v1/dfs/ev-calculator",
        json=payload,
        headers={"x-api-key": "unit-test-key"},
    )
    assert allowed.status_code == 200
