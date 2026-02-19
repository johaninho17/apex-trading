"""
Risk management system for Kalshi trading
"""
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from config import Config
from utils import calculate_profit_loss, safe_divide

logger = logging.getLogger(__name__)


class RiskManager:
    """Comprehensive risk management for trading"""
    
    def __init__(self):
        """Initialize risk manager"""
        self.max_position_size = Config.MAX_POSITION_SIZE
        self.max_total_exposure = Config.MAX_TOTAL_EXPOSURE
        self.stop_loss_pct = Config.STOP_LOSS_PERCENTAGE
        self.max_slippage = Config.MAX_SLIPPAGE
        
        # Track current positions
        self.positions = {}
        self.total_exposure = 0.0
    
    def check_position_limits(self, ticker: str, size: int, price: float) -> Tuple[bool, str]:
        """
        Check if a new position would exceed limits
        
        Args:
            ticker: Market ticker
            size: Position size (number of contracts)
            price: Price per contract in dollars
        
        Returns:
            Tuple of (is_valid, reason)
        """
        position_value = size * price
        
        # Check individual position size
        if position_value > self.max_position_size:
            return False, f"Position size ${position_value:.2f} exceeds max ${self.max_position_size}"
        
        # Check total exposure
        current_ticker_exposure = self.positions.get(ticker, {}).get('value', 0)
        new_ticker_exposure = current_ticker_exposure + position_value
        
        if new_ticker_exposure > self.max_position_size:
            return False, f"Total exposure in {ticker} would be ${new_ticker_exposure:.2f}, exceeds max ${self.max_position_size}"
        
        # Check total portfolio exposure
        new_total_exposure = self.total_exposure + position_value
        if new_total_exposure > self.max_total_exposure:
            return False, f"Total exposure ${new_total_exposure:.2f} exceeds max ${self.max_total_exposure}"
        
        return True, "OK"
    
    def calculate_position_size(self, ticker: str, strategy: str, 
                               current_price: float, confidence: float = 0.5) -> int:
        """
        Calculate optimal position size
        
        Args:
            ticker: Market ticker
            strategy: Strategy name
            current_price: Current market price
            confidence: Confidence level (0-1)
        
        Returns:
            Recommended position size in contracts
        """
        # Base position size
        base_size_usd = self.max_position_size * confidence
        
        # Adjust for existing exposure
        current_exposure = self.positions.get(ticker, {}).get('value', 0)
        available_size_usd = min(
            base_size_usd,
            self.max_position_size - current_exposure,
            self.max_total_exposure - self.total_exposure
        )
        
        if available_size_usd <= 0:
            return 0
        
        # Convert to contracts
        if current_price > 0:
            contracts = int(available_size_usd / current_price)
        else:
            contracts = 0
        
        return max(contracts, 0)
    
    def check_stop_loss(self, positions: List[Dict]) -> List[Dict]:
        """
        Check positions for stop-loss triggers
        
        Args:
            positions: List of current positions
        
        Returns:
            List of positions that should be closed
        """
        to_close = []
        
        for pos in positions:
            ticker = pos.get('ticker', '')
            entry_price = pos.get('entry_price', 0)
            current_price = pos.get('market_price', 0)
            quantity = pos.get('position', 0)
            side = pos.get('side', 'yes')
            
            if not all([ticker, entry_price, current_price, quantity]):
                continue
            
            # Calculate P&L percentage
            pnl = calculate_profit_loss(
                entry_price / 100,  # Convert cents to dollars
                current_price / 100,
                quantity,
                side
            )
            
            position_value = abs(quantity * entry_price / 100)
            if position_value == 0:
                continue
            
            pnl_pct = (pnl / position_value) * 100
            
            # Check if stop-loss triggered
            if pnl_pct <= -self.stop_loss_pct:
                logger.warning(
                    f"Stop-loss triggered for {ticker}: "
                    f"{pnl_pct:.1f}% loss (threshold: {self.stop_loss_pct}%)"
                )
                to_close.append(pos)
        
        return to_close
    
    def validate_order(self, ticker: str, side: str, quantity: int, 
                      price: float, current_market_price: float) -> Tuple[bool, str]:
        """
        Validate an order before placement
        
        Args:
            ticker: Market ticker
            side: 'yes' or 'no'
            quantity: Number of contracts
            price: Limit price in dollars
            current_market_price: Current market price
        
        Returns:
            Tuple of (is_valid, reason)
        """
        # Check position limits
        is_valid, reason = self.check_position_limits(ticker, quantity, price)
        if not is_valid:
            return False, reason
        
        # Check slippage
        if current_market_price > 0:
            slippage = abs(price - current_market_price) / current_market_price
            if slippage > self.max_slippage:
                return False, f"Slippage {slippage*100:.1f}% exceeds max {self.max_slippage*100:.1f}%"
        
        # Check price validity
        if price <= 0 or price >= 1:
            return False, f"Invalid price ${price:.2f} (must be between $0 and $1)"
        
        # Check quantity
        if quantity <= 0:
            return False, "Quantity must be positive"
        
        return True, "OK"
    
    def update_position(self, ticker: str, side: str, quantity: int, 
                       price: float, action: str = 'add'):
        """
        Update position tracking
        
        Args:
            ticker: Market ticker
            side: 'yes' or 'no'
            quantity: Number of contracts
            price: Price per contract
            action: 'add' or 'remove'
        """
        position_value = quantity * price
        
        if action == 'add':
            if ticker not in self.positions:
                self.positions[ticker] = {
                    'quantity': 0,
                    'value': 0,
                    'side': side,
                    'avg_price': 0
                }
            
            pos = self.positions[ticker]
            total_quantity = pos['quantity'] + quantity
            total_value = pos['value'] + position_value
            
            pos['quantity'] = total_quantity
            pos['value'] = total_value
            pos['avg_price'] = safe_divide(total_value, total_quantity)
            
            self.total_exposure += position_value
            
        elif action == 'remove':
            if ticker in self.positions:
                pos = self.positions[ticker]
                pos['quantity'] = max(0, pos['quantity'] - quantity)
                pos['value'] = max(0, pos['value'] - position_value)
                
                if pos['quantity'] == 0:
                    del self.positions[ticker]
                
                self.total_exposure = max(0, self.total_exposure - position_value)
    
    def get_portfolio_stats(self) -> Dict:
        """
        Get current portfolio statistics
        
        Returns:
            Dictionary with portfolio metrics
        """
        total_positions = len(self.positions)
        total_value = sum(p['value'] for p in self.positions.values())
        
        # Calculate exposure by side
        yes_exposure = sum(p['value'] for p in self.positions.values() 
                          if p.get('side') == 'yes')
        no_exposure = sum(p['value'] for p in self.positions.values() 
                         if p.get('side') == 'no')
        
        # Calculate utilization
        position_utilization = safe_divide(total_value, self.max_position_size)
        total_utilization = safe_divide(self.total_exposure, self.max_total_exposure)
        
        return {
            'total_positions': total_positions,
            'total_value': total_value,
            'total_exposure': self.total_exposure,
            'yes_exposure': yes_exposure,
            'no_exposure': no_exposure,
            'position_utilization': position_utilization,
            'total_utilization': total_utilization,
            'available_capital': max(0, self.max_total_exposure - self.total_exposure),
            'positions': dict(self.positions)
        }
    
    def reset(self):
        """Reset position tracking"""
        self.positions = {}
        self.total_exposure = 0.0
        logger.info("Risk manager reset")
    
    def load_positions(self, positions: List[Dict]):
        """
        Load positions from API
        
        Args:
            positions: List of position dictionaries from API
        """
        self.reset()
        
        for pos in positions:
            ticker = pos.get('ticker', '')
            quantity = abs(pos.get('position', 0))
            market_price = pos.get('market_price', 0) / 100  # Convert cents to dollars
            side = pos.get('side', 'yes')
            
            if ticker and quantity > 0 and market_price > 0:
                self.update_position(ticker, side, quantity, market_price, action='add')
        
        logger.info(f"Loaded {len(self.positions)} positions, "
                   f"total exposure: ${self.total_exposure:.2f}")
