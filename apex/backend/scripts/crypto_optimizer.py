import os
import sys
import asyncio
from typing import Dict, Any, List

# Add backend to path so we can import our services
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

# Bootstrap environment
os.environ["NUMBA_DISABLE_JIT"] = "1"
from dotenv import load_dotenv
load_dotenv(os.path.join(BACKEND_ROOT, ".env"))

from services.crypto.market_data import get_account_summary, get_crypto_positions
from integrations.kraken import kraken_client

# CONFIGURATION
RISK_PER_POSITION_PCT = 0.10  # 10% of equity per position
MIN_POSITION_NOTIONAL = 20.0   # $20 minimum

async def get_all_balances():
    print("🔍 Fetching balances from all 3 crypto endpoints...")
    
    results = {}
    
    # 1. Alpaca Live
    try:
        results["alpaca_live"] = {
            "summary": get_account_summary(mode="live"),
            "positions": get_crypto_positions(mode="live")
        }
        print("✅ Alpaca Live fetched.")
    except Exception as e:
        print(f"❌ Alpaca Live error: {e}")
        
    # 2. Alpaca Paper
    try:
        results["alpaca_paper"] = {
            "summary": get_account_summary(mode="paper"),
            "positions": get_crypto_positions(mode="paper")
        }
        print("✅ Alpaca Paper fetched.")
    except Exception as e:
        print(f"❌ Alpaca Paper error: {e}")

    # 3. Kraken
    try:
        results["kraken"] = {
            "summary": kraken_client.get_account_summary(),
            "positions": kraken_client.get_crypto_positions()
        }
        print("✅ Kraken fetched.")
    except Exception as e:
        print(f"❌ Kraken error: {e}")
        
    return results

def calculate_optimization(data: Dict[str, Any]):
    print("\n" + "="*50)
    print("📊 CRYPTO PORTFOLIO OPTIMIZATION REPORT")
    print("="*50)
    
    total_equity = 0.0
    total_exposure = 0.0
    account_stats = []
    
    for acct_name, acct_data in data.items():
        summary = acct_data["summary"]
        positions = acct_data["positions"]
        
        equity = summary.get("equity", 0.0)
        cash = summary.get("cash", 0.0)
        exposure = sum(p.get("market_value", 0.0) for p in positions)
        
        total_equity += equity
        total_exposure += exposure
        
        # Optimal slots for this account based on its own equity
        target_size = equity * RISK_PER_POSITION_PCT
        if target_size < MIN_POSITION_NOTIONAL:
            target_size = MIN_POSITION_NOTIONAL
            
        opt_slots = int(equity / target_size) if target_size > 0 else 0
        current_slots = len(positions)
        
        account_stats.append({
            "name": acct_name,
            "equity": equity,
            "cash": cash,
            "exposure": exposure,
            "current_slots": current_slots,
            "opt_slots": opt_slots,
            "target_size": target_size
        })
        
    # Global Metrics
    global_target_size = total_equity * RISK_PER_POSITION_PCT
    global_opt_slots = int(total_equity / global_target_size) if global_target_size > 0 else 0
    global_current_slots = sum(a["current_slots"] for a in account_stats)
    
    print(f"{'Account':<15} | {'Equity':>10} | {'Exposure':>10} | {'Slots (Cur/Opt)':<15}")
    print("-" * 65)
    for a in account_stats:
        print(f"{a['name']:<15} | ${a['equity']:>9.2f} | ${a['exposure']:>9.2f} | {a['current_slots']} / {a['opt_slots']}")
        
    print("-" * 65)
    print(f"{'TOTAL':<15} | ${total_equity:>9.2f} | ${total_exposure:>9.2f} | {global_current_slots} / {global_opt_slots}")
    
    print("\n💡 RECOMMENDATIONS:")
    for a in account_stats:
        diff = a['opt_slots'] - a['current_slots']
        if diff > 0:
            print(f"🔹 {a['name']}: You have room for {diff} more positions. Target size: ${a['target_size']:.2f} each.")
        elif diff < 0:
            print(f"⚠️ {a['name']}: Over-exposed by {abs(diff)} positions. Consider trimming.")
        else:
            print(f"✅ {a['name']}: At optimal position count.")

    if total_equity > 0:
        utilization = (total_exposure / total_equity) * 100
        print(f"\n📈 Portfolio Utilization: {utilization:.1f}%")

async def main():
    data = await get_all_balances()
    calculate_optimization(data)

if __name__ == "__main__":
    asyncio.run(main())
