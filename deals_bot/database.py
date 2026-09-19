"""Versioned state. Money is integer paise and all timestamps are UTC."""
import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from product import amazon_asin, meaningful_drop, paise


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def timestamp(value=None):
    return (value or utcnow()).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError("State schema is newer than this bot")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS posted_deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url_hash TEXT UNIQUE NOT NULL, title TEXT, source TEXT,
                    posted_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS price_observations (
                    asin TEXT NOT NULL, observed_at TEXT NOT NULL,
                    price_paise INTEGER NOT NULL CHECK(price_paise > 0),
                    source TEXT NOT NULL, verified INTEGER NOT NULL CHECK(verified IN (0,1)),
                    PRIMARY KEY(asin, observed_at, source, verified)
                );
                CREATE TABLE IF NOT EXISTS posting_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, asin TEXT NOT NULL,
                    attempted_at TEXT NOT NULL, price_paise INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','sent','rejected')),
                    message_id INTEGER, title TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS attempts_product ON posting_attempts(asin, attempted_at);
            """)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS watchlist (
                    asin TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL, last_check_at TEXT,
                    last_success_at TEXT, next_check_at TEXT NOT NULL,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    manual INTEGER NOT NULL DEFAULT 0 CHECK(manual IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS cycle_reports (
                    cycle_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                    mode TEXT NOT NULL, status TEXT NOT NULL, report_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_reports (
                    cycle_id TEXT NOT NULL, source_id TEXT NOT NULL,
                    started_at TEXT NOT NULL, report_json TEXT NOT NULL,
                    PRIMARY KEY(cycle_id, source_id)
                );
            """)
            if version < 2:
                conn.execute("""INSERT OR IGNORE INTO watchlist
                    (asin,url,title,first_seen_at,last_check_at,last_success_at,next_check_at)
                    SELECT asin,'https://www.amazon.in/dp/' || asin,asin,MIN(observed_at),
                           MAX(observed_at),MAX(observed_at),datetime(MAX(observed_at),'+6 hours')
                    FROM price_observations WHERE verified=1 AND length(asin)=10
                    AND asin NOT GLOB '*[^A-Z0-9]*'
                    GROUP BY asin HAVING MAX(observed_at)>=datetime('now','-30 days')
                    ORDER BY MAX(observed_at) DESC,asin LIMIT 100""")
            conn.execute("PRAGMA user_version=2")

    def observe(self, deal, now=None):
        price = paise(deal.deal_price)
        asin = amazon_asin(deal.url)
        if not price or not asin:
            return
        verified = bool(deal.verified_at and deal.availability is True)
        at = deal.verified_at if verified else timestamp(now)
        with self.connection() as conn:
            conn.execute("""INSERT INTO price_observations VALUES (?,?,?,?,?)
                ON CONFLICT(asin,observed_at,source,verified) DO UPDATE SET price_paise=excluded.price_paise""",
                         (asin, at, price, deal.source, int(verified)))

    def baseline(self, asin, now=None):
        today = (now or utcnow()).astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        with self.connection() as conn:
            rows = conn.execute("""SELECT MIN(price_paise) FROM price_observations
                WHERE asin=? AND verified=1 AND observed_at>=? AND observed_at<?
                GROUP BY date(observed_at)""",
                                (asin, timestamp(today - timedelta(days=30)), timestamp(today))).fetchall()
        return (int(median([r[0] for r in rows])) if len(rows) >= 7 else None, len(rows))

    def can_post(self, deal, now=None):
        return self.posting_reason(deal, now) == "eligible"

    def posting_reason(self, deal, now=None):
        now = now or utcnow()
        with self.connection() as conn:
            pending = conn.execute("SELECT 1 FROM posting_attempts WHERE asin=? AND status='pending'",
                                   (deal.asin,)).fetchone()
            if pending:
                return "pending_attempt"
            latest = conn.execute("""SELECT * FROM posting_attempts WHERE asin=?
                ORDER BY attempted_at DESC,id DESC LIMIT 1""", (deal.asin,)).fetchone()
            legacy = conn.execute("SELECT posted_at FROM posted_deals WHERE url_hash=?",
                                  (make_hash(deal.url),)).fetchone()
            sent = conn.execute("""SELECT * FROM posting_attempts WHERE asin=? AND status='sent'
                ORDER BY attempted_at DESC,id DESC LIMIT 1""", (deal.asin,)).fetchone()
        if latest:
            elapsed = now - datetime.fromisoformat(latest["attempted_at"]).replace(tzinfo=timezone.utc)
            if elapsed < timedelta(hours=24):
                return "duplicate_cooldown"
        # Legacy records have no reliable price: never manufacture a repost threshold.
        if legacy and (not sent or legacy["posted_at"] > sent["attempted_at"]):
            if legacy["posted_at"] > timestamp(now - timedelta(days=7)):
                return "duplicate_cooldown"
        if sent and sent["attempted_at"] > timestamp(now - timedelta(days=7)):
            return "eligible" if meaningful_drop(sent["price_paise"], paise(deal.deal_price)) else "duplicate_cooldown"
        return "eligible"

    def begin_attempt(self, deal, now=None):
        with self.connection() as conn:
            cursor = conn.execute("""INSERT INTO posting_attempts
                (asin,attempted_at,price_paise,status,title) VALUES (?,?,?,'pending',?)""",
                                  (deal.asin, timestamp(now), paise(deal.deal_price), deal.title))
            return cursor.lastrowid

    def finish_attempt(self, attempt_id, deal, message_id):
        with self.connection() as conn:
            conn.execute("UPDATE posting_attempts SET status=?,message_id=? WHERE id=?",
                         ("sent" if message_id else "rejected", message_id, attempt_id))
            if message_id:
                at = conn.execute("SELECT attempted_at FROM posting_attempts WHERE id=?", (attempt_id,)).fetchone()[0]
                conn.execute("""INSERT INTO posted_deals(url_hash,title,source,posted_at) VALUES(?,?,?,?)
                    ON CONFLICT(url_hash) DO UPDATE SET title=excluded.title, source=excluded.source,
                    posted_at=excluded.posted_at""", (make_hash(deal.url), deal.title, deal.source, at))

    def cleanup(self, now=None):
        with self.connection() as conn:
            conn.execute("DELETE FROM price_observations WHERE observed_at<?",
                         (timestamp((now or utcnow()) - timedelta(days=90)),))
            cutoff = timestamp((now or utcnow()) - timedelta(days=90))
            conn.execute("DELETE FROM cycle_reports WHERE started_at<?", (cutoff,))
            conn.execute("DELETE FROM source_reports WHERE started_at<?", (cutoff,))

    def save_report(self, report):
        import json
        with self.connection() as conn:
            conn.execute("INSERT OR REPLACE INTO cycle_reports VALUES (?,?,?,?,?)",
                         (report["cycle_id"], report["started_at"], report["mode"],
                          report["status"], json.dumps(report, sort_keys=True)))
            for source in report["sources"]:
                conn.execute("INSERT OR REPLACE INTO source_reports VALUES (?,?,?,?)",
                             (report["cycle_id"], source["source_id"], report["started_at"],
                              json.dumps(source, sort_keys=True)))

    def import_legacy(self, paths):
        for path in paths:
            path = Path(path).resolve()
            if not path.exists() or path == self.path.resolve():
                continue
            source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            try:
                rows = source.execute("SELECT url_hash,title,source,posted_at FROM posted_deals").fetchall()
            finally:
                source.close()
            with self.connection() as conn:
                for row in rows:
                    conn.execute("""INSERT INTO posted_deals(url_hash,title,source,posted_at) VALUES(?,?,?,?)
                        ON CONFLICT(url_hash) DO UPDATE SET title=excluded.title, source=excluded.source,
                        posted_at=excluded.posted_at WHERE excluded.posted_at > posted_deals.posted_at""", row)


def make_hash(url):
    return hashlib.md5((amazon_asin(url) or url.strip()).encode()).hexdigest()
