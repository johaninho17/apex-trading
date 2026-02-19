from typing import Any, Dict
import pandas as pd


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    avg_up = up.ewm(alpha=1.0 / max(1, period), adjust=False).mean()
    avg_down = down.ewm(alpha=1.0 / max(1, period), adjust=False).mean()
    rs = avg_up / avg_down.replace(0.0, pd.NA)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def enrich_indicators(df: pd.DataFrame, fast_ma: int = 50, slow_ma: int = 200) -> pd.DataFrame:
    data = df.copy()
    if data.empty:
        return data
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data["high"] = pd.to_numeric(data["high"], errors="coerce")
    data["low"] = pd.to_numeric(data["low"], errors="coerce")
    data["volume"] = pd.to_numeric(data["volume"], errors="coerce")

    data["ema_fast"] = data["close"].ewm(span=max(2, fast_ma), adjust=False).mean()
    data["ema_slow"] = data["close"].ewm(span=max(3, slow_ma), adjust=False).mean()
    data["rsi14"] = _rsi(data["close"], 14)
    data["bb_mid"] = data["close"].rolling(20).mean()
    data["bb_std"] = data["close"].rolling(20).std(ddof=0)
    data["bb_upper"] = data["bb_mid"] + (2.0 * data["bb_std"])
    data["bb_lower"] = data["bb_mid"] - (2.0 * data["bb_std"])
    data["vol_ma20"] = data["volume"].rolling(20).mean()
    return data


def snapshot(df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty or len(df) < 3:
        return {}
    last = df.iloc[-1]
    prev = df.iloc[-2]
    return {
        "close": float(last.get("close", 0.0) or 0.0),
        "close_prev": float(prev.get("close", 0.0) or 0.0),
        "high": float(last.get("high", 0.0) or 0.0),
        "low": float(last.get("low", 0.0) or 0.0),
        "volume": float(last.get("volume", 0.0) or 0.0),
        "vol_ma20": float(last.get("vol_ma20", 0.0) or 0.0),
        "rsi14": float(last.get("rsi14", 50.0) or 50.0),
        "bb_upper": float(last.get("bb_upper", 0.0) or 0.0),
        "bb_lower": float(last.get("bb_lower", 0.0) or 0.0),
        "ema_fast": float(last.get("ema_fast", 0.0) or 0.0),
        "ema_slow": float(last.get("ema_slow", 0.0) or 0.0),
        "ema_fast_prev": float(prev.get("ema_fast", 0.0) or 0.0),
        "ema_slow_prev": float(prev.get("ema_slow", 0.0) or 0.0),
    }

