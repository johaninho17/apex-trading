"""
ConfigManager â€” Centralized settings for all Apex modules.

Loads config.json on startup, provides get/update methods,
and persists changes to disk. Falls back to hardcoded defaults
if no file exists.
"""

import json
import os
import copy
import threading
from typing import Any, Dict

_CONFIG_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_FILE = os.environ.get(
    "APEX_CONFIG_FILE",
    os.path.join(_CONFIG_DIR, "config.json")
)

DEFAULTS: Dict[str, Any] = {
    "app": {
        "profile": "crypto",
        "enabled_route_packs": {
            "crypto": True,
            "crypto_compat": True,
            "system": True,
            "settings": True,
            "notifications": True,
            "jobs": True,
            "kalshi": False,
            "dfs": False,
            "polymarket": False,
            "legacy_alpaca": False,
            "stocks_ml": False,
        },
    },
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
            "active_brain": "drl_event_fusion",
            "ollama_model": "apexbot",
            "telegram_ollama_model": "qwen3:8b",
            "ollama_think": False,
            "live_retrain_interval_days": 3,
            "poll_interval_sec": 30,
            "timeframe": "1Min",
            "symbols": ["BTC/USD", "ETH/USD"],
            "auto_discover_pairs": False,
            "auto_discover_limit": 20,
            "auto_discover_quote": "USD",
            "auto_discover_tradable_only": True,
            "min_order_notional_usd": 150.0,
            "max_open_positions": 8,
            "max_notional_per_trade": 5000.0,
            "max_total_exposure": 69650.0,
            "max_daily_drawdown_pct": 5.0,
            "cooldown_sec": 90,
            "anti_spam_sec": 30,
            "short_term": {
                "mean_reversion_enabled": True,
                "breakout_enabled": True,
                "base_notional": 500.0,
                "breakout_notional": 750.0,
                "rsi_oversold": 32.0,
                "rsi_overbought": 75.0,
                "breakout_lookback_bars": 20,
                "breakout_volume_mult": 1.9,
                "breakout_buffer_pct": 0.15,
                "dip_notional_multiplier": 1.3,
            },
            "long_term": {
                "ma_crossover_enabled": True,
                "ma_fast": 50,
                "ma_slow": 200,
                "crossover_notional": 800.0,
                "dca_enabled": True,
                "dca_notional": 250.0,
                "dca_interval_min": 180,
                "dca_dip_pct": 3.0,
                "dca_dip_multiplier": 1.5,
            },
            "synthetic_exits": {
                "enabled": True,
                "take_profit_pct": 3.0,
                "stop_loss_pct": 3.5,
                "tp1_fraction": 0.4,
            },
            "swing": {
                "symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
                "base_notional": 500.0,
                "oracle_min": 0.9,
            },
            "position": {
                "symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
                "base_notional": 500.0,
                "oracle_min": 1.2,
                "trailing_stop_pct": 17.0,
            },
            "news": {
                "enabled": True,
                "watchlist_only": True,
                "binance_poll_sec": 20,
                "crypto_news_poll_sec": 1800,
                "cryptopanic_asset_limit": 8,
                "cryptopanic_backoff_sec": 1800,
                "event_default_ttl_min": 180,
                "dedupe_window_min": 240,
                "sources": {
                    "binance": True,
                    "cryptopanic": True
                }
            },
            "universe": {
                "enabled": True,
                "core_symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
                "discovery_limit_raw": 80,
                "discovery_limit_ranked": 25,
                "event_override_limit": 8,
                "refresh_sec": 1800,
                "min_24h_volume_usd": 2000000.0,
                "max_spread_bps": 90.0,
                "min_price": 0.05,
                "rank_weights": {
                    "liquidity": 0.30,
                    "spread": 0.20,
                    "volume": 0.20,
                    "volatility_quality": 0.10,
                    "event": 0.10,
                    "historical_edge": 0.10
                }
            },
            "brains": {
                "drl_event_fusion": {
                    "enabled": True,
                    "event_bonus_cap": 25.0,
                    "macro_bonus_cap": 15.0,
                    "hard_veto_enabled": True,
                    "golden_trade_min_score": 90.0,
                    "golden_bypass_enabled": True,
                    "golden_bypass_max_per_day": 2,
                    "golden_bypass_max_per_symbol_per_day": 1,
                    "golden_bypass_event_types": [],
                    "use_gemini_macro": True,
                    "use_ollama_events": True
                }
            },
            "startup_services": {
                "manual_opt_in": True,
                "start_oracle_on_backend_boot": True,
                "start_news_on_backend_boot": True,
                "start_reports_on_backend_boot": False,
                "start_telegram_on_backend_boot": False,
                "start_ollama_on_backend_boot": False,
                "start_ml_on_backend_boot": False,
                "oracle_backend_boot_delay_sec": 20,
                "news_backend_boot_delay_sec": 30,
                "reports_backend_boot_delay_sec": 45,
                "telegram_backend_boot_delay_sec": 10,
                "ollama_backend_boot_delay_sec": 60,
                "start_oracle_with_bot": True,
                "start_news_with_bot": True,
                "start_reports_with_bot": True,
                "start_ollama_with_bot": True,
                "start_telegram_with_bot": False,
                "start_ml_with_bot": False,
                "ml_delayed_start_sec": 300,
            },
            "telegram_oracle_updates_enabled": True,
            "telegram_oracle_update_min_interval_sec": 3600,
            "telegram_oracle_update_score_delta": 0.12,
            "telegram_retrieval": {
                "backend": "llamaindex",
                "enabled": True,
                "doc_max_nodes": 4,
                "sql_max_rows": 25,
                "sql_enabled": True,
                "docs_enabled": True
            },
            "activity_governor": {
                "enabled": True,
                "min_update_interval_sec": 600,
                "base_update_interval_sec": 1200,
                "max_update_interval_sec": 2400,
                "hysteresis_windows": 2,
                "macro_cold_damping_below": 0.85,
                "min_signal_floor": 62.0,
                "min_signal_ceiling": 78.0,
                "rsi_oversold_floor": 28.0,
                "rsi_oversold_ceiling": 38.0,
                "breakout_volume_floor": 1.5,
                "breakout_volume_ceiling": 2.3,
                "trade_spacing_floor_min": 15,
                "trade_spacing_ceiling_min": 60,
                "near_miss_band": 5.0,
                "signals_pressure_min": 4,
                "decision_feedback_window_hours": 24,
                "bad_block_relax_min": 2,
                "good_block_tighten_min": 2,
                "bad_block_relax_step": 6.0,
                "good_block_tighten_step": 6.0,
                "modes": {
                    "cold": {
                        "min_signal_score_delta": -3.0,
                        "rsi_oversold_delta": 2.0,
                        "breakout_volume_mult_delta": -0.2,
                        "trade_spacing_min_delta": -5
                    },
                    "normal": {
                        "min_signal_score_delta": 0.0,
                        "rsi_oversold_delta": 0.0,
                        "breakout_volume_mult_delta": 0.0,
                        "trade_spacing_min_delta": 0
                    },
                    "warm": {
                        "min_signal_score_delta": 1.0,
                        "rsi_oversold_delta": 0.0,
                        "breakout_volume_mult_delta": 0.0,
                        "trade_spacing_min_delta": 5
                    },
                    "hot": {
                        "min_signal_score_delta": 4.0,
                        "rsi_oversold_delta": -1.0,
                        "breakout_volume_mult_delta": 0.2,
                        "trade_spacing_min_delta": 10
                    },
                    "overactive": {
                        "min_signal_score_delta": 7.0,
                        "rsi_oversold_delta": -2.0,
                        "breakout_volume_mult_delta": 0.35,
                        "trade_spacing_min_delta": 20
                    }
                }
            },
            "decision_feedback": {
                "enabled": True,
                "eval_interval_sec": 1800,
                "limit_per_pass": 20,
                "timeframe": "1Min",
                "window_min": 180,
                "horizons_min": [30, 60, 360, 1440],
                "flat_band_pct": 0.35
            },
            "policy_overlays": {
                "enabled": True,
                "refresh_sec": 300,
                "lookback_limit": 1500,
                "min_samples": 4,
                "score_delta_cap": 20.0,
                "size_multiplier_min": 0.75,
                "size_multiplier_max": 1.25,
                "cooldown_multiplier_min": 0.75,
                "cooldown_multiplier_max": 1.5,
                "brain_self_score_interval_sec": 3600
            },
            "mode_effectiveness": {
                "enabled": True,
                "eval_interval_sec": 1800,
                "window_hours": 24,
                "min_samples": 8,
                "history_limit": 48
            },
            "smart_overhaul": {
                "enabled": True,
                "component_calibration_enabled": True,
                "behavior_state_modules_enabled": True,
                "research_scratchpad_enabled": True,
                "edge_engine_enabled": True
            },
            "component_calibration": {
                "enabled": True,
                "eval_interval_sec": 1800
            },
            "behavior_state_modules": {
                "enabled": True,
                "weights": {
                    "execution_quality": 0.25,
                    "strategy_regime_fitness": 0.20,
                    "symbol_edge": 0.20,
                    "news_reliability": 0.15,
                    "market_data_quality": 0.10,
                    "opportunity_pressure": 0.10
                }
            },
            "research_scratchpad": {
                "enabled": True,
                "channel": "telegram",
                "max_rows": 200
            },
            "setup_rearm": {
                "enabled": True,
                "candidate_window_sec": 180,
                "buy_window_sec": 900,
                "score_epsilon_candidate": 1.0,
                "score_epsilon_buy": 1.5,
                "price_rearm_pct": 0.5,
                "buyable_override_enabled": True,
                "allow_event_rearm": True,
                "allow_strategy_rearm": True,
                "allow_risk_macro_rearm": True,
                "dca": {
                    "min_add_spacing_min": 30,
                    "min_price_drop_pct": 1.0,
                    "min_oversold_delta": 1.0
                }
            },
            "risk_state": {
                "enabled": True,
                "oracle_escalate_halt_min": 30,
                "oracle_halt_score_max": 0.52,
                "oracle_recovery_cautious_min": 15,
                "daily_drawdown_halt_min": 90,
                "daily_drawdown_recovery_buffer_pct": 0.5,
                "daily_drawdown_recheck_min": 30,
                "daily_drawdown_cautious_min": 120,
                "equity_guard_hard_halt_min": 10,
                "equity_guard_hard_halt_max": 30,
                "equity_guard_post_halt_cautious_min": 30,
                "states": {
                    "normal": {"signal_bonus": 0.0, "notional_mult": 1.0, "skip_buys": False, "golden_only": False},
                    "cautious": {"signal_bonus": 3.0, "notional_mult": 0.7, "skip_buys": False, "golden_only": False},
                    "restricted": {"signal_bonus": 6.0, "notional_mult": 0.5, "skip_buys": False, "golden_only": True},
                    "halted": {"signal_bonus": 10.0, "notional_mult": 0.0, "skip_buys": True, "golden_only": False}
                }
            },
            "trade_spacing_min": 30,
            "max_trades_per_hour": 8,
            "bar_prefetch_batch_size": 8,
            "max_total_trades_per_day": 18,
            "max_trades_per_coin_per_day": 4,
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
_config_mtime: float = 0.0  # Track file mtime for hot-reload


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
    global _config, _config_mtime
    with _lock:
        if os.path.exists(_CONFIG_FILE):
            try:
                with open(_CONFIG_FILE, "r") as f:
                    saved = json.load(f)
                _config = _deep_merge(DEFAULTS, saved)
                _config_mtime = os.path.getmtime(_CONFIG_FILE)
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
    """Return current config, hot-reloading from disk if config.json was modified."""
    global _config, _config_mtime
    with _lock:
        if not _config:
            return load_config()
        # Check if file was modified since last load
        try:
            current_mtime = os.path.getmtime(_CONFIG_FILE)
            if current_mtime > _config_mtime:
                with open(_CONFIG_FILE, "r") as f:
                    saved = json.load(f)
                _config = _deep_merge(DEFAULTS, saved)
                _config_mtime = current_mtime
        except Exception:
            pass
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


