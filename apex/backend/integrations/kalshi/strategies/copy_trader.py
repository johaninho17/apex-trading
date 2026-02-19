"""
Copy trading strategy for Kalshi
Monitors and replicates trades from successful accounts
"""
import logging
import time
from typing import Dict, List, Set
from datetime import datetime, timedelta
from api_client import KalshiAPI
from risk_manager import RiskManager
from config import Config

logger = logging.getLogger(__name__)


class CopyTradingStrategy:
    """Copy trading strategy following successful accounts"""
    
    def __init__(self, api: KalshiAPI, risk_manager: RiskManager):
        """
        Initialize copy trading strategy
        
        Args:
            api: KalshiAPI instance
            risk_manager: RiskManager instance
        """
        self.api = api
        self.risk_manager = risk_manager
        self.copy_ratio = Config.COPY_TRADE_RATIO
        
        # Track last seen trades to avoid duplicates
        self.last_trade_ids: Dict[str, Set[str]] = {}
    
    def monitor_account(self, account_id: str, min_trade_size: int = 10) -> List[Dict]:
        """
        Monitor an account for new trades
        
        Args:
            account_id: Account ID to monitor
            min_trade_size: Minimum trade size to copy
        
        Returns:
            List of new trades to copy
        """
        # Get recent trades
        trades = self.api.get_account_trades(account_id, limit=50)
        
        if not trades:
            logger.warning(f"No trades found for account {account_id}")
            return []
        
        # Initialize tracking for this account
        if account_id not in self.last_trade_ids:
            # First time monitoring - store all current trade IDs
            self.last_trade_ids[account_id] = set(
                t.get('trade_id', str(i)) for i, t in enumerate(trades)
            )
            logger.info(f"Initialized monitoring for {account_id} with {len(trades)} trades")
            return []
        
        # Find new trades
        new_trades = []
        current_trade_ids = set()
        
        for trade in trades:
            trade_id = trade.get('trade_id', trade.get('fill_id', ''))
            current_trade_ids.add(trade_id)
            
            # Check if this is a new trade
            if trade_id and trade_id not in self.last_trade_ids[account_id]:
                # Check minimum size
                size = trade.get('count', trade.get('quantity', 0))
                if size >= min_trade_size:
                    new_trades.append(trade)
        
        # Update tracking
        self.last_trade_ids[account_id] = current_trade_ids
        
        if new_trades:
            logger.info(f"Found {len(new_trades)} new trades from {account_id}")
        
        return new_trades
    
    def copy_trade(self, trade: Dict, ratio: float = None, dry_run: bool = False) -> Dict:
        """
        Copy a trade
        
        Args:
            trade: Trade dictionary to copy
            ratio: Copy ratio (uses default if not provided)
            dry_run: If True, don't actually place order
        
        Returns:
            Execution result
        """
        ratio = ratio or self.copy_ratio
        
        ticker = trade.get('ticker', '')
        side = trade.get('side', 'yes')
        original_size = trade.get('count', trade.get('quantity', 0))
        price = trade.get('yes_price', trade.get('no_price', 0)) / 100  # Convert to dollars
        
        # Calculate copy size
        copy_size = int(original_size * ratio)
        
        if copy_size <= 0:
            return {'status': 'skipped', 'reason': 'Copy size too small'}
        
        logger.info(
            f"Copying trade: {ticker} {side.upper()} "
            f"{copy_size} @ ${price:.2f} (ratio: {ratio:.1%})"
        )
        
        if dry_run or not Config.ENABLE_TRADING:
            logger.info("DRY RUN: Would copy trade")
            return {
                'status': 'dry_run',
                'ticker': ticker,
                'side': side,
                'quantity': copy_size,
                'price': price
            }
        
        # Validate order
        is_valid, reason = self.risk_manager.validate_order(
            ticker, side, copy_size, price, price
        )
        
        if not is_valid:
            logger.warning(f"Order validation failed: {reason}")
            return {'status': 'error', 'reason': reason}
        
        # Place order
        try:
            order = self.api.place_order(
                ticker=ticker,
                side=side,
                quantity=copy_size,
                order_type='limit',
                price=int(price * 100)  # Convert to cents
            )
            
            if not order:
                return {'status': 'error', 'reason': 'Failed to place order'}
            
            # Update risk manager
            self.risk_manager.update_position(ticker, side, copy_size, price, 'add')
            
            logger.info(f"Successfully copied trade on {ticker}")
            
            return {
                'status': 'success',
                'ticker': ticker,
                'side': side,
                'quantity': copy_size,
                'price': price,
                'order': order
            }
            
        except Exception as e:
            logger.error(f"Failed to copy trade: {e}")
            return {'status': 'error', 'reason': str(e)}
    
    def run(self, follow_accounts: List[str], interval: int = 60, 
            max_iterations: int = None, dry_run: bool = False) -> List[Dict]:
        """
        Run copy trading strategy
        
        Args:
            follow_accounts: List of account IDs to follow
            interval: Seconds between checks
            max_iterations: Maximum iterations (None = infinite)
            dry_run: If True, don't actually place orders
        
        Returns:
            List of execution results
        """
        if not follow_accounts:
            logger.error("No accounts to follow")
            return []
        
        logger.info(f"Starting copy trading for {len(follow_accounts)} accounts")
        logger.info(f"Copy ratio: {self.copy_ratio:.1%}, Interval: {interval}s")
        
        results = []
        iteration = 0
        
        try:
            while max_iterations is None or iteration < max_iterations:
                iteration += 1
                logger.info(f"Iteration {iteration}")
                
                # Monitor each account
                for account_id in follow_accounts:
                    try:
                        new_trades = self.monitor_account(account_id)
                        
                        # Copy new trades
                        for trade in new_trades:
                            result = self.copy_trade(trade, dry_run=dry_run)
                            results.append(result)
                            
                            # Brief pause between trades
                            time.sleep(1)
                    
                    except Exception as e:
                        logger.error(f"Error monitoring {account_id}: {e}")
                
                # Wait before next iteration
                if max_iterations is None or iteration < max_iterations:
                    logger.info(f"Waiting {interval}s until next check...")
                    time.sleep(interval)
        
        except KeyboardInterrupt:
            logger.info("Copy trading stopped by user")
        
        return results
