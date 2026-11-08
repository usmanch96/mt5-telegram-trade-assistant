import sys
import warnings

# Suppress harmless library-specific warnings to keep the console clean
warnings.filterwarnings("ignore", category=UserWarning)

from database.db_manager import init_db
from mt5.connector import initialize_mt5, shutdown_mt5
from mt5.discovery import select_terminal_interactive
from bot.main import run_bot_polling
from services.logger import logger

def main():
    """Main orchestrator for the MT5 Telegram Trading Bot."""
    # Truncate/clear the app.log file on a fresh bot run
    from config import settings
    try:
        with open(settings.LOG_FILE, "w", encoding="utf-8") as f:
            f.truncate(0)
    except Exception:
        pass

    logger.info("==========================================")
    logger.info("Initializing MT5 Telegram Trading Bot...")
    logger.info("==========================================")
    
    # 1. Initialize SQLite Database
    try:
        init_db()
    except Exception as e:
        logger.error(f"Critical error initializing database: {e}")
        sys.exit(1)
        
    # 2. Interactive Terminal Selection & MT5 Connection
    chosen_terminal = select_terminal_interactive()
    mt5_success = initialize_mt5(path=chosen_terminal)
    if not mt5_success:
        logger.warning(
            "Failed to initialize MT5 connection on startup. "
            "The bot will still run and attempt automatic reconnection, "
            "but trade execution calls will fail until connection is established."
        )
    
    # 3. Start Telegram Bot
    try:
        run_bot_polling()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Shutting down bot...")
    except Exception as e:
        logger.critical(f"Unhandled exception in bot execution loop: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # 4. Clean shutdown of MT5
        shutdown_mt5()
        logger.info("Shutdown completed. Goodbye!")

if __name__ == "__main__":
    main()
