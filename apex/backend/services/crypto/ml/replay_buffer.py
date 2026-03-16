import json
import os
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# Path to the experience replay log file
EXPERIENCES_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "live_experiences.jsonl")
)

class ReplayBuffer:
    """
    Experience Replay Buffer for Online Reinforcement Learning.
    
    This class handles the ingestion of live trading experiences (wins/losses)
    directly from the bot's execution engine. It stores the exact feature state 
    at the moment of the trade, alongside the final realized PNL, allowing 
    the PPO network to rigorously penalize itself for poor live macro decisions.
    """
    
    @staticmethod
    def _ensure_file():
        os.makedirs(os.path.dirname(EXPERIENCES_FILE), exist_ok=True)
        if not os.path.exists(EXPERIENCES_FILE):
            # Create the file if it doesn't exist
            with open(EXPERIENCES_FILE, "a") as f:
                pass

    _active_entries: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def cache_live_entry(cls, symbol: str, feature_state: Dict[str, Any]) -> None:
        """
        Cache the mathematical state of the market exactly when the bot decides to open a position.
        """
        cls._active_entries[symbol] = feature_state

    @classmethod
    def commit_experience(
        cls,
        symbol: str, 
        side: str, 
        pnl_pct: float, 
        trade_duration_sec: float
    ) -> None:
        """
        Finalize a live trading outcome by pulling the cached feature state and writing it to the replay buffer.
        
        Args:
            symbol: The asset ticker (e.g. BTC_USD)
            side: 'buy' or 'sell'
            pnl_pct: The realized profit/loss percentage (e.g. 5.2 or -3.1)
            trade_duration_sec: How long the trade was held before closing.
        """
        cls._ensure_file()
        
        feature_state = cls._active_entries.pop(symbol, {})
        
        experience = {
            "symbol": symbol,
            "side": side,
            "pnl_pct": pnl_pct,
            "trade_duration_sec": trade_duration_sec,
            "feature_state": feature_state
        }
        
        try:
            with open(EXPERIENCES_FILE, "a") as f:
                f.write(json.dumps(experience) + "\n")
            logger.info(f"💾 Recorded Experience Replay for {symbol} | PNL: {pnl_pct:.2f}% | Features Captured: {bool(feature_state)}")
        except Exception as e:
            logger.error(f"Failed to record replay experience: {e}")

    @staticmethod
    def load_experiences(symbol: str = None) -> List[Dict[str, Any]]:
        """
        Loads the experience buffer for the ML training pipeline.
        
        Args:
            symbol: Optional filter to only load experiences for a specific asset.
        """
        ReplayBuffer._ensure_file()
        
        experiences = []
        try:
            with open(EXPERIENCES_FILE, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        exp = json.loads(line)
                        if symbol and exp.get("symbol") != symbol:
                            continue
                        experiences.append(exp)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"Failed to load replay experiences: {e}")
            
        return experiences
