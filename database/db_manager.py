import sqlite3
from contextlib import contextmanager
from typing import Generator
from config import settings
from services.logger import logger

@contextmanager
def get_db_connection() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for SQLite connections to ensure proper closing and thread-safety."""
    conn = None
    try:
        conn = sqlite3.connect(settings.DB_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error occurred: {e}")
        raise
    finally:
        if conn:
            conn.close()

def init_db() -> None:
    """Initializes the database schema if tables do not exist."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Table 1: Authorized Users
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS authorized_users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Table 2: Price Alerts
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                target_price REAL NOT NULL,
                condition_type TEXT NOT NULL, -- 'ABOVE' or 'BELOW'
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Table 3: Trailing Stops
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trailing_stops (
                ticket INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                trail_distance_pips REAL NOT NULL,
                min_profit_pips REAL DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
        """)
        
        # Seed initial authorized users from environment variables if table is empty
        cursor.execute("SELECT COUNT(*) as count FROM authorized_users")
        if cursor.fetchone()["count"] == 0:
            for uid in settings.AUTHORIZED_USERS:
                cursor.execute(
                    "INSERT OR IGNORE INTO authorized_users (telegram_id, username) VALUES (?, ?)",
                    (uid, "EnvAdmin")
                )
            logger.info("Seeded initial authorized users from settings.")
            
        logger.info("Database schemas initialized successfully.")
