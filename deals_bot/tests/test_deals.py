import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch, Mock
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.update(TELEGRAM_BOT_TOKEN="test-token", TELEGRAM_CHANNEL_ID="@test",
                  AMAZON_AFFILIATE_TAG="test-21")

import config
import fetcher
import main
import poster
from database import Database, make_hash, timestamp, utcnow
from formatter import format_deal_message, fmt_price
from product import amazon_asin, paise
from quality import eligible, ranking
from state_store import GitStateStore, preview_database

ASIN = "B012345678"
URL = f"https://www.amazon.in/dp/{ASIN}"
NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)


def deal(price=500, original=1000, now=None, asin=ASIN):
    return fetcher.Deal("Useful product", f"https://www.amazon.in/dp/{asin}?tag=test-21",
                        "Amazon India", deal_price=price, original_price=original,
                        asin=asin, availability=True, verified_at=timestamp(now), rating="4.5")


def page(availability="In stock", asin=ASIN, price="₹500.50", original="₹1,000.00"):
    availability_html = f'<div id="availability">{availability}</div>' if availability is not None else ""
    return f'''<input id="ASIN" value="{asin}"><span id="productTitle">Product &amp; name</span>
        {availability_html}<div id="corePriceDisplay_desktop_feature_div">
        <span class="priceToPay"><span class="a-offscreen">{price}</span></span>
        <span class="basisPrice"><span class="a-offscreen">{original}</span></span></div>'''


class IsolatedTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Database(Path(self.directory.name) / "state.sqlite")
        self.db.initialize()
        self.network = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def history(self, values, asin=ASIN):
        for day, price in enumerate(values, 1):
            self.db.observe(deal(price, now=NOW - timedelta(days=day), asin=asin))


class ProductTests(IsolatedTest):
    def test_identity_and_deceptive_domains(self):
        for url in (URL, URL + "?tag=other", URL.replace("/dp/", "/gp/product/")):
            self.assertEqual(amazon_asin(url), ASIN)
        for url in (URL.replace("amazon.in", "amazon.in.evil.org"),
                    "https://evil.org/?url=" + URL, URL.replace("https", "ftp"),
                    URL.replace("www.amazon.in", "www.amazon.in@evil.org"),
                    URL + "EXTRA", URL.replace("www.amazon.in", "www.amazon.in:bad")):
            self.assertIsNone(amazon_asin(url), url)

    def test_invalid_prices(self):
        for value in (None, 0, -1, "bad", float("nan"), float("inf"), True, "0.001"):
            self.assertIsNone(paise(value))
            self.assertEqual(fetcher.filter_deals([deal(value)]), [])
        self.assertEqual(paise("99.99"), 9999)
        for text in ("-₹99", "99 to 199", "NaN", "₹0", "1.123", "500/month"):
            self.assertIsNone(fetcher.parse_price(text))
        self.assertEqual(fetcher.parse_price("₹1,234.50"), 1234.5)

    def test_affiliate_replacement_and_canonical_url(self):
        self.assertEqual(fetcher.make_affiliate_url(URL + "?tag=someone-21&ref=abc"), URL + "?tag=test-21")
        with self.assertRaises(ValueError):
            fetcher.add_affiliate_tag("https://evil.org/" + ASIN)

    def test_production_requires_tag_preview_does_not(self):
        for tag in ("", "yourtag-21", "placeholder", "invalid tag"):
            with patch.object(config, "AMAZON_AFFILIATE_TAG", tag):
                self.assertFalse(config.validate_config())
                self.assertTrue(config.validate_config(production=False))

    def test_verification_replaces_price_and_does_not_retain_stale_image(self):
        response = SimpleNamespace(url=URL, text=page())
        candidate = deal(1)
        candidate.image_url = "https://old/image.jpg"
        with patch("fetcher.safe_get", return_value=response):
            fresh = fetcher.verify_deal(candidate)
        self.assertEqual(fresh.deal_price, 500.5)
        self.assertEqual(fresh.discount_percent, 49)
        self.assertIsNone(fresh.image_url)
        self.assertTrue(eligible(fresh, self.db))

    def test_unavailable_unknown_blocked_and_mismatched_pages(self):
        pages = [page(availability=None), page(availability="Currently unavailable"),
                 page(availability="Not in stock"), page(availability="Usually dispatched in 3 days"),
                 page(asin="B999999999"), page(price=""),
                 page() + '<input id="captchacharacters">', "<h1>Robot Check</h1>"]
        for html in pages:
            with patch("fetcher.safe_get", return_value=SimpleNamespace(url=URL, text=html)):
                self.assertIsNone(fetcher.verify_deal(deal()))
        with patch("fetcher.safe_get", return_value=SimpleNamespace(url=URL.replace(ASIN, "B999999999"), text=page())):
            self.assertIsNone(fetcher.verify_deal(deal()))

    def test_no_price_fallback_from_recommended_products(self):
        html = page(price="") + '<span class="a-price"><span class="a-offscreen">₹10</span></span>'
        with patch("fetcher.safe_get", return_value=SimpleNamespace(url=URL, text=html)):
            self.assertIsNone(fetcher.verify_deal(deal()))

    def test_bestseller_relative_product_links(self):
        response = SimpleNamespace(text=f'<div class="zg-item-immersion"><a href="/dp/{ASIN}">Product</a></div>')
        details = dict(title="Product", deal_price=500, original_price=1000)
        with patch("fetcher.safe_get", return_value=response), \
                patch("fetcher.scrape_product_page", return_value=details) as scrape, \
                patch("fetcher.time.sleep"):
            found = fetcher.fetch_amazon_bestsellers_enriched("https://www.amazon.in/gp/bestsellers/", "Home")
        scrape.assert_called_once_with(URL)
        self.assertEqual(len(found), 1)


