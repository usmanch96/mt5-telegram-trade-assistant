import sys
import os
from pathlib import Path

# Add project root to path to resolve config imports
sys.path.append(str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
from config import settings
from mt5.connector import initialize_mt5, shutdown_mt5
from mt5.monitor import get_account_details, get_symbol_info

def test_connection():
    print("Testing MetaTrader 5 connection configuration...")
    print(f"MT5_LOGIN: {settings.MT5_LOGIN}")
    print(f"MT5_SERVER: {settings.MT5_SERVER}")
    print(f"DB_PATH: {settings.DB_PATH}")
    print("------------------------------------------")
    
    # Initialize connection
    success = initialize_mt5()
    if not success:
        print("❌ Connection test failed! Check credentials, server name, or MT5 terminal status.")
        sys.exit(1)
        
    print("✅ Connection test successful!")
    
    # Check account details
    acc = get_account_details()
    if acc:
        print("\n--- Account Info ---")
        print(f"Login ID: {acc['login']}")
        print(f"Name: {acc['name']}")
        print(f"Company: {acc['company']}")
        print(f"Server: {acc['company']}")
        print(f"Balance: {acc['balance']} {acc['currency']}")
        print(f"Equity: {acc['equity']} {acc['currency']}")
        print(f"Margin Level: {acc['margin_level']}%")
    else:
        print("❌ Failed to query account details.")
        
    # Check a sample symbol
    sample = "EURUSD"
    sym = get_symbol_info(sample)
    if sym:
        print(f"\n--- Symbol Info: {sample} ---")
        print(f"Ask: {sym['ask']}")
        print(f"Bid: {sym['bid']}")
        print(f"Digits: {sym['digits']}")
        print(f"Point size: {sym['point']}")
        print(f"Min volume: {sym['volume_min']} Lots")
    else:
        print(f"❌ Failed to query symbol info for {sample}.")
        
    # Shutdown
    shutdown_mt5()
    print("\n------------------------------------------")
    print("Test run completed.")

if __name__ == "__main__":
    test_connection()
