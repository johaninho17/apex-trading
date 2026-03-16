from fastapi.testclient import TestClient

from main import app
from routers import kalshi, polymarket


client = TestClient(app)


def test_polymarket_health_contract(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            return None

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, *_args, **_kwargs):
            return _Resp()

    monkeypatch.setattr(polymarket, "_get_client", lambda: _Client())

    resp = client.get("/api/v1/polymarket/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert "proxy" in body


def test_polymarket_book_contract(monkeypatch):
    def _fake_cached_get(_key, _url, _params=None):
        return {
            "bids": [{"price": "0.48", "size": "100"}],
            "asks": [{"price": "0.52", "size": "120"}],
        }

    monkeypatch.setattr(polymarket, "_cached_get", _fake_cached_get)

    resp = client.get("/api/v1/polymarket/book/test-token")
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_id"] == "test-token"
    assert "best_bid" in body
    assert "best_ask" in body
    assert "implied_probability" in body


def test_kalshi_orderbook_contract(monkeypatch):
    class _API:
        def get_orderbook(self, _ticker):
            return {"bids": [{"price": 49}], "asks": [{"price": 51}]}

    monkeypatch.setattr(kalshi, "_get_api", lambda: _API())

    resp = client.get("/api/v1/kalshi/orderbook/TEST-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["bids"], list)
    assert isinstance(body["asks"], list)


def test_kalshi_place_order_failure_semantics(monkeypatch):
    class _API:
        def place_order(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(kalshi, "_get_api", lambda: _API())
    monkeypatch.setattr(kalshi, "_trading_enabled", lambda: True)

    resp = client.post(
        "/api/v1/kalshi/order",
        json={
            "ticker": "TEST-1",
            "side": "yes",
            "quantity": 1,
            "order_type": "limit",
            "price": 50,
        },
    )
    assert resp.status_code == 400


def test_kalshi_place_order_success_contract(monkeypatch):
    class _API:
        def place_order(self, *_args, **_kwargs):
            return {"order_id": "abc123"}

    monkeypatch.setattr(kalshi, "_get_api", lambda: _API())
    monkeypatch.setattr(kalshi, "_trading_enabled", lambda: True)

    resp = client.post(
        "/api/v1/kalshi/order",
        json={
            "ticker": "TEST-1",
            "side": "yes",
            "quantity": 1,
            "order_type": "limit",
            "price": 50,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["order"]["order_id"] == "abc123"
