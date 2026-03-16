from typing import Any, Dict, Optional

import pandas as pd

from .indicators import enrich_indicators, snapshot

try:
    from .ml.inference import predict_action
    HAS_ML = True
except ImportError:
    HAS_ML = False


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

def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


def _apply_drl_event_fusion(
    symbol: str,
    cfg: Dict[str, Any],
    bars_15m: pd.DataFrame,
    s_15m: Dict[str, Any],
    s_1m: Dict[str, Any],
    macro_risk_mult: float,
    candidates: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """Rescore strategy candidates using DRL direction plus cached event and macro state."""
    drl_action = -1
    if HAS_ML and bool(cfg.get("use_ml_model", True)):
        try:
            from .ml.inference import predict_action
            drl_action = predict_action(symbol, bars_15m, gemini_macro=macro_risk_mult)
        except Exception:
            drl_action = -1

    try:
        from .news_pipeline import get_symbol_event_state
        event_state = get_symbol_event_state(symbol)
    except Exception:
        event_state = {}

    event_net = float(event_state.get("net_event_score", 0.0) or 0.0)
    event_bias = str(event_state.get("event_bias", "neutral") or "neutral")
    hard_veto = bool(event_state.get("hard_veto", False))
    golden_trade = bool(event_state.get("golden_trade_flag", False))
    top_event_type = str(event_state.get("top_event_type", "") or "")
    top_event_score = float(event_state.get("top_event_score", 0.0) or 0.0)
    news_context_state = str(event_state.get("news_context_state", "unavailable") or "unavailable")
    news_context_complete = bool(event_state.get("news_context_complete", False))
    rsi_1m = _to_float(s_1m.get("rsi14"), 50.0)
    base_notional = _to_float(cfg.get("short_term", {}).get("base_notional"), 15.0)

    rescored: list[Dict[str, Any]] = []
    for cand in candidates:
        side = str(cand.get("side", "info")).lower()
        if side not in ("buy", "sell"):
            rescored.append(cand)
            continue
        if side == "buy" and hard_veto:
            continue

        base_score = float(cand.get("score", 0.0) or 0.0)
        drl_bonus = 0.0
        if drl_action == 1 and side == "buy":
            drl_bonus = 12.0
        elif drl_action == 2 and side == "sell":
            drl_bonus = 12.0
        elif drl_action in (1, 2):
            drl_bonus = -8.0

        if side == "buy":
            event_bonus = _clamp(event_net, -25.0, 25.0)
            macro_bonus = _clamp((macro_risk_mult - 1.0) * 20.0, -15.0, 15.0)
            micro_bonus = _clamp((55.0 - rsi_1m) * 0.6, -6.0, 6.0)
        else:
            event_bonus = _clamp(-event_net * 0.75, -18.0, 18.0)
            macro_bonus = _clamp((1.0 - macro_risk_mult) * 15.0, -12.0, 12.0)
            micro_bonus = _clamp((rsi_1m - 45.0) * 0.6, -6.0, 6.0)

        golden_bonus = 15.0 if golden_trade and side == "buy" else 0.0
        fused_score = _clamp(base_score + drl_bonus + event_bonus + macro_bonus + micro_bonus + golden_bonus, 0.0, 100.0)
        enriched = dict(cand)
        enriched["score"] = round(fused_score, 2)
        meta = dict(enriched.get("meta") or {})
        meta.update({
            "brain_mode": "drl_event_fusion",
            "opening_strategy": str(cand.get("strategy", "ai_macro") or "ai_macro").lower(),
            "drl_action": int(drl_action),
            "event_net_score": round(event_net, 2),
            "event_bias": event_bias,
            "event_hard_veto": hard_veto,
            "golden_trade_flag": golden_trade,
            "top_event_type": top_event_type,
            "top_event_score": round(top_event_score, 2),
            "news_context_state": news_context_state,
            "news_context_complete": news_context_complete,
            "score_components": {
                "base_score": round(base_score, 2),
                "drl_bonus": round(drl_bonus, 2),
                "event_bonus": round(event_bonus, 2),
                "macro_bonus": round(macro_bonus, 2),
                "micro_bonus": round(micro_bonus, 2),
                "golden_bonus": round(golden_bonus, 2),
            },
        })
        enriched["meta"] = meta
        rescored.append(enriched)

    if rescored:
        return rescored

    if drl_action not in (1, 2):
        return []

    side = "buy" if drl_action == 1 else "sell"
    if side == "buy" and hard_veto:
        return []

    if side == "buy":
        event_bonus = _clamp(event_net, -25.0, 25.0)
        macro_bonus = _clamp((macro_risk_mult - 1.0) * 20.0, -15.0, 15.0)
        micro_bonus = _clamp((55.0 - rsi_1m) * 0.6, -6.0, 6.0)
    else:
        event_bonus = _clamp(-event_net * 0.75, -18.0, 18.0)
        macro_bonus = _clamp((1.0 - macro_risk_mult) * 15.0, -12.0, 12.0)
        micro_bonus = _clamp((rsi_1m - 45.0) * 0.6, -6.0, 6.0)

    base_score = 62.0 if side == "buy" else 60.0  # Below the 68-point gate — requires at least one strategy candidate
    fused_score = _clamp(base_score + event_bonus + macro_bonus + micro_bonus, 0.0, 100.0)
    fallback = _candidate(
        "ai_macro",
        side,
        fused_score,
        _to_float(s_1m.get("close"), 0.0),
        base_notional,
        f"DRL event fusion fallback. DRL action={drl_action}, event_bias={event_bias}, macro={macro_risk_mult:.2f}.",
        {
            "brain_mode": "drl_event_fusion",
            "opening_strategy": "ai_macro",
            "drl_action": int(drl_action),
            "event_net_score": round(event_net, 2),
            "event_bias": event_bias,
            "event_hard_veto": hard_veto,
            "golden_trade_flag": golden_trade,
            "top_event_type": top_event_type,
            "top_event_score": round(top_event_score, 2),
            "news_context_state": news_context_state,
            "news_context_complete": news_context_complete,
            "score_components": {
                "base_score": round(base_score, 2),
                "event_bonus": round(event_bonus, 2),
                "macro_bonus": round(macro_bonus, 2),
                "micro_bonus": round(micro_bonus, 2),
            },
        },
    )
    return [fallback]



async def _evaluate_ai_macro(
    symbol: str,
    cfg: Dict[str, Any],
    bars_15m: pd.DataFrame,
    s_15m: Dict[str, Any],
    s_1m: Dict[str, Any],
    macro_risk_mult: float,
    position_context: Optional[Dict[str, Any]] = None,
    session_memory: Optional[Dict[str, Any]] = None,
    narrative_context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Isolated intelligence layer routing. Handles generic LLMs and strict DRL models."""
    if not HAS_ML or not bool(cfg.get("use_ml_model", True)):
        return None
        
    close_15m = _to_float(s_15m.get("close"), 0.0)
    close_1m = _to_float(s_1m.get("close"), 0.0)
    rsi_1m = _to_float(s_1m.get("rsi14"), 50.0)
    
    sniper_dip = _to_float(cfg.get("short_term", {}).get("rsi_1m_sniper_dip"), 48.0)
    sniper_rip = 100.0 - sniper_dip
    base_notional = _to_float(cfg.get("short_term", {}).get("base_notional"), 15.0)
    
    try:
        active_brain = str(cfg.get("active_brain", "drl")).lower()

        # ── DUAL BRAIN: DRL spotter + SLM gatekeeper ────────────────────────
        if active_brain == "dual_brain":
            from .ml.inference import predict_action, predict_action_ollama
            model_name = cfg.get("ollama_model", "qwen3:8b")

            # Step 1: DRL reads the price chart (instant, <1ms)
            drl_action = predict_action(symbol, bars_15m, gemini_macro=macro_risk_mult)
            if drl_action not in (1, 2):
                # DRL sees no pattern → skip SLM entirely, saving CPU
                return None

            # Step 2: SLM validates ONLY when DRL found a pattern
            slm_decision = await predict_action_ollama(
                symbol, s_15m, s_1m, macro_risk_mult, model_name,
                position_context=position_context,
                session_memory=session_memory,
                narrative_context=narrative_context,
                think=bool(cfg.get("ollama_think", False)),
            )
            slm_act = slm_decision.get("action", "HOLD")
            slm_conf = float(slm_decision.get("confidence", 0))
            slm_reason = slm_decision.get("reason", "")

            drl_dir = "BUY" if drl_action == 1 else "SELL"
            drl_conf = 88.0  # DRL sniper base confidence

            # Step 3: Both must agree on direction AND SLM >= 70
            if slm_act != drl_dir or slm_conf < 70:
                conflict_note = (
                    f"[DualBrain] DRL={drl_dir} vs SLM={slm_act}({slm_conf}%) — conflict, HOLD."
                    if slm_act != drl_dir
                    else f"[DualBrain] DRL={drl_dir} SLM agrees but low conf ({slm_conf}%) — HOLD."
                )
                return _candidate("DualBrain", "info", 0.0, close_1m, 0.0, conflict_note,
                    {"drl_action": drl_action, "slm_action": slm_act, "slm_conf": slm_conf})

            # Step 4: Agreement → fuse scores (SLM weighted higher — has live context)
            fused = round((drl_conf * 0.4) + (slm_conf * 0.6), 1)

            if drl_dir == "BUY":
                if rsi_1m <= sniper_dip:
                    return _candidate("DualBrain", "buy", fused, close_1m,
                        base_notional * (slm_conf / 60.0),
                        f"[DualBrain ✅] Both agree BUY. Fused={fused}. Oracle={macro_risk_mult:.2f}. {slm_reason}",
                        {"drl_action": drl_action, "slm_conf": slm_conf, "fused_score": fused})
                return _candidate("DualBrain", "info", 0.0, close_1m, 0.0,
                    f"[DualBrain] Both agree BUY → waiting sniper 1M RSI dip (now {rsi_1m:.1f}, need ≤{sniper_dip}).",
                    {"drl_action": drl_action, "slm_conf": slm_conf})
            else:  # SELL
                if rsi_1m >= sniper_rip:
                    return _candidate("DualBrain", "sell", fused, close_1m,
                        base_notional * (slm_conf / 60.0),
                        f"[DualBrain ✅] Both agree SELL. Fused={fused}. Oracle={macro_risk_mult:.2f}. {slm_reason}",
                        {"drl_action": drl_action, "slm_conf": slm_conf, "fused_score": fused})
                return _candidate("DualBrain", "info", 0.0, close_1m, 0.0,
                    f"[DualBrain] Both agree SELL → waiting sniper 1M RSI rip (now {rsi_1m:.1f}, need ≥{sniper_rip}).",
                    {"drl_action": drl_action, "slm_conf": slm_conf})

        # ── OLLAMA SLM only ────────────────────────────────────────────────────
        elif active_brain == "ollama_slm":
            from .ml.inference import predict_action_ollama
            model_name = cfg.get("ollama_model", "qwen3:8b")
            decision = await predict_action_ollama(
                symbol, s_15m, s_1m, macro_risk_mult, model_name,
                position_context=position_context,
                session_memory=session_memory,
                narrative_context=narrative_context,
            )

            act = decision.get("action", "HOLD")
            conf = decision.get("confidence", 0)
            reason = decision.get("reason", "Holding")

            if act == "BUY" and conf >= 70:
                return _candidate("Ollama_SLM", "buy", conf, close_1m, base_notional * (conf / 60.0), reason, {"ollama_conf": conf})
            elif act == "SELL" and conf >= 70:
                return _candidate("Ollama_SLM", "sell", conf, close_1m, base_notional * (conf / 60.0), reason, {"ollama_conf": conf})
            else:
                info_reason = f"[Low Conviction {conf}%] {reason}"
                return _candidate("Ollama_SLM", "info", 0.0, close_15m, 0.0, info_reason, {"ollama_conf": conf})

        # ── PPO DRL only (default) ───────────────────────────────────────────────
        else:
            action = predict_action(symbol, bars_15m, gemini_macro=macro_risk_mult)
            if action == 1:
                if rsi_1m <= sniper_dip:
                    return _candidate("DRL_Specialist_Sniper", "buy", 88.0, close_1m, base_notional, f"AI Macro Buy. Sniped micro-entry at 1M RSI {rsi_1m:.1f}.", {"dr_action": action, "rsi_1m": round(rsi_1m, 2)})
                return _candidate("DRL_Specialist_Sniper", "info", 0.0, close_1m, 0.0, f"AI Buy -> Wait 1M RSI dip.", {"dr_action": action})
            elif action == 2:
                if rsi_1m >= sniper_rip:
                    return _candidate("DRL_Specialist_Sniper", "sell", 88.0, close_1m, base_notional, f"AI Macro Sell. Sniped micro-exit at 1M RSI {rsi_1m:.1f}.", {"dr_action": action, "rsi_1m": round(rsi_1m, 2)})
                return _candidate("DRL_Specialist_Sniper", "info", 0.0, close_1m, 0.0, f"AI Sell -> Wait 1M RSI rip.", {"dr_action": action})
            elif action == 0:
                return _candidate("DRL_Specialist_Sniper", "info", 0.0, close_15m, 0.0, "AI Macro evaluates market as Hold.", {"dr_action": action})

    except Exception as e:
        pass
    return None


def _evaluate_swing(
    symbol: str,
    cfg: Dict[str, Any],
    s_15m: Dict[str, Any],
    s_1h: Dict[str, Any],
    close_1h: float,
    macro_risk_mult: float,
) -> Optional[Dict[str, Any]]:
    """
    Swing strategy: BTC/ETH/SOL only on 1H chart.
    Entry requires 15M uptrend + 1H RSI reset (40-45) or Lower BB touch + 1H MACD positive.
    Only fires when Oracle >= 0.9 (neutral-to-bullish).
    """
    # Oracle gate: refuse to swing-enter in fearful markets
    if macro_risk_mult < 0.9:
        return None

    # Swing symbols only
    swing_cfg = cfg.get("swing", {})
    swing_symbols = [s.upper().replace("/", "") for s in swing_cfg.get("symbols", ["BTC/USD", "ETH/USD", "SOL/USD"])]
    sym_clean = symbol.replace("/", "").upper()
    if sym_clean not in swing_symbols:
        return None

    if not s_1h or close_1h <= 0:
        return None

    # 15M trend filter: fast EMA must be above slow EMA (uptrend)
    ema_fast_15m = _to_float(s_15m.get("ema_fast"), 0.0)
    ema_slow_15m = _to_float(s_15m.get("ema_slow"), 0.0)
    if ema_fast_15m <= 0 or ema_slow_15m <= 0 or ema_fast_15m <= ema_slow_15m:
        return None  # Downtrend on 15M — no swing buys

    # 1H indicators
    rsi_1h = _to_float(s_1h.get("rsi14"), 50.0)
    bb_lower_1h = _to_float(s_1h.get("bb_lower"), 0.0)
    macd_hist_1h = _to_float(s_1h.get("macd_hist"), 0.0)

    # Entry trigger: RSI reset to 40-45 OR lower BB touch, PLUS positive MACD
    rsi_reset = 38.0 <= rsi_1h <= 47.0
    bb_touch = bb_lower_1h > 0 and close_1h <= bb_lower_1h * 1.005
    macd_positive = macd_hist_1h >= 0.0

    if not (rsi_reset or bb_touch):
        return None
    if not macd_positive:
        return None

    # Score based on how oversold RSI is
    score = min(88.0, 70.0 + (45.0 - rsi_1h) * 1.2)
    base_notional = _to_float(swing_cfg.get("base_notional"), _to_float(cfg.get("short_term", {}).get("base_notional"), 500.0))
    notional = base_notional * 2.0  # 2x sizing for swing setups

    trigger = f"1H RSI {rsi_1h:.1f} reset" if rsi_reset else f"1H Lower BB touch (${bb_lower_1h:.4f})"
    return _candidate(
        "swing_momentum",
        "buy",
        score,
        close_1h,
        notional,
        f"Swing entry: {trigger}. 15M uptrend confirmed. 1H MACD positive. Oracle: {macro_risk_mult:.2f}.",
        {"rsi_1h": round(rsi_1h, 2), "macd_hist_1h": round(macd_hist_1h, 6), "bb_touch": bb_touch},
    )


def _evaluate_position(
    symbol: str,
    cfg: Dict[str, Any],
    s_1d: Dict[str, Any],
    close_1d: float,
    macro_risk_mult: float,
) -> Optional[Dict[str, Any]]:
    """
    Position strategy: 1D Golden Cross (SMA50 > SMA200).
    BTC/ETH/SOL only. Requires Oracle > 1.2 (bullish macro).
    Fires very rarely — once per month per coin typically.
    """
    # Oracle gate: only enter long-term positions in bullish macro
    if macro_risk_mult < 1.2:
        return None

    # Position symbols only (same set as swing)
    position_cfg = cfg.get("position", {})
    position_symbols = [s.upper().replace("/", "") for s in position_cfg.get("symbols", ["BTC/USD", "ETH/USD", "SOL/USD"])]
    sym_clean = symbol.replace("/", "").upper()
    if sym_clean not in position_symbols:
        return None

    if not s_1d or close_1d <= 0:
        return None

    # 1D SMA crossover: SMA50 just crossed above SMA200 (golden cross)
    sma_fast_1d = _to_float(s_1d.get("ema_fast"), 0.0)       # Using fast MA (50D)
    sma_slow_1d = _to_float(s_1d.get("ema_slow"), 0.0)       # Using slow MA (200D)
    sma_fast_prev = _to_float(s_1d.get("ema_fast_prev"), 0.0)
    sma_slow_prev = _to_float(s_1d.get("ema_slow_prev"), 0.0)

    if sma_fast_1d <= 0 or sma_slow_1d <= 0:
        return None

    # Golden Cross: fast crossed above slow since last bar
    golden_cross = sma_fast_prev <= sma_slow_prev and sma_fast_1d > sma_slow_1d
    # Or, if already in a golden cross position and price above both MAs (trend continuation entry)
    above_both = close_1d > sma_fast_1d and close_1d > sma_slow_1d and sma_fast_1d > sma_slow_1d

    if not (golden_cross or above_both):
        return None

    base_notional = _to_float(position_cfg.get("base_notional"), _to_float(cfg.get("short_term", {}).get("base_notional"), 500.0))
    notional = base_notional * 2.5  # 2.5x sizing for multi-week position

    trigger = "Golden Cross (SMA50 crossed above SMA200)" if golden_cross else "Trend continuation above SMA50 + SMA200"
    score = 92.0 if golden_cross else 78.0

    return _candidate(
        "position_golden_cross",
        "buy",
        score,
        close_1d,
        notional,
        f"Position entry: {trigger}. Oracle: {macro_risk_mult:.2f} (Bullish). Multi-week hold.",
        {"sma50": round(sma_fast_1d, 4), "sma200": round(sma_slow_1d, 4), "golden_cross": golden_cross},
    )


async def evaluate_symbol(
    symbol: str,
    bars_15m: pd.DataFrame,
    bars_1m: pd.DataFrame,
    cfg: Dict[str, Any],
    now_ms: int,
    macro_risk_mult: float = 1.0,
    position: Optional[Dict[str, Any]] = None,
    live_equity: float = 0.0,
    total_exposure: float = 0.0,
    bars_1h: Optional[pd.DataFrame] = None,
    bars_1d: Optional[pd.DataFrame] = None,
    return_diagnostics: bool = False,
) -> Optional[Dict[str, Any]]:
    def _result(
        signal: Optional[Dict[str, Any]],
        *,
        candidates: Optional[list[Dict[str, Any]]] = None,
        best_candidate: Optional[Dict[str, Any]] = None,
        min_signal_score: float = 0.0,
        suppressed_reason: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not return_diagnostics:
            return signal
        return {
            "signal": signal,
            "candidates": [dict(c) for c in (candidates or [])],
            "best_candidate": dict(best_candidate or {}) if best_candidate else None,
            "min_signal_score": float(min_signal_score or 0.0),
            "suppressed_reason": str(suppressed_reason or ""),
        }

    if bars_15m is None or bars_15m.empty or len(bars_15m) < 40:
        return _result(None, suppressed_reason="insufficient_15m_bars")
    if bars_1m is None or bars_1m.empty or len(bars_1m) < 14:
        return _result(None, suppressed_reason="insufficient_1m_bars")

    short_cfg = (cfg.get("short_term") or {})
    long_cfg = (cfg.get("long_term") or {})

    # Execute traditional deterministic metrics on the macro timeframe
    fast_period = _to_int(long_cfg.get("ma_fast"), 50)
    slow_period = _to_int(long_cfg.get("ma_slow"), 200)
    data_15m = enrich_indicators(bars_15m, fast_ma=fast_period, slow_ma=slow_period)
    s_15m = snapshot(data_15m)
    
    # Calculate micro indicators for the Sniper entry on 1M chart
    data_1m = enrich_indicators(bars_1m, fast_ma=9, slow_ma=21)
    s_1m = snapshot(data_1m)
    
    if not s_15m or not s_1m:
        return _result(None, suppressed_reason="indicator_snapshot_missing")

    close_15m = _to_float(s_15m.get("close"), 0.0)
    close_1m = _to_float(s_1m.get("close"), 0.0)
    rsi_1m = _to_float(s_1m.get("rsi14"), 50.0)
    
    if close_1m <= 0:
        return _result(None, suppressed_reason="invalid_close")

    # Process 1H bars if available (for swing strategy)
    s_1h: Dict[str, Any] = {}
    close_1h = 0.0
    if bars_1h is not None and not bars_1h.empty and len(bars_1h) >= 26:
        data_1h = enrich_indicators(bars_1h, fast_ma=20, slow_ma=50)
        s_1h = snapshot(data_1h) or {}
        close_1h = _to_float(s_1h.get("close"), 0.0)

    # Process 1D bars if available (for position strategy)
    s_1d: Dict[str, Any] = {}
    close_1d = 0.0
    if bars_1d is not None and not bars_1d.empty and len(bars_1d) >= 200:
        data_1d = enrich_indicators(bars_1d, fast_ma=50, slow_ma=200)
        s_1d = snapshot(data_1d) or {}
        close_1d = _to_float(s_1d.get("close"), 0.0)

    # --- ARTIFICIAL INTELLIGENCE FUSION LAYER ---
    candidates = []
    active_brain = str(cfg.get("active_brain", "drl")).lower()
    
    # Build position context dict for the SLM
    pos_context: Optional[Dict[str, Any]] = None
    if position:
        pos_mv = float(position.get("market_value", 0.0) or 0.0)
        pos_entry = float(position.get("avg_entry_price", 0.0) or 0.0)
        pos_current = float(position.get("current_price", 0.0) or 0.0)
        pos_pnl = ((pos_current - pos_entry) / pos_entry * 100.0) if pos_entry > 0 else 0.0
        pos_context = {
            "pnl_pct": round(pos_pnl, 2),
            "market_value": round(pos_mv, 2),
            "total_exposure": round(total_exposure, 2),
            "exposure_pct": round((total_exposure / live_equity * 100.0) if live_equity > 0 else 0.0, 1),
        }

    # Session Memory: Fetch recent performance on this coin
    session_mem: Optional[Dict[str, Any]] = None
    try:
        from . import store as _store
        session_mem = _store.get_session_memory_sync(symbol, hours=6)
    except Exception:
        pass

    # Narrative Context: Fetch Gemini news catalyst for this coin
    narr_ctx: Optional[Dict[str, Any]] = None
    try:
        from .oracle import get_narrative_for_symbol
        narr_ctx = get_narrative_for_symbol(symbol)
    except Exception:
        pass

    if active_brain != "drl_event_fusion":
        ai_sig = await _evaluate_ai_macro(
            symbol, cfg, bars_15m, s_15m, s_1m, macro_risk_mult,
            position_context=pos_context,
            session_memory=session_mem,
            narrative_context=narr_ctx,
        )

        if ai_sig:
            candidates.append(ai_sig)

    # --- SWING STRATEGY (1H chart, BTC/ETH/SOL only) ---
    swing_sig = _evaluate_swing(symbol, cfg, s_15m, s_1h, close_1h, macro_risk_mult)
    if swing_sig:
        candidates.append(swing_sig)

    # --- POSITION STRATEGY (1D golden cross, BTC/ETH/SOL only) ---
    position_sig = _evaluate_position(symbol, cfg, s_1d, close_1d, macro_risk_mult)
    if position_sig:
        candidates.append(position_sig)

    # --- DETERMINISTIC FALLBACK MATH LAYER ---

    # 1. Micro-Scalper (Tightened: RSI < 30, requires 15M uptrend)
    vwap_1m = _to_float(s_1m.get("vwap"), 0.0)
    base_notional = _to_float(short_cfg.get("base_notional"), 6.0)
    ema_fast_15m = _to_float(s_15m.get("ema_fast"), 0.0)
    ema_slow_15m = _to_float(s_15m.get("ema_slow"), 0.0)
    trend_is_up_15m = ema_fast_15m > ema_slow_15m and ema_fast_15m > 0 and ema_slow_15m > 0

    # Threshold tightened: 38 → 30. Also requires 15M uptrend to avoid scalping into downtrends.
    if trend_is_up_15m and vwap_1m > 0 and rsi_1m <= 30.0 and close_1m <= vwap_1m * 0.998:
        score = min(75.0, 50.0 + (30.0 - rsi_1m) * 2.5)
        candidates.append(
            _candidate(
                "micro_scalper",
                "buy",
                score,
                close_1m,
                base_notional * 1.0,
                f"1M RSI {rsi_1m:.1f} deeply oversold & below VWAP. 15M uptrend confirmed.",
                {"rsi_1m": round(rsi_1m, 2), "vwap_1m": round(vwap_1m, 4)},
            )
        )
    elif vwap_1m > 0 and rsi_1m >= 68.0 and close_1m >= vwap_1m * 1.002:
        score = min(75.0, 50.0 + (rsi_1m - 68.0) * 2.5)
        candidates.append(
            _candidate(
                "micro_scalper",
                "sell",
                score,
                close_1m,
                base_notional * 0.5,
                f"1M RSI {rsi_1m:.1f} overbought & price above VWAP. Fading minor rip.",
                {"rsi_1m": round(rsi_1m, 2), "vwap_1m": round(vwap_1m, 4)},
            )
        )

    # 2. Short-term mean reversion (tightened RSI thresholds via config)
    if bool(short_cfg.get("mean_reversion_enabled", True)):
        # Tightened: rsi_oversold now 32 (was 40), rsi_overbought now 75 (was 60)
        oversold = _to_float(short_cfg.get("rsi_oversold"), 32.0)
        overbought = _to_float(short_cfg.get("rsi_overbought"), 75.0)
        base_notional = _to_float(short_cfg.get("base_notional"), 6.0)
        dip_multiplier = _to_float(short_cfg.get("dip_notional_multiplier"), 1.3)
        rsi = _to_float(s_15m.get("rsi14"), 50.0)
        bb_lower = _to_float(s_15m.get("bb_lower"), 0.0)
        bb_upper = _to_float(s_15m.get("bb_upper"), 0.0)

        if bb_lower > 0 and rsi <= oversold and close_15m <= bb_lower:
            score = min(100.0, 55.0 + (oversold - rsi) * 1.6)
            notional = base_notional * (dip_multiplier if close_15m < bb_lower * 0.993 else 1.0)
            candidates.append(
                _candidate(
                    "mean_reversion",
                    "buy",
                    score,
                    close_15m,
                    notional,
                    f"RSI {rsi:.1f} <= {oversold:.1f} and price below lower Bollinger band.",
                    {"rsi14": round(rsi, 2), "bb_lower": round(bb_lower, 4)},
                )
            )
        elif bb_upper > 0 and rsi >= overbought and close_15m >= bb_upper:
            score = min(100.0, 55.0 + (rsi - overbought) * 1.4)
            candidates.append(
                _candidate(
                    "mean_reversion",
                    "sell",
                    score,
                    close_15m,
                    base_notional,
                    f"RSI {rsi:.1f} >= {overbought:.1f} and price above upper Bollinger band.",
                    {"rsi14": round(rsi, 2), "bb_upper": round(bb_upper, 4)},
                )
            )

    # 3. Short-term breakout momentum (volume-confirmed only)
    if bool(short_cfg.get("breakout_enabled", True)):
        lookback = max(10, _to_int(short_cfg.get("breakout_lookback_bars"), 20))
        min_volume_mult = _to_float(short_cfg.get("breakout_volume_mult"), 1.9)
        breakout_buffer_pct = _to_float(short_cfg.get("breakout_buffer_pct"), 0.15)
        base_notional = _to_float(short_cfg.get("breakout_notional"), _to_float(short_cfg.get("base_notional"), 6.0))

        if len(data_15m) > lookback + 2:
            recent = data_15m.iloc[-lookback - 1: -1]
            resistance = _to_float(recent["high"].max(), 0.0)
            support = _to_float(recent["low"].min(), 0.0)
            volume = _to_float(s_15m.get("volume"), 0.0)
            vol_ma = _to_float(s_15m.get("vol_ma20"), 0.0)
            buffer = breakout_buffer_pct / 100.0

            if resistance > 0 and vol_ma > 0 and close_15m >= resistance * (1.0 + buffer) and volume >= vol_ma * min_volume_mult:
                score = min(100.0, 58.0 + ((volume / vol_ma) - min_volume_mult) * 16.0)
                candidates.append(
                    _candidate(
                        "breakout_momentum",
                        "buy",
                        score,
                        close_15m,
                        base_notional,
                        "Price broke resistance with elevated volume.",
                        {
                            "resistance": round(resistance, 4),
                            "volume_ratio": round(volume / max(vol_ma, 1e-9), 3),
                        },
                    )
                )
            elif support > 0 and vol_ma > 0 and close_15m <= support * (1.0 - buffer) and volume >= vol_ma * min_volume_mult:
                score = min(100.0, 58.0 + ((volume / vol_ma) - min_volume_mult) * 16.0)
                candidates.append(
                    _candidate(
                        "breakout_momentum",
                        "sell",
                        score,
                        close_15m,
                        base_notional,
                        "Price broke support with elevated volume.",
                        {
                            "support": round(support, 4),
                            "volume_ratio": round(volume / max(vol_ma, 1e-9), 3),
                        },
                    )
                )

    # 4. MA crossover DISABLED — replaced by swing (1H) and position (1D) trend strategies.
    # The 15M EMA 21/55 crossover was too noisy — crossed multiple times per day on volatile altcoins.
    # Real trend confirmation is now handled at proper timeframes: 1H for swing, 1D for position entries.

    # 5. Dynamic DCA (tightened — double gate: RSI < 42 AND price > 3% below slow EMA)
    if bool(long_cfg.get("dca_enabled", True)):
        base_notional = _to_float(long_cfg.get("dca_notional"), 4.0)
        dip_pct = _to_float(long_cfg.get("dca_dip_pct"), 3.0)    # Updated default: 3.0 (was 2.5)
        dip_mult = _to_float(long_cfg.get("dca_dip_multiplier"), 1.5)
        ema_slow = _to_float(s_15m.get("ema_slow"), close_15m)
        rsi_for_dca = _to_float(s_15m.get("rsi14"), 50.0)

        # Tightened gate: RSI must be < 42 (was 55) AND price must be > 3% below slow EMA (real dip only)
        dca_rsi_max = _to_float(long_cfg.get("dca_rsi_max", 42.0), 42.0)
        price_is_dip = ema_slow > 0 and close_15m <= ema_slow * (1.0 - (dip_pct / 100.0))

        if rsi_for_dca < dca_rsi_max and price_is_dip:
            notional = base_notional
            reason = f"DCA: RSI {rsi_for_dca:.1f} oversold and price {dip_pct:.1f}% below trend baseline."
            dip_distance_pct = 0.0
            if ema_slow > 0:
                dip_distance_pct = max(0.0, ((ema_slow - close_15m) / ema_slow) * 100.0)
            if close_15m < ema_slow * (1.0 - ((dip_pct + 1.5) / 100.0)):
                notional = base_notional * dip_mult
                reason = f"DCA dip boost: deep dip >{ dip_pct + 1.5:.1f}% below trend. RSI {rsi_for_dca:.1f}."
            oversold_severity = max(0.0, dca_rsi_max - rsi_for_dca)
            dip_severity = max(0.0, dip_distance_pct - dip_pct)
            score = min(82.0, 58.0 + (oversold_severity * 1.35) + (dip_severity * 4.5))
            candidates.append(
                _candidate(
                    "dynamic_dca",
                    "buy",
                    score,
                    close_15m,
                    notional,
                    reason,
                    {
                        "ema_slow": round(ema_slow, 4),
                        "rsi_15m": round(rsi_for_dca, 2),
                        "dip_distance_pct": round(dip_distance_pct, 2),
                        "oversold_severity": round(oversold_severity, 2),
                    },
                )
            )

    if not candidates:
        return _result(None, candidates=[], min_signal_score=float(cfg.get("min_signal_score", 68.0) or 68.0), suppressed_reason="no_candidate")

    # Sort by score descending — prefer higher conviction
    candidates.sort(key=lambda c: (float(c.get("score", 0.0)), 1 if c.get("side") == "buy" else 0), reverse=True)
    best = candidates[0]

    # MINIMUM SCORE GATE: Only emit signal if score >= 68.
    # Below this threshold, no signal at all — bot stays completely silent.
    # This is the most impactful overtrading fix — kills all low-conviction noise.
    min_signal_score = _to_float(cfg.get("min_signal_score", 68.0), 68.0)
    if float(best.get("score", 0.0)) < min_signal_score:
        return _result(
            None,
            candidates=candidates,
            best_candidate=best,
            min_signal_score=min_signal_score,
            suppressed_reason="strategy_score_gate",
        )

    best["symbol"] = symbol
    best["timestamp_ms"] = now_ms
    best["meta"] = best.get("meta", {})
    best["meta"]["timestamp_15m"] = str(s_15m.get("timestamp"))
    best["meta"]["rsi14"] = float(s_15m.get("rsi14", 50.0) or 50.0)
    best["meta"].setdefault("brain_mode", active_brain)
    best["meta"].setdefault("opening_strategy", str(best.get("strategy", "ai_macro") or "ai_macro").lower())
    return _result(
        best,
        candidates=candidates,
        best_candidate=best,
        min_signal_score=min_signal_score,
        suppressed_reason="",
    )
