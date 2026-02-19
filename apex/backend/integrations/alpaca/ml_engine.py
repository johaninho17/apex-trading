
"""
APEX Institutional ML Engine
----------------------------
Inference engine for the 5-Factor XGBoost Model (S&P 500 Trained).
Predicts trade success probability based on:
1. RSI (Momentum)
2. SMA Distance (Trend Extension)
3. ATR Percent (Volatility)
4. Relative Volume (Liquidity/Interest)
5. Sector Relative Strength (Market Outperformance)

Note: Training logic has been moved to train_model.py for offline batch processing.
"""

import pandas as pd
import pandas_ta as ta
import yfinance as yf
from xgboost import XGBClassifier
import os
import joblib

class TradePredictor:
    def __init__(self, model_path_clean="apex_brain_clean.json", model_path_eventual="apex_brain_eventual.json"):
        self.model_path_clean = model_path_clean
        self.model_path_eventual = model_path_eventual
        
        self.model_clean = XGBClassifier()
        self.model_eventual = XGBClassifier()
        
        # Features must match train_dual_models.py EXACTLY
        self.features = ['RSI_14', 'SMA_Dist', 'ATR_Pct', 'Relative_Volume', 'ADX', 'Sector_RS']
        
        # Load Clean Win Brain
        if os.path.exists(self.model_path_clean):
            try:
                self.model_clean.load_model(self.model_path_clean)
                self.clean_loaded = True
                print(f"🛡️ Clean Win Brain loaded from {self.model_path_clean}")
            except Exception as e:
                print(f"Error loading Clean Win Brain: {e}")
                self.clean_loaded = False
        else:
            print(f"Warning: Clean Win Brain not found at {self.model_path_clean}")
            self.clean_loaded = False
            
        # Load Eventual Win Brain
        if os.path.exists(self.model_path_eventual):
            try:
                self.model_eventual.load_model(self.model_path_eventual)
                self.eventual_loaded = True
                print(f"🔄 Eventual Win Brain loaded from {self.model_path_eventual}")
            except Exception as e:
                print(f"Error loading Eventual Win Brain: {e}")
                self.eventual_loaded = False
        else:
            print(f"Warning: Eventual Win Brain not found at {self.model_path_eventual}")
            self.eventual_loaded = False
            
        self.is_trained = self.clean_loaded and self.eventual_loaded

    def get_trade_confidence(self, ticker):
        """
        Gets trade confidence from BOTH brains and returns composite score.
        
        Returns:
            dict: {
                'clean': float,      # Clean Win score (0-100)
                'eventual': float,   # Eventual Win score (0-100)
                'composite': float   # Weighted composite (70% clean + 30% eventual)
            }
        """
        if not self.is_trained:
            return {'clean': 50.0, 'eventual': 50.0, 'composite': 50.0}
            
        try:
            # --- 1. Fetch Data ---
            df = yf.download(ticker, period="6mo", interval="1d", progress=False)
            spy = yf.download("SPY", period="6mo", interval="1d", progress=False)
            
            if df.empty or spy.empty or len(df) < 50:
                return {'clean': 50.0, 'eventual': 50.0, 'composite': 50.0}
            
            if isinstance(df.columns, pd.MultiIndex):
                try: df.columns = df.columns.droplevel(1)
                except: pass
            if isinstance(spy.columns, pd.MultiIndex):
                try: spy.columns = spy.columns.droplevel(1)
                except: pass
            
            # --- 2. Calculate Features ---
            current_rsi = ta.rsi(df['Close'], length=14).iloc[-1]
            
            sma_20 = ta.sma(df['Close'], length=20).iloc[-1]
            current_sma_dist = (df['Close'].iloc[-1] - sma_20) / sma_20
            
            # Manual ATR calculation (pandas_ta returns DataFrame)
            df['H-L'] = df['High'] - df['Low']
            df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
            df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
            df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
            atr = df['TR'].rolling(window=14).mean()
            current_atr_pct = atr.iloc[-1] / df['Close'].iloc[-1]
            
            vol_sma = df['Volume'].rolling(20).mean()
            current_rel_vol = (df['Volume'] / vol_sma).iloc[-1]
            
            adx = df.ta.adx(length=14)
            if adx is not None and 'ADX_14' in adx.columns:
                current_adx = adx['ADX_14'].iloc[-1]
            else:
                current_adx = 0
                
            stock_pct = df['Close'].pct_change()
            spy_pct = spy['Close'].pct_change()
            
            sector_rs = stock_pct - spy_pct
            current_sector_rs = sector_rs.iloc[-1]
            
            # --- 3. Prepare Feature Vector ---
            # Must match the order in train_dual_models.py EXACTLY:
            # ['RSI_14', 'SMA_Dist', 'ATR_Pct', 'Relative_Volume', 'Sector_RS', 'ADX']
            
            feature_vector = pd.DataFrame([{
                'RSI_14': current_rsi,
                'SMA_Dist': current_sma_dist,
                'ATR_Pct': current_atr_pct,
                'Relative_Volume': current_rel_vol,
                'Sector_RS': current_sector_rs,
                'ADX': current_adx
            }])
            
            # --- 4. Get Predictions from Both Brains ---
            probs_clean = self.model_clean.predict_proba(feature_vector)
            probs_eventual = self.model_eventual.predict_proba(feature_vector)
            
            clean_score = round(probs_clean[0][1] * 100, 2)
            eventual_score = round(probs_eventual[0][1] * 100, 2)
            
            # --- 5. Calculate Composite Score (70% Clean + 30% Eventual) ---
            composite_score = round(0.7 * clean_score + 0.3 * eventual_score, 2)
            
            print(f"🧠 Dual Brain Result for {ticker}:")
            print(f"  🛡️  Clean Win: {clean_score}%")
            print(f"  🔄 Eventual Win: {eventual_score}%")
            print(f"  📊 Composite: {composite_score}%")
            
            return {
                'clean': clean_score,
                'eventual': eventual_score,
                'composite': composite_score
            }

        except Exception as e:
            print(f"Inference Error for {ticker}: {e}")
            return {'clean': 50.0, 'eventual': 50.0, 'composite': 50.0}

if __name__ == "__main__":
    # Test Run
    predictor = TradePredictor()
    if predictor.is_trained:
        print(f"Testing Prediction for NVDA: {predictor.get_trade_confidence('NVDA')}%")
