from typing import Any, Dict, Optional

import pandas as pd

from .indicators import enrich_indicators, snapshot


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _candidate(
    strategy: str,
    side: str,
    score: float,
    close: float,
    notional: float,
    reason: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "strategy": strategy,
        "side": side,
        "score": round(float(score), 2),
        "close": float(close),
        "notional": round(float(notional), 4),
        "reason": reason,
        "meta": meta or {},
    }


def evaluate_symbol(
    symbol: str,
    bars_df: pd.DataFrame,
    cfg: Dict[str, Any],
    now_ms: int,
    last_dca_ts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if bars_df is None or bars_df.empty or len(bars_df) < 40:
        return None

    short_cfg = (cfg.get("short_term") or {})
    long_cfg = (cfg.get("long_term") or {})

    fast_period = _to_int(long_cfg.get("ma_fast"), 50)
    slow_period = _to_int(long_cfg.get("ma_slow"), 200)
    data = enrich_indicators(bars_df, fast_ma=fast_period, slow_ma=slow_period)
    s = snapshot(data)
    if not s:
        return None

    close = _to_float(s.get("close"), 0.0)
    if close <= 0:
        return None

    candidates = []

    # Short-term mean reversion.
    if bool(short_cfg.get("mean_reversion_enabled", True)):
        oversold = _to_float(short_cfg.get("rsi_oversold"), 28.0)
        overbought = _to_float(short_cfg.get("rsi_overbought"), 72.0)
        base_notional = _to_float(short_cfg.get("base_notional"), 6.0)
        dip_multiplier = _to_float(short_cfg.get("dip_notional_multiplier"), 1.3)
        rsi = _to_float(s.get("rsi14"), 50.0)
        bb_lower = _to_float(s.get("bb_lower"), 0.0)
        bb_upper = _to_float(s.get("bb_upper"), 0.0)

        if bb_lower > 0 and rsi <= oversold and close <= bb_lower:
            score = min(100.0, 55.0 + (oversold - rsi) * 1.6)
            notional = base_notional * (dip_multiplier if close < bb_lower * 0.993 else 1.0)
            candidates.append(
                _candidate(
                    "mean_reversion",
                    "buy",
                    score,
                    close,
                    notional,
                    f"RSI {rsi:.1f} <= {oversold:.1f} and price below lower Bollinger band.",
                    {"rsi14": round(rsi, 2), "bb_lower": round(bb_lower, 4)},
                )
            )
        elif bb_upper > 0 and rsi >= overbought and close >= bb_upper:
            score = min(100.0, 55.0 + (rsi - overbought) * 1.4)
            candidates.append(
                _candidate(
                    "mean_reversion",
                    "sell",
                    score,
                    close,
                    base_notional,
                    f"RSI {rsi:.1f} >= {overbought:.1f} and price above upper Bollinger band.",
                    {"rsi14": round(rsi, 2), "bb_upper": round(bb_upper, 4)},
                )
            )

    # Short-term breakout momentum.
    if bool(short_cfg.get("breakout_enabled", True)):
        lookback = max(10, _to_int(short_cfg.get("breakout_lookback_bars"), 20))
        min_volume_mult = _to_float(short_cfg.get("breakout_volume_mult"), 1.9)
        breakout_buffer_pct = _to_float(short_cfg.get("breakout_buffer_pct"), 0.15)
        base_notional = _to_float(short_cfg.get("breakout_notional"), _to_float(short_cfg.get("base_notional"), 6.0))

        if len(data) > lookback + 2:
            recent = data.iloc[-lookback - 1: -1]
            resistance = _to_float(recent["high"].max(), 0.0)
            support = _to_float(recent["low"].min(), 0.0)
            volume = _to_float(s.get("volume"), 0.0)
            vol_ma = _to_float(s.get("vol_ma20"), 0.0)
            buffer = breakout_buffer_pct / 100.0

            if resistance > 0 and vol_ma > 0 and close >= resistance * (1.0 + buffer) and volume >= vol_ma * min_volume_mult:
                score = min(100.0, 58.0 + ((volume / vol_ma) - min_volume_mult) * 16.0)
                candidates.append(
                    _candidate(
                        "breakout_momentum",
                        "buy",
                        score,
                        close,
                        base_notional,
                        "Price broke resistance with elevated volume.",
                        {
                            "resistance": round(resistance, 4),
                            "volume_ratio": round(volume / max(vol_ma, 1e-9), 3),
                        },
                    )
                )
            elif support > 0 and vol_ma > 0 and close <= support * (1.0 - buffer) and volume >= vol_ma * min_volume_mult:
                score = min(100.0, 58.0 + ((volume / vol_ma) - min_volume_mult) * 16.0)
                candidates.append(
                    _candidate(
                        "breakout_momentum",
                        "sell",
                        score,
                        close,
                        base_notional,
                        "Price broke support with elevated volume.",
                        {
                            "support": round(support, 4),
                            "volume_ratio": round(volume / max(vol_ma, 1e-9), 3),
                        },
                    )
                )

    # Long-term moving average crossover.
    if bool(long_cfg.get("ma_crossover_enabled", True)):
        fast = _to_float(s.get("ema_fast"), 0.0)
        slow = _to_float(s.get("ema_slow"), 0.0)
        fast_prev = _to_float(s.get("ema_fast_prev"), 0.0)
        slow_prev = _to_float(s.get("ema_slow_prev"), 0.0)
        base_notional = _to_float(long_cfg.get("crossover_notional"), 8.0)
        if fast_prev <= slow_prev and fast > slow:
            candidates.append(
                _candidate(
                    "ma_crossover",
                    "buy",
                    66.0,
                    close,
                    base_notional,
                    "Fast EMA crossed above slow EMA (golden cross).",
                    {"ema_fast": round(fast, 4), "ema_slow": round(slow, 4)},
                )
            )
        elif fast_prev >= slow_prev and fast < slow:
            candidates.append(
                _candidate(
                    "ma_crossover",
                    "sell",
                    66.0,
                    close,
                    base_notional,
                    "Fast EMA crossed below slow EMA (trend reversal).",
                    {"ema_fast": round(fast, 4), "ema_slow": round(slow, 4)},
                )
            )

    # Long-term dynamic DCA.
    if bool(long_cfg.get("dca_enabled", True)):
        interval_min = max(5, _to_int(long_cfg.get("dca_interval_min"), 180))
        base_notional = _to_float(long_cfg.get("dca_notional"), 4.0)
        dip_pct = _to_float(long_cfg.get("dca_dip_pct"), 1.5)
        dip_mult = _to_float(long_cfg.get("dca_dip_multiplier"), 1.5)
        ema_slow = _to_float(s.get("ema_slow"), close)
        should_fire = last_dca_ts is None or (now_ms - int(last_dca_ts)) >= interval_min * 60_000
        if should_fire:
            notional = base_notional
            reason = "Periodic DCA interval reached."
            if ema_slow > 0 and close <= ema_slow * (1.0 - (dip_pct / 100.0)):
                notional = base_notional * dip_mult
                reason = f"DCA dip boost triggered ({dip_pct:.2f}% under trend baseline)."
            candidates.append(
                _candidate(
                    "dynamic_dca",
                    "buy",
                    52.0,
                    close,
                    notional,
                    reason,
                    {"interval_min": interval_min, "ema_slow": round(ema_slow, 4)},
                )
            )

    if not candidates:
        return None

    # Prefer higher score; tie-breaker favors buy over sell in paper accumulation mode.
    candidates.sort(key=lambda c: (float(c.get("score", 0.0)), 1 if c.get("side") == "buy" else 0), reverse=True)
    best = candidates[0]
    best["symbol"] = symbol
    best["timestamp_ms"] = now_ms
    return best

