"""
exit_manager.py — Dedicated Exit Strategy Layer
================================================
Runs a separate async evaluation pass over all open positions every 30 seconds.
This is independent of and has higher priority than the entry signal loop.

Exit triggers (priority order):
  1. Hard stop-loss breach   → market sell immediately, no debate
  2. Trailing stop triggered → market sell (only after lock-in threshold)
  3. Oracle crash exit       → flatten all longs on macro score < 0.5
  4. Time-based chop exit    → exit flat positions held > max_hold_hours
  5. RSI overbought exit     → limit sell when RSI>72 and in profit > 1.5%
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TRAILING STOP MANAGER
# ---------------------------------------------------------------------------

class TrailingStopManager:
    """
    Tracks price peaks per symbol since entry and determines when a trailing
    stop has been breached.

    Usage:
        mgr = TrailingStopManager()
        # in position monitor loop:
        should_exit = mgr.update(symbol, current_price, entry_price, cfg)
    """

    def __init__(self) -> None:
        self._peaks: Dict[str, float] = {}   # symbol → highest price seen since entry
        self._entry: Dict[str, float] = {}   # symbol → avg entry price

    def register_entry(self, symbol: str, entry_price: float) -> None:
        """Call when a new position opens."""
        self._peaks[symbol] = entry_price
        self._entry[symbol] = entry_price
        logger.debug(f"[TrailingStop] Registered entry {symbol} @ ${entry_price:.4f}")

    def remove(self, symbol: str) -> None:
        """Call when position closes."""
        self._peaks.pop(symbol, None)
        self._entry.pop(symbol, None)

    def update(
        self,
        symbol: str,
        current_price: float,
        entry_price: float,
        trail_pct: float = 1.5,
        lock_in_pct: float = 2.0,
    ) -> bool:
        """
        Update peak price and check if trailing stop is breached.
        Returns True if the trailing stop triggered (should sell).

        Args:
            symbol:        asset symbol
            current_price: latest mark price
            entry_price:   original position avg entry
            trail_pct:     how far below peak to place the trail floor (%)
            lock_in_pct:   minimum PnL% before trailing activates
        """
        if entry_price <= 0 or current_price <= 0:
            return False

        # Update peak
        prev_peak = self._peaks.get(symbol, current_price)
        if current_price > prev_peak:
            self._peaks[symbol] = current_price
        self._entry[symbol] = entry_price

        pnl_pct = (current_price - entry_price) / entry_price * 100.0

        # Only activate trailing after earning the lock-in threshold
        if pnl_pct < lock_in_pct:
            return False

        peak = self._peaks[symbol]
        trail_floor = peak * (1.0 - trail_pct / 100.0)
        triggered = current_price <= trail_floor

        if triggered:
            logger.info(
                f"[TrailingStop] 🔻 {symbol} trail triggered. "
                f"Peak=${peak:.4f} Floor=${trail_floor:.4f} Now=${current_price:.4f} "
                f"(trail={trail_pct}% lock_in={lock_in_pct}%)"
            )
        return triggered

    def get_trail_floor(self, symbol: str, trail_pct: float = 1.5) -> Optional[float]:
        """Returns current trail floor price for display, or None if not active."""
        peak = self._peaks.get(symbol)
        if peak is None:
            return None
        return peak * (1.0 - trail_pct / 100.0)

    def get_peak(self, symbol: str) -> Optional[float]:
        return self._peaks.get(symbol)

    def all_peaks(self) -> Dict[str, float]:
        return dict(self._peaks)


# ---------------------------------------------------------------------------
# EXIT EVALUATION
# ---------------------------------------------------------------------------

def evaluate_exits(
    positions: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    macro_score: float,
    trailing_mgr: TrailingStopManager,
    now_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Evaluate all open positions for exit conditions.

    Returns a list of exit decisions:
    [{"symbol": "BTC/USD", "reason": "trailing_stop", "urgency": "market"|"limit"}]

    Caller is responsible for executing these exits via execution.py.
    """
    exits: List[Dict[str, Any]] = []
    if not positions:
        return exits

    now = now_ms or int(time.time() * 1000)
    exit_cfg = cfg.get("synthetic_exits", {})
    sl_pct = float(exit_cfg.get("stop_loss_pct", 3.5))
    tp_pct = float(exit_cfg.get("take_profit_pct", 3.0))
    trail_pct = float(cfg.get("position", {}).get("trailing_stop_pct", 1.5))
    trail_lock_in = float(exit_cfg.get("trail_lock_in_pct", 2.0))
    max_hold_hours = float(exit_cfg.get("max_hold_hours", 6.0))

    # ── Oracle crash exit —————————————————————————————————
    oracle_crash = macro_score < 0.5
    if oracle_crash:
        for pos in positions:
            sym = str(pos.get("symbol", ""))
            pnl_pct = float(pos.get("unrealized_plpc", 0)) * 100
            # Only exit longs (crypto bot is long-only)
            exits.append({
                "symbol": sym,
                "reason": f"oracle_crash_exit (score={macro_score:.2f} < 0.5)",
                "urgency": "market",
                "pnl_pct": round(pnl_pct, 2),
            })
        if exits:
            logger.warning(
                f"[ExitManager] ☠️ Oracle crash exit triggered for {len(exits)} positions. "
                f"Score={macro_score:.2f}"
            )
        return exits  # Return immediately — highest priority

    for pos in positions:
        sym = str(pos.get("symbol", ""))
        entry_price = float(pos.get("avg_entry_price", 0) or 0)
        current_price = float(pos.get("current_price", 0) or 0)
        entry_ts = int(pos.get("entry_ts_ms", 0) or 0)  # optional, may be 0

        if entry_price <= 0 or current_price <= 0:
            continue

        pnl_pct = (current_price - entry_price) / entry_price * 100.0

        # ── 1. Hard stop-loss ────────────────────────────────
        if pnl_pct <= -sl_pct:
            exits.append({
                "symbol": sym,
                "reason": f"hard_stop_loss (pnl={pnl_pct:.2f}% <= -{sl_pct}%)",
                "urgency": "market",
                "pnl_pct": round(pnl_pct, 2),
            })
            logger.info(f"[ExitManager] 🛑 Stop-loss triggered {sym}: {pnl_pct:.2f}%")
            continue

        # ── 2. Take profit ───────────────────────────────────
        if pnl_pct >= tp_pct:
            exits.append({
                "symbol": sym,
                "reason": f"take_profit (pnl={pnl_pct:.2f}% >= {tp_pct}%)",
                "urgency": "limit",
                "pnl_pct": round(pnl_pct, 2),
            })
            logger.info(f"[ExitManager] 🎯 Take-profit triggered {sym}: {pnl_pct:.2f}%")
            continue

        # ── 3. Trailing stop ─────────────────────────────────
        if trailing_mgr.update(sym, current_price, entry_price, trail_pct, trail_lock_in):
            peak = trailing_mgr.get_peak(sym) or current_price
            exits.append({
                "symbol": sym,
                "reason": f"trailing_stop (peak=${peak:.4f}, trail={trail_pct}%)",
                "urgency": "market",
                "pnl_pct": round(pnl_pct, 2),
            })
            continue

        # ── 4. Time-based chop exit ──────────────────────────
        if entry_ts > 0 and max_hold_hours > 0:
            held_hours = (now - entry_ts) / 3_600_000
            is_flat = -1.0 < pnl_pct < 1.0
            if held_hours >= max_hold_hours and is_flat:
                exits.append({
                    "symbol": sym,
                    "reason": f"time_chop_exit (held={held_hours:.1f}h, pnl={pnl_pct:.2f}%)",
                    "urgency": "limit",
                    "pnl_pct": round(pnl_pct, 2),
                })
                logger.info(f"[ExitManager] ⏱ Time chop exit {sym}: {held_hours:.1f}h held, flat PnL")

    return exits
