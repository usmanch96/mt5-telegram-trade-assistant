import MetaTrader5 as mt5
import threading
from config import settings
from services.logger import logger

# Thread lock to prevent concurrent initialization/login collisions
_connection_lock = threading.Lock()

def initialize_mt5(path: str = None) -> bool:
    """
    Initializes connection to the MT5 terminal and logs in.
    Returns True if connection and login are successful, False otherwise.
    """
    with _connection_lock:
        try:
            if path:
                settings.MT5_PATH = path

            # Check if terminal is already initialized and account is logged in
            term_info = mt5.terminal_info()
            acc_info = mt5.account_info()

            if term_info is not None:
                # Check if currently connected terminal matches requested MT5_PATH
                path_matches = True
                if settings.MT5_PATH:
                    req_path = str(settings.MT5_PATH).replace("/", "\\").lower().rstrip("\\")
                    curr_path = str(getattr(term_info, "path", "")).replace("/", "\\").lower().rstrip("\\")
                    if not (curr_path in req_path or req_path in curr_path):
                        path_matches = False
                        logger.info(f"Connected to terminal at '{curr_path}', but target is '{settings.MT5_PATH}'. Switching terminal...")
                        mt5.shutdown()
                        term_info = None
                        acc_info = None

            if term_info is not None:
                if acc_info is not None:
                    # If an explicit MT5_LOGIN is set, make sure it matches
                    if not settings.MT5_LOGIN or acc_info.login == settings.MT5_LOGIN:
                        logger.info(f"Connected to running MT5 terminal. Account: {acc_info.login}, Server: {acc_info.server}")
                        return True

                # Terminal is running but not logged in (or wrong account)
                if settings.MT5_LOGIN and settings.MT5_PASSWORD and settings.MT5_SERVER:
                    logger.info(f"Terminal running. Logging in to account {settings.MT5_LOGIN}...")
                    if mt5.login(login=settings.MT5_LOGIN, password=settings.MT5_PASSWORD, server=settings.MT5_SERVER):
                        logger.info(f"Successfully logged in to MT5 account {settings.MT5_LOGIN}.")
                        return True
                    else:
                        logger.warning(f"mt5.login() failed: {mt5.last_error()}. Re-initializing terminal...")
                        mt5.shutdown()

            logger.info("Initializing MetaTrader 5 terminal...")
            
            # Setup initialization arguments
            init_args = {}
            if settings.MT5_PATH:
                init_args["path"] = settings.MT5_PATH
            if settings.MT5_LOGIN and settings.MT5_PASSWORD and settings.MT5_SERVER:
                init_args["login"] = settings.MT5_LOGIN
                init_args["password"] = settings.MT5_PASSWORD
                init_args["server"] = settings.MT5_SERVER
                
            # Attempt initialization
            if not mt5.initialize(**init_args):
                err = mt5.last_error()
                logger.error(f"MT5 initialization failed. Error code: {err}")
                return False
                
            # Double-check terminal status and authorization
            account_info = mt5.account_info()
            if account_info is None:
                err = mt5.last_error()
                logger.error(f"MT5 login failed. Invalid credentials or server. Error code: {err}")
                mt5.shutdown()
                return False
                
            logger.info(f"Successfully connected to MT5. Account: {account_info.login}, Server: {account_info.server}")
            return True
            
        except Exception as e:
            logger.error(f"Unexpected exception during MT5 initialization: {e}", exc_info=True)
            return False

def check_connection() -> bool:
    """
    Checks if connection to MT5 is alive. If not, attempts to reconnect.
    """
    try:
        if mt5.terminal_info() is not None and mt5.account_info() is not None:
            return True
        logger.warning("MT5 terminal connection lost. Attempting reconnection...")
        return initialize_mt5()
    except Exception as e:
        logger.error(f"Error checking MT5 connection: {e}")
        return False

def shutdown_mt5() -> None:
    """Disconnects and shuts down the MT5 terminal connection."""
    with _connection_lock:
        try:
            mt5.shutdown()
            logger.info("MetaTrader 5 terminal shut down.")
        except Exception as e:
            logger.error(f"Error shutting down MT5: {e}")
