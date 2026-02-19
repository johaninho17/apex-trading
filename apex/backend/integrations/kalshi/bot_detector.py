"""
Bot detection heuristics for analyzing Kalshi accounts
"""
import logging
from typing import Dict, List, Tuple
from datetime import datetime, timedelta
from collections import Counter
import numpy as np
from config import Config

logger = logging.getLogger(__name__)


class BotDetector:
    """Detects automated trading bots based on account behavior"""
    
    def __init__(self, min_trades: int = None, bot_threshold: float = None):
        """
        Initialize bot detector
        
        Args:
            min_trades: Minimum trades required for analysis
            bot_threshold: Score threshold for bot classification (0-1)
        """
        self.min_trades = min_trades or Config.MIN_TRADES_FOR_ANALYSIS
        self.bot_threshold = bot_threshold or Config.BOT_SCORE_THRESHOLD
    
    def analyze_account(self, trades: List[Dict], positions: List[Dict] = None) -> Dict:
        """
        Analyze account for bot-like behavior
        
        Args:
            trades: List of trade dictionaries
            positions: List of current positions (optional)
        
        Returns:
            Analysis results with bot score and indicators
        """
        if len(trades) < self.min_trades:
            return {
                'error': f'Insufficient trades for analysis (need {self.min_trades}, got {len(trades)})',
                'bot_score': 0,
                'is_bot': False
            }
        
        # Calculate individual indicators
        indicators = {
            'frequency': self._analyze_frequency(trades),
            'timing': self._analyze_timing(trades),
            'size_pattern': self._analyze_size_patterns(trades),
            'market_diversity': self._analyze_market_diversity(trades),
            'execution_speed': self._analyze_execution_speed(trades),
            'round_numbers': self._analyze_round_numbers(trades)
        }
        
        # Calculate overall bot score (weighted average)
        weights = {
            'frequency': 0.20,
            'timing': 0.20,
            'size_pattern': 0.15,
            'market_diversity': 0.15,
            'execution_speed': 0.20,
            'round_numbers': 0.10
        }
        
        bot_score = sum(indicators[k] * weights[k] for k in weights)
        
        # Determine classification
        is_bot = bot_score >= self.bot_threshold
        
        # Confidence level
        if bot_score >= 0.85:
            confidence = 'VERY_HIGH'
        elif bot_score >= 0.70:
            confidence = 'HIGH'
        elif bot_score >= 0.50:
            confidence = 'MEDIUM'
        else:
            confidence = 'LOW'
        
        # Identify top indicators
        sorted_indicators = sorted(indicators.items(), key=lambda x: x[1], reverse=True)
        top_indicators = [k for k, v in sorted_indicators[:3]]
        
        return {
            'bot_score': bot_score,
            'is_bot': is_bot,
            'confidence': confidence,
            'classification': 'LIKELY_BOT' if is_bot else 'LIKELY_HUMAN',
            'indicators': indicators,
            'top_indicators': top_indicators,
            'total_trades': len(trades),
            'reason': f"Top indicators: {', '.join(top_indicators)}"
        }
    
    def _analyze_frequency(self, trades: List[Dict]) -> float:
        """
        Analyze trading frequency
        High-frequency trading is a bot indicator
        
        Returns:
            Score 0-1 (higher = more bot-like)
        """
        if len(trades) < 2:
            return 0.0
        
        # Calculate trades per day
        timestamps = [self._parse_timestamp(t.get('created_time', t.get('timestamp'))) 
                     for t in trades]
        timestamps = [ts for ts in timestamps if ts is not None]
        
        if len(timestamps) < 2:
            return 0.0
        
        time_span_days = (max(timestamps) - min(timestamps)).total_seconds() / 86400
        if time_span_days < 1:
            time_span_days = 1
        
        trades_per_day = len(trades) / time_span_days
        
        # Score based on frequency
        # >50 trades/day = very likely bot
        # >20 trades/day = likely bot
        # >10 trades/day = possibly bot
        if trades_per_day >= 50:
            return 1.0
        elif trades_per_day >= 20:
            return 0.8
        elif trades_per_day >= 10:
            return 0.6
        elif trades_per_day >= 5:
            return 0.4
        else:
            return 0.2
    
    def _analyze_timing(self, trades: List[Dict]) -> float:
        """
        Analyze trading timing patterns
        24/7 trading is a bot indicator
        
        Returns:
            Score 0-1 (higher = more bot-like)
        """
        timestamps = [self._parse_timestamp(t.get('created_time', t.get('timestamp'))) 
                     for t in trades]
        timestamps = [ts for ts in timestamps if ts is not None]
        
        if len(timestamps) < 10:
            return 0.0
        
        # Analyze hour distribution
        hours = [ts.hour for ts in timestamps]
        hour_counts = Counter(hours)
        
        # Check for off-hours trading (midnight to 6am)
        off_hours_trades = sum(hour_counts.get(h, 0) for h in range(0, 6))
        off_hours_ratio = off_hours_trades / len(timestamps)
        
        # Check for weekend trading
        weekdays = [ts.weekday() for ts in timestamps]
        weekend_trades = sum(1 for w in weekdays if w >= 5)  # 5=Saturday, 6=Sunday
        weekend_ratio = weekend_trades / len(timestamps)
        
        # Check for uniform distribution (bots trade at all hours)
        hours_with_trades = len(hour_counts)
        hour_uniformity = hours_with_trades / 24
        
        # Combine signals
        score = (off_hours_ratio * 0.4 + weekend_ratio * 0.3 + hour_uniformity * 0.3)
        
        return min(score * 2, 1.0)  # Scale up and cap at 1.0
    
    def _analyze_size_patterns(self, trades: List[Dict]) -> float:
        """
        Analyze bet size patterns
        Consistent sizes are a bot indicator
        
        Returns:
            Score 0-1 (higher = more bot-like)
        """
        sizes = []
        for trade in trades:
            # Try different field names for size
            size = trade.get('count', trade.get('quantity', trade.get('size', 0)))
            if size > 0:
                sizes.append(size)
        
        if len(sizes) < 5:
            return 0.0
        
        # Calculate coefficient of variation (std / mean)
        mean_size = np.mean(sizes)
        std_size = np.std(sizes)
        
        if mean_size == 0:
            return 0.0
        
        cv = std_size / mean_size
        
        # Low CV indicates consistent sizing (bot-like)
        # CV < 0.2 = very consistent
        # CV < 0.5 = somewhat consistent
        if cv < 0.2:
            return 0.9
        elif cv < 0.5:
            return 0.6
        elif cv < 1.0:
            return 0.3
        else:
            return 0.1
    
    def _analyze_market_diversity(self, trades: List[Dict]) -> float:
        """
        Analyze market diversity
        Trading in many markets simultaneously is a bot indicator
        
        Returns:
            Score 0-1 (higher = more bot-like)
        """
        tickers = [t.get('ticker', t.get('market_ticker', '')) for t in trades]
        tickers = [t for t in tickers if t]
        
        if not tickers:
            return 0.0
        
        unique_markets = len(set(tickers))
        total_trades = len(tickers)
        
        # Calculate market switching rate
        switches = sum(1 for i in range(1, len(tickers)) if tickers[i] != tickers[i-1])
        switch_rate = switches / max(len(tickers) - 1, 1)
        
        # High diversity + high switching = bot-like
        diversity_ratio = unique_markets / total_trades
        
        # Score based on metrics
        diversity_score = min(diversity_ratio * 3, 1.0)  # >33% unique = max score
        switch_score = min(switch_rate * 2, 1.0)  # >50% switching = max score
        
        return (diversity_score * 0.6 + switch_score * 0.4)
    
    def _analyze_execution_speed(self, trades: List[Dict]) -> float:
        """
        Analyze execution speed
        Very fast execution after market events is a bot indicator
        
        Returns:
            Score 0-1 (higher = more bot-like)
        """
        timestamps = [self._parse_timestamp(t.get('created_time', t.get('timestamp'))) 
                     for t in trades]
        timestamps = [ts for ts in timestamps if ts is not None]
        
        if len(timestamps) < 2:
            return 0.0
        
        # Calculate time between consecutive trades
        intervals = []
        for i in range(1, len(timestamps)):
            interval = (timestamps[i] - timestamps[i-1]).total_seconds()
            if interval > 0:
                intervals.append(interval)
        
        if not intervals:
            return 0.0
        
        # Look for very fast trades (< 1 second apart)
        very_fast = sum(1 for i in intervals if i < 1)
        fast = sum(1 for i in intervals if i < 5)
        
        very_fast_ratio = very_fast / len(intervals)
        fast_ratio = fast / len(intervals)
        
        # Calculate minimum interval
        min_interval = min(intervals)
        
        # Score based on speed
        if min_interval < 0.1:  # Sub-second execution
            speed_score = 1.0
        elif min_interval < 1:
            speed_score = 0.8
        elif min_interval < 5:
            speed_score = 0.5
        else:
            speed_score = 0.2
        
        # Combine with ratio of fast trades
        ratio_score = (very_fast_ratio * 0.7 + fast_ratio * 0.3)
        
        return (speed_score * 0.5 + ratio_score * 0.5)
    
    def _analyze_round_numbers(self, trades: List[Dict]) -> float:
        """
        Analyze use of round numbers
        Bots often use exact round numbers
        
        Returns:
            Score 0-1 (higher = more bot-like)
        """
        sizes = []
        prices = []
        
        for trade in trades:
            # Get size
            size = trade.get('count', trade.get('quantity', trade.get('size', 0)))
            if size > 0:
                sizes.append(size)
            
            # Get price (in cents)
            price = trade.get('yes_price', trade.get('no_price', trade.get('price', 0)))
            if price > 0:
                prices.append(price)
        
        round_count = 0
        total_count = 0
        
        # Check sizes for round numbers
        for size in sizes:
            total_count += 1
            if size % 10 == 0 or size % 5 == 0:
                round_count += 1
        
        # Check prices for round numbers (multiples of 5 cents)
        for price in prices:
            total_count += 1
            if price % 5 == 0:
                round_count += 1
        
        if total_count == 0:
            return 0.0
        
        round_ratio = round_count / total_count
        
        # High use of round numbers is bot-like
        if round_ratio >= 0.9:
            return 0.9
        elif round_ratio >= 0.7:
            return 0.7
        elif round_ratio >= 0.5:
            return 0.5
        else:
            return 0.2
    
    def _parse_timestamp(self, ts: any) -> datetime:
        """Parse timestamp from various formats"""
        if isinstance(ts, datetime):
            return ts
        elif isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts)
        elif isinstance(ts, str):
            try:
                # Try ISO format
                return datetime.fromisoformat(ts.replace('Z', '+00:00'))
            except:
                try:
                    # Try parsing as timestamp
                    return datetime.fromtimestamp(float(ts))
                except:
                    return None
        return None
