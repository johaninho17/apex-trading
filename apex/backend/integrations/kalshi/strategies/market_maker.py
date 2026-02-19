"""
Market making strategy for Kalshi
Provides liquidity and captures bid-ask spread
"""
import logging
import time
from typing import Dict, List, Optional
from api_client import KalshiAPI
from risk_manager import RiskManager
from config import Config

logger = logging.getLogger(__name__)


class MarketMakerStrategy:
    """Market making strategy providing liquidity"""
    
    def __init__(self, api: KalshiAPI, risk_manager: RiskManager):
        """
        Initialize market maker strategy
        
        Args:
            api: KalshiAPI instance
            risk_manager: RiskManager instance
        """
        self.api = api
        self.risk_manager = risk_manager
        self.target_spread = Config.MARKET_MAKER_SPREAD
        self.max_inventory = Config.MAX_POSITION_SIZE
        
        # Track our active quotes
        self.active_quotes: Dict[str, Dict] = {}
    
    def calculate_quotes(self, ticker: str, orderbook: Dict, 
                        current_inventory: int = 0) -> Optional[Dict]:
        """
        Calculate bid and ask quotes
        
        Args:
            ticker: Market ticker
            orderbook: Current orderbook
            current_inventory: Current position in this market
        
        Returns:
            Dictionary with bid and ask prices, or None if can't quote
        """
        yes_bids = orderbook.get('yes', [])
        yes_asks = orderbook.get('yes', [])
        no_bids = orderbook.get('no', [])
        no_asks = orderbook.get('no', [])
        
        if not (yes_bids and yes_asks):
            return None
        
        # Get mid price
        best_yes_bid = max(yes_bids, key=lambda x: x.get('price', 0))
        best_yes_ask = min(yes_asks, key=lambda x: x.get('price', float('inf')))
        
        yes_bid_price = best_yes_bid.get('price', 0) / 100
        yes_ask_price = best_yes_ask.get('price', 100) / 100
        
        mid_price = (yes_bid_price + yes_ask_price) / 2
        
        # Calculate half spread
        half_spread = self.target_spread / 2
        
        # Adjust for inventory (skew quotes if we have position)
        inventory_skew = 0
        if current_inventory != 0:
            # If we're long, lower our quotes to encourage selling
            # If we're short, raise our quotes to encourage buying
            max_skew = 0.05  # Maximum 5% skew
            inventory_ratio = current_inventory / self.max_inventory
            inventory_skew = -inventory_ratio * max_skew
        
        # Calculate our quotes
        our_bid = mid_price - half_spread + inventory_skew
        our_ask = mid_price + half_spread + inventory_skew
        
        # Ensure quotes are within valid range
        our_bid = max(0.01, min(0.99, our_bid))
        our_ask = max(0.01, min(0.99, our_ask))
        
        # Ensure bid < ask
        if our_bid >= our_ask:
            return None
        
        return {
            'ticker': ticker,
            'bid_price': our_bid,
            'ask_price': our_ask,
            'mid_price': mid_price,
            'spread': our_ask - our_bid
        }
    
    def place_quotes(self, ticker: str, quotes: Dict, 
                    quantity: int, dry_run: bool = False) -> Dict:
        """
        Place bid and ask quotes
        
        Args:
            ticker: Market ticker
            quotes: Quote dictionary from calculate_quotes
            quantity: Quantity for each side
            dry_run: If True, don't actually place orders
        
        Returns:
            Result dictionary
        """
        bid_price = quotes['bid_price']
        ask_price = quotes['ask_price']
        
        logger.info(
            f"Placing quotes on {ticker}: "
            f"Bid {quantity} @ ${bid_price:.2f}, Ask {quantity} @ ${ask_price:.2f}"
        )
        
        if dry_run or not Config.ENABLE_TRADING:
            logger.info("DRY RUN: Would place quotes")
            return {
                'status': 'dry_run',
                'ticker': ticker,
                'bid_price': bid_price,
                'ask_price': ask_price,
                'quantity': quantity
            }
        
        # Cancel existing quotes for this ticker
        self.cancel_quotes(ticker)
        
        try:
            # Place bid (buy YES at lower price)
            bid_order = self.api.place_order(
                ticker=ticker,
                side='yes',
                quantity=quantity,
                order_type='limit',
                price=int(bid_price * 100)
            )
            
            if not bid_order:
                return {'status': 'error', 'reason': 'Failed to place bid'}
            
            # Place ask (sell YES at higher price, which means buy NO)
            ask_order = self.api.place_order(
                ticker=ticker,
                side='no',
                quantity=quantity,
                order_type='limit',
                price=int((1 - ask_price) * 100)  # NO price = 1 - YES price
            )
            
            if not ask_order:
                # Cancel bid if ask fails
                if bid_order.get('order_id'):
                    self.api.cancel_order(bid_order['order_id'])
                return {'status': 'error', 'reason': 'Failed to place ask'}
            
            # Track active quotes
            self.active_quotes[ticker] = {
                'bid_order_id': bid_order.get('order_id'),
                'ask_order_id': ask_order.get('order_id'),
                'bid_price': bid_price,
                'ask_price': ask_price,
                'quantity': quantity
            }
            
            logger.info(f"Successfully placed quotes on {ticker}")
            
            return {
                'status': 'success',
                'ticker': ticker,
                'bid_order': bid_order,
                'ask_order': ask_order
            }
            
        except Exception as e:
            logger.error(f"Failed to place quotes: {e}")
            return {'status': 'error', 'reason': str(e)}
    
    def cancel_quotes(self, ticker: str = None):
        """
        Cancel quotes for a ticker or all tickers
        
        Args:
            ticker: Ticker to cancel (None = all)
        """
        if ticker:
            if ticker in self.active_quotes:
                quote = self.active_quotes[ticker]
                
                if quote.get('bid_order_id'):
                    self.api.cancel_order(quote['bid_order_id'])
                
                if quote.get('ask_order_id'):
                    self.api.cancel_order(quote['ask_order_id'])
                
                del self.active_quotes[ticker]
                logger.info(f"Cancelled quotes for {ticker}")
        else:
            # Cancel all quotes
            for t in list(self.active_quotes.keys()):
                self.cancel_quotes(t)
    
    def manage_inventory(self, positions: List[Dict]) -> List[Dict]:
        """
        Manage inventory - close positions that exceed limits
        
        Args:
            positions: Current positions
        
        Returns:
            List of positions to close
        """
        to_close = []
        
        for pos in positions:
            ticker = pos.get('ticker', '')
            quantity = abs(pos.get('position', 0))
            
            # Check if inventory exceeds limit
            if quantity > self.max_inventory:
                logger.warning(
                    f"Inventory for {ticker} ({quantity}) exceeds limit ({self.max_inventory})"
                )
                to_close.append(pos)
        
        return to_close
    
    def run(self, markets: List[str], refresh_interval: int = 30,
            max_iterations: int = None, dry_run: bool = False) -> List[Dict]:
        """
        Run market making strategy
        
        Args:
            markets: List of market tickers to make
            refresh_interval: Seconds between quote updates
            max_iterations: Maximum iterations (None = infinite)
            dry_run: If True, don't actually place orders
        
        Returns:
            List of execution results
        """
        if not markets:
            logger.error("No markets specified")
            return []
        
        logger.info(f"Starting market making on {len(markets)} markets")
        logger.info(f"Target spread: {self.target_spread:.1%}, Refresh: {refresh_interval}s")
        
        results = []
        iteration = 0
        
        try:
            while max_iterations is None or iteration < max_iterations:
                iteration += 1
                logger.info(f"Iteration {iteration}")
                
                # Get current positions
                positions = self.api.get_positions()
                position_map = {p.get('ticker'): p.get('position', 0) for p in positions}
                
                # Update quotes for each market
                for ticker in markets:
                    try:
                        # Get orderbook
                        orderbook = self.api.get_orderbook(ticker)
                        if not orderbook:
                            logger.warning(f"No orderbook for {ticker}")
                            continue
                        
                        # Calculate quotes
                        current_inventory = position_map.get(ticker, 0)
                        quotes = self.calculate_quotes(ticker, orderbook, current_inventory)
                        
                        if not quotes:
                            logger.warning(f"Could not calculate quotes for {ticker}")
                            continue
                        
                        # Calculate quote size
                        quote_size = self.risk_manager.calculate_position_size(
                            ticker, 'market_maker', quotes['mid_price'], confidence=0.5
                        )
                        
                        if quote_size <= 0:
                            logger.warning(f"No available size for {ticker}")
                            continue
                        
                        # Place quotes
                        result = self.place_quotes(ticker, quotes, quote_size, dry_run)
                        results.append(result)
                        
                        time.sleep(0.5)  # Brief pause between markets
                    
                    except Exception as e:
                        logger.error(f"Error making market on {ticker}: {e}")
                
                # Check inventory limits
                if positions:
                    to_close = self.manage_inventory(positions)
                    if to_close:
                        logger.warning(f"Need to close {len(to_close)} positions")
                
                # Wait before next iteration
                if max_iterations is None or iteration < max_iterations:
                    logger.info(f"Waiting {refresh_interval}s until next refresh...")
                    time.sleep(refresh_interval)
        
        except KeyboardInterrupt:
            logger.info("Market making stopped by user")
            self.cancel_quotes()  # Cancel all quotes on exit
        
        return results
