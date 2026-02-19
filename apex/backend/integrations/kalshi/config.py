"""
Configuration management for Kalshi bot
Loads settings from environment variables with sensible defaults
"""
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Central configuration for Kalshi trading bot"""
    
    # Kalshi API Configuration
    KALSHI_API_KEY_ID = os.getenv('KALSHI_API_KEY_ID', '')
    KALSHI_PRIVATE_KEY = os.getenv('KALSHI_PRIVATE_KEY', '')
    KALSHI_API_URL = os.getenv('KALSHI_API_URL', 'https://api.elections.kalshi.com/trade-api/v2')
    KALSHI_DEMO_URL = os.getenv('KALSHI_DEMO_URL', 'https://demo-api.kalshi.co/trade-api/v2')
    USE_DEMO = os.getenv('USE_DEMO', 'false').lower() == 'true'
    
    # Trading Configuration
    MAX_POSITION_SIZE = float(os.getenv('MAX_POSITION_SIZE', '100'))  # USD
    MAX_TOTAL_EXPOSURE = float(os.getenv('MAX_TOTAL_EXPOSURE', '1000'))  # USD
    ENABLE_TRADING = os.getenv('ENABLE_TRADING', 'false').lower() == 'true'
    
    # Risk Management
    STOP_LOSS_PERCENTAGE = float(os.getenv('STOP_LOSS_PERCENTAGE', '10'))  # %
    MAX_SLIPPAGE = float(os.getenv('MAX_SLIPPAGE', '0.02'))  # 2%
    MIN_PROFIT_THRESHOLD = float(os.getenv('MIN_PROFIT_THRESHOLD', '0.01'))  # $0.01
    
    # Bot Detection Thresholds
    MIN_TRADES_FOR_ANALYSIS = int(os.getenv('MIN_TRADES_FOR_ANALYSIS', '10'))
    BOT_SCORE_THRESHOLD = float(os.getenv('BOT_SCORE_THRESHOLD', '0.7'))
    
    # Strategy Configuration
    ARBITRAGE_MIN_PROFIT = float(os.getenv('ARBITRAGE_MIN_PROFIT', '0.02'))  # $0.02
    MARKET_MAKER_SPREAD = float(os.getenv('MARKET_MAKER_SPREAD', '0.02'))  # 2%
    COPY_TRADE_RATIO = float(os.getenv('COPY_TRADE_RATIO', '0.1'))  # 10%
    
    # Logging Configuration
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', 'kalshi_bot.log')
    
    # Rate Limiting
    API_CALLS_PER_SECOND = int(os.getenv('API_CALLS_PER_SECOND', '10'))
    
    @classmethod
    def validate(cls):
        """
        Validate configuration
        
        Raises:
            ValueError: If required configuration is missing or invalid
        """
        errors = []
        
        # Check API credentials if trading is enabled
        if cls.ENABLE_TRADING and not cls.USE_DEMO:
            if not cls.KALSHI_API_KEY_ID:
                errors.append("KALSHI_API_KEY_ID is required when trading is enabled")
            if not cls.KALSHI_PRIVATE_KEY:
                errors.append("KALSHI_PRIVATE_KEY is required when trading is enabled")
        
        # Validate numeric ranges
        if cls.MAX_POSITION_SIZE <= 0:
            errors.append("MAX_POSITION_SIZE must be positive")
        
        if cls.MAX_TOTAL_EXPOSURE <= 0:
            errors.append("MAX_TOTAL_EXPOSURE must be positive")
        
        if cls.MAX_POSITION_SIZE > cls.MAX_TOTAL_EXPOSURE:
            errors.append("MAX_POSITION_SIZE cannot exceed MAX_TOTAL_EXPOSURE")
        
        if not 0 <= cls.STOP_LOSS_PERCENTAGE <= 100:
            errors.append("STOP_LOSS_PERCENTAGE must be between 0 and 100")
        
        if not 0 <= cls.MAX_SLIPPAGE <= 1:
            errors.append("MAX_SLIPPAGE must be between 0 and 1")
        
        if not 0 <= cls.BOT_SCORE_THRESHOLD <= 1:
            errors.append("BOT_SCORE_THRESHOLD must be between 0 and 1")
        
        if errors:
            raise ValueError("Configuration errors:\n" + "\n".join(f"  - {e}" for e in errors))
    
    @classmethod
    def get_api_url(cls):
        """Get the appropriate API URL based on demo mode"""
        return cls.KALSHI_DEMO_URL if cls.USE_DEMO else cls.KALSHI_API_URL
    
    @classmethod
    def summary(cls):
        """Return a summary of current configuration"""
        return f"""
Kalshi Bot Configuration:
========================
API URL: {cls.get_api_url()}
Demo Mode: {cls.USE_DEMO}
Trading Enabled: {cls.ENABLE_TRADING}

Position Limits:
  Max Position Size: ${cls.MAX_POSITION_SIZE}
  Max Total Exposure: ${cls.MAX_TOTAL_EXPOSURE}

Risk Management:
  Stop Loss: {cls.STOP_LOSS_PERCENTAGE}%
  Max Slippage: {cls.MAX_SLIPPAGE * 100}%
  Min Profit: ${cls.MIN_PROFIT_THRESHOLD}

Bot Detection:
  Min Trades: {cls.MIN_TRADES_FOR_ANALYSIS}
  Bot Threshold: {cls.BOT_SCORE_THRESHOLD}

Logging:
  Level: {cls.LOG_LEVEL}
  File: {cls.LOG_FILE}
"""
