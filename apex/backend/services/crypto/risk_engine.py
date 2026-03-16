"""
Dynamic Risk Engine — Apex Trading Bot
=======================================
Single source of truth for all risk parameters.
Everything derives from live Alpaca account equity — no hardcoded dollar amounts
except tier thresholds.

Refresh Triggers:
  - Every 30 minutes (time-based cache)
  - After every trade (call invalidate_cache())
  - When equity crosses a tier boundary (auto-detected on next get_risk_params call)
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TIER DEFINITIONS
# ---------------------------------------------------------------------------
# All dollar values are THRESHOLDS only — no dollar amounts in risk params.
_TIERS: Dict[str, Dict[str, Any]] = {
    "MICRO": {
        "equity_min": 0.0,
        "equity_max": 10_000.0,
        "max_positions": 3,
        "max_trades_per_day": 10,
        "cooldown_sec": 600,       # 10 minutes
        "anti_spam_sec": 120,
        "circuit_breaker_pct": 3.0,
        "sl_scalar": 0.75,
        "pdt_restricted": False,   # PDT only applies to stocks, not crypto
    },
    "SMALL": {
        "equity_min": 10_000.0,
        "equity_max": 25_000.0,
        "max_positions": 5,
        "max_trades_per_day": 15,
        "cooldown_sec": 420,       # 7 minutes
        "anti_spam_sec": 90,
        "circuit_breaker_pct": 4.0,
        "sl_scalar": 0.85,
        "pdt_restricted": True,    # Under $25k — PDT enforced for stocks
    },
    "STANDARD": {
        "equity_min": 25_000.0,
        "equity_max": 50_000.0,
        "max_positions": 8,
        "max_trades_per_day": 25,
        "cooldown_sec": 300,       # 5 minutes
        "anti_spam_sec": 90,
        "circuit_breaker_pct": 5.0,
        "sl_scalar": 0.95,
        "pdt_restricted": False,
    },
    "GROWTH": {
        "equity_min": 50_000.0,
        "equity_max": 100_000.0,
        "max_positions": 8,
        "max_trades_per_day": 30,
        "cooldown_sec": 300,       # 5 minutes
        "anti_spam_sec": 90,
        "circuit_breaker_pct": 5.0,
        "sl_scalar": 1.0,
        "pdt_restricted": False,
    },
    "ADVANCED": {
        "equity_min": 100_000.0,
        "equity_max": 250_000.0,
        "max_positions": 12,
        "max_trades_per_day": 40,
        "cooldown_sec": 240,       # 4 minutes
        "anti_spam_sec": 60,
        "circuit_breaker_pct": 4.5,
        "sl_scalar": 1.1,
        "pdt_restricted": False,
    },
    "ELITE": {
        "equity_min": 250_000.0,
        "equity_max": float("inf"),
        "max_positions": 15,
        "max_trades_per_day": 50,
        "cooldown_sec": 180,       # 3 minutes
        "anti_spam_sec": 45,
        "circuit_breaker_pct": 4.0,
        "sl_scalar": 1.2,
        "pdt_restricted": False,
    },
}

# Ordered tier list for threshold checks
_TIER_ORDER = ["MICRO", "SMALL", "STANDARD", "GROWTH", "ADVANCED", "ELITE"]

# ---------------------------------------------------------------------------
# ASSET CLASSIFICATION
# ---------------------------------------------------------------------------
_BTC_ETH = frozenset({
    "BTC/USD", "ETH/USD", "BTC/USDT", "ETH/USDT",
    "BTCUSD", "ETHUSD",
})
_MID_CAP_ALTS = frozenset({
    "SOL/USD", "AVAX/USD", "MATIC/USD", "DOT/USD", "LINK/USD",
    "ADA/USD", "UNI/USD", "ATOM/USD", "LTC/USD", "BCH/USD",
    "XRP/USD", "NEAR/USD", "FTM/USD", "OP/USD", "ARB/USD",
    "SOLUSD", "AVAXUSD", "MATICUSD", "DOTUSD", "LINKUSD",
})
_STOCK_ETF = frozenset({"SPY", "QQQ", "SOXL", "TQQQ", "SOXS", "IWM", "GLD", "SLV"})
_HIGH_BETA_STOCKS = frozenset({"NVDA", "TSLA", "AMD", "META", "MSFT", "GOOGL", "SMCI", "PLTR", "ARM"})

# Base stop loss percentages by asset class
_BASE_SL: Dict[str, float] = {
    "btc_eth": 3.5,
    "mid_cap_alt": 5.0,
    "small_alt": 7.0,
    "stock_etf": 2.5,
    "high_beta_stock": 4.0,
}

# ---------------------------------------------------------------------------
# ENGINE CACHE
# ---------------------------------------------------------------------------
_cache: Dict[str, Any] = {
    "params": None,
    "tier": None,
    "computed_at": 0.0,
    "equity_at_compute": 0.0,
}
_CACHE_TTL_SEC = 1800  # 30 minutes


def invalidate_cache() -> None:
    """Call this after every successful trade to force re-evaluation."""
    _cache["params"] = None
    _cache["computed_at"] = 0.0
    logger.debug("[RiskEngine] Cache invalidated (post-trade)")


# ---------------------------------------------------------------------------
# CORE PUBLIC API
# ---------------------------------------------------------------------------

def get_tier(equity: float) -> Tuple[str, Dict[str, Any]]:
    """Return (tier_name, tier_params) for the given equity level."""
    for name in reversed(_TIER_ORDER):
        t = _TIERS[name]
        if equity >= t["equity_min"]:
            return name, t
    return "MICRO", _TIERS["MICRO"]


def get_risk_params(equity: float, cash: float) -> Dict[str, Any]:
    """
    Return the complete risk parameter dict for the current equity level.
    Cached for 30 minutes; invalidated after trades or tier crossings.
    """
    now = time.monotonic()
    tier_name, tier = get_tier(equity)

    # Bust cache if tier changed since last compute
    tier_changed = _cache.get("tier") != tier_name
    cache_expired = (now - _cache["computed_at"]) > _CACHE_TTL_SEC
    cache_empty = _cache["params"] is None

    if cache_empty or cache_expired or tier_changed:
        params = _compute_params(equity, cash, tier_name, tier)
        _cache["params"] = params
        _cache["tier"] = tier_name
        _cache["computed_at"] = now
        _cache["equity_at_compute"] = equity
        logger.info(
            "[RiskEngine] Computed params — Tier: %s | Positions: %d | "
            "Trades/day: %d | Cooldown: %ds | Circuit: %.1f%% | "
            "Min trade: $%.0f | Hard cap: $%.0f",
            tier_name,
            params["max_positions"],
            params["max_trades_per_day"],
            params["cooldown_sec"],
            params["circuit_breaker_pct"],
            params["min_trade_usd"],
            params["hard_cap_usd"],
        )
    else:
        params = _cache["params"]

    return params


def get_asset_sl_pct(symbol: str, tier_name: str) -> float:
    """
    Return the stop-loss percentage for a given symbol and current tier.
    Base SL is scaled by the tier's sl_scalar.
    """
    asset_class = _classify_asset(symbol)
    base_sl = _BASE_SL.get(asset_class, _BASE_SL["small_alt"])
    scalar = _TIERS.get(tier_name, _TIERS["GROWTH"]).get("sl_scalar", 1.0)
    return round(base_sl * scalar, 2)


def get_confidence_floor(daily_trades: int, daily_max: int) -> float:
    """
    Return minimum signal confidence required based on daily trade utilization.
    Tightens progressively as the bot approaches its daily limit.
    Prevents quota-filling on weak signals near the ceiling.
    """
    if daily_max <= 0:
        return 55.0
    utilization = daily_trades / daily_max
    if utilization < 0.50:
        return 55.0   # < 50% used — normal threshold
    elif utilization < 0.75:
        return 65.0   # 50–75% used
    elif utilization < 0.90:
        return 75.0   # 75–90% used
    else:
        return 85.0   # 90%+ used — near-certain setups only


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _compute_params(
    equity: float,
    cash: float,
    tier_name: str,
    tier: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute all derived risk parameters from live equity + cash."""
    # Position sizing — all derived from live numbers
    ai_ceiling_usd = cash * 0.10                          # 10% of cash
    hard_cap_usd = cash * 0.085                           # 8.5% of cash
    min_trade_usd = max(150.0, equity * 0.0015)           # max($150, 0.15% of equity)
    max_exposure_usd = equity * 0.80                      # 80% of equity

    return {
        # Tier identity
        "tier": tier_name,
        "equity": equity,
        "cash": cash,

        # Trading limits
        "max_positions": tier["max_positions"],
        "max_trades_per_day": tier["max_trades_per_day"],
        "cooldown_sec": tier["cooldown_sec"],
        "anti_spam_sec": tier["anti_spam_sec"],
        "circuit_breaker_pct": tier["circuit_breaker_pct"],
        "pdt_restricted": tier["pdt_restricted"],
        "sl_scalar": tier["sl_scalar"],

        # Position sizing (derived, not hardcoded)
        "ai_ceiling_usd": round(ai_ceiling_usd, 2),
        "hard_cap_usd": round(hard_cap_usd, 2),
        "min_trade_usd": round(min_trade_usd, 2),
        "max_exposure_usd": round(max_exposure_usd, 2),
        "notional_pct_of_cash": 0.10,
    }


