"""
Utility functions for Kalshi bot
"""
import time
import logging
from typing import List, Dict, Any
from datetime import datetime
from collections import deque


def setup_logging(log_level: str = 'INFO', log_file: str = None):
    """
    Setup logging configuration
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional log file path
    """
    handlers = [logging.StreamHandler()]
    
    if log_file:
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )


class RateLimiter:
    """Rate limiter to respect API limits"""
    
    def __init__(self, calls_per_second: int = 10):
        """
        Initialize rate limiter
        
        Args:
            calls_per_second: Maximum API calls per second
        """
        self.calls_per_second = calls_per_second
        self.min_interval = 1.0 / calls_per_second
        self.last_call = 0
        self.call_times = deque(maxlen=calls_per_second)
    
    def wait_if_needed(self):
        """Wait if necessary to respect rate limit"""
        now = time.time()
        
        # Remove calls older than 1 second
        while self.call_times and now - self.call_times[0] > 1.0:
            self.call_times.popleft()
        
        # If we've hit the limit, wait
        if len(self.call_times) >= self.calls_per_second:
            sleep_time = 1.0 - (now - self.call_times[0])
            if sleep_time > 0:
                time.sleep(sleep_time)
        
        self.call_times.append(time.time())


def format_timestamp(timestamp: Any) -> str:
    """
    Format timestamp to readable string
    
    Args:
        timestamp: Unix timestamp, datetime object, or ISO string
    
    Returns:
        Formatted timestamp string
    """
    if isinstance(timestamp, (int, float)):
        dt = datetime.fromtimestamp(timestamp)
    elif isinstance(timestamp, str):
        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    elif isinstance(timestamp, datetime):
        dt = timestamp
    else:
        return str(timestamp)
    
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def format_usd(amount: float) -> str:
    """
    Format USD amount
    
    Args:
        amount: Dollar amount
    
    Returns:
        Formatted string like "$123.45"
    """
    return f"${amount:,.2f}"


def format_percentage(value: float) -> str:
    """
    Format percentage
    
    Args:
        value: Percentage value (0-1 or 0-100)
    
    Returns:
        Formatted string like "45.2%"
    """
    if value <= 1:
        value *= 100
    return f"{value:.1f}%"


def truncate_address(address: str, start: int = 6, end: int = 4) -> str:
    """
    Truncate long address/ID for display
    
    Args:
        address: Full address or ID
        start: Number of characters to show at start
        end: Number of characters to show at end
    
    Returns:
        Truncated string like "0x1234...5678"
    """
    if len(address) <= start + end:
        return address
    return f"{address[:start]}...{address[-end:]}"


def calculate_statistics(values: List[float]) -> Dict[str, float]:
    """
    Calculate statistical measures
    
    Args:
        values: List of numeric values
    
    Returns:
        Dictionary with mean, median, std, min, max, percentiles
    """
    if not values:
        return {
            'mean': 0,
            'median': 0,
            'std': 0,
            'min': 0,
            'max': 0,
            'p25': 0,
            'p75': 0,
            'p90': 0,
            'p95': 0
        }
    
    import numpy as np
    
    arr = np.array(values)
    
    return {
        'mean': float(np.mean(arr)),
        'median': float(np.median(arr)),
        'std': float(np.std(arr)),
        'min': float(np.min(arr)),
        'max': float(np.max(arr)),
        'p25': float(np.percentile(arr, 25)),
        'p75': float(np.percentile(arr, 75)),
        'p90': float(np.percentile(arr, 90)),
        'p95': float(np.percentile(arr, 95))
    }


def is_round_number(value: float, tolerance: float = 0.01) -> bool:
    """
    Check if a value is close to a round number
    
    Args:
        value: Value to check
        tolerance: Tolerance for rounding
    
    Returns:
        True if value is close to a round number
    """
    # Check if close to integer
    if abs(value - round(value)) < tolerance:
        return True
    
    # Check if close to common fractions
    common_fractions = [0.25, 0.5, 0.75]
    for frac in common_fractions:
        if abs(value - round(value) - frac) < tolerance:
            return True
        if abs(value - round(value) + frac) < tolerance:
            return True
    
    return False


def calculate_profit_loss(entry_price: float, current_price: float, 
                         quantity: int, side: str) -> float:
    """
    Calculate profit/loss for a position
    
    Args:
        entry_price: Entry price per contract
        current_price: Current price per contract
        quantity: Number of contracts
        side: 'yes' or 'no'
    
    Returns:
        Profit/loss in USD
    """
    if side.lower() == 'yes':
        return (current_price - entry_price) * quantity
    else:  # 'no'
        return (entry_price - current_price) * quantity


def calculate_win_rate(trades: List[Dict]) -> float:
    """
    Calculate win rate from trade history
    
    Args:
        trades: List of trade dictionaries with 'pnl' field
    
    Returns:
        Win rate as percentage (0-100)
    """
    if not trades:
        return 0.0
    
    winning_trades = sum(1 for t in trades if t.get('pnl', 0) > 0)
    return (winning_trades / len(trades)) * 100


def format_duration(seconds: float) -> str:
    """
    Format duration in seconds to human-readable string
    
    Args:
        seconds: Duration in seconds
    
    Returns:
        Formatted string like "2h 30m" or "45s"
    """
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        return f"{hours}h {minutes}m"


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Safely divide two numbers, returning default if denominator is zero
    
    Args:
        numerator: Numerator
        denominator: Denominator
        default: Default value if division by zero
    
    Returns:
        Result of division or default
    """
    return numerator / denominator if denominator != 0 else default