class HistoryTests(IsolatedTest):
    def test_cold_start_requires_mrp_discount(self):
        self.assertTrue(eligible(deal(600, 1000, NOW), self.db, NOW))
        self.assertFalse(eligible(deal(601, 1000, NOW), self.db, NOW))
        self.assertFalse(eligible(deal(99, None, NOW), self.db, NOW))

    def test_baseline_daily_minimum_and_median(self):
        self.history([1000, 1200, 1100, 900, 800, 1300, 1400])
        self.db.observe(deal(500, now=NOW - timedelta(days=1, hours=1)))
        self.assertEqual(self.db.baseline(ASIN, NOW), (110000, 7))

    def test_duplicate_observations_do_not_create_history_days(self):
        sample = deal(1000, now=NOW - timedelta(days=1))
        for _ in range(10):
            self.db.observe(sample)
        self.assertEqual(self.db.baseline(ASIN, NOW), (None, 1))

    def test_excludes_today_unverified_and_outside_window(self):
        self.history([1000] * 6)
        self.db.observe(deal(1, now=NOW))
        self.db.observe(deal(1, now=NOW - timedelta(days=31)))
        unverified = deal(1, now=NOW - timedelta(days=7))
        unverified.verified_at = None
        self.db.observe(unverified, NOW - timedelta(days=7))
        self.assertEqual(self.db.baseline(ASIN, NOW), (None, 6))
        self.db.observe(deal(1000, now=NOW.replace(hour=0) - timedelta(days=30)))
        self.assertEqual(self.db.baseline(ASIN, NOW), (100000, 7))

    def test_history_thresholds_and_no_mrp_fallback(self):
        self.history([1000] * 7)
        self.assertTrue(eligible(deal(900, 10000, NOW), self.db, NOW))
        self.assertFalse(eligible(deal(900.01, 10000, NOW), self.db, NOW))
        self.assertFalse(eligible(deal(1000, 10000, NOW), self.db, NOW))
        self.history([400] * 7, asin="B999999999")
        self.assertFalse(eligible(deal(360, 1000, NOW, "B999999999"), self.db, NOW))
        self.assertTrue(eligible(deal(350, 1000, NOW, "B999999999"), self.db, NOW))

    def test_stale_and_future_verifications_rejected(self):
        for delta in (timedelta(minutes=-6), timedelta(minutes=1)):
            self.assertFalse(eligible(deal(now=NOW + delta), self.db, NOW))

    def test_history_ranks_before_mrp_and_ties_are_deterministic(self):
        self.history([1000] * 7)
        historical = deal(900, 1000, NOW)
        cold = deal(100, 1000, NOW, "B999999999")
        self.assertTrue(eligible(historical, self.db, NOW))
        self.assertTrue(eligible(cold, self.db, NOW))
        self.assertEqual(sorted([cold, historical], key=ranking)[0], historical)
        twin = deal(100, 1000, NOW, "B888888888")
        eligible(twin, self.db, NOW)
        self.assertEqual(sorted([cold, twin], key=ranking)[0], twin)

    def test_early_repost_requires_24_hours_and_meaningful_drop(self):
        old = deal(1000, now=NOW - timedelta(days=2))
        attempt = self.db.begin_attempt(old, NOW - timedelta(days=2))
        self.db.finish_attempt(attempt, old, 123)
        self.assertTrue(self.db.can_post(deal(900, now=NOW), NOW))
        self.assertFalse(self.db.can_post(deal(901, now=NOW), NOW))
        self.assertFalse(self.db.can_post(deal(500, now=NOW), NOW - timedelta(days=1, seconds=1)))
        self.assertTrue(self.db.can_post(deal(1100, now=NOW), NOW + timedelta(days=5)))

    def test_pending_suppressed_indefinitely_and_rejected_cools_down(self):
        candidate = deal(now=NOW)
        attempt = self.db.begin_attempt(candidate, NOW)
        self.assertFalse(self.db.can_post(candidate, NOW + timedelta(days=100)))
        self.db.finish_attempt(attempt, candidate, None)
        self.assertFalse(self.db.can_post(candidate, NOW + timedelta(hours=1)))
        self.assertTrue(self.db.can_post(candidate, NOW + timedelta(days=1)))

    def test_legacy_import_uses_latest_timestamp_and_no_invented_price(self):
        paths = []
        for i, days in enumerate((10, 2)):
            path = Path(self.directory.name) / f"legacy{i}.db"
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE posted_deals(url_hash TEXT,title TEXT,source TEXT,posted_at TEXT)")
                conn.execute("INSERT INTO posted_deals VALUES (?,?,?,?)",
                             (make_hash(URL), "Old title", "Amazon", timestamp(NOW - timedelta(days=days))))
            conn.close()
            paths.append(path)
        self.db.import_legacy(paths)
        self.db.initialize()
        self.assertFalse(self.db.can_post(deal(1, now=NOW), NOW))
        self.assertTrue(self.db.can_post(deal(1, now=NOW), NOW + timedelta(days=5)))
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0], 0)

    def test_migration_preserves_old_schema_rows(self):
        path = Path(self.directory.name) / "old.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE posted_deals(id INTEGER PRIMARY KEY,url_hash TEXT UNIQUE,title TEXT,source TEXT,posted_at TEXT)")
            conn.execute("INSERT INTO posted_deals VALUES(1,?,'old','Amazon',?)", (make_hash(URL), timestamp(NOW)))
        conn.close()
        old = Database(path)
        old.initialize()
        old.initialize()
        self.assertFalse(old.can_post(deal(now=NOW), NOW))

    def test_cleanup_retains_pending_and_recent_observations(self):
        self.db.observe(deal(now=NOW - timedelta(days=91)))
        self.db.observe(deal(now=NOW - timedelta(days=89)))
        self.db.begin_attempt(deal(now=NOW), NOW - timedelta(days=100))
        self.db.cleanup(NOW)
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0], 1)
        self.assertFalse(self.db.can_post(deal(now=NOW), NOW))


