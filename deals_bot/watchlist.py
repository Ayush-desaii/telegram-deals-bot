"""Bounded product retention and due-check scheduling; no network access."""
from datetime import timedelta
from pathlib import Path

from database import timestamp, utcnow
from product import product_identity, product_key, retail_url


def load_manual(path, product_urls=None):
    asins = set()
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        asin = product_key(line)
        if not asin:
            raise ValueError(f"Invalid supported product URL on watchlist line {number}")
        if product_urls is not None:
            product_urls[asin] = line
        asins.add(asin)
    if len(asins) > 20:
        raise ValueError("Manual watchlist allows at most 20 distinct products")
    return sorted(asins)


class Watchlist:
    def __init__(self, db):
        self.db = db

    def _make_room(self, conn):
        if conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] >= 100:
            conn.execute("""DELETE FROM watchlist WHERE asin=(SELECT asin FROM watchlist
                WHERE manual=0 ORDER BY COALESCE(last_success_at,first_seen_at),asin LIMIT 1)""")

    def sync_manual(self, asins, now=None):
        urls = {a: self.db.product_url(a) or f"https://www.amazon.in/dp/{a}" for a in asins}
        if len(set(asins)) > 20 or any(product_key(urls[a]) != a for a in asins):
            raise ValueError("Invalid manual watchlist")
        at = timestamp(now)
        with self.db.connection() as conn:
            conn.execute("UPDATE watchlist SET manual=0")
            # Protect all already-tracked manual entries before making room for new ones.
            conn.executemany("UPDATE watchlist SET manual=1 WHERE asin=?", [(a,) for a in asins])
            for asin in asins:
                if not conn.execute("SELECT 1 FROM watchlist WHERE asin=?", (asin,)).fetchone():
                    self._make_room(conn)
                    conn.execute("""INSERT INTO watchlist
                        (asin,url,title,first_seen_at,next_check_at,manual) VALUES (?,?,?,?,?,1)""",
                                 (asin, urls[asin], asin, at, at))
        self.expire(now)

    def expire(self, now=None):
        with self.db.connection() as conn:
            conn.execute("""DELETE FROM watchlist WHERE manual=0
                AND COALESCE(last_success_at,first_seen_at)<?""",
                         (timestamp((now or utcnow()) - timedelta(days=30)),))

    def due(self, now=None, force=False):
        with self.db.connection() as conn:
            return [dict(row) for row in conn.execute("""SELECT w.*,
                (SELECT COUNT(DISTINCT date(observed_at)) FROM price_observations p
                 WHERE p.asin=w.asin AND p.verified=1) AS history_days
                FROM watchlist w WHERE ? OR next_check_at<=?
                ORDER BY next_check_at,manual DESC,history_days DESC,asin""", (int(force), timestamp(now)))]

    def all_asins(self):
        with self.db.connection() as conn:
            return {row[0] for row in conn.execute("SELECT asin FROM watchlist")}

    def record(self, asin, result, now=None):
        now = now or utcnow()
        if result.reason == "budget_skipped":
            return
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM watchlist WHERE asin=?", (asin,)).fetchone()
            if result.deal is not None:
                if not row:
                    self._make_room(conn)
                    conn.execute("""INSERT INTO watchlist
                        (asin,url,title,first_seen_at,next_check_at) VALUES (?,?,?,?,?)""",
                                 (asin, product_identity(retail_url(result.deal))[2], result.deal.title,
                                  timestamp(now), timestamp(now)))
                conn.execute("""UPDATE watchlist SET title=?,last_check_at=?,last_success_at=?,
                    next_check_at=?,failure_count=0 WHERE asin=?""",
                             (result.deal.title, timestamp(now), timestamp(now),
                              timestamp(now + timedelta(hours=6)), asin))
            elif row:
                failures = row["failure_count"] + 1
                delay = 24 if result.reason == "unavailable" else (6, 12, 24)[min(failures - 1, 2)]
                conn.execute("""UPDATE watchlist SET last_check_at=?,next_check_at=?,failure_count=?
                    WHERE asin=?""", (timestamp(now), timestamp(now + timedelta(hours=delay)), failures, asin))
