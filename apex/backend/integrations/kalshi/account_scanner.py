"""
Account scanner for analyzing Kalshi accounts
"""
import logging
from typing import Dict, List, Optional
from datetime import datetime
from api_client import KalshiAPI
from bot_detector import BotDetector
from utils import format_timestamp, format_usd, truncate_address, calculate_statistics

logger = logging.getLogger(__name__)


class AccountScanner:
    """Scanner for analyzing Kalshi account activity"""
    
    def __init__(self, api: KalshiAPI = None):
        """
        Initialize account scanner
        
        Args:
            api: KalshiAPI instance (creates new one if not provided)
        """
        self.api = api or KalshiAPI()
        self.bot_detector = BotDetector()
    
    def scan_account(self, account_id: str = None, detailed: bool = False) -> Dict:
        """
        Scan an account for bot-like behavior
        
        Args:
            account_id: Account ID to scan (uses authenticated user if None)
            detailed: Include detailed trade analysis
        
        Returns:
            Account analysis results
        """
        logger.info(f"Scanning account: {account_id or 'authenticated user'}")
        
        # Fetch account data
        trades = self.api.get_account_trades(account_id)
        positions = self.api.get_positions() if not account_id else []
        
        if not trades:
            return {
                'error': 'No trades found for this account',
                'account_id': account_id
            }
        
        # Run bot detection
        bot_analysis = self.bot_detector.analyze_account(trades, positions)
        
        # Calculate trading statistics
        trade_stats = self._calculate_trade_stats(trades)
        
        # Calculate position statistics if available
        position_stats = self._calculate_position_stats(positions) if positions else {}
        
        # Build analysis result
        analysis = {
            'account_id': account_id or 'authenticated_user',
            'scan_time': datetime.now().isoformat(),
            'bot_analysis': bot_analysis,
            'trade_stats': trade_stats,
            'position_stats': position_stats
        }
        
        # Add detailed trade info if requested
        if detailed:
            analysis['recent_trades'] = self._format_trades(trades[:20])
            if positions:
                analysis['positions'] = self._format_positions(positions)
        
        return analysis
    
    def scan_top_traders(self, limit: int = 50) -> List[Dict]:
        """
        Scan top traders for bot activity
        
        Args:
            limit: Number of top traders to scan
        
        Returns:
            List of account analyses
        """
        # Note: This would require a leaderboard API endpoint
        # For now, return empty list with warning
        logger.warning("Kalshi API may not provide public leaderboard access")
        logger.warning("This feature requires account IDs to be provided manually")
        return []
    
    def _calculate_trade_stats(self, trades: List[Dict]) -> Dict:
        """Calculate trading statistics"""
        if not trades:
            return {}
        
        # Extract trade data
        sizes = []
        prices = []
        timestamps = []
        tickers = []
        
        for trade in trades:
            size = trade.get('count', trade.get('quantity', 0))
            if size > 0:
                sizes.append(size)
            
            price = trade.get('yes_price', trade.get('no_price', trade.get('price', 0)))
            if price > 0:
                prices.append(price / 100)  # Convert cents to dollars
            
            ts = trade.get('created_time', trade.get('timestamp'))
            if ts:
                timestamps.append(ts)
            
            ticker = trade.get('ticker', '')
            if ticker:
                tickers.append(ticker)
        
        # Calculate statistics
        stats = {
            'total_trades': len(trades),
            'unique_markets': len(set(tickers)) if tickers else 0,
            'total_volume': sum(s * p for s, p in zip(sizes[:len(prices)], prices)) if sizes and prices else 0
        }
        
        if sizes:
            size_stats = calculate_statistics(sizes)
            stats.update({
                'avg_size': size_stats['mean'],
                'median_size': size_stats['median'],
                'min_size': size_stats['min'],
                'max_size': size_stats['max']
            })
        
        if prices:
            price_stats = calculate_statistics(prices)
            stats.update({
                'avg_price': price_stats['mean'],
                'median_price': price_stats['median']
            })
        
        if len(timestamps) >= 2:
            # Calculate time span
            first_trade = min(timestamps) if isinstance(timestamps[0], (int, float)) else timestamps[-1]
            last_trade = max(timestamps) if isinstance(timestamps[0], (int, float)) else timestamps[0]
            stats['first_trade'] = first_trade
            stats['last_trade'] = last_trade
        
        return stats
    
    def _calculate_position_stats(self, positions: List[Dict]) -> Dict:
        """Calculate position statistics"""
        if not positions:
            return {}
        
        total_value = 0
        total_pnl = 0
        
        for pos in positions:
            value = pos.get('position', 0) * pos.get('market_price', 0) / 100
            total_value += value
            total_pnl += pos.get('realized_pnl', 0) + pos.get('unrealized_pnl', 0)
        
        return {
            'total_positions': len(positions),
            'total_value': total_value,
            'total_pnl': total_pnl
        }
    
    def _format_trades(self, trades: List[Dict]) -> List[Dict]:
        """Format trades for display"""
        formatted = []
        
        for trade in trades:
            formatted.append({
                'ticker': trade.get('ticker', 'N/A'),
                'side': trade.get('side', 'N/A'),
                'count': trade.get('count', 0),
                'price': trade.get('yes_price', trade.get('no_price', 0)),
                'timestamp': trade.get('created_time', trade.get('timestamp', 'N/A'))
            })
        
        return formatted
    
    def _format_positions(self, positions: List[Dict]) -> List[Dict]:
        """Format positions for display"""
        formatted = []
        
        for pos in positions:
            formatted.append({
                'ticker': pos.get('ticker', 'N/A'),
                'position': pos.get('position', 0),
                'market_price': pos.get('market_price', 0),
                'unrealized_pnl': pos.get('unrealized_pnl', 0)
            })
        
        return formatted
    
    def generate_report(self, analysis: Dict) -> str:
        """
        Generate human-readable report from account analysis
        
        Args:
            analysis: Account analysis dictionary
        
        Returns:
            Formatted report string
        """
        if 'error' in analysis:
            return f"Error: {analysis['error']}"
        
        account_id = analysis.get('account_id', 'Unknown')
        bot_analysis = analysis.get('bot_analysis', {})
        trade_stats = analysis.get('trade_stats', {})
        
        # Build report
        lines = []
        lines.append("=" * 60)
        lines.append(f"ACCOUNT ANALYSIS: {account_id}")
        lines.append("=" * 60)
        lines.append("")
        
        # Bot detection section
        if bot_analysis and 'bot_score' in bot_analysis:
            lines.append("🤖 BOT DETECTION")
            lines.append("-" * 60)
            
            bot_score = bot_analysis['bot_score']
            classification = bot_analysis.get('classification', 'UNKNOWN')
            confidence = bot_analysis.get('confidence', 'UNKNOWN')
            
            # Visual indicator
            if bot_score >= 0.7:
                indicator = "🚨 LIKELY BOT"
            elif bot_score >= 0.5:
                indicator = "⚠️  UNCERTAIN"
            else:
                indicator = "✅ LIKELY HUMAN"
            
            lines.append(f"Bot Score: {bot_score:.2f} / 1.00")
            lines.append(f"Classification: {indicator}")
            lines.append(f"Confidence: {confidence}")
            lines.append(f"Reason: {bot_analysis.get('reason', 'N/A')}")
            lines.append("")
            
            # Indicators
            lines.append("📊 INDICATORS")
            lines.append("-" * 60)
            
            indicators = bot_analysis.get('indicators', {})
            for name, score in sorted(indicators.items(), key=lambda x: x[1], reverse=True):
                bar_length = int(score * 20)
                bar = "█" * bar_length
                lines.append(f"{name.replace('_', ' ').title():.<30} {score:.2f} {bar}")
            
            lines.append("")
        
        # Trading statistics
        if trade_stats:
            lines.append("📈 TRADING STATISTICS")
            lines.append("-" * 60)
            lines.append(f"Total Trades: {trade_stats.get('total_trades', 0)}")
            lines.append(f"Unique Markets: {trade_stats.get('unique_markets', 0)}")
            lines.append(f"Total Volume: {format_usd(trade_stats.get('total_volume', 0))}")
            
            if 'avg_size' in trade_stats:
                lines.append(f"Avg Size: {trade_stats['avg_size']:.1f} contracts")
                lines.append(f"Median Size: {trade_stats['median_size']:.1f} contracts")
            
            if 'avg_price' in trade_stats:
                lines.append(f"Avg Price: {format_usd(trade_stats['avg_price'])}")
            
            lines.append("")
        
        # Position statistics
        position_stats = analysis.get('position_stats', {})
        if position_stats:
            lines.append("💼 POSITIONS")
            lines.append("-" * 60)
            lines.append(f"Total Positions: {position_stats.get('total_positions', 0)}")
            lines.append(f"Total Value: {format_usd(position_stats.get('total_value', 0))}")
            lines.append(f"Total P&L: {format_usd(position_stats.get('total_pnl', 0))}")
            lines.append("")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
