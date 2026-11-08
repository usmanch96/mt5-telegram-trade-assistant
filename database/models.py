from typing import List, Dict, Any, Optional
from database.db_manager import get_db_connection
from services.logger import logger

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
