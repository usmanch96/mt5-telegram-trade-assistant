import os
from pathlib import Path
from dotenv import load_dotenv

# Base directory of the project
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables
load_dotenv(BASE_DIR / ".env")

# Create necessary directories if they don't exist
(BASE_DIR / "database").mkdir(exist_ok=True)
(BASE_DIR / "logs").mkdir(exist_ok=True)

# Configuration settings
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Parse authorized user IDs as a set of integers
authorized_users_raw = os.getenv("AUTHORIZED_USERS", "")
AUTHORIZED_USERS = set()
if authorized_users_raw:
    for uid in authorized_users_raw.split(","):
        try:
            AUTHORIZED_USERS.add(int(uid.strip()))
        except ValueError:
            pass

# MT5 Connection Settings
MT5_LOGIN = 0
try:
    MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
except ValueError:
    pass

MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_PATH = os.getenv("MT5_PATH", None)

# Database and Logging
DB_PATH = str(BASE_DIR / os.getenv("DB_PATH", "database/trading_bot.db"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = str(BASE_DIR / "logs" / "app.log")

def is_config_valid() -> bool:
    """Checks if essential configurations are populated."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_bot_token_here":
        return False
    if not AUTHORIZED_USERS:
        return False
    if MT5_LOGIN == 0 or not MT5_PASSWORD or not MT5_SERVER:
        return False
    return True
