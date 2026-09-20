import copy
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from test_deals import IsolatedTest, deal, ASIN, URL
from database import Database, timestamp, utcnow, make_hash
from discovery import ProductCandidate, SourceResult, VerificationResult, HttpResult, verify_candidate, select_candidates
from earnkaro import EarnKaroLinks, purchase_url, valid_profit_url
from flipkart import parse_product, discover_page
from formatter import format_deal_message
from poster import build_deal_keyboard, AmbiguousDelivery
from product import product_key, product_identity
from quality import eligible
from reporting import CycleReport
from state_store import preview_database
from watchlist import Watchlist, load_manual
import main
import config
import fetcher

PID = "ACCDUEMNADBMZDDG"
KEY = "flipkart:" + PID
FK_URL = "https://www.flipkart.com/headphones/p/itm0123456789abcdef?pid=" + PID
PROFIT = "https://ekaro.in/enkrTEST123456"


def candidate():
    return ProductCandidate(KEY, FK_URL, source_ids={"flipkart_search:Audio"})


def product():
    return {"@type": "Product", "sku": PID, "url": FK_URL, "name": "Headphones & <good>",
            "offers": {"@type": "Offer", "url": FK_URL, "price": "499.50", "priceCurrency": "INR",
                       "availability": "https://schema.org/InStock",
                       "priceSpecification": {"name": "MRP", "priceCurrency": "INR", "price": "1000"}}}


def response(data=None, url=FK_URL):
    html = '<script type="application/ld+json">' + json.dumps(data or product()) + '</script>'
    return SimpleNamespace(url=url, text=html)


def fk_deal():
    return parse_product(candidate(), response()).deal


def registry():
    return EarnKaroLinks([{"product_url": FK_URL, "profit_url": PROFIT}])


class IdentityAndParserTests(IsolatedTest):
    def test_store_identity_is_strict_and_preserves_amazon(self):
        self.assertEqual(product_key(URL), ASIN)
        self.assertEqual(product_key(FK_URL), KEY)
        self.assertEqual(product_identity(FK_URL + "&affid=someone&otracker=ads")[2], FK_URL)
        for url in (FK_URL.replace("flipkart.com", "flipkart.com.evil.org"),
                    FK_URL.replace("https:", "http:"), FK_URL.replace("www.flipkart.com", "evil@www.flipkart.com"),
                    FK_URL + "&pid=" + PID, FK_URL.replace(PID, "bad"), FK_URL.replace("/p/", "/search/"),
                    "https://evil.org/?url=" + FK_URL, None, 123):
            self.assertIsNone(product_key(url))

    def test_real_price_availability_and_decimals_are_required(self):
        result = parse_product(candidate(), response())
        self.assertEqual(result.reason, "verified")
        self.assertEqual(str(result.deal.deal_price), "499.5")
        self.assertTrue(eligible(result.deal, self.db))
        cases = [("price", None, "invalid_price"), ("price", -5, "invalid_price"),
                 ("price", "NaN", "invalid_price"), ("price", 0, "invalid_price"),
                 ("priceCurrency", "USD", "invalid_price"),
                 ("availability", None, "unknown_availability"),
                 ("availability", "https://schema.org/PreOrder", "unknown_availability"),
                 ("availability", "https://schema.org/OutOfStock", "unavailable"),
                 ("priceValidUntil", "2020-01-01", "stale_offer")]
        for field, value, reason in cases:
            data = product(); data["offers"][field] = value
            self.assertEqual(parse_product(candidate(), response(data)).reason, reason)

    def test_mismatched_product_variants_and_multiple_offers_rejected(self):
        data = product(); data["sku"] = "OTHERPRODUCT1234"
        self.assertIsNone(parse_product(candidate(), response(data)).deal)
        data = product(); data["offers"]["url"] = FK_URL.replace(PID, "ACCDUEMNADBMZDDD")
        self.assertEqual(parse_product(candidate(), response(data)).reason, "identity_mismatch")
        data = product(); data["offers"] = [data["offers"], copy.deepcopy(data["offers"])]
        self.assertEqual(parse_product(candidate(), response(data)).reason, "parse_error")
        self.assertEqual(parse_product(candidate(), response(url=URL)).reason, "identity_mismatch")
        self.assertIsNone(parse_product(candidate(), response({"@type": "ItemList", "items": [product()]})).deal)

    def test_no_mrp_invention_from_high_price_or_discount(self):
        data = product(); data["offers"].pop("priceSpecification")
        data["offers"].update(highPrice=1000, discount=90)
        item = parse_product(candidate(), response(data)).deal
        self.assertIsNone(item.original_price)
        self.assertFalse(eligible(item, self.db))
        for day in range(1, 8):
            old = copy.deepcopy(item); old.deal_price = 800
            old.verified_at = timestamp(utcnow() - timedelta(days=day))
            self.db.observe(old)
        self.assertTrue(eligible(item, self.db))

    def test_listing_candidates_need_no_price_and_only_one_request(self):
        html = f'<a href="{FK_URL}">Headphones</a><a href="{FK_URL}&x=1">duplicate</a>'
        client = Mock(get=Mock(return_value=HttpResult("successful", SimpleNamespace(text=html))))
        result = discover_page("flipkart_search:Audio", "https://www.flipkart.com/search?q=headphones", client)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].metadata, {})
        client.get.assert_called_once()

    def test_blocked_empty_and_malformed_listings(self):
        for html, status in [("<title>Access Denied</title>", "blocked"),
                             ('<input name="q">Sorry, no results found', "empty"),
                             ("<html>Something changed</html>", "parse_error")]:
            client = Mock(get=Mock(return_value=HttpResult("successful", SimpleNamespace(text=html))))
            self.assertEqual(discover_page("flipkart", "https://www.flipkart.com/search", client).status, status)

    def test_verifier_routes_by_retailer(self):
        client = Mock(get=Mock(return_value=HttpResult("successful", response())))
        self.assertEqual(verify_candidate(candidate(), client).reason, "verified")
        client.get.assert_called_once_with(FK_URL)

    def test_new_slots_include_both_stores_and_deduplicate(self):
        amazon = [ProductCandidate(f"B{i:09d}", f"https://www.amazon.in/dp/B{i:09d}") for i in range(30)]
        selected, _, _ = select_candidates([], amazon + [candidate(), candidate()], set())
        self.assertEqual(len(selected), 30)
        self.assertEqual(sum(c.asin == KEY for c in selected), 1)
        self.assertEqual(selected[1].asin, KEY)


