import json
import os
from typing import Tuple


_ALPACA_ROOT = os.path.dirname(os.path.abspath(__file__))
_SETTINGS_PATH = os.path.join(_ALPACA_ROOT, ".trading_settings.json")


def _normalize_mode(mode: str) -> str:
    return "live" if str(mode or "").lower() == "live" else "paper"


def get_trading_mode(default: str = "paper") -> str:
    fallback = _normalize_mode(default)
    try:
        with open(_SETTINGS_PATH, "r") as f:
            payload = json.load(f)
        return _normalize_mode(payload.get("mode", fallback))
    except Exception:
        return fallback


def save_trading_mode(mode: str) -> str:
    normalized = _normalize_mode(mode)
    os.makedirs(os.path.dirname(_SETTINGS_PATH), exist_ok=True)
    with open(_SETTINGS_PATH, "w") as f:
        json.dump({"mode": normalized}, f)
    return normalized


def get_alpaca_credentials(mode: str | None = None) -> Tuple[str, str, bool]:
    selected = _normalize_mode(mode or get_trading_mode())
    if selected == "live":
        api_key = os.getenv("LIVE_API_KEY", "") or os.getenv("ALPACA_LIVE_API_KEY", "")
        secret_key = os.getenv("LIVE_SECRET_KEY", "") or os.getenv("ALPACA_LIVE_SECRET_KEY", "")
        paper = False
    else:
        api_key = os.getenv("PAPER_API_KEY", "") or os.getenv("ALPACA_PAPER_API_KEY", "")
        secret_key = os.getenv("PAPER_SECRET_KEY", "") or os.getenv("ALPACA_PAPER_SECRET_KEY", "")
        paper = True

    # Backward-compatible fallback for legacy env shape.
    if not api_key:
        api_key = os.getenv("ALPACA_API_KEY", "")
    if not secret_key:
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")

    return api_key, secret_key, paper
