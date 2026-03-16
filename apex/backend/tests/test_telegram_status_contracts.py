import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from integrations.telegram import bot_process


class _DummyRuntime:
    def status(self):
        return {
            "running": True,
            "risk_state": {"state": "restricted", "source": "oracle", "reason": "panic_crash cooling off"},
            "activity_governor": {
                "mode": "cold",
                "pressure_score": -22.0,
                "metrics": {"signals_6h": 2, "blocked_traces_6h": 3, "near_miss_6h": 4, "buys_24h": 1},
                "reason_codes": ["near_miss_cluster"],
            },
            "mode_effectiveness": {"effectiveness_score": 12.0, "confidence_score": 71.0, "verdict": "helping"},
            "decision_feedback": {"evaluated": 3, "recorded": 3, "failed": 0},
            "last_status": {
                "trading_mode": "live",
                "symbols": ["BTC/USD", "ETH/USD"],
                "market_regime": "bear",
                "macro_risk_multiplier": 0.72,
                "oracle_summary": "Macro still weak.",
                "effective_thresholds": {"min_signal_score": 66.0, "trade_spacing_min": 25, "rsi_oversold": 34.0, "breakout_volume_mult": 1.7},
                "decision_feedback": {"evaluated": 3, "recorded": 3, "failed": 0},
            },
        }


@patch("services.crypto.market_data.get_crypto_positions")
@patch("services.crypto.market_data.get_account_summary")
@patch("services.crypto.bot._RUNTIME", new=_DummyRuntime())
@patch("core.state.state", new=SimpleNamespace(is_domain_paused=lambda domain: False))
def test_handle_status_includes_risk_governor_and_macro(mock_account, mock_positions):
    mock_account.return_value = {"equity": 100000.0, "cash": 42000.0}
    mock_positions.return_value = [
        {"symbol": "BTC/USD", "qty": 0.1, "unrealized_pl": -120.0, "unrealized_plpc": -0.02},
        {"symbol": "ETH/USD", "qty": 1.2, "unrealized_pl": 25.0, "unrealized_plpc": 0.01},
    ]
    text = bot_process._handle_status()
    assert "Risk state: restricted via oracle" in text
    assert "Governor: cold | Pressure -22.0" in text
    assert "Macro: bear @ 0.72" in text
    assert "Thresholds: min_score 66.0" in text
