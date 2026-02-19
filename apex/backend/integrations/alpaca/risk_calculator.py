"""
Risk-Based Position Sizing Calculator

Implements the "1% Rule" for consistent risk management across all trades.
Instead of fixed dollar amounts, calculates shares based on acceptable risk per trade.
"""

def calculate_position_size(account_balance, risk_percent, entry_price, stop_price):
    """
    Calculate number of shares to buy based on risk tolerance.
    
    The "1% Rule": Risk the same percentage of your account on every trade.
    This ensures consistent risk management regardless of stock price or volatility.
    
    Args:
        account_balance: Total account value (e.g., 1000.0)
        risk_percent: Max % to risk per trade as decimal (e.g., 0.01 for 1%)
        entry_price: Planned entry price
        stop_price: Stop loss price
    
    Returns:
        dict with shares, position_value, risk_dollars, and risk_per_share
    
    Example:
        Account: $1000
        Risk: 1% = $10
        Entry: $50, Stop: $48
        Risk/share: $2
        Shares: $10 / $2 = 5 shares
        Position: 5 * $50 = $250
    """
    # Calculate dollar amount to risk
    risk_dollars = account_balance * risk_percent
    
    # Calculate risk per share
    risk_per_share = entry_price - stop_price
    
    # Prevent division by zero or negative stops
    if risk_per_share <= 0:
        return {
            'shares': 0,
            'position_value': 0,
            'risk_dollars': risk_dollars,
            'risk_per_share': 0,
            'error': 'Invalid stop loss (must be below entry price)'
        }
    
    # Calculate shares
    shares = risk_dollars / risk_per_share
    position_value = shares * entry_price
    
    # Round shares to 2 decimals (Alpaca supports fractional shares)
    shares = round(shares, 2)
    
    return {
        'shares': shares,
        'position_value': round(position_value, 2),
        'risk_dollars': round(risk_dollars, 2),
        'risk_per_share': round(risk_per_share, 2),
        'error': None
    }


def validate_position_size(shares, entry_price, account_balance, max_position_pct=0.25):
    """
    Validate that position size doesn't exceed portfolio concentration limits.
    
    Args:
        shares: Number of shares to buy
        entry_price: Entry price
        account_balance: Total account value
        max_position_pct: Maximum % of portfolio for single position (default 25%)
    
    Returns:
        dict with is_valid, warnings, and adjusted_shares if needed
    """
    position_value = shares * entry_price
    position_pct = position_value / account_balance
    
    warnings = []
    adjusted_shares = shares
    
    # Check portfolio concentration
    if position_pct > max_position_pct:
        warnings.append(f"Position is {position_pct*100:.1f}% of portfolio (max recommended: {max_position_pct*100:.0f}%)")
        # Adjust to max allowed
        adjusted_shares = (account_balance * max_position_pct) / entry_price
        adjusted_shares = round(adjusted_shares, 2)
    
    # Check minimum viable position
    if position_value < 10:  # Minimum $10 position
        warnings.append(f"Position value too small (${position_value:.2f}). Consider higher risk % or different stock.")
    
    is_valid = position_pct <= max_position_pct and position_value >= 10
    
    return {
        'is_valid': is_valid,
        'warnings': warnings,
        'adjusted_shares': adjusted_shares,
        'position_pct': round(position_pct * 100, 1)
    }


def format_risk_summary(sizing_result, validation_result):
    """
    Format a human-readable risk summary for display.
    
    Returns:
        str: Formatted summary
    """
    summary = f"""
📊 **Risk Analysis**
- Max Risk: ${sizing_result['risk_dollars']:.2f}
- Risk/Share: ${sizing_result['risk_per_share']:.2f}
- Shares: {sizing_result['shares']}
- Position Value: ${sizing_result['position_value']:.2f}
- Portfolio %: {validation_result['position_pct']:.1f}%
"""
    
    if validation_result['warnings']:
        summary += "\n⚠️ **Warnings:**\n"
        for warning in validation_result['warnings']:
            summary += f"   - {warning}\n"
    
    return summary.strip()
