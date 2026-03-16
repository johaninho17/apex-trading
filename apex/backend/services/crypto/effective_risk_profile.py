from __future__ import annotations

from typing import Any, Dict, Optional


def build_effective_risk_profile(
    cfg: Dict[str, Any],
    risk_params: Dict[str, Any],
    *,
    account_equity: float,
    account_cash: float,
    exposure: float,
    open_positions_count: int,
    risk_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = dict(risk_state or {})
    state_name = str(state.get("state", "normal") or "normal")
    state_notional_mult = float(state.get("notional_mult", 1.0) or 1.0)
    signal_bonus = float(state.get("signal_bonus", 0.0) or 0.0)
    skip_buys = bool(state.get("skip_buys", False))
    golden_only = bool(state.get("golden_only", False))

    hard_cap_usd = float(risk_params.get("hard_cap_usd", cfg.get("max_notional_per_trade", 0.0)) or 0.0)
    ai_ceiling_usd = float(risk_params.get("ai_ceiling_usd", hard_cap_usd) or hard_cap_usd)
    max_exposure_usd = float(risk_params.get("max_exposure_usd", cfg.get("max_total_exposure", 0.0)) or 0.0)
    notional_pct_of_cash = float(risk_params.get("notional_pct_of_cash", cfg.get("notional_pct_of_cash", 0.0)) or 0.0)
    min_trade_usd = float(risk_params.get("min_trade_usd", cfg.get("min_order_notional_usd", 0.0)) or 0.0)
    max_positions = int(risk_params.get("max_positions", cfg.get("max_open_positions", 0)) or 0)
    max_trades_per_day = int(risk_params.get("max_trades_per_day", cfg.get("max_total_trades_per_day", 0)) or 0)
    trade_spacing_min = max(1, int((risk_params.get("cooldown_sec", cfg.get("trade_spacing_min", 30) * 60) or 0) / 60))
    hourly_cap = int(cfg.get("max_trades_per_hour", 0) or 0)
    coin_cap = int(cfg.get("max_trades_per_coin_per_day", 0) or 0)

    effective_hard_cap = round(hard_cap_usd * state_notional_mult, 2)
    effective_ai_ceiling = round(ai_ceiling_usd * state_notional_mult, 2)

    available_exposure_usd = round(max(0.0, max_exposure_usd - float(exposure or 0.0)), 2)
    capacity_remaining = max(0, max_positions - int(open_positions_count or 0))

    return {
        "tier": str(risk_params.get("tier", "")),
        "account_equity": round(float(account_equity or 0.0), 2),
        "account_cash": round(float(account_cash or 0.0), 2),
        "current_exposure_usd": round(float(exposure or 0.0), 2),
        "available_exposure_usd": available_exposure_usd,
        "hard_cap_usd": round(hard_cap_usd, 2),
        "ai_ceiling_usd": round(ai_ceiling_usd, 2),
        "effective_hard_cap_usd": effective_hard_cap,
        "effective_ai_ceiling_usd": effective_ai_ceiling,
        "max_exposure_usd": round(max_exposure_usd, 2),
        "notional_pct_of_cash": round(notional_pct_of_cash, 4),
        "min_trade_usd": round(min_trade_usd, 2),
        "max_positions": max_positions,
        "open_positions_count": int(open_positions_count or 0),
        "capacity_remaining": capacity_remaining,
        "max_trades_per_day": max_trades_per_day,
        "max_trades_per_hour": hourly_cap,
        "max_trades_per_coin_per_day": coin_cap,
        "trade_spacing_min": trade_spacing_min,
        "risk_state": state_name,
        "risk_state_notional_mult": round(state_notional_mult, 4),
        "risk_state_signal_bonus": round(signal_bonus, 2),
        "skip_buys": skip_buys,
        "golden_only": golden_only,
    }
