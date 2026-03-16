import asyncio

import pandas as pd

from services.crypto import strategy


def _bars(rows: int) -> pd.DataFrame:
    base = pd.Timestamp("2026-03-01T00:00:00Z")
    data = []
    price = 100.0
    for idx in range(rows):
        ts = base + pd.Timedelta(minutes=idx)
        data.append({
            "timestamp": ts,
            "open": price,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price,
            "volume": 1000 + idx,
        })
        price += 0.1
    return pd.DataFrame(data)


def test_evaluate_symbol_returns_candidate_diagnostics_when_score_gate_blocks(monkeypatch):
    bars_15m = _bars(260)
    bars_1m = _bars(60)

    monkeypatch.setattr(strategy, "enrich_indicators", lambda df, **kwargs: df)
    snapshots = iter([
        {"close": 101.0, "timestamp": "2026-03-01T01:00:00Z", "rsi14": 50.0},
        {"close": 100.5, "timestamp": "2026-03-01T01:00:00Z", "rsi14": 50.0},
    ])
    monkeypatch.setattr(strategy, "snapshot", lambda _df: next(snapshots))
    monkeypatch.setattr(strategy, "_evaluate_swing", lambda *args, **kwargs: {
        "strategy": "swing_momentum",
        "side": "buy",
        "score": 65.0,
        "close": 101.0,
        "notional": 500.0,
        "reason": "Test candidate",
        "meta": {},
    })
    monkeypatch.setattr(strategy, "_evaluate_position", lambda *args, **kwargs: None)

    result = asyncio.run(strategy.evaluate_symbol(
        symbol="BTC/USD",
        bars_15m=bars_15m,
        bars_1m=bars_1m,
        cfg={"active_brain": "drl_event_fusion", "min_signal_score": 68.0, "short_term": {}, "long_term": {}},
        now_ms=1_000_000,
        return_diagnostics=True,
    ))

    assert result["signal"] is None
    assert result["suppressed_reason"] == "strategy_score_gate"
    assert result["best_candidate"]["strategy"] == "swing_momentum"
    assert result["min_signal_score"] == 68.0
    assert len(result["candidates"]) == 1


def test_dynamic_dca_score_is_not_hardcoded(monkeypatch):
    bars_15m = _bars(260)
    bars_1m = _bars(60)

    monkeypatch.setattr(strategy, "enrich_indicators", lambda df, **kwargs: df)
    snapshots = iter([
        {
            "close": 95.0,
            "timestamp": "2026-03-01T01:00:00Z",
            "rsi14": 35.0,
            "ema_slow": 100.0,
            "bb_lower": 96.0,
            "bb_upper": 104.0,
            "vol_ma20": 1000.0,
            "volume": 1300.0,
        },
        {
            "close": 95.0,
            "timestamp": "2026-03-01T01:00:00Z",
            "rsi14": 35.0,
            "vwap": 96.0,
        },
    ])
    monkeypatch.setattr(strategy, "snapshot", lambda _df: next(snapshots))
    monkeypatch.setattr(strategy, "_evaluate_swing", lambda *args, **kwargs: None)
    monkeypatch.setattr(strategy, "_evaluate_position", lambda *args, **kwargs: None)
    async def _no_ai(*args, **kwargs):
        return None
    monkeypatch.setattr(strategy, "_evaluate_ai_macro", _no_ai)

    result = asyncio.run(strategy.evaluate_symbol(
        symbol="BTC/USD",
        bars_15m=bars_15m,
        bars_1m=bars_1m,
        cfg={
            "active_brain": "drl_event_fusion",
            "min_signal_score": 80.0,
            "short_term": {"mean_reversion_enabled": False, "breakout_enabled": False},
            "long_term": {
                "dca_enabled": True,
                "dca_notional": 4.0,
                "dca_dip_pct": 3.0,
                "dca_dip_multiplier": 1.5,
                "dca_rsi_max": 42.0,
            },
        },
        now_ms=1_000_000,
        return_diagnostics=True,
    ))

    assert result["signal"] is None
    assert result["suppressed_reason"] == "strategy_score_gate"
    best = result["best_candidate"]
    assert best["strategy"] == "dynamic_dca"
    assert best["score"] != 60.0
    assert best["score"] > 60.0
    assert best["meta"]["dip_distance_pct"] == 5.0
