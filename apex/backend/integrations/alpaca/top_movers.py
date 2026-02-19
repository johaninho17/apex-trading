#!/usr/bin/env python3
import os
import pandas as pd
import yfinance as yf
import pandas_ta as ta
from dotenv import load_dotenv
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from ml_engine import TradePredictor
from runtime_config import get_alpaca_credentials

# Role: Top Movers Scanner
# Objective: Fetch the top 20 most active stocks from Alpaca and enrich with analysis

load_dotenv()

def get_top_movers():
    """Fetch top movers from Alpaca API"""
    api_key, secret_key, _ = get_alpaca_credentials()
    
    if not api_key or not secret_key:
        print("Warning: Alpaca API keys not found. Using fallback list.")
        return get_fallback_movers()
    
    try:
        # Use Alpaca's market data API to get top movers
        # Note: Alpaca doesn't have a direct "get_movers" endpoint for paper trading
        # Instead, we'll fetch the most actively traded stocks from a curated list
        
        # Top volume leaders (updated periodically)
        top_volume_tickers = [
            "SPY", "TSLA", "NVDA", "AAPL", "AMD", "SOFI", "PLTR", "AMZN", "MSFT",
            "META", "GOOGL", "NFLX", "COIN", "HOOD", "RIVN", "LCID", "F", "GM",
            "BAC", "WFC", "JPM", "XOM", "CVX", "INTC", "MU", "QCOM", "AVGO"
        ]
        
        data_client = StockHistoricalDataClient(api_key, secret_key)
        
        # Get latest trades for volume/activity
        request = StockLatestTradeRequest(symbol_or_symbols=top_volume_tickers)
        latest_trades = data_client.get_stock_latest_trade(request)
        
        # Sort by recent activity (price * volume as proxy for "moving")
        movers = []
        for symbol, trade in latest_trades.items():
            movers.append({
                'ticker': symbol,
                'price': float(trade.price),
                'activity_score': float(trade.price) * float(trade.size)
            })
        
        # Sort by activity and take top 20
        movers_df = pd.DataFrame(movers)
        movers_df = movers_df.sort_values('activity_score', ascending=False).head(20)
        
        return movers_df['ticker'].tolist()
        
    except Exception as e:
        print(f"Error fetching top movers: {e}")
        return get_fallback_movers()

def get_fallback_movers():
    """Fallback list of high-activity stocks"""
    return [
        "SPY", "TSLA", "NVDA", "AAPL", "AMD", "SOFI", "PLTR", "AMZN", "MSFT",
        "META", "GOOGL", "NFLX", "COIN", "HOOD", "RIVN", "LCID", "F", "GM", "INTC", "MU"
    ]

def analyze_top_movers(tickers):
    """Analyze top movers with technical indicators and AI scores"""
    predictor = TradePredictor()
    results = []
    
    print(f"Analyzing {len(tickers)} top movers...")
    
    for i, ticker in enumerate(tickers):
        try:
            print(f"[{i+1}/{len(tickers)}] Analyzing {ticker}...")
            
            # Fetch recent data
            df = yf.download(ticker, period="1mo", interval="1d", progress=False)
            
            # Flatten columns if MultiIndex (yfinance sometimes returns multi-level columns)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            
            if df.empty or len(df) < 5:
                continue
            
            # Calculate indicators
            df['RSI_14'] = ta.rsi(df['Close'], length=14)
            df['VOL_SMA_10'] = ta.sma(df['Volume'], length=10)
            
            # Manual ATR calculation (pandas_ta sometimes returns None or DataFrame)
            df['H-L'] = df['High'] - df['Low']
            df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
            df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
            df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
            df['ATR_14'] = df['TR'].rolling(window=14).mean()
            
            # Calculate ATR percentage
            df['ATRr_14'] = (df['ATR_14'] / df['Close']) * 100
            
            # Debug: Check types (remove this after fixing)
            if pd.isna(df['ATRr_14'].iloc[-1]):
                print(f"  DEBUG {ticker}: ATRr_14 is NaN")
                print(f"  ATR_14 type: {type(df['ATR_14'])}, Close type: {type(df['Close'])}")
                continue
            
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else last
            
            price = last['Close']
            change = ((price - prev['Close']) / prev['Close']) * 100
            atr_pct = last['ATRr_14']
            rsi = last['RSI_14']
            volume = last['Volume']
            avg_vol = last['VOL_SMA_10']
            
            # Get AI score
            ai_scores = predictor.get_trade_confidence(ticker)
            composite = ai_scores['composite']
            
            results.append({
                'Ticker': ticker,
                'Price': round(price, 2),
                'Change_%': round(change, 2),
                'ATR_%': round(atr_pct, 2) if not pd.isna(atr_pct) else 0,
                'RSI': round(rsi, 2) if not pd.isna(rsi) else 50,
                'Volume': int(volume),
                'Rel_Vol': round(volume / avg_vol, 2) if not pd.isna(avg_vol) and avg_vol > 0 else 1.0,
                'AI_Score': round(composite, 0)
            })
            
        except Exception as e:
            print(f"Error analyzing {ticker}: {e}")
            continue
    
    return pd.DataFrame(results)

def main():
    """Main execution"""
    print("\n" + "="*60)
    print("TOP MOVERS SCANNER")
    print("="*60)
    
    # Fetch top movers
    tickers = get_top_movers()
    print(f"Fetched {len(tickers)} top movers")
    
    # Analyze
    results_df = analyze_top_movers(tickers)
    
    # Handle empty results
    if results_df.empty:
        print("\n⚠️  No stocks analyzed successfully. Creating empty CSV.")
        results_df.to_csv("top_movers.csv", index=False)
        print("="*60)
        return results_df
    
    # Sort by absolute change (biggest movers first)
    results_df['Abs_Change'] = results_df['Change_%'].abs()
    results_df = results_df.sort_values('Abs_Change', ascending=False)
    results_df = results_df.drop('Abs_Change', axis=1)
    
    # Save
    results_df.to_csv("top_movers.csv", index=False)
    
    print(f"\n✅ Saved {len(results_df)} top movers to top_movers.csv")
    print("="*60)
    
    return results_df

if __name__ == "__main__":
    main()