class AffiliateTests(IsolatedTest):
    def test_rejects_guessed_links_deceptive_hosts_and_conflicts(self):
        for link in ("https://earnkaro.com/deal?url=x&r=123", PROFIT.replace("ekaro.in", "ekaro.in.evil.org"),
                     PROFIT.replace("https", "http"), "https://ekaro.in/", PROFIT + "?token=SECRET", None):
            self.assertFalse(valid_profit_url(link))
            with self.assertRaises(ValueError):
                EarnKaroLinks([{"product_url": FK_URL, "profit_url": link}])
        with self.assertRaises(ValueError):
            EarnKaroLinks([{"product_url": FK_URL, "profit_url": PROFIT},
                          {"product_url": FK_URL, "profit_url": PROFIT + "other"}])
        with self.assertRaises(ValueError):
            fetcher.make_affiliate_url(FK_URL)

    def test_only_registered_link_with_matching_destination_is_used(self):
        item = fk_deal()
        client = Mock(get=Mock(return_value=HttpResult("successful", response())))
        self.assertEqual(registry().prepare(item, client), "ready")
        self.assertEqual(purchase_url(item), PROFIT)
        client.get.assert_called_once_with(PROFIT, method="HEAD")
        client.get.return_value = HttpResult("successful", response(url=URL))
        self.assertEqual(registry().prepare(item, client), "affiliate_identity_mismatch")
        self.assertIsNone(item.affiliate_url)
        with self.assertRaises(ValueError): purchase_url(item)

    def test_missing_timeout_budget_and_stale_links_never_publish(self):
        item = fk_deal()
        self.assertEqual(EarnKaroLinks().prepare(item, Mock()), "affiliate_missing")
        for status, reason in [("timeout", "affiliate_unavailable"), ("blocked", "affiliate_unavailable"),
                               ("budget_skipped", "budget_skipped")]:
            self.assertEqual(registry().prepare(item, Mock(get=Mock(return_value=HttpResult(status)))), reason)
        item.affiliate_url = PROFIT
        item.affiliate_verified_at = timestamp(utcnow() - timedelta(minutes=6))
        with self.assertRaises(ValueError): format_deal_message(item)

    def test_caption_and_button_retain_profit_link_and_store(self):
        item = fk_deal(); eligible(item, self.db)
        registry().prepare(item, Mock(get=Mock(return_value=HttpResult("successful", response()))))
        item.title = '<test> & "name"' * 100
        text = format_deal_message(item)
        self.assertIn(PROFIT, text)
        self.assertIn("Buy on Flipkart", text)
        self.assertIn("₹499.50", text)
        self.assertIn("Affiliate link:", text)
        self.assertNotIn("Buy on Amazon", text)
        self.assertLessEqual(len(text.encode('utf-16-le')) // 2, 1024)
        button = build_deal_keyboard(item)["inline_keyboard"][0][0]
        self.assertEqual(button["url"], PROFIT)
        self.assertIn("FLIPKART", button["text"])

    def test_invalid_config_errors_do_not_echo_secrets(self):
        with self.assertRaises(ValueError) as err:
            EarnKaroLinks.load("ignored", '{"password": "SECRET"')
        self.assertNotIn("SECRET", str(err.exception))


class MultiStoreStateTests(IsolatedTest):
    def test_schema_two_upgrade_preserves_pending_history_and_manual_watchlist(self):
        item = deal(); self.db.observe(item)
        attempt = self.db.begin_attempt(item)
        Watchlist(self.db).sync_manual([ASIN])
        with self.db.connection() as conn:
            conn.executescript("DROP TABLE product_catalog; PRAGMA user_version=2;")
        self.db.initialize()
        self.assertEqual(self.db.product_url(ASIN), URL)
        self.assertEqual(self.db.posting_reason(item), "pending_attempt")
        with self.db.connection() as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM price_observations').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT manual FROM watchlist').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT status FROM posting_attempts WHERE id=?', (attempt,)).fetchone()[0], 'pending')

    def test_watchlist_manual_and_history_survive_independent_run(self):
        path = Path(self.directory.name) / 'watchlist.txt'; path.write_text(FK_URL)
        urls = {}; keys = load_manual(path, urls)
        self.db.register_product(urls[KEY]); Watchlist(self.db).sync_manual(keys)
        item = fk_deal(); self.db.observe(item)
        Watchlist(self.db).record(KEY, VerificationResult("verified", item))
        reopened = Database(self.db.path); reopened.initialize()
        self.assertEqual(reopened.product_url(KEY), FK_URL)
        self.assertEqual(Watchlist(reopened).all_asins(), {KEY})
        self.assertEqual(Watchlist(reopened).due(force=True)[0]['url'], FK_URL)
        self.assertNotEqual(make_hash(FK_URL), make_hash(URL))

    def test_profit_link_changes_do_not_reset_duplicate_or_pending_protection(self):
        item = fk_deal(); item.affiliate_url = PROFIT
        attempt = self.db.begin_attempt(item)
        item.affiliate_url = PROFIT + 'changed'
        self.assertEqual(self.db.posting_reason(item), 'pending_attempt')
        self.db.finish_attempt(attempt, item, 123)
        self.assertEqual(self.db.posting_reason(item), 'duplicate_cooldown')

    def test_preview_migration_never_changes_schema_two_source(self):
        with self.db.connection() as conn:
            conn.executescript('DROP TABLE product_catalog; PRAGMA user_version=2;')
        preview = preview_database(Path(self.directory.name) / 'preview.sqlite', self.db.path)
        preview.register_product(FK_URL)
        with self.db.connection() as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 2)


