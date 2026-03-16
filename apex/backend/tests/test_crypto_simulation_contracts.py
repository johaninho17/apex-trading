import os
import sys
import asyncio


backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


from services.crypto import simulation


def test_resolve_simulation_symbols_for_custom_scope():
    symbols = simulation.resolve_simulation_symbols("custom", symbols=["btc/usd", "ETH/USD", "btc/usd"], limit=4)
    assert symbols == ["BTC/USD", "ETH/USD"]


def test_run_simulation_aggregates_results(monkeypatch):
    async def _fake_backtest(**kwargs):
        symbol = kwargs["symbol"]
        initial_equity = float(kwargs["initial_equity"])
        return {
            "symbol": symbol,
            "final_equity": initial_equity + 250.0,
            "equity_curve": [
                {"ts_ms": 1_700_000_000_000, "equity": initial_equity},
                {"ts_ms": 1_700_000_600_000, "equity": initial_equity + 250.0},
            ],
            "trades": [
                {"symbol": symbol, "pnl_pct": 2.5, "pnl_usd": 250.0, "hold_hrs": 1.5}
            ],
            "diagnostics": {"blocked_reasons": {"cooldown": 1}},
        }

    monkeypatch.setattr(simulation, "run_backtest", _fake_backtest)
    monkeypatch.setattr(simulation, "current_crypto_config", lambda resolve_symbols=False: {"account_mode": "paper"})

    payload = asyncio.run(
        simulation.run_simulation(
            scope="custom",
            symbols=["BTC/USD", "ETH/USD"],
            initial_equity=10_000.0,
            macro_score=1.0,
        )
    )

    assert payload["status"] == "fresh"
    assert payload["stats"]["symbol_count"] == 2
    assert payload["stats"]["total_trades"] == 2
    assert payload["stats"]["final_equity"] == 10_500.0
    assert payload["safeguard_diagnostics"]["blocked_reasons"]["cooldown"] == 2
