"""
Arbitrage trading strategy for Kalshi
Exploits pricing inefficiencies where YES + NO < $1
"""
import logging
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from api_client import KalshiAPI
from risk_manager import RiskManager
from config import Config

logger = logging.getLogger(__name__)


class ArbitrageStrategy:
    """Arbitrage strategy exploiting mispriced markets"""
    
    def __init__(self, api: KalshiAPI, risk_manager: RiskManager):
        """
        Initialize arbitrage strategy
        
        Args:
            api: KalshiAPI instance
            risk_manager: RiskManager instance
        """
        self.api = api
        self.risk_manager = risk_manager
        self.min_profit = Config.ARBITRAGE_MIN_PROFIT
    
    def _check_market_for_arbitrage(self, market: Dict) -> Optional[Dict]:
        """
        Check a single market for arbitrage opportunity
        
        Args:
            market: Market dictionary
        
        Returns:
            Opportunity dict if found, None otherwise
        """
        ticker = market.get('ticker', '')
        if not ticker:
            return None
        
        # Get orderbook
        orderbook_response = self.api.get_orderbook(ticker)
        if not orderbook_response:
            return None
        
        # Kalshi nests orderbook data under 'orderbook' key
        orderbook = orderbook_response.get('orderbook', {})
        if not orderbook:
            return None
        
        # Get best prices - Kalshi returns arrays of orders
        yes_orders = orderbook.get('yes', [])
        no_orders = orderbook.get('no', [])
        
        # Skip if no orders (empty orderbook)
        if not yes_orders or not no_orders:
            return None
        
        # Best ask prices (what we'd pay to buy)
        # Assuming orders are sorted, take first one
        if isinstance(yes_orders, list) and len(yes_orders) > 0:
            best_yes_ask = yes_orders[0]
        else:
            return None
            
        if isinstance(no_orders, list) and len(no_orders) > 0:
            best_no_ask = no_orders[0]
        else:
            return None
        
        yes_price = best_yes_ask.get('price', 0) / 100  # Convert cents to dollars
        no_price = best_no_ask.get('price', 0) / 100
        
        # Check for arbitrage (YES + NO < $1)
        total_cost = yes_price + no_price
        
        if total_cost < 1.0:
            profit = 1.0 - total_cost
            
            # Check if profit exceeds minimum threshold
            if profit >= self.min_profit:
                # Calculate max quantity we can trade
                max_yes_qty = best_yes_ask.get('quantity', 0)
                max_no_qty = best_no_ask.get('quantity', 0)
                max_qty = min(max_yes_qty, max_no_qty)
                
                # Calculate position size based on risk limits
                avg_price = total_cost / 2
                position_size = self.risk_manager.calculate_position_size(
                    ticker, 'arbitrage', avg_price, confidence=1.0
                )
                
                quantity = min(max_qty, position_size)
                
                if quantity > 0:
                    return {
                        'ticker': ticker,
                        'title': market.get('title', ''),
                        'yes_price': yes_price,
                        'no_price': no_price,
                        'total_cost': total_cost,
                        'profit_per_contract': profit,
                        'max_quantity': max_qty,
                        'recommended_quantity': quantity,
                        'expected_profit': profit * quantity
                    }
        
        return None
    
    def find_opportunities(self, markets: List[Dict] = None, parallel: bool = True) -> List[Dict]:
        """
        Find arbitrage opportunities
        
        Args:
            markets: Optional list of markets to scan (fetches if not provided)
            parallel: Use parallel processing for faster scanning
        
        Returns:
            List of arbitrage opportunities
        """
        if markets is None:
            markets = self.api.get_markets(limit=1000, status='open')  # Scan more markets
        
        opportunities = []
        
        if parallel:
            # Parallel scanning - much faster!
            logger.info(f"Scanning {len(markets)} markets in parallel...")
            
            with ThreadPoolExecutor(max_workers=10) as executor:
                # Submit all market checks
                future_to_market = {
                    executor.submit(self._check_market_for_arbitrage, market): market 
                    for market in markets
                }
                
                # Collect results as they complete
                for future in as_completed(future_to_market):
                    try:
                        opportunity = future.result()
                        if opportunity:
                            opportunities.append(opportunity)
                            logger.info(f"Found opportunity: {opportunity['ticker']} - ${opportunity['expected_profit']:.4f} profit")
                    except Exception as e:
                        logger.error(f"Error checking market: {e}")
        else:
            # Sequential scanning - slower but simpler
            for market in markets:
                opportunity = self._check_market_for_arbitrage(market)
                if opportunity:
                    opportunities.append(opportunity)
        
        # Sort by expected profit
        opportunities.sort(key=lambda x: x['expected_profit'], reverse=True)
        
        return opportunities
    
    def execute_arbitrage(self, opportunity: Dict, dry_run: bool = False) -> Dict:
        """
        Execute an arbitrage trade
        
        Args:
            opportunity: Arbitrage opportunity dictionary
            dry_run: If True, don't actually place orders
        
        Returns:
            Execution result
        """
        ticker = opportunity['ticker']
        yes_price = opportunity['yes_price']
        no_price = opportunity['no_price']
        quantity = opportunity['recommended_quantity']
        
        logger.info(
            f"Executing arbitrage on {ticker}: "
            f"Buy {quantity} YES @ ${yes_price:.2f} and {quantity} NO @ ${no_price:.2f}"
        )
        
        if dry_run or not Config.ENABLE_TRADING:
            logger.info("DRY RUN: Would execute arbitrage")
            return {
                'status': 'dry_run',
                'ticker': ticker,
                'quantity': quantity,
                'expected_profit': opportunity['expected_profit']
            }
        
        # Validate orders
        yes_valid, yes_reason = self.risk_manager.validate_order(
            ticker, 'yes', quantity, yes_price, yes_price
        )
        
        no_valid, no_reason = self.risk_manager.validate_order(
            ticker, 'no', quantity, no_price, no_price
        )
        
        if not yes_valid:
            logger.error(f"YES order validation failed: {yes_reason}")
            return {'status': 'error', 'reason': yes_reason}
        
        if not no_valid:
            logger.error(f"NO order validation failed: {no_reason}")
            return {'status': 'error', 'reason': no_reason}
        
        # Place orders
        try:
            # Place YES order
            yes_order = self.api.place_order(
                ticker=ticker,
                side='yes',
                quantity=quantity,
                order_type='limit',
                price=int(yes_price * 100)  # Convert to cents
            )
            
            if not yes_order:
                return {'status': 'error', 'reason': 'Failed to place YES order'}
            
            # Place NO order
            no_order = self.api.place_order(
                ticker=ticker,
                side='no',
                quantity=quantity,
                order_type='limit',
                price=int(no_price * 100)  # Convert to cents
            )
            
            if not no_order:
                # Cancel YES order if NO order fails
                if yes_order.get('order_id'):
                    self.api.cancel_order(yes_order['order_id'])
                return {'status': 'error', 'reason': 'Failed to place NO order'}
            
            # Update risk manager
            self.risk_manager.update_position(ticker, 'yes', quantity, yes_price, 'add')
            self.risk_manager.update_position(ticker, 'no', quantity, no_price, 'add')
            
            logger.info(f"Arbitrage executed successfully on {ticker}")
            
            return {
                'status': 'success',
                'ticker': ticker,
                'yes_order': yes_order,
                'no_order': no_order,
                'quantity': quantity,
                'expected_profit': opportunity['expected_profit']
            }
            
        except Exception as e:
            logger.error(f"Failed to execute arbitrage: {e}")
            return {'status': 'error', 'reason': str(e)}
    
    def run(self, max_opportunities: int = 5, dry_run: bool = False) -> List[Dict]:
        """
        Run arbitrage strategy
        
        Args:
            max_opportunities: Maximum number of opportunities to execute
            dry_run: If True, don't actually place orders
        
        Returns:
            List of execution results
        """
        logger.info("Scanning for arbitrage opportunities...")
        
        opportunities = self.find_opportunities()
        
        if not opportunities:
            logger.info("No arbitrage opportunities found")
            return []
        
        logger.info(f"Found {len(opportunities)} arbitrage opportunities")
        
        # Execute top opportunities
        results = []
        for opp in opportunities[:max_opportunities]:
            result = self.execute_arbitrage(opp, dry_run=dry_run)
            results.append(result)
            
            # Stop if we hit an error
            if result.get('status') == 'error':
                logger.warning("Stopping due to error")
                break
        
        return results
