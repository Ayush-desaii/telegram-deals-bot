import json
import os
from pathlib import Path
import sys
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_deals import IsolatedTest, deal, page, ASIN, URL
from database import Database, timestamp, utcnow
from discovery import (Budget, HttpClient, HttpResult, ProductCandidate, SourceResult,
                       VerificationResult, discover_source, verify_candidate, select_candidates)
from reporting import CycleReport
from state_store import preview_database
from watchlist import Watchlist, load_manual
import main


def asin(number):
    return f"B{number:09d}"


def candidate(number=0, source="search", metadata=None):
    identity = asin(number)
    return ProductCandidate(identity, f"https://www.amazon.in/dp/{identity}", metadata or {}, {source})


def html_client(html, url=URL):
    return Mock(get=Mock(return_value=HttpResult("successful", SimpleNamespace(
        text=html, content=html.encode(), url=url))))


class WatchlistTests(IsolatedTest):
    def setUp(self):
        super().setUp()
        self.watch = Watchlist(self.db)
        self.now = utcnow()

    def test_manual_validation_deduplication_and_limit(self):
        path = Path(self.directory.name) / "watchlist.txt"
        path.write_text(f"# Comment\n\n{URL}\n{URL}?tag=another-21\n", encoding="utf-8")
        self.assertEqual(load_manual(path), [ASIN])
        path.write_text("https://amazon.in.evil.org/dp/B012345678", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_manual(path)
        path.write_text("\n".join(candidate(i).url for i in range(21)), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_manual(path)

    def test_schema_one_migration_preserves_history_and_pending(self):
        old = deal(now=self.now - timedelta(days=1))
        self.db.observe(old)
        attempt = self.db.begin_attempt(old, self.now)
        with self.db.connection() as conn:
            conn.executescript("DROP TABLE watchlist; DROP TABLE cycle_reports; DROP TABLE source_reports; PRAGMA user_version=1;")
        self.db.initialize()
        self.db.initialize()
        self.assertIn(ASIN, self.watch.all_asins())
        self.assertFalse(self.db.can_post(old))
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT status FROM posting_attempts WHERE id=?", (attempt,)).fetchone()[0], "pending")

    def test_unverified_observations_not_bootstrapped(self):
        item = deal()
        item.verified_at = None
        self.db.observe(item)
        with self.db.connection() as conn:
            conn.execute("PRAGMA user_version=1")
        self.db.initialize()
        self.assertEqual(self.watch.all_asins(), set())

    def test_manual_entries_protected_at_capacity(self):
        self.watch.sync_manual([asin(i) for i in range(20)], self.now - timedelta(days=20))
        for i in range(20, 100):
            self.watch.record(asin(i), VerificationResult("verified", deal(asin=asin(i))),
                              self.now - timedelta(days=100-i))
        self.assertEqual(len(self.watch.all_asins()), 100)
        self.watch.record(asin(100), VerificationResult("verified", deal(asin=asin(100))), self.now)
        self.assertEqual(len(self.watch.all_asins()), 100)
        self.assertNotIn(asin(20), self.watch.all_asins())
        self.assertTrue({asin(i) for i in range(20)} <= self.watch.all_asins())

    def test_expiry_demotes_removed_manual_and_keeps_active_manual(self):
        self.watch.sync_manual([asin(1), asin(2)], self.now - timedelta(days=31))
        self.watch.sync_manual([asin(1)], self.now)
        self.assertEqual(self.watch.all_asins(), {asin(1)})
        self.watch.record(asin(3), VerificationResult("verified", deal(asin=asin(3))), self.now - timedelta(days=31))
        self.watch.expire(self.now)
        self.assertNotIn(asin(3), self.watch.all_asins())

    def test_backoff_and_success_reset(self):
        self.watch.sync_manual([ASIN], self.now)
        for hours in (6, 12, 24, 24):
            self.watch.record(ASIN, VerificationResult("timeout"), self.now)
            with self.db.connection() as conn:
                row = conn.execute("SELECT * FROM watchlist WHERE asin=?", (ASIN,)).fetchone()
            self.assertEqual(row["next_check_at"], timestamp(self.now + timedelta(hours=hours)))
        self.watch.record(ASIN, VerificationResult("verified", deal()), self.now)
        self.watch.record(ASIN, VerificationResult("parse_error"), self.now)
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT failure_count FROM watchlist").fetchone()[0], 1)
        self.watch.record(ASIN, VerificationResult("unavailable"), self.now)
        self.assertEqual(self.watch.due(self.now + timedelta(hours=23)), [])
        self.assertEqual(len(self.watch.due(self.now + timedelta(hours=24))), 1)

    def test_budget_skip_does_not_change_schedule(self):
        self.watch.sync_manual([ASIN], self.now)
        before = self.watch.due(self.now)
        self.watch.record(ASIN, VerificationResult("budget_skipped"), self.now)
        self.assertEqual(self.watch.due(self.now), before)

    def test_failed_new_product_is_not_admitted(self):
        self.watch.record(ASIN, VerificationResult("unknown_availability"), self.now)
        self.assertEqual(self.watch.all_asins(), set())

    def test_due_order_fairness_manual_then_history_ties(self):
        for i in range(4):
            self.watch.record(asin(i), VerificationResult("verified", deal(asin=asin(i))), self.now - timedelta(days=1))
        self.watch.sync_manual([asin(1)], self.now)
        self.db.observe(deal(asin=asin(2), now=self.now - timedelta(days=1)))
        with self.db.connection() as conn:
            conn.execute("UPDATE watchlist SET next_check_at=? WHERE asin=?", (timestamp(self.now - timedelta(days=2)), asin(3)))
        self.assertEqual([r["asin"] for r in self.watch.due(self.now)], [asin(3), asin(1), asin(2), asin(0)])

    def test_independent_database_instance_restores_schedule(self):
        self.watch.sync_manual([ASIN], self.now)
        self.watch.record(ASIN, VerificationResult("timeout"), self.now)
        reopened = Watchlist(Database(self.db.path))
        self.assertEqual(reopened.due(self.now), [])
        self.assertEqual(len(reopened.due(self.now + timedelta(hours=6))), 1)


class DiscoveryTests(IsolatedTest):
    def test_bestseller_and_reddit_discovery_do_not_fetch_product_pages(self):
        client = html_client(f'<div class="zg-item-immersion"><a href="/dp/{ASIN}">Item</a></div>')
        result = discover_source("bestsellers", "https://www.amazon.in/gp/bestsellers/", "bestseller", client)
        self.assertEqual(result.status, "successful")
        self.assertEqual(result.candidates[0].asin, ASIN)
        self.assertEqual(client.get.call_count, 1)
        xml = f'<feed xmlns="http://www.w3.org/2005/Atom"><title>Deals</title><entry><title>Offer</title><content type="html">&lt;a href="{URL}"&gt;Deal&lt;/a&gt;</content></entry></feed>'
        client = html_client(xml)
        result = discover_source("reddit", "https://www.reddit.com/r/IndiaDeals/new/.rss", "reddit", client)
        self.assertEqual(result.candidates[0].asin, ASIN)
        self.assertEqual(client.get.call_count, 1)

    def test_empty_blocked_and_parser_failure_are_distinct(self):
        for text, expected in [('<div id="search">No results for your query</div>', "empty"),
                               ('<title>Robot Check</title>', "blocked"),
                               ('<h1>Unexpected layout</h1>', "parse_error")]:
            self.assertEqual(discover_source("search", "https://www.amazon.in/s", "search", html_client(text)).status, expected)
        for status in ("blocked", "timeout", "budget_skipped", "network_error"):
            client = Mock(get=Mock(return_value=HttpResult(status)))
            self.assertEqual(discover_source("search", URL, "search", client).status, status)

    def test_search_candidate_does_not_require_price_or_title(self):
        client = html_client(f'<div data-asin="{ASIN}" data-component-type="s-search-result"></div>')
        result = discover_source("search", "https://www.amazon.in/s", "search", client)
        self.assertEqual(result.candidates[0].asin, ASIN)
        self.assertIsNone(result.candidates[0].metadata["deal_price"])
        verified = verify_candidate(result.candidates[0], html_client(page()))
        self.assertEqual(verified.reason, "verified")
        self.assertIsNotNone(verified.deal.deal_price)

    def test_verification_reason_codes(self):
        item = ProductCandidate(ASIN, URL)
        for html, reason in [(page(asin=asin(1)), "identity_mismatch"),
                             (page(price=""), "invalid_price"),
                             (page(availability="Currently unavailable"), "unavailable"),
                             (page(availability=None), "unknown_availability"),
                             ("<title>Robot Check</title>", "blocked"),
                             ("<div>Unknown layout</div>", "parse_error")]:
            self.assertEqual(verify_candidate(item, html_client(html)).reason, reason)

    def test_http_retry_timeouts_and_budget(self):
        import requests
        clock = [0.0]
        budget = Budget(5, clock=lambda: clock[0], sleeper=lambda seconds: clock.__setitem__(0, clock[0]+seconds))
        session = Mock(request=Mock(side_effect=requests.Timeout("SECRET_NOT_FOR_REPORTS")))
        client = HttpClient(budget, session)
        self.assertEqual(client.get(URL).status, "timeout")
        self.assertEqual(session.request.call_count, 2)
        for call in session.request.call_args_list:
            self.assertLessEqual(sum(call.kwargs["timeout"]), 5)
        clock[0] = 5
        self.assertEqual(client.get(URL).status, "budget_skipped")
        self.assertEqual(session.request.call_count, 2)

    def test_http_budget_expires_during_body(self):
        clock = [0.0]
        budget = Budget(5, clock=lambda: clock[0])
        response = Mock(status_code=200)
        def chunks(size):
            clock[0] = 6
            yield b"body"
        response.iter_content.side_effect = chunks
        self.assertEqual(HttpClient(budget, Mock(request=Mock(return_value=response))).get(URL).status, "budget_skipped")
        response.close.assert_called_once()

    def test_slot_reservations_spillover_and_backoff(self):
        rows = [{"asin": asin(i), "url": candidate(i).url, "title": "Product"} for i in range(25)]
        selected, merged, deferred = select_candidates(rows, [candidate(i) for i in range(25,45)], {asin(i) for i in range(25)})
        self.assertEqual(len(selected), 30)
        self.assertEqual(sum("watchlist" in c.source_ids for c in selected), 20)
        selected, _, _ = select_candidates(rows, [candidate(25)], {asin(i) for i in range(25)})
        self.assertEqual(len(selected), 26)
        selected, _, deferred = select_candidates([], [candidate(1)], {asin(1)})
        self.assertEqual(selected, [])
        self.assertEqual(deferred[asin(1)], "not_due")


class HealthPipelineTests(IsolatedTest):
    def run_pipeline(self, sources, verify, **kwargs):
        with patch("main.discover", return_value=sources), \
                patch("main.verify_candidate", side_effect=verify) as checked, \
                patch("main.post_deal", return_value=42) as send, patch("main.time.sleep"):
            result = main.run_deal_cycle(self.db, lambda: None, **kwargs)
        return result, checked, send

    def test_watched_product_absent_from_discovery_can_post_price_drop(self):
        now = utcnow()
        for day in range(1,8):
            self.db.observe(deal(1000, now=now-timedelta(days=day)))
        watch = Watchlist(self.db)
        watch.record(ASIN, VerificationResult("verified", deal(1000)), now-timedelta(days=1))
        report = CycleReport()
        stats, checked, send = self.run_pipeline([SourceResult("search", "empty")],
            lambda item, client: VerificationResult("verified", deal(900)), report=report)
        self.assertEqual(stats["discovered"], 0)
        self.assertEqual(stats["watchlist_checks"], 1)
        self.assertEqual(stats["posted"], 1)
        send.assert_called_once()
        self.assertEqual(checked.call_count, 2)  # One initial check plus mandatory pre-send refresh.

    def test_dedup_provenance_and_report_has_no_credentials(self):
        Watchlist(self.db).sync_manual([ASIN])
        a = ProductCandidate(ASIN, URL, {"title":"SECRET", "url":"?token=SECRET"}, {"a"})
        b = ProductCandidate(ASIN, URL, {}, {"b"})
        report = CycleReport(True)
        stats, checked, send = self.run_pipeline([SourceResult("a","successful",[a]),SourceResult("b","successful",[b])],
            lambda item,client: VerificationResult("verified",deal(950)), test_mode=True, manual_asins=[ASIN], report=report)
        self.assertEqual(checked.call_count, 1)
        self.assertEqual(stats["discovered"], 1)
        self.assertEqual(stats["verified"], 1)
        self.assertEqual(report.products[ASIN]["source_ids"], ["a","b","watchlist"])
        self.assertNotIn("SECRET", json.dumps(report.data()))
        self.assertEqual(report.data()["reasons"], {"insufficient_mrp_discount":1})

    def test_all_source_errors_fail_but_save_report(self):
        item = candidate()
        output = Path(self.directory.name) / "report"
        with self.assertRaises(main.SourceUnavailable):
            self.run_pipeline([SourceResult("search","successful",[item])],
                              lambda item,client: VerificationResult("blocked"), report_dir=output)
        report = json.loads((output / "report.json").read_text())
        self.assertEqual(report["failure"], "source_unavailable")
        self.assertEqual(report["reasons"], {"blocked":1})
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT status FROM cycle_reports").fetchone()[0], "failed")

    def test_all_discovery_sources_blocked_not_reported_as_empty_success(self):
        with self.assertRaises(main.SourceUnavailable):
            self.run_pipeline([SourceResult("search","blocked"), SourceResult("api","disabled")], Mock())

    def test_unavailable_inventory_is_a_healthy_zero_post_cycle(self):
        stats, _, send = self.run_pipeline([SourceResult("search","successful",[candidate()])],
                                          lambda item,client: VerificationResult("unavailable"))
        self.assertEqual(stats["posted"], 0)
        send.assert_not_called()

    def test_budget_exhaustion_keeps_watch_due_and_reports_skips(self):
        report = CycleReport(True)
        Watchlist(self.db).sync_manual([ASIN])
        stats, checked, send = self.run_pipeline([SourceResult("search","budget_skipped")], Mock(),
            test_mode=True, manual_asins=[ASIN], budget=Budget(0), report=report)
        checked.assert_not_called()
        self.assertEqual(report.data()["reasons"], {"budget_skipped":1})
        self.assertEqual(len(Watchlist(self.db).due()), 1)

    def test_preview_isolates_watchlist_and_reports(self):
        source = self.db
        before = source.path.read_bytes()
        self.db = preview_database(Path(self.directory.name) / "preview.sqlite", source.path)
        self.run_pipeline([SourceResult("search","empty")],
                          lambda item,client: VerificationResult("verified",deal(950)),
                          test_mode=True, manual_asins=[ASIN])
        self.assertEqual(before, source.path.read_bytes())
        self.assertIn(ASIN, Watchlist(self.db).all_asins())

    def test_reports_retained_90_days_and_markdown_written(self):
        report = CycleReport(True)
        report.add_source(SourceResult("api","disabled"))
        directory = Path(self.directory.name) / "output"
        summary = Path(self.directory.name) / "summary.md"
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY":str(summary)}):
            report.write(directory)
        self.assertIn("counts overlap", summary.read_text())
        self.assertTrue((directory/"report.json").exists())
        self.db.save_report(report.data())
        self.db.cleanup(utcnow()+timedelta(days=91))
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cycle_reports").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM source_reports").fetchone()[0], 0)

    def test_only_four_final_refreshes_and_two_sends(self):
        items = [candidate(i) for i in range(8)]
        stats, checked, send = self.run_pipeline([SourceResult("search","successful",items)],
            lambda item,client: VerificationResult("verified",deal(asin=item.asin)))
        self.assertEqual(checked.call_count, 12)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(stats["posted"], 2)

    def test_release_preview_checks_not_due_products_on_temporary_state(self):
        watch = Watchlist(self.db)
        watch.record(ASIN, VerificationResult("verified", deal()))
        before = self.db.path.read_bytes()
        source = self.db
        self.db = preview_database(Path(self.directory.name)/"release-preview.sqlite",source.path)
        stats, checked, send = self.run_pipeline([SourceResult("search","empty")],
            lambda item,client: VerificationResult("verified",deal(950)), test_mode=True,
            force_preview_checks=True)
        self.assertEqual(stats["verified"],1)
        send.assert_not_called()
        self.assertEqual(source.path.read_bytes(),before)

    def test_failed_persistence_still_writes_safe_failure_report(self):
        output = Path(self.directory.name)/"failed"
        with patch("main.discover") as find, patch("main.post_deal") as send:
            with self.assertRaises(RuntimeError):
                main.run_deal_cycle(self.db, Mock(side_effect=RuntimeError("SECRET")),report_dir=output)
        find.assert_not_called()
        send.assert_not_called()
        data = (output/"report.json").read_text()
        self.assertNotIn("SECRET",data)
        self.assertEqual(json.loads(data)["failure"],"state_persistence_failed")


if __name__ == "__main__":
    unittest.main()
