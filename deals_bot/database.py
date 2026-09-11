"""
database.py - SQLite database to track posted deals (prevents duplicates)
"""
import sqlite3
import hashlib
from datetime import datetime
from config import DB_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS posted_deals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url_hash    TEXT UNIQUE NOT NULL,
                title       TEXT,
                source      TEXT,
                posted_at   TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.commit()
    print("✅ Database initialized")


def make_hash(url: str) -> str:
    """Create a short hash from a URL to use as unique key."""
    return hashlib.md5(url.strip().encode()).hexdigest()


def is_already_posted(url: str) -> bool:
    """Check if a deal URL has already been posted."""
    url_hash = make_hash(url)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM posted_deals WHERE url_hash = ?", (url_hash,)
        ).fetchone()
    return row is not None


def mark_as_posted(url: str, title: str, source: str) -> None:
    """Mark a deal URL as posted so it won't be posted again."""
    url_hash = make_hash(url)
    with get_connection() as conn:
        try:
            conn.execute(
                "INSERT INTO posted_deals (url_hash, title, source) VALUES (?, ?, ?)",
                (url_hash, title, source),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            pass  # Already exists, ignore


def get_total_posted() -> int:
    """Return total number of deals posted so far."""
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM posted_deals").fetchone()
    return row["cnt"] if row else 0


def cleanup_old_records(days: int = 30) -> None:
    """Delete records older than X days to keep DB small."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM posted_deals WHERE posted_at < datetime('now', ?, 'localtime')",
            (f"-{days} days",),
        )
        conn.commit()
