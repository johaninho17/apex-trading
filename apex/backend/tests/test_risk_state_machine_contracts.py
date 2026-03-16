import os
import sys

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from services.crypto.bot import _dynamic_halt_minutes, _risk_state_policy


def test_risk_state_policy_exposes_restricted_golden_only_behavior():
    cfg = {
        "risk_state": {
            "states": {
                "restricted": {"signal_bonus": 6.0, "notional_mult": 0.5, "skip_buys": False, "golden_only": True}
            }
        }
    }
    policy = _risk_state_policy(cfg, "restricted")
    assert policy["golden_only"] is True
    assert policy["skip_buys"] is False
    assert policy["signal_bonus"] == 6.0


def test_dynamic_halt_minutes_scales_with_breach_depth():
    shallow = _dynamic_halt_minutes(4.2, 4.0, 10, 30)
    deep = _dynamic_halt_minutes(8.0, 4.0, 10, 30)
    assert 10 <= shallow <= 30
    assert deep == 30
    assert deep >= shallow