def _classify_asset(symbol: str) -> str:
    """Classify symbol into an asset class for SL tier lookup."""
    sym = symbol.strip().upper().replace("-", "/")
    if sym in _BTC_ETH:
        return "btc_eth"
    if sym in _MID_CAP_ALTS:
        return "mid_cap_alt"
    if sym in _STOCK_ETF:
        return "stock_etf"
    if sym in _HIGH_BETA_STOCKS:
        return "high_beta_stock"
    # Default: small altcoin (highest SL to give room for volatility)
    return "small_alt"


def get_engine_summary() -> Dict[str, Any]:
    """
    Return a human-readable summary of the current engine state.
    Useful for logging and the UI status panel.
    """
    if _cache["params"] is None:
        return {"status": "not_initialized"}
    p = _cache["params"]
    return {
        "tier": p["tier"],
        "equity": p["equity"],
        "max_positions": p["max_positions"],
        "max_trades_per_day": p["max_trades_per_day"],
        "cooldown_sec": p["cooldown_sec"],
        "circuit_breaker_pct": p["circuit_breaker_pct"],
        "min_trade_usd": p["min_trade_usd"],
        "hard_cap_usd": p["hard_cap_usd"],
        "pdt_restricted": p["pdt_restricted"],
        "sl_btc_eth": get_asset_sl_pct("BTC/USD", p["tier"]),
        "sl_mid_alt": get_asset_sl_pct("SOL/USD", p["tier"]),
        "sl_small_alt": get_asset_sl_pct("DOGE/USD", p["tier"]),
        "cache_age_sec": round(time.monotonic() - _cache["computed_at"], 0),
    }
