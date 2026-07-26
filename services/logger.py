import logging
import sys
from logging.handlers import RotatingFileHandler
from config import settings

# ANSI color escape codes for terminal styling
COLOR_BLUE = "\033[94m"
COLOR_CYAN = "\033[96m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"

# Dynamic level indicators (safe for all Windows console character maps)
LEVEL_ICONS = {
    "DEBUG": "[d] ",
    "INFO": "[*] ",
    "WARNING": "[!] ",
    "ERROR": "[x] ",
    "CRITICAL": "[X] "
}

# Color mapping
LEVEL_COLORS = {
    "DEBUG": COLOR_CYAN,
    "INFO": COLOR_GREEN,
    "WARNING": COLOR_YELLOW,
    "ERROR": COLOR_RED,
    "CRITICAL": COLOR_RED + COLOR_BOLD
}

# Native Windows CMD ANSI escape code activation
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        # STD_OUTPUT_HANDLE = -11
        # ENABLE_PROCESSED_OUTPUT = 1, ENABLE_WRAP_AT_EOL_OUTPUT = 2, ENABLE_VIRTUAL_TERMINAL_PROCESSING = 4
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7 | 4)
    except Exception:
        pass

class TerminalColorFormatter(logging.Formatter):
    """Custom console log formatter with dynamic coloring, alignments, and icons."""
    def format(self, record):
        level = record.levelname
        color = LEVEL_COLORS.get(level, COLOR_RESET)
        icon = LEVEL_ICONS.get(level, "[-] ")
        
        # Only output HH:MM:SS in console for clean CLI look
        asctime = self.formatTime(record, "%H:%M:%S")
        
        # Base clean message line
        message = (
            f"{COLOR_BLUE}{asctime}{COLOR_RESET} | "
            f"{color}{icon}{record.getMessage()}{COLOR_RESET}"
        )
        
        # Append diagnostic file info only for non-standard info levels (warning/error/debug)
        if level in ["WARNING", "ERROR", "CRITICAL", "DEBUG"]:
            file_info = f" ({record.filename}:{record.lineno})"
            message += f"{COLOR_CYAN}{file_info}{COLOR_RESET}"
            
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
            
        return message

def setup_logger(name: str = "trading_bot") -> logging.Logger:
    """Sets up a logger with colored console prints and clean plain file outputs."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))
    
    if logger.handlers:
        return logger
        
    # Console handler with colored formatting
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(TerminalColorFormatter())
    logger.addHandler(console_handler)
    
    # File handler (max 5MB, plain text formatting)
    try:
        plain_formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(filename)s:%(lineno)d]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler = RotatingFileHandler(
            settings.LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        file_handler.setFormatter(plain_formatter)
        logger.addHandler(file_handler)
    except Exception as e:
        # Fallback console log for setup failures
        print(f"Failed to setup file logging handler: {e}")
        
    return logger

logger = setup_logger()
