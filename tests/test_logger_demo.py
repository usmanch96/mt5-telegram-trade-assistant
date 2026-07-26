import sys
from pathlib import Path

# Setup import paths
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from services.logger import logger

def main():
    print("==================================================")
    print("           LOGGER DESIGN DEMONSTRATION            ")
    print("==================================================")
    
    logger.info("Initializing MT5 Telegram Trading Bot...")
    logger.debug("Database loaded successfully (8 whitelisted users).")
    logger.info("MetaTrader 5 terminal connected (Account: 262529500).")
    logger.warning("Spreads are widening on Gold (XAUUSD spread: 35 points).")
    logger.error("Failed to modify stops: Trade request rejected by broker (10016).")
    
    print("==================================================")

if __name__ == "__main__":
    main()
