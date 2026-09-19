"""Watch, discover, verify, and report. Preview never sends or saves remote state."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config
from database import Database
from discovery import (Budget, HttpClient, ProductCandidate, SourceResult, VerificationResult,
                       SOURCE_ERRORS, discover, verify_candidate, select_candidates)
from fetcher import Deal
from formatter import format_deal_message
from poster import post_deal, AmbiguousDelivery
from quality import selection_reason, ranking
from reporting import CycleReport
from state_store import GitStateStore, preview_database
from watchlist import Watchlist, load_manual


class SourceUnavailable(RuntimeError):
    pass


def run_deal_cycle(db, save_state, test_mode=False, report_dir=None, manual_asins=(),
                   budget=None, report=None, force_preview_checks=False):
    report = report or CycleReport(test_mode)
    watch = Watchlist(db)
    client = HttpClient(budget or Budget())
    state_writable = True
    error = None

    def persist():
        nonlocal state_writable
        try:
            save_state()
        except Exception:
            state_writable = False
            report.fail("state_persistence_failed")
            raise

    def check(candidate, phase_client):
        if phase_client.budget.remaining() <= 0:
            return VerificationResult("budget_skipped")
        try:
            return verify_candidate(candidate, phase_client)
        except Exception:
            return VerificationResult("parse_error")

    def assess(deal, item):
        reason = selection_reason(deal, db)
        item["eligible"] = reason == "eligible"
        if reason == "eligible":
            reason = db.posting_reason(deal)
        item["selection"] = reason
        return reason == "eligible"

    try:
        watch.sync_manual(manual_asins)
        db.cleanup()
        persist()  # Fail before contacting Telegram if state cannot be saved.
        due = watch.due(force=test_mode and force_preview_checks)
        tracked = watch.all_asins()
        # Discovery cannot spend the whole cycle on blocked sources. Reserve
        # time for watched products, and the last two minutes for final refreshes.
        discovery_budget = Budget(min(300, client.budget.remaining()),
                                  clock=client.budget.clock, sleeper=client.budget.sleeper)
        results = discover(HttpClient(discovery_budget, client.session))
        discoveries = []
        for source in results:
            report.add_source(source)
            discoveries.extend(source.candidates)
        report.add_source(SourceResult("watchlist", "successful" if due else "empty"))
        selected, merged, deferred = select_candidates(due, discoveries, tracked)
        found = {candidate.asin for candidate in discoveries}
        watched = {row["asin"] for row in due}
        for asin, candidate in merged.items():
            item = report.add_candidate(candidate, asin in found, asin in watched)
            item["selection"] = deferred.get(asin)
            # An optional search price is an unverified observation, never a posting input.
            if candidate.metadata.get("deal_price") is not None:
                db.observe(Deal(candidate.metadata.get("title", ""), candidate.url, "Amazon India",
                                deal_price=candidate.metadata["deal_price"]))
        persist()

        qualified = []
        initial_budget = Budget(max(0, client.budget.remaining() - 120),
                                clock=client.budget.clock, sleeper=client.budget.sleeper)
        initial_client = HttpClient(initial_budget, client.session)
        for candidate in selected:
            item = report.products[candidate.asin]
            result = check(candidate, initial_client)
            item["initial"] = result.reason
            watch.record(candidate.asin, result)
            if result.deal is not None:
                db.observe(result.deal)
                if assess(result.deal, item):
                    qualified.append((result.deal, candidate))
        persist()

        attempted = [item["initial"] for item in report.products.values()
                     if item["initial"] not in (None, "budget_skipped")]
        source_statuses = [source.status for source in results if source.status != "disabled"]
        if ((attempted and all(reason in SOURCE_ERRORS for reason in attempted)) or
                (not attempted and source_statuses and any(s in SOURCE_ERRORS for s in source_statuses)
                 and all(s in SOURCE_ERRORS | {"budget_skipped"} for s in source_statuses))):
            raise SourceUnavailable("All attempted verification failed because of source errors")

        ready = []
        for index, (deal, candidate) in enumerate(sorted(qualified, key=lambda pair: ranking(pair[0]))):
            item = report.products[candidate.asin]
            if index >= 4:
                item["selection"] = "posting_limit"
                continue
            result = check(candidate, client)
            item["refresh"] = result.reason
            watch.record(candidate.asin, result)
            if result.deal is not None:
                db.observe(result.deal)
                if assess(result.deal, item):
                    ready.append(result.deal)
            else:
                item["eligible"] = False
                item["selection"] = result.reason
        persist()

        attempts = 0
        for deal in sorted(ready, key=ranking):
            item = report.products[deal.asin]
            if attempts >= min(config.MAX_DEALS_PER_CYCLE, 2):
                item["selection"] = "posting_limit"
                continue
            if not assess(deal, item):
                continue
            caption = format_deal_message(deal)
            if test_mode:
                print("PREVIEW (not posted):\n" + caption)
                item["delivery"] = "preview"
                attempts += 1
                continue
            attempt = db.begin_attempt(deal)
            persist()  # Durable reservation BEFORE sending.
            attempts += 1
            item["attempted"] = True
            item["delivery"] = "pending"
            try:
                message_id = post_deal(deal)
            except AmbiguousDelivery:
                item["selection"] = "pending_delivery"
                continue
            db.finish_attempt(attempt, deal, message_id)
            persist()  # If this fails, remote pending remains and no further send occurs.
            item["delivery"] = "sent" if message_id else "rejected"
            if not message_id:
                item["selection"] = "delivery_rejected"
            time.sleep(3)
    except Exception as exc:
        error = exc
        if report.status != "failed":
            report.fail("source_unavailable" if isinstance(exc, SourceUnavailable) else "cycle_failed")
    finally:
        try:
            report.watchlist_size = len(watch.all_asins())
            if state_writable:
                db.save_report(report.data())
                persist()
        except Exception as exc:
            report.fail("state_persistence_failed")
            error = error or exc
        try:
            report.write(report_dir)
        except Exception as exc:
            error = error or exc
            report.fail("report_write_failed")
        print("CYCLE_RESULT " + json.dumps(report.data()["totals"], sort_keys=True))
    if error:
        raise error
    return report.data()["totals"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", "--preview", action="store_true", dest="preview")
    parser.add_argument("--now", action="store_true", help="Run once (also the default)")
    parser.add_argument("--git-state", action="store_true")
    parser.add_argument("--require-verified", action="store_true")
    parser.add_argument("--report-dir", help="Write report.json and report.md here")
    args = parser.parse_args()
    report = CycleReport(args.preview)
    cycle_started = False
    try:
        if not config.validate_config(production=not args.preview):
            raise ValueError("Invalid production configuration")
        manual = load_manual(config.BASE_DIR / "watchlist.txt")
        if os.getenv("GITHUB_ACTIONS") == "true" and not args.preview:
            if not args.git_state:
                raise RuntimeError("GitHub production requires durable Git state")
            if os.getenv("DEALS_APPROVED_SHA") != os.getenv("GITHUB_SHA"):
                raise RuntimeError("This commit has not passed the production preview gate")
        repo = Path(__file__).resolve().parent.parent
        legacy = [Path(config.DB_PATH), repo / "deals_bot.db", repo / "deals_bot" / "deals_bot.db"]
        with tempfile.TemporaryDirectory(prefix="deals-preview-") as directory:
            db = (preview_database(Path(directory) / "deals_bot.db", config.DB_PATH)
                  if args.preview else Database(config.DB_PATH))
            store = GitStateStore(repo, db) if args.git_state else None
            if store:
                store.restore(legacy)
                report.restore_commit = store.tip
            else:
                db.initialize()
                db.import_legacy(legacy)
            save = (lambda: None) if args.preview or not store else store.save
            cycle_started = True
            stats = run_deal_cycle(db, save, test_mode=args.preview, report_dir=args.report_dir,
                                   manual_asins=manual, report=report,
                                   force_preview_checks=args.require_verified)
            if args.require_verified and stats["verified"] == 0:
                report.fail("no_verified_products")
                report.write(args.report_dir)
                return 1
            return 0
    except Exception as error:
        if not cycle_started:
            report.fail("startup_failed")
            report.write(args.report_dir)
        print(f"Bot stopped: {type(error).__name__}; see the cycle report.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
