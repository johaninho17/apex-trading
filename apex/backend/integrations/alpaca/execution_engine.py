import os
import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from runtime_config import get_alpaca_credentials

# Role: Execution Engineer (@Claude-Sonnet-4.5)
# Objective: Execute the chosen trade safely.

# Note: dotenv is loaded by dashboard.py - don't reload here to preserve dynamic switching

class ExecutionEngine:
    def __init__(self, paper=None):
        # Resolve credentials from persisted trading mode instead of mutating process env.
        mode = None if paper is None else ("paper" if paper else "live")
        self.api_key, self.secret_key, default_paper = get_alpaca_credentials(mode=mode)
        self.paper = default_paper if paper is None else bool(paper)

        if not self.api_key or not self.secret_key:
            print("WARNING: Alpaca API Keys not found in environment. Execution will be simulated.")
            self.client = None
        else:
            mode_str = "PAPER" if self.paper else "LIVE"
            key_prefix = self.api_key[:4] if self.api_key else "None"
            print(f"Execution Engine initialized ({mode_str} mode) - Key prefix: {key_prefix}...")
            self.client = TradingClient(self.api_key, self.secret_key, paper=self.paper)

    def validation_check(self, price, qty):
        """Simple checks to prevent bad orders."""
        if price <= 0 or qty <= 0:
            print("Error: Invalid Price or Quantity.")
            return False
            
        cost = price * qty
        if cost > 100000: # Safety Cap
            print(f"Error: Order cost ${cost} exceeds likely safety limit.")
            return False
            
        print(f"Validating Order: Cost ${cost:.2f}...")
        
        # Check Buying Power if live
        if self.client:
            account = self.client.get_account()
            bp = float(account.buying_power)
            print(f"Account Buying Power: ${bp}")
            if cost > bp:
                print("Error: Insufficient Buying Power.")
                return False
                
        return True

    def calculate_kelly_size(self, win_rate_pct, risk_reward_ratio, account_balance):
        """
        Calculates optimal position size using Half-Kelly Criterion.
        f* = 0.5 * (p - (q / b))
        """
        if risk_reward_ratio <= 0: return 0.0
        
        p = win_rate_pct / 100.0
        q = 1.0 - p
        b = risk_reward_ratio
        
        f_star = 0.5 * (p - (q / b))
        
        if f_star <= 0:
            return 0.0 # Don't trade if edge is negative
            
        # Cap at 10% of account for extra safety in this demo
        f_star = min(f_star, 0.10) 
        
        position_size = account_balance * f_star
        return round(position_size, 2)

    def execute_trade(self, setup_type="Conservative (Pullback)", investment_amount=None, trade_details=None):
        symbol = "Unknown"
        qty = 0
        entry_price = 0.0
        
        if trade_details:
             print(f"\n--- Executing Custom Trade from Dashboard ---")
             symbol = trade_details['Symbol']
             entry_price = float(trade_details['Entry'])
             take_profit_price = float(trade_details['Target'])
             stop_loss_price = float(trade_details['Stop_Loss'])
             setup_type = trade_details['Type']
             use_trailing_stop = trade_details.get('Trailing_Stop', False)
             use_kelly = trade_details.get('Use_Kelly', False)
             risk_reward = trade_details.get('Risk_Reward', 0.0)
             
             # Calculate Qty (Override if Kelly is used)
             if use_kelly:
                 # In a real bot, we'd pass the win_rate in trade_details.
                 # For now, we assume a win rate or fetch it? 
                 # Let's use the 'Win_Rate' passed from Dashboard
                 win_rate = trade_details.get('Win_Rate', 50.0)
                 
                 # Get Account Balance
                 balance = 100000.0 # Default fallback
                 if self.client:
                     balance = float(self.client.get_account().equity)
                     
                 kelly_amount = self.calculate_kelly_size(win_rate, risk_reward, balance)
                 print(f"Kelly Criterion Sizing: ${kelly_amount} (Win Rate: {win_rate}%, R/R: {risk_reward})")
                 
                 qty = round(kelly_amount / entry_price, 4)
             else:
                 qty = float(trade_details['Qty'])

             
        else:
            # Legacy/CLI Mode
            if not os.path.exists("trade_setups.csv"):
                print("Error: No trade setups found. Run analyst first.")
                return

            setups = pd.read_csv("trade_setups.csv")
            
            # Filter for the chosen setup
            trade = setups[setups['Type'] == setup_type]
            if trade.empty:
                print(f"Setup '{setup_type}' not found. Defaulting to first available.")
                trade = setups.iloc[0]
            else:
                trade = trade.iloc[0] # Take the series
                
            print(f"\n--- Executing Trade: {setup_type} ---")
            print(trade)
            
            # Extract Trade Details
            try:
                if os.path.exists("analysis_summary.csv"):
                    summary = pd.read_csv("analysis_summary.csv")
                    symbol = summary.iloc[0]['Ticker']
                else:
                    print("Error: Ticker symbol not found.")
                    return

                entry_price = float(trade['Entry'])

                # Calculate Quantity based on Investment Amount or use Default from CSV
                if investment_amount:
                    print(f"Using Custom Investment Amount: ${investment_amount}")
                    qty = round(float(investment_amount) / entry_price, 4)
                else:
                    qty = float(trade['Qty'])
                
                take_profit_price = float(trade['Target'])
                stop_loss_price = float(trade['Stop_Loss'])
                use_trailing_stop = False # CLI default
            
            except Exception as e:
                print(f"Error parsing trade details: {e}")
                return

        try:     
            if not self.validation_check(entry_price, qty):
                return
            
            if self.client:
                # Handle Trailing Stop for Market Orders (Aggressive / Trend Follower)
                if use_trailing_stop:
                    if "Pullback" in setup_type:
                        print("WARNING: Trailing Stop not compatible with Limit Orders (Pullback). Reverting to Fixed Bracket.")
                        # Fall through to standard logic below
                    else:
                        # MARKET ORDER + SPLIT TRAILING STOP
                        print(f"Submitting MARKET Order for {symbol} (Waiting for fill to attach Trailing Stop)...")
                        
                        # 1. Submit Entry Order
                        market_order = MarketOrderRequest(
                            symbol=symbol,
                            qty=qty,
                            side=OrderSide.BUY,
                            time_in_force=TimeInForce.DAY
                        )
                        entry_order = self.client.submit_order(market_order)
                        print(f"Entry Submitted. ID: {entry_order.id}")
                        
                        # 2. Wait for Fill (Simple Polling)
                        import time
                        filled = False
                        print("Waiting for Market Order fill (up to 60s)...")
                        # Increase wait time to 60 seconds to avoid timeout on slow fills
                        for _ in range(30): 
                            entry_order = self.client.get_order_by_id(entry_order.id)
                            if entry_order.status == 'filled':
                                filled = True
                                break
                            time.sleep(2) # check every 2 seconds
                            
                        if filled:
                            print("Order Filled! Attaching Trailing Stop...")
                            from alpaca.trading.requests import TrailingStopOrderRequest
                            
                            # 3. Submit Trailing Stop
                            # Note: trail_percent=5.0 means 5%
                            trailing_stop_req = TrailingStopOrderRequest(
                                symbol=symbol,
                                qty=qty,
                                side=OrderSide.SELL,
                                time_in_force=TimeInForce.GTC,
                                trail_percent=5.0 
                            )
                            stop_order = self.client.submit_order(trailing_stop_req)
                            print(f"Trailing Stop (5%) Attached! ID: {stop_order.id}")
                            return {"success": True, "message": f"Entry Filled & Trailing Stop Attached! ID: {stop_order.id}", "id": stop_order.id}
                        else:
                            err_msg = "Error: Entry order not filled within 60s. Trailing Stop NOT attached. Please check Alpaca to verify if entry eventually fills."
                            print(err_msg)
                            return {"success": False, "message": err_msg, "id": entry_order.id}

                # Standard Fixed Bracket Logic (If Trailing Stop not used or discarded)
                
                # CRITICAL: Round all prices to 2 decimal places for Alpaca API compliance
                # Alpaca rejects orders with excessive decimal precision (sub-penny increments)
                entry_price = round(entry_price, 2)
                take_profit_price = round(take_profit_price, 2)
                stop_loss_price = round(stop_loss_price, 2)
                
                print(f"Rounded Prices - Entry: ${entry_price:.2f}, Stop: ${stop_loss_price:.2f}, Target: ${take_profit_price:.2f}")
                
                order_request = None
                
                # CRITICAL: Alpaca bracket orders require BOTH stop_price AND limit_price in stop_loss leg
                # stop_price = trigger price, limit_price = execution limit (slightly below stop for safety)
                stop_limit_price = round(stop_loss_price * 0.995, 2)  # 0.5% below stop price
                
                tp_req = TakeProfitRequest(limit_price=take_profit_price)
                sl_req = StopLossRequest(
                    stop_price=stop_loss_price,
                    limit_price=stop_limit_price  # Required for bracket orders
                )
                
                print(f"\n=== BRACKET ORDER DETAILS ===")
                print(f"Entry: ${entry_price:.2f}")
                print(f"Take Profit (limit): ${take_profit_price:.2f}")
                print(f"Stop Loss (stop): ${stop_loss_price:.2f}")
                print(f"Stop Loss (limit): ${stop_limit_price:.2f}")
                print(f"============================\n")
                
                if "Pullback" in setup_type:
                    # Limit Order
                    print(f"Submitting LIMIT Order for {symbol} at ${entry_price}...")
                    from alpaca.trading.requests import LimitOrderRequest
                    from alpaca.trading.enums import OrderClass
                    
                    order_request = LimitOrderRequest(
                        symbol=symbol,
                        qty=qty,
                        side=OrderSide.BUY,
                        time_in_force=TimeInForce.GTC, # Good Till Cancelled for limit
                        limit_price=entry_price,
                        order_class=OrderClass.BRACKET,  # CRITICAL: Explicitly set bracket order
                        take_profit=tp_req,
                        stop_loss=sl_req
                    )
                else:
                    # Market Order
                    print(f"Submitting MARKET Order for {symbol}...")
                    from alpaca.trading.enums import OrderClass
                    
                    order_request = MarketOrderRequest(
                        symbol=symbol,
                        qty=qty,
                        side=OrderSide.BUY,
                        time_in_force=TimeInForce.DAY,
                        order_class=OrderClass.BRACKET,  # CRITICAL: Explicitly set bracket order
                        take_profit=tp_req,
                        stop_loss=sl_req
                    )
                
                # Submit
                try:
                    print(f"\n=== SUBMITTING BRACKET ORDER ===")
                    print(f"Symbol: {symbol}")
                    print(f"Qty: {qty}")
                    print(f"Entry: ${entry_price:.2f}")
                    print(f"Take Profit (limit_price): ${take_profit_price:.2f}")
                    print(f"Stop Loss (stop_price): ${stop_loss_price:.2f}")
                    print(f"Stop Loss (limit_price): ${stop_limit_price:.2f}")
                    print(f"Order Class: bracket")
                    print(f"================================\n")
                    
                    order = self.client.submit_order(order_data=order_request)
                    print(f"✅ Order Submitted Successfully! ID: {order.id}")
                    print(f"Status: {order.status}")
                    
                    # Query the order back with nested=true to see child legs
                    import time
                    time.sleep(1)  # Wait a moment for order to process
                    
                    try:
                        # Get order with legs
                        full_order = self.client.get_order_by_id(order.id)
                        
                        print(f"\n=== ORDER VERIFICATION ===")
                        print(f"Parent Order ID: {full_order.id}")
                        print(f"Order Class: {full_order.order_class}")
                        print(f"Type: {full_order.type}")
                        
                        # Check if order has legs attribute
                        if hasattr(full_order, 'legs') and full_order.legs:
                            print(f"\n📋 CHILD ORDERS (LEGS):")
                            for i, leg in enumerate(full_order.legs):
                                print(f"\n  Leg {i+1} - {leg.id}")
                                print(f"  Type: {leg.type}")
                                print(f"  Side: {leg.side}")
                                if hasattr(leg, 'limit_price') and leg.limit_price:
                                    print(f"  Limit Price: ${float(leg.limit_price):.2f}")
                                if hasattr(leg, 'stop_price') and leg.stop_price:
                                    print(f"  Stop Price: ${float(leg.stop_price):.2f}")
                        else:
                            print(f"\n⚠️ No legs found. Order might not be a bracket.")
                            
                        print(f"=========================\n")
                        
                    except Exception as e:
                        print(f"Could not verify order legs: {e}")
                    
                    return {"success": True, "message": f"Order Submitted! ID: {order.id}. Check 'Orders' tab for attached Stop Loss/Take Profit.", "id": order.id}
                except Exception as e:
                    print(f"Order Failed: {e}")
                    return {"success": False, "message": f"Order Failed: {str(e)}", "id": None}

            else:
                sim_msg = f"[SIMULATION] BUY {qty} {symbol} @ ${entry_price}"
                print(sim_msg)
                return {"success": True, "message": sim_msg + " (Paper Mode)", "id": "SIM_ID"}

        except Exception as e:
            print(f"Execution Error: {e}")
            return {"success": False, "message": f"Execution Error: {str(e)}", "id": None}

        except Exception as e:
            print(f"Execution Error: {e}")

if __name__ == "__main__":
    engine = ExecutionEngine()
    # executing Conservative by default as per 'Smart' requirement (buy low)
    engine.execute_trade("Conservative (Pullback)")
