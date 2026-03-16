import pandas as pd
from typing import Dict, Any, Optional
import math
import logging

logger = logging.getLogger("apex.stocks.strategy")

def evaluate_symbol(
    symbol: str,
    bars: pd.DataFrame,
    cfg: Dict[str, Any],
    market_regime: str = "normal",
    macro_mult: float = 1.0,
) -> Dict[str, Any]:
    """
    Swing Strategy for Stocks: Evaluates 15m/1H bars for entries/exits.
    Avoids PDT by holding positions longer.
    """
    if bars.empty or len(bars) < 20:
        return {"signal": "pass", "reason": "Not enough data", "conviction": 0.0, "target_notional": 0.0}

    # Extract configuration
    atr_period = int(cfg.get("atr_period", 14) or 14)
    trend_ema_fast = int(cfg.get("ema_fast", 20) or 20)
    trend_ema_slow = int(cfg.get("ema_slow", 50) or 50)
    
    # Calculate Indicators
    close = bars["close"]
    
    # EMAs
    ema_fast = close.ewm(span=trend_ema_fast, adjust=False).mean()
    ema_slow = close.ewm(span=trend_ema_slow, adjust=False).mean()
    
    # ATR (Simple proxy using rolling high-low)
    tr = bars["high"] - bars["low"]
    atr = tr.rolling(window=atr_period).mean()
    
    # Current values
    current_close = close.iloc[-1]
    current_ema_fast = ema_fast.iloc[-1]
    current_ema_slow = ema_slow.iloc[-1]
    current_atr = atr.iloc[-1]
    
    # Signal Logic (Trend Following Swing)
    # Buy long if fast EMA crosses above slow EMA and price is above fast EMA
    is_bullish = current_ema_fast > current_ema_slow and current_close > current_ema_fast
    
    # Sell/Short if fast EMA crosses below slow EMA and price is below fast EMA 
    # (assuming we want to short, otherwise just cash out)
    is_bearish = current_ema_fast < current_ema_slow and current_close < current_ema_fast

    signal = "pass"
    conviction = 0.0
    reason = "Neutral trend"

    if is_bullish:
        signal = "buy"
        # Stronger conviction if price is accelerating away from EMA
        diff = (current_close - current_ema_fast) / current_close
        conviction = min(1.0, 0.5 + (diff * 10.0)) * macro_mult
        reason = f"Bullish [{market_regime}]: Close > EMA{trend_ema_fast} > EMA{trend_ema_slow}"
        
    elif is_bearish:
        signal = "sell"
        diff = (current_ema_fast - current_close) / current_close
        conviction = min(1.0, 0.5 + (diff * 10.0)) * macro_mult
        reason = f"Bearish [{market_regime}]: Close < EMA{trend_ema_fast} < EMA{trend_ema_slow}"

    return {
        "signal": signal,
        "conviction": conviction,
        "reason": reason,
        "atr": current_atr,
        "current_price": current_close
    }
