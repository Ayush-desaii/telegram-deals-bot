"""
database.py - SQLite database to track posted deals (prevents duplicates)

Deals expire after EXPIRY_DAYS so the same product can be reposted
after enough time has passed (keeps the channel fresh).
"""
import sqlite3
import hashlib
from datetime import datetime
from config import DB_PATH

# How many days before a posted deal becomes eligible for reposting
EXPIRY_DAYS = 7


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
                posted_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    print("✅ Database initialized")


def make_hash(url: str) -> str:
    """Create a short hash from a URL (based on ASIN only, so variant URLs dedup correctly)."""
    import re
    asin_match = re.search(r"/dp/([A-Z0-9]{10})", url)
    key = asin_match.group(1) if asin_match else url.strip()
    return hashlib.md5(key.encode()).hexdigest()


def is_already_posted(url: str) -> bool:
    """
    Check if a deal URL has been posted recently (within EXPIRY_DAYS).
    Older records are treated as fresh — allowing weekly reposts.
    """
    url_hash = make_hash(url)
    with get_connection() as conn:
        row = conn.execute(
            """SELECT 1 FROM posted_deals
               WHERE url_hash = ?
               AND posted_at > datetime('now', ?)""",
            (url_hash, f"-{EXPIRY_DAYS} days"),
        ).fetchone()
    return row is not None


def mark_as_posted(url: str, title: str, source: str) -> None:
    """Mark a deal URL as posted. Uses INSERT OR REPLACE to reset the timer."""
    url_hash = make_hash(url)
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO posted_deals (url_hash, title, source, posted_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(url_hash) DO UPDATE SET
                   posted_at = datetime('now'),
                   title = excluded.title""",
            (url_hash, title, source),
        )
        conn.commit()


def get_total_posted() -> int:
    """Return total number of deals posted so far."""
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) as cnt FROM posted_deals").fetchone()
    return row["cnt"] if row else 0


def cleanup_old_records(days: int = 60) -> None:
    """Delete records older than X days to keep DB small."""
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM posted_deals WHERE posted_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        conn.commit()