class MessageTests(IsolatedTest):
    def test_escaped_long_caption_preserves_decimals_and_disclosure(self):
        candidate = deal(499.99, 1000)
        candidate.title = '<&"\'😀' * 200
        self.assertTrue(eligible(candidate, self.db))
        caption = format_deal_message(candidate)
        ET.fromstring("<root>" + caption + "</root>")
        self.assertLessEqual(len(caption.encode("utf-16-le")) // 2, 1024)
        self.assertIn("₹499.99", caption)
        self.assertIn("off MRP", caption)
        self.assertIn("Affiliate link", caption)
        self.assertIn(" UTC", caption)
        self.assertNotIn("Best Seller", caption)
        self.assertEqual(fmt_price(99.01), "₹99.01")

    def test_historical_caption_describes_observed_sample(self):
        self.history([1000] * 7)
        candidate = deal(900, now=NOW)
        eligible(candidate, self.db, NOW)
        caption = format_deal_message(candidate)
        self.assertIn("below observed price", caption)
        self.assertIn("7 days sampled", caption)

    def test_timeout_does_not_fallback_to_another_send(self):
        import requests
        candidate = deal()
        candidate.image_url = "https://example.org/image.jpg"
        eligible(candidate, self.db)
        with patch("poster.add_watermark", return_value=b"image"), \
                patch("poster.requests.post", side_effect=requests.Timeout()) as send:
            with self.assertRaises(poster.AmbiguousDelivery):
                poster.post_deal(candidate)
            self.assertEqual(send.call_count, 1)

    def test_invalid_response_and_server_error_are_ambiguous(self):
        for response in (Mock(status_code=503), Mock(status_code=200, json=Mock(side_effect=ValueError())),
                         Mock(status_code=200, json=Mock(return_value={"ok": True, "result": {}}))):
            with self.assertRaises(poster.AmbiguousDelivery):
                poster.message_result(response)
        self.assertIsNone(poster.message_result(Mock(status_code=400, json=Mock(return_value={"ok": False, "error_code": 400}))))


class PipelineTests(IsolatedTest):
    def run_cycle(self, verify=None, save=None, preview=False, send=None):
        with patch("main.fetch_all_deals", return_value=[deal()]), \
                patch("main.verify_deal", side_effect=verify or (lambda item: deal())), \
                patch("main.post_deal", side_effect=send or (lambda item: 123)) as post, \
                patch("main.time.sleep"):
            stats = main.run_deal_cycle(self.db, save or (lambda: None), test_mode=preview)
            return stats, post

    def test_preview_sends_nothing_and_records_no_attempt(self):
        stats, post = self.run_cycle(preview=True)
        post.assert_not_called()
        self.assertEqual(stats["posted"], 0)
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM posting_attempts").fetchone()[0], 0)

    def test_changed_price_fails_second_validation(self):
        values = iter([deal(), deal(950, 1000)])
        stats, post = self.run_cycle(verify=lambda item: next(values))
        post.assert_not_called()
        self.assertEqual(stats["posted"], 0)

    def test_failed_verification_never_posts_stale_candidate(self):
        stats, post = self.run_cycle(verify=lambda item: None)
        post.assert_not_called()
        self.assertEqual(stats["verified"], 0)

    def test_pending_is_saved_before_send_and_success_immediately_after(self):
        events = []
        def save():
            with self.db.connection() as conn:
                events.append([r[0] for r in conn.execute("SELECT status FROM posting_attempts")])
        def send(item):
            self.assertEqual(events[-1], ["pending"])
            return 123
        stats, post = self.run_cycle(save=save, send=send)
        self.assertEqual(events[-1], ["sent"])
        self.assertEqual(stats["posted"], 1)

    def test_persistence_failure_prevents_send(self):
        with patch("main.post_deal") as post:
            with self.assertRaises(RuntimeError):
                self.run_cycle(save=Mock(side_effect=RuntimeError("storage down")))
            post.assert_not_called()

    def test_pending_save_failure_prevents_send(self):
        def save():
            with self.db.connection() as conn:
                if conn.execute("SELECT COUNT(*) FROM posting_attempts").fetchone()[0]:
                    raise RuntimeError("pending save failed")
        send = Mock()
        with self.assertRaises(RuntimeError):
            self.run_cycle(save=save, send=send)
        send.assert_not_called()

    def test_timeout_remains_pending(self):
        stats, post = self.run_cycle(send=Mock(side_effect=poster.AmbiguousDelivery()))
        self.assertEqual(stats["pending"], 1)
        self.assertFalse(self.db.can_post(deal()))

    def test_uncertain_sends_count_against_two_post_limit(self):
        items = [deal(asin=asin) for asin in (ASIN, "B999999999", "B888888888")]
        with patch("main.fetch_all_deals", return_value=items), \
                patch("main.verify_deal", side_effect=lambda item: deal(asin=item.asin)), \
                patch("main.post_deal", side_effect=poster.AmbiguousDelivery()) as send:
            stats = main.run_deal_cycle(self.db, lambda: None)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(stats["pending"], 2)

    def test_after_send_persistence_failure_leaves_durable_pending(self):
        snapshots = []
        def save():
            with self.db.connection() as conn:
                states = [r[0] for r in conn.execute("SELECT status FROM posting_attempts")]
            if states == ["sent"]:
                raise RuntimeError("remote push failed")
            snapshots.append(states)
        send = Mock(return_value=123)
        with self.assertRaises(RuntimeError):
            self.run_cycle(save=save, send=send)
        send.assert_called_once()
        self.assertEqual(snapshots[-1], ["pending"])

    def test_preview_copy_never_changes_source_database(self):
        self.db.observe(deal())
        before = self.db.path.read_bytes()
        temporary = preview_database(Path(self.directory.name) / "preview.db", self.db.path)
        temporary.observe(deal(999))
        self.assertEqual(before, self.db.path.read_bytes())


class StateStoreTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.repo = Path(self.directory.name) / "code"
        self.remote = Path(self.directory.name) / "remote.git"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", str(self.repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin", str(self.remote)], check=True, capture_output=True)

    def test_restore_independent_run_and_reject_concurrent_writer(self):
        first = GitStateStore(self.repo, self.db)
        first.restore()
        first.save()
        other_db = Database(Path(self.directory.name) / "other.sqlite")
        other = GitStateStore(self.repo, other_db)
        other.restore()
        self.db.observe(deal())
        self.db.begin_attempt(deal())
        first.save()
        other_db.observe(deal(600))
        with self.assertRaises(RuntimeError):
            other.save()
        fresh_db = Database(Path(self.directory.name) / "fresh.sqlite")
        fresh = GitStateStore(self.repo, fresh_db)
        fresh.restore()
        self.assertFalse(fresh_db.can_post(deal()))
        with fresh_db.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0], 1)

    def test_restore_error_is_not_treated_as_empty_state(self):
        store = GitStateStore(self.repo, self.db)
        with patch.object(store, "git", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                store.restore()


if __name__ == "__main__":
    unittest.main()
