"""
Kalshi Scalper Service — S&P 500 Close Contract Scalping Engine.

Monitors the S&P 500 index price via Alpaca data stream and compares it
to Kalshi's daily close contracts. Detects momentum shifts in the final
30-minute window and emits real-time scalp signals.
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("apex.kalshi.scalper")


@dataclass
class PriceSnapshot:
    price: float
    timestamp: float


@dataclass
class ScalpSignal:
    direction: str  # 'BUY_YES' or 'BUY_NO'
    confidence: float  # 0-1
    contract_ticker: str
    strike_level: float
    current_price: float
    momentum: float  # price change rate per second
    reasoning: str


class KalshiScalper:
    """
    Real-time S&P 500 scalping engine for Kalshi daily close contracts.
    
    Strategy:
    - Track S&P 500 price movement in the final 30 min before close (3:30-4:00 PM ET)
    - Identify momentum shifts (price accelerating toward or away from strike levels)
    - Emit BUY_YES when price trending above strike, BUY_NO when trending below
    - Confidence based on momentum strength and distance from strike
    """

    def __init__(self):
        self.price_history: List[PriceSnapshot] = []
        self.max_history = 300  # ~5 min of 1s snapshots
        self.active_contracts: List[Dict[str, Any]] = []
        self.is_running = False
        self.last_signal: Optional[ScalpSignal] = None
        self.stats = {
            "signals_emitted": 0,
            "prices_processed": 0,
            "session_start": None,
        }

    def add_price(self, price: float, timestamp: Optional[float] = None) -> None:
        """Record a new S&P 500 price tick."""
        ts = timestamp or time.time()
        self.price_history.append(PriceSnapshot(price=price, timestamp=ts))
        self.stats["prices_processed"] += 1

        # Trim old data
        if len(self.price_history) > self.max_history:
            self.price_history = self.price_history[-self.max_history:]

    def calculate_momentum(self, window: int = 30) -> float:
        """
        Calculate price momentum over the given window (in samples).
        Returns price change per second (positive = bullish, negative = bearish).
        """
        if len(self.price_history) < 2:
            return 0.0

        # Use the last N samples
        samples = self.price_history[-min(window, len(self.price_history)):]
        if len(samples) < 2:
            return 0.0

        price_delta = samples[-1].price - samples[0].price
        time_delta = samples[-1].timestamp - samples[0].timestamp

        if time_delta == 0:
            return 0.0

        return price_delta / time_delta

    def calculate_volatility(self, window: int = 60) -> float:
        """Calculate recent price volatility (standard deviation of returns)."""
        if len(self.price_history) < 3:
            return 0.0

        samples = self.price_history[-min(window, len(self.price_history)):]
        prices = [s.price for s in samples]
        returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]

        if not returns:
            return 0.0

        mean_ret = sum(returns) / len(returns)
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
        return variance ** 0.5

    def set_contracts(self, contracts: List[Dict[str, Any]]) -> None:
        """
        Set the active Kalshi contracts to monitor.
        Each contract should have: ticker, strike_level, yes_price, no_price
        """
        self.active_contracts = contracts
        logger.info(f"Loaded {len(contracts)} contracts for scalping")

    def generate_signals(self) -> List[ScalpSignal]:
        """
        Analyze current momentum against all active contracts and
        generate scalp signals where edge exists.
        """
        if not self.price_history or not self.active_contracts:
            return []

        current_price = self.price_history[-1].price
        momentum_short = self.calculate_momentum(window=10)  # ~10s trend
        momentum_medium = self.calculate_momentum(window=30)  # ~30s trend
        momentum_long = self.calculate_momentum(window=60)   # ~60s trend
        volatility = self.calculate_volatility()

        signals = []

        for contract in self.active_contracts:
            strike = contract.get("strike_level", 0)
            ticker = contract.get("ticker", "")
            yes_price = contract.get("yes_price", 50)  # cents

            if not strike or not ticker:
                continue

            # Distance from strike as a percentage
            distance = (current_price - strike) / strike
            distance_pct = abs(distance) * 100

            # Determine direction prediction
            if momentum_short > 0 and momentum_medium > 0:
                # Price trending up
                if current_price > strike:
                    # Above strike and climbing → strong YES
                    direction = "BUY_YES"
                    confidence = min(0.95, 0.5 + (momentum_short * 100) + (distance_pct * 0.1))
                    reasoning = f"Price ${current_price:.2f} above {strike}, momentum +{momentum_short*100:.2f}pts/s"
                else:
                    # Below strike but climbing → moderate YES
                    direction = "BUY_YES"
                    confidence = min(0.7, 0.3 + (momentum_short * 50))
                    reasoning = f"Price ${current_price:.2f} approaching {strike} from below, momentum +{momentum_short*100:.2f}pts/s"
            elif momentum_short < 0 and momentum_medium < 0:
                # Price trending down
                if current_price < strike:
                    # Below strike and falling → strong NO
                    direction = "BUY_NO"
                    confidence = min(0.95, 0.5 + (abs(momentum_short) * 100) + (distance_pct * 0.1))
                    reasoning = f"Price ${current_price:.2f} below {strike}, momentum {momentum_short*100:.2f}pts/s"
                else:
                    # Above strike but falling → moderate NO
                    direction = "BUY_NO"
                    confidence = min(0.7, 0.3 + (abs(momentum_short) * 50))
                    reasoning = f"Price ${current_price:.2f} dropping toward {strike}, momentum {momentum_short*100:.2f}pts/s"
            else:
                # Mixed signals — low confidence
                continue

            # Only emit signals with reasonable confidence
            if confidence > 0.4:
                # Apply value filter — only trade if contract is mispriced
                implied_prob = yes_price / 100
                if direction == "BUY_YES" and confidence > implied_prob + 0.05:
                    signals.append(ScalpSignal(
                        direction=direction,
                        confidence=round(confidence, 3),
                        contract_ticker=ticker,
                        strike_level=strike,
                        current_price=current_price,
                        momentum=round(momentum_short, 6),
                        reasoning=reasoning,
                    ))
                elif direction == "BUY_NO" and (1 - confidence) < implied_prob - 0.05:
                    signals.append(ScalpSignal(
                        direction=direction,
                        confidence=round(confidence, 3),
                        contract_ticker=ticker,
                        strike_level=strike,
                        current_price=current_price,
                        momentum=round(momentum_short, 6),
                        reasoning=reasoning,
                    ))

        self.stats["signals_emitted"] += len(signals)
        if signals:
            self.last_signal = signals[0]
        return signals

    def get_dashboard_data(self) -> Dict[str, Any]:
        """Return current state for the frontend dashboard."""
        current_price = self.price_history[-1].price if self.price_history else None
        momentum = self.calculate_momentum()
        volatility = self.calculate_volatility()

        return {
            "current_price": current_price,
            "momentum": round(momentum, 6),
            "momentum_direction": "bullish" if momentum > 0 else "bearish" if momentum < 0 else "neutral",
            "volatility": round(volatility, 8),
            "price_count": len(self.price_history),
            "contracts": self.active_contracts,
            "last_signal": {
                "direction": self.last_signal.direction,
                "confidence": self.last_signal.confidence,
                "contract_ticker": self.last_signal.contract_ticker,
                "ticker": self.last_signal.contract_ticker,  # backward compatibility
                "strike_level": self.last_signal.strike_level,
                "current_price": self.last_signal.current_price,
                "momentum": self.last_signal.momentum,
                "reasoning": self.last_signal.reasoning,
            } if self.last_signal else None,
            "stats": self.stats,
        }


# ── Singleton Instance ──
_scalper = KalshiScalper()


def get_scalper() -> KalshiScalper:
    return _scalper
