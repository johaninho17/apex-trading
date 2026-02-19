"""
ConfigManager — Centralized settings for all Apex modules.

Loads config.json on startup, provides get/update methods,
and persists changes to disk. Falls back to hardcoded defaults
if no file exists.
"""

import json
import os
import copy
import threading
from typing import Any, Dict

_CONFIG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_FILE = os.path.join(_CONFIG_DIR, "config.json")

DEFAULTS: Dict[str, Any] = {
    "stocks": {
        "atr_multipliers": {
            "aggressive": 2.0,
            "conservative": 2.5,
            "trend": 3.0,
        },
        "rsi_period": 14,
        "sma_periods": [20, 50, 200],
        "ema_periods": [9, 21],
        "backtest_targets": {
            "aggressive_target_pct": 6.0,
            "aggressive_stop_pct": 3.0,
            "conservative_target_pct": 10.0,
            "conservative_stop_pct": 5.0,
        },
        "scanner_min_price": 5.0,
        "scanner_min_volume": 500000,
        "quick_settings": {
            "min_play_score": 55.0,
            "hide_below_min_score": True,
            "auto_sort_play_score": True,
        },
        "calc_profile": {
            "atrWeight": 1.2,
            "rsiWeight": 0.9,
            "emaWeight": 1.1,
            "crossoverWeight": 1.15,
            "volatilityPenalty": 0.8,
            "liquidityWeight": 0.7,
            "trendStrengthBonus": 6.0,
            "scoreSmoothing": 0.6,
            "useRsiFilter": True,
            "useAtrTrendGate": True,
            "useCrossoverBoost": True,
            "useLiquidityFilter": True,
        },
        "crypto": {
            "enabled": True,
            "trading_mode": "offline",
            "account_mode": "paper",
            "poll_interval_sec": 30,
            "timeframe": "1Min",
            "symbols": ["BTC/USD", "ETH/USD"],
            "auto_discover_pairs": False,
            "auto_discover_limit": 20,
            "auto_discover_quote": "USD",
            "auto_discover_tradable_only": True,
            "min_order_notional_usd": 10.0,
            "max_open_positions": 3,
            "max_notional_per_trade": 15.0,
            "max_total_exposure": 250.0,
            "max_daily_drawdown_pct": 4.0,
            "cooldown_sec": 90,
            "anti_spam_sec": 30,
            "short_term": {
                "mean_reversion_enabled": True,
                "breakout_enabled": True,
                "base_notional": 6.0,
                "breakout_notional": 7.5,
                "rsi_oversold": 28.0,
                "rsi_overbought": 72.0,
                "breakout_lookback_bars": 20,
                "breakout_volume_mult": 1.9,
                "breakout_buffer_pct": 0.15,
                "dip_notional_multiplier": 1.3,
            },
            "long_term": {
                "ma_crossover_enabled": True,
                "ma_fast": 50,
                "ma_slow": 200,
                "crossover_notional": 8.0,
                "dca_enabled": True,
                "dca_notional": 4.0,
                "dca_interval_min": 180,
                "dca_dip_pct": 1.5,
                "dca_dip_multiplier": 1.5,
            },
            "synthetic_exits": {
                "enabled": True,
                "take_profit_pct": 3.0,
                "stop_loss_pct": 1.8,
            },
        },
    },
    "dfs": {
        "sniper": {
            "min_line_diff": 1.5,
            "poll_interval": 30,
            "max_stale_window": 600,
            "max_movements": 100,
        },
        "slip_builder": {
            "slip_sizes": [3, 4, 5],
            "min_edge_pct": 0.0,
            "top_n_slips": 5,
            "max_pool_size": 15,
        },
        "ev_calculator": {
            "default_stake": 100.0,
            "kelly_fraction_cap": 0.25,
        },
        "quick_settings": {
            "min_edge": 1.2,
            "plays_only": True,
            "side_filter": "all",
            "auto_sort_play_score": True,
            "sleeper_markets_only": True,
        },
        "consensus": {
            "min_books": 2,
            "line_window": 1.0,
            "main_line_only": True,
            "min_trend_count": 0,
            "weights": {
                "bookmaker": 4.0,
                "pinnacle": 3.0,
                "fanduel": 6.0,
                "draftkings": 4.0,
            },
        },
        "calc_profile": {
            "edgeWeight": 1.8,
            "confidenceWeight": 1.2,
            "stakeWeight": 1.0,
            "kellyCapPct": 25.0,
            "useDevig": True,
            "useConfidenceShrink": True,
            "useVigPenalty": True,
            "useTrendBonus": True,
            "useKellyCap": True,
            "useCorrelationPenalty": True,
        },
    },
    "events": {
        "quick_settings": {
            "min_play_score": 50.0,
            "sort_by_play_score": True,
            "show_scans_in_activity": False,
        },
        "calc_profile": {
            "spreadWeight": 1.5,
            "liquidityWeight": 1.2,
            "depthWeight": 1.0,
            "momentumWeight": 1.1,
            "confidenceWeight": 1.0,
            "volatilityPenalty": 0.8,
            "executionRiskPenalty": 0.9,
            "scalpSensitivity": 1.0,
            "useDepthBoost": True,
            "useVolatilityPenalty": True,
            "useExecutionRisk": True,
            "useMomentumBoost": True,
            "useConfidenceScaling": True,
        },
        "kalshi": {
            "trading_mode": "live",
            "max_position_size": 100.0,
            "max_total_exposure": 1000.0,
            "stop_loss_pct": 10.0,
            "arbitrage_min_profit": 0.02,
            "market_maker_spread": 0.02,
            "copy_trade_ratio": 0.1,
            "copy_follow_accounts": [],
            "bot_detection_threshold": 0.7,
            "bot_interval": 60,
        },
    },
}

_lock = threading.Lock()
_config: Dict[str, Any] = {}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base, preserving defaults for missing keys."""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def load_config() -> Dict[str, Any]:
    """Load config from disk, merging with defaults."""
    global _config
    with _lock:
        if os.path.exists(_CONFIG_FILE):
            try:
                with open(_CONFIG_FILE, "r") as f:
                    saved = json.load(f)
                _config = _deep_merge(DEFAULTS, saved)
            except Exception:
                _config = copy.deepcopy(DEFAULTS)
        else:
            _config = copy.deepcopy(DEFAULTS)
            _save_config_locked()
        return copy.deepcopy(_config)


def _save_config_locked():
    """Save current config to disk. Must hold _lock."""
    with open(_CONFIG_FILE, "w") as f:
        json.dump(_config, f, indent=2)


def get_config() -> Dict[str, Any]:
    """Return a copy of the current config."""
    with _lock:
        if not _config:
            return load_config()
        return copy.deepcopy(_config)


def get_section(section: str) -> Dict[str, Any]:
    """Return a specific section (stocks, dfs, events)."""
    cfg = get_config()
    return cfg.get(section, {})


def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge partial updates into the config and persist."""
    global _config
    with _lock:
        if not _config:
            load_config()
        _config = _deep_merge(_config, updates)
        _save_config_locked()
        return copy.deepcopy(_config)


def reset_config() -> Dict[str, Any]:
    """Reset all settings to defaults and persist."""
    global _config
    with _lock:
        _config = copy.deepcopy(DEFAULTS)
        _save_config_locked()
        return copy.deepcopy(_config)


# Load on import
load_config()
