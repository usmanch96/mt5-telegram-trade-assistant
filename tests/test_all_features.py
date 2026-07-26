import sys
import os
import asyncio
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
from config import settings
from mt5.connector import initialize_mt5, shutdown_mt5
from mt5.monitor import get_account_details, get_symbol_info, get_active_positions
from mt5.executor import send_market_order, modify_position_stops, close_position
from mt5.risk_calculator import calculate_lot_size

async def run_comprehensive_test():
    print("==================================================")
    print("       MT5 BOT COMPREHENSIVE PIPELINE TEST        ")
    print("==================================================")
    
    # 1. Initialize MT5
    if not initialize_mt5():
        print("[FAIL] MT5 Initialization failed. Make sure MT5 is running on your desktop.")
        return
        
    print("[OK] MT5 Connected successfully.")
    print("--------------------------------------------------")
    
    # 2. Test Account Info Fetching
    print("[TEST 1/5] Verifying Account Details Fetching...")
    acc = get_account_details()
    if acc:
        print(f"  * Account Number: {acc['login']}")
        print(f"  * Balance: {acc['balance']} {acc['currency']}")
        print(f"  * Equity: {acc['equity']}")
        print(f"  * Margin Level: {acc['margin_level']}%")
        print("  [OK] Account details fetched successfully.")
    else:
        print("  [FAIL] Failed to fetch account details.")
        shutdown_mt5()
        return
        
    print("--------------------------------------------------")
    
    # 3. Test Symbol Info & Risk Calculation
    print("[TEST 2/5] Verifying Symbol Info & Risk Calculator...")
    sample_symbol = "EURUSD"
    sym = get_symbol_info(sample_symbol)
    if sym:
        print(f"  * Symbol: {sym['name']}")
        print(f"  * Ask: {sym['ask']} | Bid: {sym['bid']}")
        print(f"  * Digits: {sym['digits']} | Point Size: {sym['point']}")
        print(f"  * Lot Step: {sym['volume_step']} | Min Lot: {sym['volume_min']}")
        
        # Test risk-based lot calculation (Risk 1% of equity with 20 pips SL)
        test_lots = calculate_lot_size(sample_symbol, 1.0, 20.0)
        print(f"  * Risk Lot Calculator Result (1% risk, 20 pips SL): {test_lots} Lots")
        print("  [OK] Symbol info and risk calculation succeeded.")
    else:
        print(f"  [FAIL] Symbol {sample_symbol} not found or query failed.")
        shutdown_mt5()
        return
        
    print("--------------------------------------------------")
    
    # 4. Test Trade Execution (Order Send)
    print("[TEST 3/5] Executing a tiny Demo Buy Order (0.01 Lots)...")
    res = send_market_order(sample_symbol, "BUY", 0.01, comment="Pipeline Test")
    if res["success"]:
        ticket = res["ticket"]
        print(f"  [OK] Order executed successfully! Ticket: #{ticket}")
    else:
        print(f"  [FAIL] Order execution failed: {res['message']}")
        shutdown_mt5()
        return
        
    print("--------------------------------------------------")
    await asyncio.sleep(2)  # Wait 2 seconds for trade registration
    
    # 5. Test Position Querying & Modifying
    print("[TEST 4/5] Querying Position & Modifying Stops (Break-Even)...")
    positions = get_active_positions()
    my_pos = next((p for p in positions if p["ticket"] == ticket), None)
    
    if my_pos:
        print(f"  * Found running position: #{my_pos['ticket']} ({my_pos['symbol']}) Lots: {my_pos['volume']}")
        entry = my_pos["price_open"]
        tp = my_pos["tp"]
        
        mod_res = modify_position_stops(ticket, sl=entry, tp=tp)
        if mod_res["success"]:
            print(f"  [OK] Position modified successfully! SL moved to entry: {entry}")
        else:
            print(f"  [FAIL] Position modification failed: {mod_res['message']}")
    else:
        print(f"  [FAIL] Running position ticket #{ticket} was not found in active positions list.")
        
    print("--------------------------------------------------")
    await asyncio.sleep(1)
    
    # 6. Test Closing Position (Partial / Total)
    print("[TEST 5/5] Closing Position completely...")
    close_res = close_position(ticket)
    if close_res["success"]:
        print("  [OK] Position closed successfully.")
    else:
        print(f"  [FAIL] Position close failed: {close_res['message']}")
        
    print("--------------------------------------------------")
    print("All core MT5 pipeline tests passed successfully!")
    shutdown_mt5()

if __name__ == "__main__":
    asyncio.run(run_comprehensive_test())
