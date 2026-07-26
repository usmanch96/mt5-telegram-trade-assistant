from typing import List, Dict, Any, Optional
from database.db_manager import get_db_connection
from services.logger import logger

# --- Whitelist Operations ---

def is_user_authorized(telegram_id: int) -> bool:
    """Checks if a telegram user ID is whitelisted."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM authorized_users WHERE telegram_id = ?",
                (telegram_id,)
            )
            return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking user authorization: {e}")
        return False

def authorize_user(telegram_id: int, username: Optional[str] = None) -> bool:
    """Adds a new user to the authorized whitelist."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO authorized_users (telegram_id, username) VALUES (?, ?)",
                (telegram_id, username)
            )
            return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error authorizing user {telegram_id}: {e}")
        return False

def revoke_user(telegram_id: int) -> bool:
    """Removes a user from the authorized whitelist."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM authorized_users WHERE telegram_id = ?",
                (telegram_id,)
            )
            return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error revoking user {telegram_id}: {e}")
        return False

def get_all_authorized_users() -> List[Dict[str, Any]]:
    """Retrieves all authorized users."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT telegram_id, username, added_at FROM authorized_users ORDER BY added_at DESC")
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error getting authorized users list: {e}")
        return []

# --- Price Alert Operations ---

def add_price_alert(symbol: str, target_price: float, condition_type: str) -> Optional[int]:
    """Adds a price alert. condition_type should be 'ABOVE' or 'BELOW'."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO price_alerts (symbol, target_price, condition_type) VALUES (?, ?, ?)",
                (symbol.upper(), target_price, condition_type.upper())
            )
            return cursor.lastrowid
    except Exception as e:
        logger.error(f"Error adding price alert for {symbol}: {e}")
        return None

def get_active_price_alerts() -> List[Dict[str, Any]]:
    """Retrieves all currently active price alerts."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, symbol, target_price, condition_type, created_at FROM price_alerts WHERE is_active = 1"
            )
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching active price alerts: {e}")
        return []

def deactivate_price_alert(alert_id: int) -> bool:
    """Disables a price alert once triggered or deleted."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE price_alerts SET is_active = 0 WHERE id = ?",
                (alert_id,)
            )
            return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error deactivating price alert {alert_id}: {e}")
        return False

# --- Trailing Stop Operations ---

def add_trailing_stop(ticket: int, symbol: str, trail_distance_pips: float, min_profit_pips: float = 0.0) -> bool:
    """Adds or updates a trailing stop configuration for an active position ticket."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO trailing_stops 
                   (ticket, symbol, trail_distance_pips, min_profit_pips, is_active) 
                   VALUES (?, ?, ?, ?, 1)""",
                (ticket, symbol.upper(), trail_distance_pips, min_profit_pips)
            )
            return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error setting trailing stop for ticket {ticket}: {e}")
        return False

def remove_trailing_stop(ticket: int) -> bool:
    """Removes a trailing stop configuration (typically run after a position is closed)."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trailing_stops WHERE ticket = ?", (ticket,))
            return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error removing trailing stop for ticket {ticket}: {e}")
        return False

def get_active_trailing_stops() -> List[Dict[str, Any]]:
    """Retrieves all active trailing stop settings."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT ticket, symbol, trail_distance_pips, min_profit_pips FROM trailing_stops WHERE is_active = 1"
            )
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching active trailing stops: {e}")
        return []
