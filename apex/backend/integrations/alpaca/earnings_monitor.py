"""
Earnings Collision Monitor

Prevents trades from being executed close to earnings announcements.
Earnings create unpredictable volatility that can gap through stop losses.
"""

import yfinance as yf
from datetime import datetime, timedelta, date
import pandas as pd


def check_earnings_risk(ticker, min_days_buffer=5):
    """
    Check if a stock's earnings date is too close for safe trading.
    
    Args:
        ticker: Stock symbol
        min_days_buffer: Minimum days until/since earnings (default 5)
    
    Returns:
        dict with:
            - safe: bool - True if safe to trade
            - next_earnings: datetime - Next earnings date (or None)
            - days_until: int - Days until earnings (negative if past)
            - message: str - Human-readable explanation
    """
    try:
        stock = yf.Ticker(ticker)
        
        # Get earnings calendar
        calendar = stock.calendar
        
        # Handle both dict and DataFrame from yfinance
        if calendar is None or (hasattr(calendar, 'empty') and calendar.empty) or (isinstance(calendar, dict) and not calendar):
            # No earnings data available - proceed with caution
            return {
                'safe': True,
                'next_earnings': None,
                'days_until': None,
                'message': f"⚠️ No earnings data available for {ticker}. Proceed with caution."
            }
        
        # Extract next earnings date
        next_earnings = None
        
        # 1. Handle DataFrame (classic yfinance) - accessing index safely
        if hasattr(calendar, 'index') and 'Earnings Date' in calendar.index:
            target = calendar.loc['Earnings Date']
            if isinstance(target, pd.Series):
                 next_earnings = target.iloc[0]
            else:
                 next_earnings = target
                 
        # 2. Handle Dictionary (new yfinance or different structure)
        elif isinstance(calendar, dict):
            # Try various keys potentially used by yfinance
            if 'Earnings Date' in calendar:
                next_earnings = calendar['Earnings Date'][0]
            elif 'Earnings Low' in calendar: # sometimes keys differ, fallback logic
                 pass
        
        # Convert to datetime if it's a date object or string (happens for both paths)
        if next_earnings is not None:
            if isinstance(next_earnings, str):
                next_earnings = pd.to_datetime(next_earnings)
            elif isinstance(next_earnings, date) and not isinstance(next_earnings, datetime):
                # Convert date to datetime
                next_earnings = datetime.combine(next_earnings, datetime.min.time())
            
            # Additional safety: Convert pandas Timestamp to pydatetime if needed
            if hasattr(next_earnings, 'to_pydatetime'):
                next_earnings = next_earnings.to_pydatetime()
            
            # Normalize today to midnight for accurate day calculation
            today = datetime.now()
            # If next_earnings is offset-naive, make sure today is too
            if hasattr(next_earnings, 'tzinfo') and next_earnings.tzinfo is None:
                today = today.replace(tzinfo=None)
            
            days_until = (next_earnings - today).days
            
            # Check if too close
            safe = abs(days_until) > min_days_buffer
            
            if days_until > 0:
                message = f"📅 Earnings in {days_until} days ({next_earnings.strftime('%Y-%m-%d')})"
            else:
                message = f"📅 Earnings was {abs(days_until)} days ago ({next_earnings.strftime('%Y-%m-%d')})"
            
            if not safe:
                message += f" ⚠️ **Too close! Wait {min_days_buffer} days.**"
            
            return {
                'safe': safe,
                'next_earnings': next_earnings,
                'days_until': days_until,
                'message': message
            }
        
        # No earnings date found
        return {
            'safe': True,
            'next_earnings': None,
            'days_until': None,
            'message': "✅ No upcoming earnings found"
        }
        
    except Exception as e:
        print(f"Error checking earnings for {ticker}: {e}")
        # On error, be conservative and allow trade
        return {
            'safe': True,
            'next_earnings': None,
            'days_until': None,
            'message': f"⚠️ Could not fetch earnings data. Error: {str(e)[:50]}"
        }


def get_earnings_calendar_display(ticker):
    """
    Get formatted earnings calendar for display in dashboard.
    
    Returns:
        str: Formatted markdown string with earnings info
    """
    result = check_earnings_risk(ticker)
    
    if not result['safe']:
        return f"""
⚠️ **EARNINGS RISK DETECTED**

{result['message']}

**Recommendation:** Wait until {result['next_earnings'] + timedelta(days=5)} before trading.

**Why?** Earnings create unpredictable gaps that can blow through your stop loss overnight.
"""
    
    elif result['next_earnings']:
        return f"""
📅 **Next Earnings:** {result['next_earnings'].strftime('%B %d, %Y')} ({result['days_until']} days)

✅ Safe to trade (outside 5-day buffer zone)
"""
    
    else:
        return "✅ No earnings conflicts detected"
