import sqlite3
import threading
from contextlib import contextmanager
from typing import Generator
from config import settings
from services.logger import logger

_db_lock = threading.Lock()

@contextmanager
def get_db_connection() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for SQLite connections with thread locking and WAL mode."""
    conn = None
    with _db_lock:
        try:
            conn = sqlite3.connect(settings.DB_PATH, timeout=15.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=15000;")
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
        
        # Whitelisted authorized users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS authorized_users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            
        logger.info("Database schema initialized successfully.")