class MultiStoreCycleTests(IsolatedTest):
    def test_production_requires_separate_exact_commit_store_approval(self):
        for approved, expected in [(None, False), ('old-sha', False), ('current-sha', True)]:
            env = {'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': 'current-sha', 'DEALS_APPROVED_SHA': 'current-sha'}
            if approved:
                env['FLIPKART_APPROVED_SHA'] = approved
            with patch.dict(os.environ, env, clear=True), patch.object(config, 'DB_PATH', str(self.db.path)), \
                 patch.object(config, 'FLIPKART_ENABLED', True), patch('main.GitStateStore'), \
                 patch('main.config.validate_config', return_value=True), patch('main.load_manual', return_value=[]), \
                 patch('main.EarnKaroLinks.load', return_value=registry()), \
                 patch.object(sys, 'argv', ['main.py', '--git-state']), \
                 patch('main.run_deal_cycle', return_value={'verified': 1}) as cycle:
                self.assertEqual(main.main(), 0)
                self.assertEqual(cycle.call_args.kwargs['include_flipkart'], expected)

    def test_required_store_preview_cannot_pass_on_amazon_success_alone(self):
        output = Path(self.directory.name) / 'reports'
        with patch.object(config, 'DB_PATH', str(self.db.path)), \
             patch('main.config.validate_config', return_value=True), patch('main.load_manual', return_value=[]), \
             patch('main.EarnKaroLinks.load', return_value=registry()), \
             patch.object(sys, 'argv', ['main.py', '--test', '--require-store', 'flipkart', '--report-dir', str(output)]), \
             patch('main.run_deal_cycle', return_value={'verified': 1}):
            self.assertEqual(main.main(), 1)
        self.assertEqual(json.loads((output / 'report.json').read_text())['failure'], 'store_preview_incomplete')

    def test_two_attempt_limit_shared_by_amazon_and_flipkart(self):
        amazon_candidates = [ProductCandidate(f"B{i:09d}", f"https://www.amazon.in/dp/B{i:09d}") for i in range(3)]
        sources = [SourceResult('amazon_search:Audio', 'successful', amazon_candidates)]
        def verified(item, client):
            return VerificationResult('verified', fk_deal() if item.asin == KEY else deal(600, asin=item.asin))
        with patch('main.discover', return_value=sources), \
             patch('main.flipkart.discover', return_value=[SourceResult('flipkart_search:Audio', 'successful', [candidate()])]), \
             patch('main.verify_candidate', side_effect=verified) as checked, \
             patch('main.HttpClient.get', return_value=HttpResult('successful', response())), \
             patch('main.post_deal', return_value=123) as posted, patch('main.time.sleep'):
            stats = main.run_deal_cycle(self.db, lambda: None, include_flipkart=True, affiliate_links=registry())
        self.assertEqual(stats['attempted'], 2)
        self.assertEqual(checked.call_count, 8)  # Four initials, four final refreshes.
        self.assertEqual(posted.call_args_list[0].args[0].asin, KEY)
        self.assertFalse(posted.call_args_list[1].args[0].asin.startswith('flipkart:'))

    def test_disabled_watched_store_does_not_consume_amazon_verification_slots(self):
        Watchlist(self.db).record(KEY, VerificationResult('verified', fk_deal()), utcnow() - timedelta(days=1))
        amazon_candidates = [ProductCandidate(f"B{i:09d}", f"https://www.amazon.in/dp/B{i:09d}") for i in range(30)]
        with patch('main.discover', return_value=[SourceResult('amazon_search:Audio', 'successful', amazon_candidates)]), \
             patch('main.verify_candidate', side_effect=lambda item, client: VerificationResult('verified', deal(900, asin=item.asin))) as checked, \
             patch('main.post_deal') as posted:
            stats = main.run_deal_cycle(self.db, lambda: None, include_flipkart=False)
        self.assertEqual(stats['checks_completed'], 30)
        self.assertEqual(checked.call_count, 30)
        posted.assert_not_called()

    def test_changed_price_or_profit_destination_on_refresh_cannot_post(self):
        for changed_link in (False, True):
            current = fk_deal()
            changed = fk_deal(); changed.deal_price = 999
            link_results = [HttpResult('successful', response()),
                            HttpResult('successful', response(url=URL if changed_link else FK_URL))]
            with patch('main.discover', return_value=[]), \
                 patch('main.flipkart.discover', return_value=[SourceResult('flipkart_search:Audio', 'successful', [candidate()])]), \
                 patch('main.verify_candidate', side_effect=[VerificationResult('verified', current),
                       VerificationResult('verified', fk_deal() if changed_link else changed)]), \
                 patch('main.HttpClient.get', side_effect=link_results), patch('main.post_deal') as post:
                stats = main.run_deal_cycle(self.db, lambda: None, include_flipkart=True,
                                            affiliate_links=registry(), test_mode=True, force_preview_checks=True)
            post.assert_not_called()
            self.assertEqual(stats['previewed'], 0)

    def test_preview_does_not_send_or_create_attempts(self):
        with patch('main.discover', return_value=[]), \
             patch('main.flipkart.discover', return_value=[SourceResult('flipkart_search:Audio', 'successful', [candidate()])]), \
             patch('main.verify_candidate', side_effect=lambda *_: VerificationResult('verified', fk_deal())), \
             patch('main.HttpClient.get', return_value=HttpResult('successful', response())), patch('main.post_deal') as post:
            stats = main.run_deal_cycle(self.db, lambda: None, include_flipkart=True, affiliate_links=registry(), test_mode=True)
        self.assertEqual(stats['previewed'], 1)
        post.assert_not_called()
        with self.db.connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM posting_attempts').fetchone()[0], 0)

    def cycle(self, links=None, error=None, report=None, enabled=True):
        src = SourceResult('flipkart_search:Audio', 'successful', [candidate()])
        with patch('main.discover', return_value=[]), patch('main.flipkart.discover', return_value=[src]), \
             patch('main.verify_candidate', side_effect=lambda *_: VerificationResult('verified', fk_deal())), \
             patch('main.HttpClient.get', return_value=HttpResult('successful', response())), \
             patch('main.post_deal', side_effect=error, return_value=123) as post, patch('main.time.sleep'):
            result = main.run_deal_cycle(self.db, lambda: None, include_flipkart=enabled,
                                        affiliate_links=links, report=report)
        return result, post

    def test_registered_verified_product_posts_and_persists(self):
        stats, post = self.cycle(registry())
        self.assertEqual(stats['posted'], 1)
        self.assertEqual(post.call_args.args[0].affiliate_url, PROFIT)
        self.assertIn(KEY, Watchlist(self.db).all_asins())

    def test_missing_profit_link_retains_price_history_but_never_posts(self):
        report = CycleReport()
        stats, post = self.cycle(report=report)
        self.assertEqual(stats['verified'], 1)
        post.assert_not_called()
        self.assertEqual(report.products[KEY]['selection'], 'affiliate_missing')
        self.assertIn(KEY, Watchlist(self.db).all_asins())
        self.assertNotIn(PROFIT, json.dumps(report.data()))
        self.assertNotIn(FK_URL, json.dumps(report.data()))

    def test_disabled_store_does_not_send(self):
        stats, post = self.cycle(registry(), enabled=False)
        post.assert_not_called()
        self.assertEqual(stats['verified'], 0)

    def test_ambiguous_flipkart_delivery_retains_pending(self):
        stats, _ = self.cycle(registry(), error=AmbiguousDelivery('uncertain'))
        self.assertEqual(stats['pending'], 1)
        self.assertEqual(self.db.posting_reason(fk_deal()), 'pending_attempt')

    def test_watchlist_works_without_rediscovery(self):
        item = fk_deal()
        self.db.observe(item)
        Watchlist(self.db).record(KEY, VerificationResult('verified', item), utcnow() - timedelta(days=1))
        with patch('main.discover', return_value=[]), patch('main.flipkart.discover', return_value=[]), \
             patch('main.verify_candidate', side_effect=lambda *_: VerificationResult('verified', fk_deal())) as verify, \
             patch('main.HttpClient.get', return_value=HttpResult('successful', response())), \
             patch('main.post_deal', return_value=123), patch('main.time.sleep'):
            stats = main.run_deal_cycle(self.db, lambda: None, include_flipkart=True, affiliate_links=registry())
        self.assertEqual(stats['watchlist_checks'], 1)
        self.assertEqual(stats['posted'], 1)
        self.assertEqual(verify.call_count, 2)


if __name__ == '__main__':
    unittest.main()
