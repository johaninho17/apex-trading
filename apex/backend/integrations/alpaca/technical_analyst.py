import pandas as pd
import pandas_ta as ta
import yfinance as yf
import numpy as np
import warnings
import os
from ml_engine import TradePredictor

os.environ['TRANSFORMERS_OFFLINE'] = '1'  # Skip online checks
warnings.filterwarnings('ignore', message='.*UNEXPECTED.*')

# Role: Lead Quant Analyst (@Claude-Opus-4.5-Thinking)
# Objective: Perform deep technical analysis and generate trade plans.

class TechnicalAnalyst:
    def __init__(self, scanner_file="scanner_results.csv"):
        self.candidates = pd.read_csv(scanner_file) if pd.io.common.file_exists(scanner_file) else pd.DataFrame()
        self.predictor = TradePredictor()  # Initialize AI Brain

    def get_data(self, ticker):
        """Fetches detailed data (1 year) for analysis."""
        df = yf.download(ticker, period="1y", interval="1d", progress=False)
        if df.empty: return None
        
        if isinstance(df.columns, pd.MultiIndex):
            try: df.columns = df.columns.droplevel(1)
            except: pass
        df = df.loc[:, ~df.columns.duplicated()]
        return df

    def analyze_stock(self, ticker):
        """Calculates extensive indicators for multiple strategies."""
        df = self.get_data(ticker)
        if df is None: return None
        
        # --- Trend Filters ---
        df.ta.sma(length=20, append=True)
        df.ta.sma(length=50, append=True)
        df.ta.sma(length=200, append=True)
        
        # --- Strategy B: Trend Surfer Indicators ---
        df.ta.ema(length=9, append=True)
        df.ta.ema(length=21, append=True)
        
        # --- Oscillators ---
        df.ta.rsi(length=14, append=True)
        df.ta.macd(append=True)
        
        # --- Volatility ---
        df.ta.bbands(length=20, std=2, append=True)
        df.ta.atr(length=14, append=True)
        
        # --- Pivot Points (Approx 3-month) ---
        recent_high = df['High'].tail(60).max()
        recent_low = df['Low'].tail(60).min()
        
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2] if len(df) > 1 else last_row
        
        return {
            'Ticker': ticker,
            'Price': last_row['Close'],
            'SMA_20': last_row['SMA_20'],
            'SMA_50': last_row['SMA_50'],
            'SMA_200': last_row.get('SMA_200', last_row['Close']),
            'EMA_9': last_row['EMA_9'],
            'EMA_21': last_row['EMA_21'],
            'Prev_EMA_9': prev_row['EMA_9'],
            'Prev_EMA_21': prev_row['EMA_21'],
            'RSI': last_row['RSI_14'],
            'MACD': last_row['MACD_12_26_9'],
            'ATR': last_row['ATRr_14'],
            'Recent_High': recent_high,
            'Recent_Low': recent_low,
        }

    def analyze_sentiment(self, text):
        """Uses FinBERT to analyze sentiment of financial text."""
        try:
            from transformers import pipeline
            if not hasattr(self, 'sentiment_analyzer'):
                self.sentiment_analyzer = pipeline("text-classification", model="ProsusAI/finbert", tokenizer="ProsusAI/finbert")
            result = self.sentiment_analyzer(text[:512])[0]
            confidence = result['score'] * 100
            
            if result['label'] == 'positive': return 'Bullish', confidence
            elif result['label'] == 'negative': return 'Bearish', confidence
            else: return 'Neutral', confidence
        except:
            return 'Neutral', 50.0

    def fetch_news(self, ticker):
        """Fetches recent news and analyzes sentiment."""
        try:
            stock = yf.Ticker(ticker)
            news = stock.news
            if not news: return []
            
            enriched_news = []
            for article in news[:3]:
                content = article.get('content', {})
                title = content.get('title', article.get('title', ''))
                summary = content.get('summary', content.get('description', ''))
                sentiment, confidence = self.analyze_sentiment(f"{title}. {summary}")
                enriched_news.append({'raw': article, 'sentiment': sentiment, 'confidence': confidence, 'summary': summary})
            return enriched_news
        except: return []

    def calculate_atr_stop(self, entry_price, atr_value, multiplier=2.0):
        """Calculate adaptive stop loss based on ATR."""
        stop_distance = atr_value * multiplier
        stop_price = entry_price - stop_distance
        return {
            'stop_price': round(stop_price, 2),
            'stop_distance': round(stop_distance, 2),
            'atr_multiplier': multiplier,
            'description': f"{multiplier}x ATR (${stop_distance:.2f} buffer)"
        }

    def generate_trade_setups(self, analysis, strategy_type="ATR", ml_confidence=None):
        """Generates trade setups based on the selected strategy."""
        price = analysis['Price']
        atr = analysis['ATR']
        account_balance = 20.00 # Placeholder base unit
        setups = []
        
        # --- ML Confidence Injection ---
        if ml_confidence is None:
            ml_confidence = self.predictor.get_trade_confidence(analysis['Ticker'])
        
        if strategy_type == "MA":
            # --- Strategy B: Trend Surfer (MA Crossover) ---
            # Signal: EMA 9 Crosses Above EMA 21
            ema_9 = analysis['EMA_9']
            ema_21 = analysis['EMA_21']
            prev_ema_9 = analysis['Prev_EMA_9']
            prev_ema_21 = analysis['Prev_EMA_21']
            
            # Check for fresh crossover
            is_bullish_cross = (prev_ema_9 <= prev_ema_21) and (ema_9 > ema_21)
            is_already_trend = (ema_9 > ema_21) and (price > ema_9) # Riding the wave
            
            if is_bullish_cross or is_already_trend:
                # Setup 1: Trend Surfer Standard
                # Stop Loss: Dynamic 21-EMA (Trailing support)
                stop_ma = ema_21
                risk = price - stop_ma
                if risk <= 0: risk = 0.01 # Prevent div/0
                
                target = price + (risk * 2.5) # 1:2.5 RR
                
                description = "Fresh Crossover" if is_bullish_cross else "Trend Continuation"
                
                setups.append({
                    'Type': f'Trend Surfer ({description})',
                    'Entry': round(price, 2),
                    'Stop_Loss': round(stop_ma, 2),
                    'Stop_Description': f"21-EMA Trailing Support",
                    'Target': round(target, 2),
                    'Risk_Reward': 2.5,
                    'Qty': round(account_balance / price, 4),
                    'ATR_Buffer': round(risk, 2), # Using risk as proxy for buffer
                    'ML_Confidence': ml_confidence
                })
        
        else:
            # --- Strategy A: Volatility Hunter (ATR) --- (Keep existing logic)
            
            # 1. Aggressive (Momentum)
            atr_stop_agg = self.calculate_atr_stop(price, atr, multiplier=2.0)
            target_agg = max(analysis['Recent_High'], price * 1.06)
            risk_agg = price - atr_stop_agg['stop_price']
            rr_agg = (target_agg - price) / risk_agg if risk_agg > 0 else 0
            
            setups.append({
                'Type': 'Aggressive (Momentum)',
                'Entry': round(price, 2),
                'Stop_Loss': atr_stop_agg['stop_price'],
                'Stop_Description': atr_stop_agg['description'],
                'Target': round(target_agg, 2),
                'Risk_Reward': round(rr_agg, 2),
                'Qty': round(account_balance / price, 4),
                'ATR_Buffer': atr_stop_agg['stop_distance'],
                'ML_Confidence': ml_confidence
            })
            
            # 2. Conservative (Pullback)
            entry_con = analysis['SMA_20']
            atr_stop_con = self.calculate_atr_stop(entry_con, atr, multiplier=2.5)
            risk_con = entry_con - atr_stop_con['stop_price']
            rr_con = (analysis['Recent_High'] - entry_con) / risk_con if risk_con > 0 else 0
            
            setups.append({
                'Type': 'Conservative (Pullback)',
                'Entry': round(entry_con, 2),
                'Stop_Loss': atr_stop_con['stop_price'],
                'Stop_Description': atr_stop_con['description'],
                'Target': round(analysis['Recent_High'], 2),
                'Risk_Reward': round(rr_con, 2),
                'Qty': round(account_balance / entry_con, 4),
                'ATR_Buffer': atr_stop_con['stop_distance'],
                'ML_Confidence': ml_confidence
            })
            
            # 3. Trend Follower
            atr_stop_tr = self.calculate_atr_stop(price, atr, multiplier=3.0)
            setups.append({
                'Type': 'Trend Follower',
                'Entry': round(price, 2),
                'Stop_Loss': atr_stop_tr['stop_price'],
                'Stop_Description': atr_stop_tr['description'],
                'Target': round(price + ((price - atr_stop_tr['stop_price']) * 3), 2),
                'Risk_Reward': 3.0,
                'Qty': round(account_balance / price, 4),
                'ATR_Buffer': atr_stop_tr['stop_distance'],
                'ML_Confidence': ml_confidence
            })
            
        return setups

    def run(self):
        if self.candidates.empty:
            print("No candidates to analyze.")
            return
        
        # Pick best candidate (Top 1)
        best_ticker = self.candidates.iloc[0]['Ticker']
        print(f"Analyzing Best Candidate: {best_ticker}")
        
        analysis = self.analyze_stock(best_ticker)
        
        if analysis:
            print(f"\n--- Technical Analysis ({best_ticker}) ---")
            print(f"Price: ${analysis['Price']:.2f} | RSI: {analysis['RSI']:.1f}")
            
            # Generate setups for both strategies to showcase functionality
            setups_atr = self.generate_trade_setups(analysis, strategy_type="ATR")
            setups_ma = self.generate_trade_setups(analysis, strategy_type="MA")
            
            all_setups = setups_atr + setups_ma
            setups_df = pd.DataFrame(all_setups)
            
            print("\n--- Trade Setups (All Strategies) ---")
            print(setups_df[['Type', 'Entry', 'Stop_Loss', 'ML_Confidence']])
            
            setups_df.to_csv("trade_setups.csv", index=False)
            pd.DataFrame([analysis]).to_csv("analysis_summary.csv", index=False)
            
            return best_ticker, all_setups

if __name__ == "__main__":
    analyst = TechnicalAnalyst()
    analyst.run()
