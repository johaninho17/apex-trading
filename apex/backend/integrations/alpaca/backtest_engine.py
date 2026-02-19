import pandas as pd
import pandas_ta as ta
import yfinance as yf
import numpy as np

class Backtester:
    def __init__(self, ticker, strategy_type, investment_amount=10000.0):
        self.ticker = ticker
        self.strategy = strategy_type
        self.initial_balance = investment_amount
        self.balance = investment_amount
        self.trades = []
        self.equity_curve = []

    def get_data(self):
        # Fetch 1 year of data
        try:
            df = yf.download(self.ticker, period="1y", interval="1d", progress=False)
            if df.empty: return None
            
            if isinstance(df.columns, pd.MultiIndex):
                try: df.columns = df.columns.droplevel(1)
                except: pass
            df = df.loc[:, ~df.columns.duplicated()]
            
            # Indicators
            df.ta.sma(length=20, append=True)
            df.ta.sma(length=50, append=True)
            df.ta.rsi(length=14, append=True)
            df.ta.atr(length=14, append=True)
            
            return df
        except Exception as e:
            print(f"Error fetching data: {e}")
            return None

    def run(self):
        df = self.get_data()
        if df is None or df.empty: 
            return None

        in_position = False
        entry_price = 0.0
        stop_loss = 0.0
        target_price = 0.0
        qty = 0
        entry_date = None
        
        # We need a continuous equity curve, not just trade endpoints
        # So we iterate day by day and track balance
        
        dates = df.index
        for i in range(50, len(df)): # Start after indicators stabilize
            row = df.iloc[i]
            date = dates[i]
            price = row['Close']
            high = row['High']
            low = row['Low']
            
            # Logic: Check Exit first if in position
            if in_position:
                exit_signal = False
                exit_price = 0.0
                reason = ""
                
                # Check Stop Loss (Assume triggered at Stop Price)
                if low <= stop_loss:
                    exit_signal = True
                    exit_price = stop_loss
                    reason = "Stop Loss"
                # Check Target (Assume triggered at Target Price)
                elif high >= target_price:
                    exit_signal = True
                    exit_price = target_price
                    reason = "Target Hit"
                # Force close at end
                elif i == len(df) - 1:
                    exit_signal = True
                    exit_price = price
                    reason = "End of Data"

                if exit_signal:
                    pnl = (exit_price - entry_price) * qty
                    self.balance += pnl
                    self.trades.append({
                        'Entry Date': entry_date,
                        'Exit Date': date,
                        'Entry Price': round(entry_price, 2),
                        'Exit Price': round(exit_price, 2),
                        'PnL': round(pnl, 2),
                        'Reason': reason,
                        'Return %': round((pnl / (entry_price * qty)) * 100, 2)
                    })
                    in_position = False
                    qty = 0

            # Logic: Check Entry if not in position (and didn't just exit same day)
            if not in_position and i < len(df) - 1:
                entry_signal = False
                
                # --- STRATEGIES ---
                if "Aggressive" in self.strategy:
                    # Momentum: RSI > 50 AND Price > SMA 20
                    if row['RSI_14'] > 50 and price > row['SMA_20']:
                        entry_signal = True
                        stop_loss = price * 0.97
                        target_price = price * 1.06
                        
                elif "Conservative" in self.strategy:
                    # Pullback: Price touched SMA 20 (Low < SMA20 < High) AND RSI < 60
                    if low <= row['SMA_20'] <= high and row['RSI_14'] < 60:
                        entry_signal = True
                        # Assume we bought at SMA 20 limit
                        sim_entry = row['SMA_20']
                        # Re-check if valid within this bar? 
                        # If we buy at SMA 20, price must have crossed it. 
                        price = sim_entry 
                        stop_loss = price * 0.95
                        target_price = price * 1.10

                elif "Trend" in self.strategy:
                    # Trend: Price > SMA 50
                    if price > row['SMA_50']:
                        entry_signal = True
                        stop_loss = row['SMA_50'] * 0.98
                        if stop_loss > price: stop_loss = price * 0.90 # Sanity check
                        risk = price - stop_loss
                        target_price = price + (risk * 3)

                if entry_signal:
                    in_position = True
                    entry_price = price
                    entry_date = date
                    # Position Sizing: Use current balance
                    qty = self.balance / entry_price
            
            # Record Equity Curve (Mark to Market)
            current_equity = self.balance
            if in_position:
                # Unrealized PnL
                current_equity = self.balance + ((price - entry_price) * qty)
                
            self.equity_curve.append({
                'Date': date,
                'Equity': round(current_equity, 2)
            })
                    
        return self.get_results()

    def get_results(self):
        trades_df = pd.DataFrame(self.trades)
        equity_df = pd.DataFrame(self.equity_curve)
        
        if trades_df.empty:
            return {
                "Total Trades": 0,
                "Win Rate": 0,
                "Profit Factor": 0,
                "Total Return": 0,
                "Trades": pd.DataFrame(),
                "Equity": equity_df
            }
        
        wins = trades_df[trades_df['PnL'] > 0]
        losses = trades_df[trades_df['PnL'] <= 0]
        
        win_rate = (len(wins) / len(trades_df)) * 100
        gross_win = wins['PnL'].sum()
        gross_loss = abs(losses['PnL'].sum())
        profit_factor = gross_win / gross_loss if gross_loss > 0 else 999.0
        
        return {
            "Total Trades": len(trades_df),
            "Win Rate": round(win_rate, 2),
            "Profit Factor": round(profit_factor, 2),
            "Total Return": round(self.balance - self.initial_balance, 2),
            "Trades": trades_df,
            "Equity": equity_df
        }

    def get_win_rate(self):
        """Runs the backtest and returns just the Win Rate %."""
        results = self.run()
        if results and results['Total Trades'] > 5: # Require minimum trades for validity
            return results['Win Rate']
        return 50.0 # Default fallback if not enough data

if __name__ == "__main__":
    # Test
    bt = Backtester("NVDA", "Aggressive (Momentum)")
    results = bt.run()
    print(results)
