#!/usr/bin/env python3
import os
import json
import time
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from datetime import datetime
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockSnapshotRequest
from ml_engine import TradePredictor
from dotenv import load_dotenv
from runtime_config import get_alpaca_credentials

# Role: Background Scanner Worker (OPTIMIZED)
# Objective: Scan the entire market for ATR and MA setups in ~3-5 minutes

load_dotenv()

class BackgroundScanner:
    def __init__(self):
        self.api_key, self.secret_key, paper = get_alpaca_credentials()
        self.client = TradingClient(self.api_key, self.secret_key, paper=paper) if self.api_key else None
        self.data_client = StockHistoricalDataClient(self.api_key, self.secret_key) if self.api_key else None
        self.predictor = TradePredictor()
        self.predictor = TradePredictor()
        self.status_file = "scan_status.json"
        self.pause_check_callback = None  # Function to call to check for pause
        
    def update_status(self, status, progress=0, total=0, last_match="", message=""):
        """Write current scan progress to JSON file"""
        status_data = {
            "status": status,
            "progress": progress,
            "total": total,
            "last_match": last_match,
            "message": message,
            "timestamp": int(time.time())
        }
        with open(self.status_file, 'w') as f:
            json.dump(status_data, f)
    
    def fetch_all_assets(self):
        """Fetch all tradeable US equities from Alpaca"""
        self.update_status("running", 0, 0, "", "Fetching asset list from Alpaca...")
        
        if not self.client:
            print("Warning: No Alpaca client. Using fallback ticker list.")
            return self.get_extended_ticker_list()
        
        try:
            # Fetch all active US equities
            assets = self.client.get_all_assets()
            tickers = [
                asset.symbol for asset in assets 
                if asset.tradable and asset.status == 'active' and asset.asset_class == 'us_equity'
            ]
            print(f"Fetched {len(tickers)} tradeable assets from Alpaca.")
            return tickers
        except Exception as e:
            print(f"Error fetching assets: {e}")
            return self.get_extended_ticker_list()
    
    def get_extended_ticker_list(self):
        """Extended fallback ticker list (broader than dashboard)"""
        return [
            # Tech Giants
            "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD", "NFLX",
            
            # Semiconductors
            "AVGO", "QCOM", "TXN", "INTC", "MU", "AMAT", "LRCX", "ADI", "MRVL", "TSM", "ARM", "SMCI",
            
            # Software & Cloud
            "CRM", "ADBE", "ORCL", "NOW", "INTU", "IBM", "PANW", "SNOW", "PLTR", "CRWD", "DDOG", "NET",
            
            # Fintech
            "V", "MA", "PYPL", "SQ", "COIN", "HOOD", "AFRM", "SOFI", "UPST",
            
            # EVs
            "RIVN", "LCID", "NIO", "XPEV", "LI", "F", "GM",
            
            # Consumer
            "COST", "WMT", "TGT", "HD", "LOW", "NKE", "SBUX", "MCD", "CMG", "LULU",
            
            # Healthcare
            "UNH", "JNJ", "PFE", "ABBV", "TMO", "ABT", "DHR", "LLY", "MRK", "BMY",
            
            # Energy
            "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "VLO", "MPC", "OXY", "HAL",
            
            # Finance
            "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW", "AXP", "USB",
            
            # Industrials
            "BA", "CAT", "GE", "HON", "UPS", "RTX", "LMT", "DE", "MMM", "FDX",
            
            # ETFs
            "SPY", "QQQ", "IWM", "DIA", "XLK", "XLE", "XLF", "XLV", "XLI"
        ]
    
    def pre_filter_with_snapshots(self, tickers):
        """OPTIMIZED: Use Alpaca Snapshots to filter in 1 API call"""
        self.update_status("running", 0, len(tickers), "", "Pre-filtering with Alpaca Snapshots...")
        
        if not self.data_client:
            print("Warning: No Alpaca data client. Using fallback filter.")
            return tickers[:200]  # Limit to avoid timeout
        
        try:
            print(f"Requesting snapshots for {len(tickers)} symbols...")
            
            # Split into chunks of 1000 (Alpaca limit)
            filtered = []
            chunk_size = 1000
            
            for i in range(0, len(tickers), chunk_size):
                # Check for pause
                if self.pause_check_callback:
                    self.pause_check_callback()

                chunk = tickers[i:i+chunk_size]
                self.update_status("running", i, len(tickers), "", f"Fetching snapshots {i}/{len(tickers)}...")
                
                try:
                    snapshot_request = StockSnapshotRequest(symbol_or_symbols=chunk)
                    snapshots = self.data_client.get_stock_snapshot(snapshot_request)
                    
                    # Filter: price >= $5, volume >= 500k
                    for symbol, snapshot in snapshots.items():
                        try:
                            if snapshot.latest_trade and snapshot.daily_bar:
                                price = float(snapshot.latest_trade.price)
                                volume = int(snapshot.daily_bar.volume)
                                
                                if price >= 5.0 and volume >= 500000:
                                    filtered.append(symbol)
                        except:
                            continue
                except Exception as e:
                    print(f"Error fetching chunk {i}: {e}")
                    continue
            
            print(f"✅ Pre-filter complete: {len(filtered)} / {len(tickers)} tickers passed (${5.0}+, 500k+ vol)")
            return filtered
            
        except Exception as e:
            print(f"Snapshot filtering failed: {e}. Falling back to extended list.")
            return self.get_extended_ticker_list()
    
    def batch_download_data(self, tickers):
        """OPTIMIZED: Download historical data in batches"""
        self.update_status("running", 0, len(tickers), "", "Downloading historical data in batches...")
        
        stock_data = {}
        batch_size = 200  # Download 200 stocks at once
        
        for i in range(0, len(tickers), batch_size):
            # Check for pause
            if self.pause_check_callback:
                self.pause_check_callback()

            batch = tickers[i:i+batch_size]
            self.update_status("running", i, len(tickers), "", f"Downloading batch {i//batch_size + 1}/{len(tickers)//batch_size + 1}...")
            
            try:
                print(f"Downloading batch {i//batch_size + 1}: {len(batch)} tickers...")
                
                # Download all tickers in this batch at once
                df_batch = yf.download(batch, period="1y", interval="1d", group_by='ticker', progress=False, threads=True)
                
                # Process each ticker's data
                for ticker in batch:
                    try:
                        if len(batch) == 1:
                            # Single ticker case - no grouping
                            df = df_batch
                        else:
                            # Multi-ticker case - grouped by ticker
                            if ticker in df_batch.columns.levels[0]:
                                df = df_batch[ticker]
                            else:
                                continue
                        
                        if df.empty or len(df) < 50:
                            continue
                        
                        # Calculate technical indicators
                        df['RSI_14'] = ta.rsi(df['Close'], length=14)
                        df['SMA_20'] = ta.sma(df['Close'], length=20)
                        df['SMA_50'] = ta.sma(df['Close'], length=50)
                        df['EMA_9'] = ta.ema(df['Close'], length=9)
                        df['EMA_21'] = ta.ema(df['Close'], length=21)
                        df['VOL_SMA_10'] = ta.sma(df['Volume'], length=10)
                        
                        # Manual ATR calculation (pandas_ta sometimes returns None or DataFrame)
                        df['H-L'] = df['High'] - df['Low']
                        df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
                        df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
                        df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
                        df['ATR_14'] = df['TR'].rolling(window=14).mean()
                        df['ATRr_14'] = (df['ATR_14'] / df['Close']) * 100
                        
                        stock_data[ticker] = df
                        
                    except Exception as e:
                        print(f"Error processing {ticker}: {e}")
                        continue
                
            except Exception as e:
                print(f"Error downloading batch {i//batch_size + 1}: {e}")
                continue
        
        print(f"✅ Downloaded data for {len(stock_data)} stocks")
        return stock_data
    
    def scan_atr_strategy(self, ticker, df):
        """Check if ticker matches ATR (Volatility Hunter) criteria"""
        if df is None or len(df) < 50:
            return None
        
        try:
            last = df.iloc[-1]
            price = last['Close']
            atr_pct = last['ATRr_14']
            rsi = last['RSI_14']
            avg_vol = last['VOL_SMA_10']
            
            # ATR Strategy Criteria: Sweet Spot Volatility (2.5% - 6%)
            # Stocks >6% are handled by the "High Volatility Screener"
            if pd.isna(atr_pct) or pd.isna(rsi):
                return None
            
            if 2.5 <= atr_pct <= 6.0 and avg_vol > 1_000_000:  # Moderate volatility + Volume
                # Get AI Score
                ai_scores = self.predictor.get_trade_confidence(ticker)
                composite = ai_scores['composite']
                
                return {
                    'Ticker': ticker,
                    'Price': round(price, 2),
                    'ATR_Pct': round(atr_pct, 2),
                    'RSI': round(rsi, 2),
                    'AI_Composite': round(composite, 0),
                    'Vol_10D_Avg': int(avg_vol)
                }
        except Exception as e:
            print(f"Error scanning ATR for {ticker}: {e}")
            
        return None
    
    def scan_ma_strategy(self, ticker, df):
        """Check if ticker matches MA (Trend Surfer) criteria"""
        if df is None or len(df) < 50:
            return None
        
        try:
            last = df.iloc[-1]
            prev = df.iloc[-2]
            
            price = last['Close']
            ema9 = last['EMA_9']
            ema21 = last['EMA_21']
            sma50 = last['SMA_50']
            rsi = last['RSI_14']
            avg_vol = last['VOL_SMA_10']
            
            # MA Strategy Criteria: EMA 9 > EMA 21 (Bullish crossover)
            if pd.isna(ema9) or pd.isna(ema21) or pd.isna(sma50):
                return None
            
            if ema9 > ema21 and price > sma50 and avg_vol > 1_000_000:
                # Check if crossover is recent (within last 5 days)
                crossover_status = "Active"
                if prev['EMA_9'] <= prev['EMA_21']:
                    crossover_status = "Recent"
                
                # Get AI Score
                ai_scores = self.predictor.get_trade_confidence(ticker)
                composite = ai_scores['composite']
                
                return {
                    'Ticker': ticker,
                    'Price': round(price, 2),
                    'EMA_9': round(ema9, 2),
                    'EMA_21': round(ema21, 2),
                    'RSI': round(rsi, 2),
                    'AI_Composite': round(composite, 0),
                    'Crossover': crossover_status
                }
        except Exception as e:
            print(f"Error scanning MA for {ticker}: {e}")
            
        return None
    
    def run_full_scan(self):
        """Execute the OPTIMIZED full market scan"""
        print("\n" + "="*60)
        print("BACKGROUND SCANNER STARTED (OPTIMIZED)")
        print("="*60)
        
        start_time = time.time()
        
        # Step 1: Fetch all assets
        all_tickers = self.fetch_all_assets()
        print(f"Step 1: Fetched {len(all_tickers)} assets")
        
        # Step 2: OPTIMIZED Pre-filter with Alpaca Snapshots
        filtered_tickers = self.pre_filter_with_snapshots(all_tickers)
        print(f"Step 2: Filtered to {len(filtered_tickers)} candidates")
        
        # Step 3: OPTIMIZED Batch download
        stock_data = self.batch_download_data(filtered_tickers)
        print(f"Step 3: Downloaded {len(stock_data)} stock datasets")
        
        # Step 4: Scan with both strategies
        atr_results = []
        ma_results = []
        
        total = len(stock_data)
        for i, (ticker, df) in enumerate(stock_data.items()):
            # Check for pause signal
            if self.pause_check_callback:
                self.pause_check_callback()

            if i % 5 == 0:
                self.update_status("running", i+1, total, ticker, f"Analyzing {ticker} ({i+1}/{total})...")
            
            # Run both strategy checks
            atr_match = self.scan_atr_strategy(ticker, df)
            ma_match = self.scan_ma_strategy(ticker, df)
            
            if atr_match:
                atr_results.append(atr_match)
                print(f"✅ ATR Match: {ticker}")
            
            if ma_match:
                ma_results.append(ma_match)
                print(f"✅ MA Match: {ticker}")
        
        # Step 5: Save results
        print(f"\n📊 Preparing to save results...")
        print(f"ATR Results count: {len(atr_results)}")
        print(f"MA Results count: {len(ma_results)}")
        
        if atr_results:
            print(f"Sample ATR result: {atr_results[0]}")
            atr_df = pd.DataFrame(atr_results).sort_values('AI_Composite', ascending=False).head(50)
            print(f"ATR DataFrame shape: {atr_df.shape}")
            print(f"ATR DataFrame columns: {list(atr_df.columns)}")
        else:
            atr_df = pd.DataFrame()
            
        if ma_results:
            print(f"Sample MA result: {ma_results[0]}")
            ma_df = pd.DataFrame(ma_results).sort_values('AI_Composite', ascending=False).head(50)
            print(f"MA DataFrame shape: {ma_df.shape}")
            print(f"MA DataFrame columns: {list(ma_df.columns)}")
        else:
            ma_df = pd.DataFrame()
        
        print(f"\n💾 Writing to CSV files...")
        atr_df.to_csv("scanner_results_atr.csv", index=False)
        print(f"✅ Wrote {len(atr_df)} rows to scanner_results_atr.csv")
        
        ma_df.to_csv("scanner_results_ma.csv", index=False)
        print(f"✅ Wrote {len(ma_df)} rows to scanner_results_ma.csv")
        
        # Step 6: Mark complete
        elapsed = int(time.time() - start_time)
        self.update_status(
            "completed", 
            total, 
            total, 
            "", 
            f"Scan complete in {elapsed}s! {len(atr_results)} ATR | {len(ma_results)} MA opportunities"
        )
        
        print("\n" + "="*60)
        print(f"SCAN COMPLETE: {len(atr_results)} ATR | {len(ma_results)} MA setups")
        print(f"Time Elapsed: {elapsed} seconds ({elapsed//60}m {elapsed%60}s)")
        print("="*60)

if __name__ == "__main__":
    scanner = BackgroundScanner()
    scanner.run_full_scan()
