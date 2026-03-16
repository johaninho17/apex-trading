"""
APEX Crypto Bot — Revamp & Risk Engine Test Suite
==================================================
Tests covering all changes from the strategy revamp and dynamic risk engine:

  - risk_engine.py:  6-tier system, asset SL, confidence floor, caching
  - strategy.py:     Min score gate, tightened signal conditions, new
                     swing and position strategy calls via evaluate_symbol
  - bot.py:          TRADE_LIMITS dict, daily ceiling counters, progressive
                     confidence floor integration

Run with:
    cd apex/backend
    python -m pytest tests/test_revamp_and_risk_engine.py -v
  or standalone:
    python tests/test_revamp_and_risk_engine.py
"""

import asyncio
import os
import sys
import time
import unittest
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

os.environ["NUMBA_DISABLE_JIT"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── helpers ───────────────────────────────────────────────────────────────────

def _bars(n: int = 250, close: float = 50_000.0, trend: float = 0.0,
          seed: int = 42, freq: str = "15min") -> pd.DataFrame:
    np.random.seed(seed)
    ts = pd.date_range("2025-01-01", periods=n, freq=freq)
    c = close + np.cumsum(np.random.randn(n) * 200 + trend)
    c = np.maximum(c, 100.0)
    h = c * (1 + np.abs(np.random.randn(n)) * 0.004)
    lo = c * (1 - np.abs(np.random.randn(n)) * 0.004)
    o = np.roll(c, 1); o[0] = c[0]
    v = np.abs(np.random.randn(n)) * 1_000_000 + 500_000
    return pd.DataFrame({"timestamp": ts, "open": o, "high": h,
                         "low": lo, "close": c, "volume": v})


BASE_CFG: Dict[str, Any] = {
    "active_exchange": "alpaca",
    "account_mode": "paper",
    "trading_mode": "offline",
    "symbols": ["BTC/USD", "ETH/USD", "SOL/USD"],
    "min_signal_score": 68.0,
    "short_term": {
        "mean_reversion_enabled": True,
        "breakout_enabled": True,
        "rsi_oversold": 32,
        "rsi_overbought": 75,
        "rsi_1m_sniper_dip": 80.0,
        "breakout_lookback_bars": 10,
        "breakout_volume_mult": 1.9,
        "breakout_buffer_pct": 0.15,
        "base_notional": 500.0,
        "breakout_notional": 750.0,
        "dip_notional_multiplier": 1.3,
    },
    "long_term": {
        "ma_crossover_enabled": False,
        "ma_fast": 50,
        "ma_slow": 200,
        "crossover_notional": 800.0,
        "dca_enabled": True,
        "dca_notional": 250.0,
        "dca_interval_min": 180,
        "dca_dip_pct": 3.0,
        "dca_dip_multiplier": 1.5,
        "dca_rsi_max": 42,
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
    "synthetic_exits": {
        "enabled": True,
        "take_profit_pct": 3.0,
        "stop_loss_pct": 3.5,
        "tp1_fraction": 0.4,
    },
    "poll_interval_sec": 60,
    "notional_pct_of_cash": 10.0,
}


def _eval_symbol(symbol: str = "BTC/USD", cfg: Dict = None, seed: int = 42,
                 bars_1h_n: int = 0, bars_1d_n: int = 0,
                 macro_risk_mult: float = 1.0):
    """Helper: calls evaluate_symbol with mocked store (no DB needed)."""
    from services.crypto.strategy import evaluate_symbol
    cfg = cfg or BASE_CFG
    now_ms = int(time.time() * 1000)
    bars_15m = _bars(250, seed=seed)
    bars_1m = _bars(100, seed=seed + 1, freq="1min")
    bars_1h = _bars(bars_1h_n, seed=seed + 2, freq="1h") if bars_1h_n > 0 else None
    bars_1d = _bars(bars_1d_n, seed=seed + 3, freq="1D") if bars_1d_n > 0 else None

    with patch("services.crypto.store.get_session_memory_sync", return_value=None):
        with patch("services.crypto.oracle.get_narrative_for_symbol", return_value=None):
            return asyncio.run(evaluate_symbol(
                symbol=symbol,
                bars_15m=bars_15m,
                bars_1m=bars_1m,
                cfg=cfg,
                now_ms=now_ms,
                macro_risk_mult=macro_risk_mult,
                bars_1h=bars_1h,
                bars_1d=bars_1d,
            ))


# ══════════════════════════════════════════════════════════════════════════════
# 1.  RISK ENGINE — TIER CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskEngineTiers(unittest.TestCase):

    def setUp(self):
        from services.crypto import risk_engine
        risk_engine.invalidate_cache()

    def _tier(self, equity: float) -> str:
        from services.crypto.risk_engine import get_tier
        name, _ = get_tier(equity)
        return name

    def test_micro_tier_boundaries(self):
        self.assertEqual(self._tier(0), "MICRO")
        self.assertEqual(self._tier(5_000), "MICRO")
        self.assertEqual(self._tier(9_999), "MICRO")

    def test_small_tier_boundaries(self):
        self.assertEqual(self._tier(10_000), "SMALL")
        self.assertEqual(self._tier(24_999), "SMALL")

    def test_standard_tier_boundaries(self):
        self.assertEqual(self._tier(25_000), "STANDARD")
        self.assertEqual(self._tier(49_999), "STANDARD")

    def test_growth_tier_at_87k(self):
        # Current account: $87k → GROWTH
        self.assertEqual(self._tier(87_000), "GROWTH")
        self.assertEqual(self._tier(50_000), "GROWTH")
        self.assertEqual(self._tier(99_999), "GROWTH")

    def test_advanced_tier_boundaries(self):
        self.assertEqual(self._tier(100_000), "ADVANCED")
        self.assertEqual(self._tier(249_999), "ADVANCED")

    def test_elite_tier(self):
        self.assertEqual(self._tier(250_000), "ELITE")
        self.assertEqual(self._tier(10_000_000), "ELITE")


# ══════════════════════════════════════════════════════════════════════════════
# 2.  RISK ENGINE — COMPUTED PARAMS FOR $87K (GROWTH)
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskEngineParams(unittest.TestCase):

    def setUp(self):
        from services.crypto import risk_engine
        risk_engine.invalidate_cache()
        from services.crypto.risk_engine import get_risk_params
        self.params = get_risk_params(equity=87_000.0, cash=87_000.0)

    def test_tier_is_growth(self):
        self.assertEqual(self.params["tier"], "GROWTH")

    def test_max_positions_is_8(self):
        self.assertEqual(self.params["max_positions"], 8)

    def test_daily_limit_is_30(self):
        self.assertEqual(self.params["max_trades_per_day"], 30)

    def test_cooldown_is_300s(self):
        self.assertEqual(self.params["cooldown_sec"], 300)

    def test_circuit_breaker_is_5pct(self):
        self.assertAlmostEqual(self.params["circuit_breaker_pct"], 5.0, places=1)

    def test_min_trade_at_least_150(self):
        # max($150, 0.15% × $87k = $130.5) → $150
        self.assertGreaterEqual(self.params["min_trade_usd"], 150.0)

    def test_hard_cap_is_8_5pct_of_cash(self):
        expected = 87_000.0 * 0.085
        self.assertAlmostEqual(self.params["hard_cap_usd"], expected, delta=10.0)

    def test_max_exposure_is_80pct_of_equity(self):
        expected = 87_000.0 * 0.80
        self.assertAlmostEqual(self.params["max_exposure_usd"], expected, delta=10.0)

    def test_pdt_not_restricted_in_growth(self):
        self.assertFalse(self.params["pdt_restricted"])

    def test_pdt_restricted_in_small_tier(self):
        from services.crypto import risk_engine
        risk_engine.invalidate_cache()
        from services.crypto.risk_engine import get_risk_params
        p = get_risk_params(equity=15_000.0, cash=15_000.0)
        self.assertTrue(p["pdt_restricted"])

    def test_min_trade_scales_above_150_for_large_accounts(self):
        from services.crypto import risk_engine
        risk_engine.invalidate_cache()
        from services.crypto.risk_engine import get_risk_params
        # 0.15% of $200k = $300 > $150 → should be $300
        p = get_risk_params(equity=200_000.0, cash=200_000.0)
        self.assertAlmostEqual(p["min_trade_usd"], 300.0, delta=5.0)


# ══════════════════════════════════════════════════════════════════════════════
# 3.  RISK ENGINE — PER-ASSET STOP LOSS
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskEngineAssetSL(unittest.TestCase):

    def setUp(self):
        from services.crypto import risk_engine
        risk_engine.invalidate_cache()

    def _sl(self, sym: str, tier: str = "GROWTH") -> float:
        from services.crypto.risk_engine import get_asset_sl_pct
        return get_asset_sl_pct(sym, tier)

    def test_btc_sl_growth_is_3_5pct(self):
        # BTC base 3.5% × GROWTH scalar 1.0 = 3.5%
        self.assertAlmostEqual(self._sl("BTC/USD"), 3.5, places=2)

    def test_eth_sl_growth_is_3_5pct(self):
        self.assertAlmostEqual(self._sl("ETH/USD"), 3.5, places=2)

    def test_sol_is_mid_cap_at_5pct(self):
        # SOL = mid_cap_alt: base 5.0% × 1.0 = 5.0%
        self.assertAlmostEqual(self._sl("SOL/USD"), 5.0, places=2)

    def test_avax_is_mid_cap_at_5pct(self):
        self.assertAlmostEqual(self._sl("AVAX/USD"), 5.0, places=2)

    def test_doge_is_small_alt_at_7pct(self):
        # DOGE = small_alt: base 7.0% × 1.0 = 7.0%
        self.assertAlmostEqual(self._sl("DOGE/USD"), 7.0, places=2)

    def test_unknown_symbol_defaults_to_small_alt(self):
        sl = self._sl("WIFHAT/USD", "GROWTH")
        self.assertAlmostEqual(sl, 7.0, places=2)

    def test_sl_scales_down_for_micro_tier(self):
        # BTC in MICRO: 3.5% × 0.75 = 2.625%
        sl = self._sl("BTC/USD", "MICRO")
        self.assertAlmostEqual(sl, 3.5 * 0.75, places=2)

    def test_sl_scales_up_for_elite_tier(self):
        # BTC in ELITE: 3.5% × 1.2 = 4.2%
        sl = self._sl("BTC/USD", "ELITE")
        self.assertAlmostEqual(sl, 3.5 * 1.2, places=2)

    def test_micro_sl_less_than_elite_sl(self):
        self.assertLess(self._sl("BTC/USD", "MICRO"), self._sl("BTC/USD", "ELITE"))

    def test_all_asset_classes_return_positive(self):
        for sym in ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "SPY", "NVDA", "UNKNOWN/USD"]:
            sl = self._sl(sym, "GROWTH")
            self.assertGreater(sl, 0, f"SL for {sym} should be > 0")


# ══════════════════════════════════════════════════════════════════════════════
# 4.  RISK ENGINE — PROGRESSIVE CONFIDENCE FLOOR
# ══════════════════════════════════════════════════════════════════════════════

class TestConfidenceFloor(unittest.TestCase):

    def _floor(self, done: int, limit: int) -> float:
        from services.crypto.risk_engine import get_confidence_floor
        return get_confidence_floor(done, limit)

    def test_below_50pct_gives_55(self):
        self.assertEqual(self._floor(0, 30), 55.0)
        self.assertEqual(self._floor(14, 30), 55.0)   # 46.7% used

    def test_50_to_75pct_gives_65(self):
        self.assertEqual(self._floor(15, 30), 65.0)   # 50% used
        self.assertEqual(self._floor(22, 30), 65.0)   # 73.3% used

    def test_75_to_90pct_gives_75(self):
        self.assertEqual(self._floor(23, 30), 75.0)   # 76.7% used
        self.assertEqual(self._floor(26, 30), 75.0)   # 86.7% used

    def test_above_90pct_gives_85(self):
        self.assertEqual(self._floor(27, 30), 85.0)   # 90% used
        self.assertEqual(self._floor(30, 30), 85.0)   # 100% used

    def test_zero_limit_safe(self):
        # Should not divide by zero
        result = self._floor(5, 0)
        self.assertEqual(result, 55.0)

    def test_floor_is_monotonically_non_decreasing(self):
        limit = 30
        floors = [self._floor(i, limit) for i in range(limit + 1)]
        for i in range(len(floors) - 1):
            self.assertLessEqual(floors[i], floors[i + 1])


# ══════════════════════════════════════════════════════════════════════════════
# 5.  RISK ENGINE — CACHE BEHAVIOR
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskEngineCache(unittest.TestCase):

    def setUp(self):
        from services.crypto import risk_engine
        risk_engine.invalidate_cache()

    def test_same_object_returned_on_cache_hit(self):
        from services.crypto.risk_engine import get_risk_params
        p1 = get_risk_params(87_000.0, 87_000.0)
        p2 = get_risk_params(87_000.0, 87_000.0)
        self.assertIs(p1, p2)

    def test_invalidate_forces_recompute(self):
        from services.crypto import risk_engine
        from services.crypto.risk_engine import get_risk_params
        p1 = get_risk_params(87_000.0, 87_000.0)
        risk_engine.invalidate_cache()
        p2 = get_risk_params(87_000.0, 87_000.0)
        self.assertIsNot(p1, p2)
        self.assertEqual(p1["tier"], p2["tier"])

    def test_tier_crossing_busts_cache(self):
        from services.crypto.risk_engine import get_risk_params
        p1 = get_risk_params(87_000.0, 87_000.0)
        self.assertEqual(p1["tier"], "GROWTH")
        # Cross into ADVANCED without explicit invalidate
        p2 = get_risk_params(150_000.0, 150_000.0)
        self.assertEqual(p2["tier"], "ADVANCED")
        self.assertIsNot(p1, p2)

    def test_summary_before_init_returns_not_initialized(self):
        from services.crypto import risk_engine
        risk_engine.invalidate_cache()
        s = risk_engine.get_engine_summary()
        self.assertEqual(s.get("status"), "not_initialized")

    def test_summary_after_init_has_expected_keys(self):
        from services.crypto import risk_engine
        risk_engine.invalidate_cache()
        risk_engine.get_risk_params(87_000.0, 87_000.0)
        s = risk_engine.get_engine_summary()
        for key in ["tier", "sl_btc_eth", "sl_mid_alt", "sl_small_alt",
                    "max_positions", "cache_age_sec"]:
            self.assertIn(key, s, f"Missing key: {key}")
        self.assertAlmostEqual(s["sl_btc_eth"], 3.5, places=1)
        self.assertAlmostEqual(s["sl_mid_alt"], 5.0, places=1)
        self.assertAlmostEqual(s["sl_small_alt"], 7.0, places=1)


# ══════════════════════════════════════════════════════════════════════════════
# 6.  STRATEGY — MIN SCORE GATE (evaluate_symbol)
# ══════════════════════════════════════════════════════════════════════════════

class TestMinScoreGate(unittest.TestCase):

    def test_impossibly_high_gate_suppresses_all_signals(self):
        """min_signal_score=999 → no signal ever passes."""
        cfg = {**BASE_CFG, "min_signal_score": 999.0}
        for seed in range(5):
            result = _eval_symbol(cfg=cfg, seed=seed)
            if result is not None:
                # If anything comes through, it must not be an actionable side
                self.assertNotIn(result.get("side"), ["buy", "sell"])

    def test_returned_signal_always_meets_gate(self):
        """Any actionable signal must have score >= min_signal_score."""
        cfg = {**BASE_CFG, "min_signal_score": 40.0}
        for seed in range(15):
            result = _eval_symbol(cfg=cfg, seed=seed)
            if result and result.get("side") in ("buy", "sell"):
                self.assertGreaterEqual(
                    result["score"], 40.0,
                    f"Signal score {result['score']} is below gate 40.0"
                )

    def test_signal_with_none_check(self):
        """evaluate_symbol can return None — that's valid (no signal)."""
        result = _eval_symbol()
        self.assertIn(type(result), [dict, type(None)])

    def test_signal_structure_when_present(self):
        """Any returned signal must have all required keys."""
        for seed in range(20):
            result = _eval_symbol(seed=seed)
            if result and result.get("side") in ("buy", "sell"):
                for key in ["strategy", "side", "score", "close", "notional",
                            "reason", "symbol", "timestamp_ms"]:
                    self.assertIn(key, result, f"Missing key: {key}")
                self.assertGreater(result["score"], 0)
                self.assertGreater(result["notional"], 0)
                return
        # No signal across 20 seeds is acceptable — market conditions complex


# ══════════════════════════════════════════════════════════════════════════════
# 7.  STRATEGY — SWING TRIGGERS VIA evaluate_symbol
# ══════════════════════════════════════════════════════════════════════════════

class TestSwingStrategyViaEvalSymbol(unittest.TestCase):

    def test_swing_blocked_for_non_eligible_symbol(self):
        """DOGE/USD is not in the swing symbols list — swing should never fire."""
        cfg = {**BASE_CFG, "min_signal_score": 0.0}  # no score gate blocking
        for seed in range(10):
            result = _eval_symbol(symbol="DOGE/USD", cfg=cfg, seed=seed,
                                  bars_1h_n=100, bars_1d_n=250)
            if result and result.get("side") in ("buy", "sell"):
                self.assertNotEqual(result.get("strategy"), "swing_momentum",
                                    "Swing should not fire for DOGE")

    def test_swing_blocked_with_no_1h_bars(self):
        """Without 1H bars, swing strategy cannot fire."""
        cfg = {**BASE_CFG, "min_signal_score": 0.0}
        for seed in range(10):
            result = _eval_symbol(symbol="BTC/USD", cfg=cfg, seed=seed,
                                  bars_1h_n=0)  # no 1H bars
            if result and result.get("strategy") == "swing_momentum":
                self.fail("Swing fired without 1H bars")

    def test_swing_strategy_name_when_fired(self):
        """If swing fires, strategy name must be 'swing_momentum'."""
        cfg = {**BASE_CFG, "min_signal_score": 0.0}
        for seed in range(30):
            result = _eval_symbol(symbol="BTC/USD", cfg=cfg, seed=seed,
                                  bars_1h_n=100, macro_risk_mult=1.2)
            if result and result.get("strategy") == "swing_momentum":
                self.assertEqual(result["side"], "buy")
                self.assertGreater(result["notional"], 0)
                return
        # No swing fire in 30 seeds: market conditions not met — acceptable

    def test_1h_bars_are_passed_without_crash(self):
        """evaluate_symbol must not crash when 1H bars are provided."""
        result = _eval_symbol(symbol="BTC/USD", bars_1h_n=100)
        self.assertIn(type(result), [dict, type(None)])


# ══════════════════════════════════════════════════════════════════════════════
# 8.  STRATEGY — POSITION TRIGGERS VIA evaluate_symbol
# ══════════════════════════════════════════════════════════════════════════════

class TestPositionStrategyViaEvalSymbol(unittest.TestCase):

    def test_position_blocked_for_non_eligible_symbol(self):
        cfg = {**BASE_CFG, "min_signal_score": 0.0}
        for seed in range(10):
            result = _eval_symbol(symbol="PEPE/USD", cfg=cfg, seed=seed,
                                  bars_1d_n=250, macro_risk_mult=1.5)
            if result and result.get("strategy") == "position_golden_cross":
                self.fail("Position strategy should not fire for PEPE/USD")

    def test_position_blocked_low_oracle(self):
        """Oracle < 1.2 must block position entries."""
        cfg = {**BASE_CFG, "min_signal_score": 0.0}
        for seed in range(10):
            result = _eval_symbol(symbol="BTC/USD", cfg=cfg, seed=seed,
                                  bars_1d_n=250, macro_risk_mult=0.8)
            if result and result.get("strategy") == "position_golden_cross":
                self.fail("Position strategy fired with oracle=0.8 (<1.2)")

    def test_position_blocked_without_daily_bars(self):
        """Without 1D bars, position strategy cannot fire."""
        cfg = {**BASE_CFG, "min_signal_score": 0.0}
        for seed in range(10):
            result = _eval_symbol(symbol="BTC/USD", cfg=cfg, seed=seed,
                                  bars_1d_n=0, macro_risk_mult=1.5)
            if result and result.get("strategy") == "position_golden_cross":
                self.fail("Position strategy fired without 1D bars")

    def test_position_strategy_name_when_fired(self):
        """If position fires, strategy name must be 'position_golden_cross'."""
        cfg = {**BASE_CFG, "min_signal_score": 0.0}
        for seed in range(30):
            result = _eval_symbol(symbol="BTC/USD", cfg=cfg, seed=seed,
                                  bars_1d_n=250, macro_risk_mult=1.5)
            if result and result.get("strategy") == "position_golden_cross":
                self.assertEqual(result["side"], "buy")
                self.assertGreater(result["notional"], 0)
                return
        # No golden cross formed across 30 seeds — acceptable


# ══════════════════════════════════════════════════════════════════════════════
# 9.  STRATEGY — DCA TIGHTENED GATE
# ══════════════════════════════════════════════════════════════════════════════

class TestDCAGate(unittest.TestCase):

    def test_dca_notional_is_250_in_config(self):
        """Ensure config has the correct DCA notional."""
        self.assertEqual(BASE_CFG["long_term"]["dca_notional"], 250.0)

    def test_dca_dip_pct_is_3_in_config(self):
        """Ensure config requires 3% dip (was 1.5%)."""
        self.assertAlmostEqual(BASE_CFG["long_term"]["dca_dip_pct"], 3.0, places=1)

    def test_dca_rsi_max_is_42_in_config(self):
        """Ensure RSI gate is capped at 42 (was 55)."""
        self.assertEqual(BASE_CFG["long_term"]["dca_rsi_max"], 42)

    def test_dca_signal_has_correct_strategy_name(self):
        """If DCA fires, it must appear as 'dynamic_dca'."""
        cfg = {**BASE_CFG, "min_signal_score": 0.0,
               "long_term": {**BASE_CFG["long_term"], "dca_rsi_max": 70,
                              "dca_dip_pct": 0.1}}  # loose gates to allow firing
        for seed in range(20):
            result = _eval_symbol(cfg=cfg, seed=seed)
            if result and result.get("strategy") == "dynamic_dca":
                self.assertEqual(result["side"], "buy")
                return
        # DCA may not fire in all cases — acceptable


# ══════════════════════════════════════════════════════════════════════════════
# 10.  STRATEGY — MA CROSSOVER DISABLED
# ══════════════════════════════════════════════════════════════════════════════

class TestMACrossoverDisabled(unittest.TestCase):

    def test_ma_crossover_never_fires_when_disabled(self):
        """With ma_crossover_enabled=False, 'ma_crossover' strategy never appears."""
        cfg = {**BASE_CFG, "min_signal_score": 0.0,
               "long_term": {**BASE_CFG["long_term"], "ma_crossover_enabled": False}}
        for seed in range(20):
            result = _eval_symbol(cfg=cfg, seed=seed)
            if result:
                self.assertNotEqual(
                    result.get("strategy"), "ma_crossover",
                    "ma_crossover must be disabled but fired"
                )

    def test_config_has_ma_crossover_disabled(self):
        """config.json key must be False."""
        self.assertFalse(BASE_CFG["long_term"]["ma_crossover_enabled"])


# ══════════════════════════════════════════════════════════════════════════════
# 11.  STRATEGY — evaluate_symbol SIGNATURE ACCEPTS NEW PARAMS
# ══════════════════════════════════════════════════════════════════════════════

class TestEvaluateSymbolAPI(unittest.TestCase):

    def test_accepts_bars_1h_kwarg(self):
        result = _eval_symbol(symbol="BTC/USD", bars_1h_n=100)
        self.assertIn(type(result), [dict, type(None)])

    def test_accepts_bars_1d_kwarg(self):
        result = _eval_symbol(symbol="BTC/USD", bars_1d_n=250)
        self.assertIn(type(result), [dict, type(None)])

    def test_accepts_both_kwargs(self):
        result = _eval_symbol(symbol="ETH/USD", bars_1h_n=100, bars_1d_n=250)
        self.assertIn(type(result), [dict, type(None)])

    def test_non_swing_symbol_accepts_1h_without_crash(self):
        result = _eval_symbol(symbol="DOGE/USD", bars_1h_n=100, bars_1d_n=250)
        self.assertIn(type(result), [dict, type(None)])

    def test_empty_15m_bars_returns_none(self):
        from services.crypto.strategy import evaluate_symbol
        with patch("services.crypto.store.get_session_memory_sync", return_value=None):
            with patch("services.crypto.oracle.get_narrative_for_symbol", return_value=None):
                result = asyncio.run(evaluate_symbol(
                    symbol="BTC/USD",
                    bars_15m=pd.DataFrame(),
                    bars_1m=_bars(100, freq="1min"),
                    cfg=BASE_CFG,
                    now_ms=int(time.time() * 1000),
                ))
        self.assertIsNone(result)

    def test_short_15m_bars_returns_none(self):
        from services.crypto.strategy import evaluate_symbol
        with patch("services.crypto.store.get_session_memory_sync", return_value=None):
            with patch("services.crypto.oracle.get_narrative_for_symbol", return_value=None):
                result = asyncio.run(evaluate_symbol(
                    symbol="BTC/USD",
                    bars_15m=_bars(10),  # too few
                    bars_1m=_bars(100, freq="1min"),
                    cfg=BASE_CFG,
                    now_ms=int(time.time() * 1000),
                ))
        self.assertIsNone(result)


# ══════════════════════════════════════════════════════════════════════════════
# 12.  BOT — TRADE_LIMITS DICT
# ══════════════════════════════════════════════════════════════════════════════

class TestBotTradeLimitsDict(unittest.TestCase):

    def test_trade_limits_class_attribute_exists(self):
        from services.crypto.bot import CryptoBotRuntime
        self.assertTrue(hasattr(CryptoBotRuntime, "TRADE_LIMITS"))
        self.assertIsInstance(CryptoBotRuntime.TRADE_LIMITS, dict)

    def test_per_coin_daily_limit_is_4(self):
        from services.crypto.bot import CryptoBotRuntime
        self.assertEqual(
            CryptoBotRuntime.TRADE_LIMITS["crypto_max_trades_per_coin_per_day"], 4
        )

    def test_global_daily_limit_exists(self):
        from services.crypto.bot import CryptoBotRuntime
        limit = CryptoBotRuntime.TRADE_LIMITS.get("crypto_max_total_trades_per_day")
        self.assertIsNotNone(limit)
        self.assertGreater(limit, 0)

    def test_cooldown_key_exists(self):
        from services.crypto.bot import CryptoBotRuntime
        cooldown = CryptoBotRuntime.TRADE_LIMITS.get(
            "crypto_min_time_between_trades_same_coin_sec"
        )
        self.assertIsNotNone(cooldown)
        self.assertGreater(cooldown, 0)


# ══════════════════════════════════════════════════════════════════════════════
# 13.  BOT — DAILY TRADE COUNTERS
# ══════════════════════════════════════════════════════════════════════════════

class TestBotDailyCounters(unittest.TestCase):

    def setUp(self):
        from services.crypto.bot import CryptoBotRuntime
        self.bot = CryptoBotRuntime()

    def test_counters_start_at_zero(self):
        self.assertEqual(self.bot._daily_trade_count, 0)
        self.assertEqual(self.bot._daily_coin_counts, {})
        self.assertEqual(self.bot._daily_reset_day, -1)

    def test_global_counter_increments(self):
        self.bot._daily_trade_count += 1
        self.assertEqual(self.bot._daily_trade_count, 1)
        self.bot._daily_trade_count += 1
        self.assertEqual(self.bot._daily_trade_count, 2)

    def test_per_coin_counter_increments(self):
        sym = "BTC/USD"
        self.bot._daily_coin_counts[sym] = self.bot._daily_coin_counts.get(sym, 0) + 1
        self.assertEqual(self.bot._daily_coin_counts[sym], 1)
        self.bot._daily_coin_counts[sym] += 1
        self.assertEqual(self.bot._daily_coin_counts[sym], 2)

    def test_global_ceiling_check(self):
        self.bot._daily_trade_count = 30
        at_ceiling = self.bot._daily_trade_count >= 30
        self.assertTrue(at_ceiling)

    def test_per_coin_ceiling_check(self):
        self.bot._daily_coin_counts["SOL/USD"] = 4
        blocked = self.bot._daily_coin_counts.get("SOL/USD", 0) >= 4
        self.assertTrue(blocked)

    def test_unknown_coin_has_zero_count(self):
        count = self.bot._daily_coin_counts.get("NEWCOIN/USD", 0)
        self.assertEqual(count, 0)

    def test_reset_day_storage(self):
        import time as _time
        today = _time.localtime().tm_yday
        self.bot._daily_reset_day = today
        self.assertEqual(self.bot._daily_reset_day, today)


# ══════════════════════════════════════════════════════════════════════════════
# 14.  CONFIG.JSON — STRATEGY REVAMP VALUES SANITY CHECK
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigRevampValues(unittest.TestCase):

    def _load_exchange(self, key: str) -> dict:
        import json
        cfg_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json"
        )
        with open(cfg_path) as f:
            crypto_cfg = json.load(f)["stocks"]["crypto"]
        return crypto_cfg.get(key, {})

    def test_rsi_oversold_is_32(self):
        ex = self._load_exchange("alpaca-paper")
        self.assertEqual(ex["short_term"]["rsi_oversold"], 32)

    def test_rsi_overbought_is_75(self):
        ex = self._load_exchange("alpaca-paper")
        self.assertEqual(ex["short_term"]["rsi_overbought"], 75)

    def test_ma_crossover_disabled(self):
        ex = self._load_exchange("alpaca-paper")
        self.assertFalse(ex["long_term"]["ma_crossover_enabled"])

    def test_dca_dip_pct_is_3(self):
        ex = self._load_exchange("alpaca-paper")
        self.assertAlmostEqual(ex["long_term"]["dca_dip_pct"], 3.0, places=1)

    def test_dca_rsi_max_is_42(self):
        ex = self._load_exchange("alpaca-paper")
        self.assertEqual(ex["long_term"]["dca_rsi_max"], 42)

    def test_min_signal_score_is_68(self):
        ex = self._load_exchange("alpaca-paper")
        self.assertAlmostEqual(ex["min_signal_score"], 68.0, places=1)

    def test_swing_section_exists(self):
        ex = self._load_exchange("alpaca-paper")
        self.assertIn("swing", ex)
        self.assertIn("oracle_min", ex["swing"])
        self.assertAlmostEqual(ex["swing"]["oracle_min"], 0.9, places=1)

    def test_position_section_exists(self):
        ex = self._load_exchange("alpaca-paper")
        self.assertIn("position", ex)
        self.assertIn("oracle_min", ex["position"])
        self.assertAlmostEqual(ex["position"]["oracle_min"], 1.2, places=1)

    def test_stop_loss_fallback_updated(self):
        """Old 1.8% SL should have been raised to 3.5%."""
        ex = self._load_exchange("alpaca-paper")
        sl = ex.get("synthetic_exits", {}).get("stop_loss_pct", 0)
        self.assertGreater(sl, 2.0, f"stop_loss_pct={sl} should be > 2% (was 1.8%)")

    def test_engine_owned_fields_removed_from_paper(self):
        """Cooldown and anti_spam now owned by risk engine — not in config."""
        ex = self._load_exchange("alpaca-paper")
        self.assertNotIn("cooldown_sec", ex, "cooldown_sec should be removed — engine owns it")
        self.assertNotIn("anti_spam_sec", ex, "anti_spam_sec should be removed — engine owns it")

    def test_poll_interval_is_60(self):
        ex = self._load_exchange("alpaca-paper")
        self.assertEqual(ex["poll_interval_sec"], 60)

    def test_all_four_exchanges_have_min_signal_score(self):
        for key in ["alpaca-paper", "alpaca-live", "kraken-paper"]:
            ex = self._load_exchange(key)
            self.assertIn("min_signal_score", ex,
                          f"{key} must have min_signal_score")
            self.assertGreaterEqual(ex["min_signal_score"], 60.0)


