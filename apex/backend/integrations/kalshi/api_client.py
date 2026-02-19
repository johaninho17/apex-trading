"""
Unified API client for Kalshi
Wraps Kalshi API with RSA signature-based authentication
"""
import requests
import logging
import base64
import time
import os
from urllib.parse import urlparse
from typing import Dict, List, Optional, Any
from datetime import datetime
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
from config import Config
from utils import RateLimiter

logger = logging.getLogger(__name__)


class KalshiAPI:
    """Unified Kalshi API client with RSA authentication"""
    
    def __init__(self, api_key_id: str = None, private_key: str = None):
        """
        Initialize Kalshi API client
        
        Args:
            api_key_id: Kalshi API Key ID (optional, uses config if not provided)
            private_key: RSA private key or path to key file (optional, uses config if not provided)
        """
        self.api_key_id = api_key_id or Config.KALSHI_API_KEY_ID
        private_key_input = private_key or Config.KALSHI_PRIVATE_KEY
        self.api_url = Config.get_api_url()
        self.token = None  # Back-compat flag used by legacy auth checks.
        
        # Session for connection pooling
        self.session = requests.Session()
        
        # Rate limiter
        self.rate_limiter = RateLimiter(calls_per_second=Config.API_CALLS_PER_SECOND)
        
        # Load private key
        self.private_key_obj = None
        if private_key_input:
            try:
                self.private_key_obj = self._load_private_key(private_key_input)
                self.token = "rsa_auth"
                logger.info("Kalshi API client initialized successfully with RSA authentication")
            except Exception as e:
                logger.error(f"Failed to load private key: {e}")
    
    def _load_private_key(self, private_key_input: str):
        """
        Load RSA private key from string or file
        
        Args:
            private_key_input: Private key content or path to key file
        
        Returns:
            Loaded private key object
        """
        # Check if it's a file path
        if os.path.exists(private_key_input):
            with open(private_key_input, 'rb') as f:
                key_data = f.read()
        else:
            # Treat as key content
            # Handle escaped newlines if present
            key_data = private_key_input.replace('\\n', '\n').encode()
        
        # Load the private key
        try:
            private_key = serialization.load_pem_private_key(
                key_data,
                password=None,
                backend=default_backend()
            )
            return private_key
        except Exception as e:
            logger.error(f"Failed to parse private key: {e}")
            raise
    
    def _generate_signature(self, timestamp: str, method: str, path: str) -> str:
        """
        Generate KALSHI-ACCESS-SIGNATURE for authentication
        
        Args:
            timestamp: Request timestamp in milliseconds
            method: HTTP method (GET, POST, DELETE)
            path: Request path without query parameters
        
        Returns:
            Base64-encoded signature
        """
        if not self.private_key_obj:
            raise ValueError("Private key not loaded")
        
        # Create message to sign: timestamp + method + full path (no query params)
        message = f"{timestamp}{method.upper()}{path.split('?')[0]}"
        message_bytes = message.encode('utf-8')
        
        # Sign with RSA-PSS and SHA256 (Kalshi expects DIGEST_LENGTH salt)
        signature = self.private_key_obj.sign(
            message_bytes,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )
        
        # Base64 encode
        return base64.b64encode(signature).decode('utf-8')
    
    def _get_auth_headers(self, method: str, path: str) -> Dict[str, str]:
        """
        Generate authentication headers for Kalshi API
        
        Args:
            method: HTTP method
            path: Request path without query parameters
        
        Returns:
            Dictionary of authentication headers
        """
        if not self.api_key_id or not self.private_key_obj:
            return {}
        
        # Generate timestamp in milliseconds
        timestamp = str(int(time.time() * 1000))
        
        # Generate signature
        signature = self._generate_signature(timestamp, method, path)
        
        return {
            'KALSHI-ACCESS-KEY': self.api_key_id,
            'KALSHI-ACCESS-TIMESTAMP': timestamp,
            'KALSHI-ACCESS-SIGNATURE': signature
        }
    
    def _make_request(self, method: str, endpoint: str, 
                     params: Dict = None, json: Dict = None) -> Any:
        """
        Make HTTP request with rate limiting and authentication
        
        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: API endpoint (without base URL)
            params: Query parameters
            json: JSON body
        
        Returns:
            Response JSON data
        """
        self.rate_limiter.wait_if_needed()
        
        url = f"{self.api_url}{endpoint}"
        
        # Sign the exact request path Kalshi expects (e.g. /trade-api/v2/portfolio/balance)
        parsed = urlparse(url)
        path = parsed.path
        
        # Get authentication headers
        headers = self._get_auth_headers(method.upper(), path)
        
        try:
            response = self.session.request(
                method=method,
                url=url,
                params=params,
                json=json,
                headers=headers,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {method} {endpoint} - {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            return None
    
    # Market Data Methods
    
    def get_markets(self, limit: int = 100, cursor: str = None, 
                   status: str = 'open') -> List[Dict]:
        """
        Get list of markets
        
        Args:
            limit: Number of markets to return
            cursor: Pagination cursor
            status: Market status filter ('open', 'closed', 'settled')
        
        Returns:
            List of market dictionaries
        """
        params = {
            'limit': limit,
            'status': status
        }
        
        if cursor:
            params['cursor'] = cursor
        
        result = self._make_request('GET', '/markets', params=params)
        
        if result and 'markets' in result:
            return result['markets']
        return []
    
    def get_market(self, ticker: str) -> Optional[Dict]:
        """
        Get specific market by ticker
        
        Args:
            ticker: Market ticker symbol
        
        Returns:
            Market dictionary or None
        """
        return self._make_request('GET', f'/markets/{ticker}')
    
    def get_market_history(self, ticker: str, limit: int = 100) -> List[Dict]:
        """
        Get historical data for a market
        
        Args:
            ticker: Market ticker
            limit: Number of historical points
        
        Returns:
            List of historical data points
        """
        params = {'limit': limit}
        result = self._make_request('GET', f'/markets/{ticker}/history', params=params)
        
        if result and 'history' in result:
            return result['history']
        return []
    
    def get_orderbook(self, ticker: str) -> Optional[Dict]:
        """
        Get orderbook for a market
        
        Args:
            ticker: Market ticker
        
        Returns:
            Orderbook data with bids and asks
        """
        return self._make_request('GET', f'/markets/{ticker}/orderbook')
    
    def get_trades(self, ticker: str = None, limit: int = 100) -> List[Dict]:
        """
        Get recent trades
        
        Args:
            ticker: Optional market ticker to filter
            limit: Number of trades to return
        
        Returns:
            List of trade dictionaries
        """
        params = {'limit': limit}
        
        if ticker:
            params['ticker'] = ticker
        
        result = self._make_request('GET', '/trades', params=params)
        
        if result and 'trades' in result:
            return result['trades']
        return []
    
    # Account Analysis Methods
    
    def get_account_trades(self, member_id: str = None, limit: int = 1000) -> List[Dict]:
        """
        Get trade history for an account
        
        Args:
            member_id: Member ID (uses authenticated user if not provided)
            limit: Maximum number of trades to return
        
        Returns:
            List of trade dictionaries
        """
        # Note: Kalshi API may not support querying other users' trades
        # This would need to be adapted based on actual API capabilities
        
        if not self.token and member_id:
            logger.warning("Cannot fetch other users' trades without authentication")
            return []
        
        params = {'limit': limit}
        
        if member_id:
            params['member_id'] = member_id
        
        result = self._make_request('GET', '/portfolio/fills', params=params)
        
        if result and 'fills' in result:
            return result['fills']
        return []
    
    def get_portfolio(self) -> Optional[Dict]:
        """
        Get current portfolio for authenticated user
        
        Returns:
            Portfolio data with positions and balance
        """
        if not self.token:
            logger.error("Authentication required to get portfolio")
            return None
        
        return self._make_request('GET', '/portfolio')
    
    def get_positions(self) -> List[Dict]:
        """
        Get current positions for authenticated user
        
        Returns:
            List of position dictionaries
        """
        if not self.token:
            logger.error("Authentication required to get positions")
            return []
        
        result = self._make_request('GET', '/portfolio/positions')
        
        if result and 'positions' in result:
            return result['positions']
        return []
    
    def get_balance(self) -> Optional[Dict]:
        """
        Get account balance
        
        Returns:
            Balance information
        """
        if not self.token:
            logger.error("Authentication required to get balance")
            return None
        
        return self._make_request('GET', '/portfolio/balance')
    
    # Trading Methods
    
    def place_order(self, ticker: str, side: str, quantity: int, 
                   order_type: str = 'limit', price: int = None,
                   expiration_ts: int = None) -> Optional[Dict]:
        """
        Place an order
        
        Args:
            ticker: Market ticker
            side: 'yes' or 'no'
            quantity: Number of contracts
            order_type: 'limit' or 'market'
            price: Limit price in cents (required for limit orders)
            expiration_ts: Optional expiration timestamp
        
        Returns:
            Order response or None
        """
        if not self.token:
            logger.error("Authentication required to place orders")
            return None
        
        if not Config.ENABLE_TRADING:
            logger.warning(
                f"DRY RUN: Would place {side.upper()} order for {quantity} "
                f"contracts of {ticker} @ {price} cents"
            )
            return {
                'status': 'dry_run',
                'ticker': ticker,
                'side': side,
                'quantity': quantity,
                'price': price
            }
        
        payload = {
            'ticker': ticker,
            'action': 'buy',  # Always 'buy' for Kalshi
            'side': side.lower(),
            'count': quantity,
            'type': order_type
        }
        
        if order_type == 'limit' and price is not None:
            payload['yes_price'] = price if side.lower() == 'yes' else None
            payload['no_price'] = price if side.lower() == 'no' else None
        
        if expiration_ts:
            payload['expiration_ts'] = expiration_ts
        
        try:
            result = self._make_request('POST', '/portfolio/orders', json=payload)
            logger.info(f"Order placed: {result}")
            return result
        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None
    
    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order
        
        Args:
            order_id: Order ID to cancel
        
        Returns:
            True if successful
        """
        if not self.token:
            logger.error("Authentication required to cancel orders")
            return False
        
        try:
            self._make_request('DELETE', f'/portfolio/orders/{order_id}')
            logger.info(f"Order cancelled: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order: {e}")
            return False
    
    def get_open_orders(self) -> List[Dict]:
        """
        Get all open orders for the authenticated account
        
        Returns:
            List of open orders
        """
        if not self.token:
            logger.error("Authentication required to get open orders")
            return []
        
        result = self._make_request('GET', '/portfolio/orders')
        
        if result and 'orders' in result:
            return result['orders']
        return []
    
    def cancel_all_orders(self, ticker: str = None) -> bool:
        """
        Cancel all open orders, optionally filtered by ticker
        
        Args:
            ticker: Optional ticker to filter cancellations
        
        Returns:
            True if successful
        """
        if not self.token:
            logger.error("Authentication required to cancel orders")
            return False
        
        orders = self.get_open_orders()
        
        if ticker:
            orders = [o for o in orders if o.get('ticker') == ticker]
        
        success = True
        for order in orders:
            if not self.cancel_order(order.get('order_id')):
                success = False
        
        return success
