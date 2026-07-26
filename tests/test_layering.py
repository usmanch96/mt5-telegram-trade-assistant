import sys
from pathlib import Path
import asyncio

# Setup import paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

import MetaTrader5 as mt5
from mt5.connector import initialize_mt5
from mt5.monitor import get_symbol_info
from mt5.executor import send_market_order, send_pending_order, cancel_pending_order, close_position

async def main():
    print("==================================================")
    print("        PROGRESSIVE LAYERING INTEGRATION TEST     ")
    print("==================================================")
    
    # 1. Initialize MT5
    print("\n[STEP 1] Connecting to MetaTrader 5 terminal...")
    if not initialize_mt5():
        print("[-] Connect failed. Please ensure MT5 terminal is open.")
        sys.exit(1)
        
    print("[OK] Connected to MT5.")
    
    # 2. Get symbol tick price (using BTCUSD for weekend testing)
    symbol = "BTCUSD"
    print(f"\n[STEP 2] Fetching tick prices for {symbol}...")
    sym_info = get_symbol_info(symbol)
    if not sym_info:
        print(f"[-] Failed to get symbol specs for {symbol}.")
        sys.exit(1)
        
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        print(f"[-] Failed to get current tick for {symbol}.")
        sys.exit(1)
        
    current_ask = tick.ask
    print(f"[OK] Current {symbol} Ask Price: {current_ask}")
    
    # 3. Setup test prices
    # For Bitcoin: SL $200 below entry, TP $200 above entry
    sl = current_ask - 200.0
    tp = current_ask + 200.0
    
    # 4. Place Layer 1 (Market Order)
    print(f"\n[STEP 3] Placing Layer 1: Market BUY of 0.01 Lots @ {current_ask:.2f}...")
    res_market = send_market_order(
        symbol=symbol,
        direction="BUY",
        volume=0.01,
        sl=sl,
        tp=tp,
        comment="Test Layer 1"
    )
    
    if not res_market["success"]:
        print(f"[-] Market order failed: {res_market['message']}")
        sys.exit(1)
        
    market_ticket = res_market["ticket"]
    print(f"[OK] Market order placed. Ticket: {market_ticket}")
    
    # 5. Place Layer 2 (Pending BUY LIMIT order)
    # Put Layer 2 $50.00 below market entry
    limit_price = current_ask - 50.00
    limit_sl = sl
    limit_tp = tp
    
    print(f"\n[STEP 4] Placing Layer 2: BUY LIMIT of 0.01 Lots @ {limit_price:.2f}...")
    res_pending = send_pending_order(
        symbol=symbol,
        direction="BUY_LIMIT",
        volume=0.01,
        price=limit_price,
        sl=limit_sl,
        tp=limit_tp,
        comment="Test Layer 2"
    )
    
    if not res_pending["success"]:
        print(f"[-] Pending order failed: {res_pending['message']}")
        # Clean up market order first
        close_position(market_ticket)
        sys.exit(1)
        
    pending_ticket = res_pending["ticket"]
    print(f"[OK] Pending limit order placed. Ticket: {pending_ticket}")
    
    # Wait for 3 seconds so the trader can visually inspect if needed
    print("\n[INFO] Waiting 3 seconds before cleaning up...")
    await asyncio.sleep(3)
    
    # 6. Clean up: Cancel pending limit order
    print(f"\n[STEP 5] Cleaning up: Cancelling pending order #{pending_ticket}...")
    res_cancel = cancel_pending_order(pending_ticket)
    if res_cancel["success"]:
        print("[OK] Pending order cancelled successfully.")
    else:
        print(f"[-] Failed to cancel pending order: {res_cancel['message']}")
        
    # 7. Clean up: Close market order
    print(f"\n[STEP 6] Cleaning up: Closing market trade position #{market_ticket}...")
    res_close = close_position(market_ticket)
    if res_close["success"]:
        print("[OK] Market position closed successfully.")
    else:
        print(f"[-] Failed to close market position: {res_close['message']}")
        
    print("\n==================================================")
    print("      ALL PROGRESSIVE LAYERING TESTS PASSED       ")
    print("==================================================")

if __name__ == "__main__":
    asyncio.run(main())