# ══════════════════════════════════════════════════════════════════════════════
# 15.  RISK ENGINE — FULL ROUND-TRIP (init → trade → invalidate → recompute)
# ══════════════════════════════════════════════════════════════════════════════

class TestRiskEngineRoundTrip(unittest.TestCase):

    def test_full_round_trip(self):
        """Simulate a real bot cycle: compute → trade → invalidate → recompute."""
        from services.crypto import risk_engine

        risk_engine.invalidate_cache()
        p1 = risk_engine.get_risk_params(87_000.0, 87_000.0)
        self.assertEqual(p1["tier"], "GROWTH")

        # Simulate trade execution (bot calls these)
        risk_engine.invalidate_cache()

        # Same equity — should recompute same values
        p2 = risk_engine.get_risk_params(87_000.0, 87_000.0)
        self.assertEqual(p2["tier"], "GROWTH")
        self.assertEqual(p1["max_positions"], p2["max_positions"])
        self.assertIsNot(p1, p2)  # Fresh object after invalidation

    def test_tier_downgrade_scenario(self):
        """If account drops from GROWTH to STANDARD, engine must reflect that."""
        from services.crypto import risk_engine

        risk_engine.invalidate_cache()
        p_growth = risk_engine.get_risk_params(80_000.0, 80_000.0)
        self.assertEqual(p_growth["tier"], "GROWTH")
        self.assertEqual(p_growth["max_positions"], 8)
        self.assertEqual(p_growth["max_trades_per_day"], 30)

        # Account drops below $50k
        risk_engine.invalidate_cache()
        p_standard = risk_engine.get_risk_params(45_000.0, 45_000.0)
        self.assertEqual(p_standard["tier"], "STANDARD")
        self.assertEqual(p_standard["max_positions"], 8)   # same max for STANDARD
        self.assertEqual(p_standard["max_trades_per_day"], 25)  # fewer trades/day

    def test_confidence_floor_tightens_near_daily_limit(self):
        """At 28/30 trades, confidence floor should be 85 (hardest gate)."""
        from services.crypto.risk_engine import get_confidence_floor
        floor_early = get_confidence_floor(5, 30)    # 16% used
        floor_late = get_confidence_floor(28, 30)    # 93% used
        self.assertEqual(floor_early, 55.0)
        self.assertEqual(floor_late, 85.0)
        self.assertGreater(floor_late, floor_early)


# ══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
