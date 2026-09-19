"""Verified deals pipeline. --test is isolated and never sends or persists state."""
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
from fetcher import fetch_all_deals, filter_deals, verify_deal
from formatter import format_deal_message
from poster import post_deal, AmbiguousDelivery
from quality import eligible, ranking
from state_store import GitStateStore, preview_database


def run_deal_cycle(db, save_state, test_mode=False):
    stats = dict(discovered=0, valid=0, verified=0, qualified=0, attempted=0, posted=0, pending=0)
    discovered = fetch_all_deals()
    stats["discovered"] = len(discovered)
    candidates = filter_deals(discovered)
    stats["valid"] = len(candidates)
    for deal in candidates:
        db.observe(deal)
    db.cleanup()
    save_state()

    verified = []
    seen = set()
    for deal in candidates:
        if deal.asin in seen:
            continue
        seen.add(deal.asin)
        fresh = verify_deal(deal)
        if fresh is None:
            continue
        stats["verified"] += 1
        db.observe(fresh)
        if eligible(fresh, db) and db.can_post(fresh):
            verified.append(fresh)
    save_state()
    stats["qualified"] = len(verified)

    # Revisit each qualifying candidate after collection; prices may have moved
    # during a long discovery run. Rank using only these refreshed snapshots.
    ready = []
    for deal in verified:
        fresh = verify_deal(deal)
        if fresh is not None:
            db.observe(fresh)
            if eligible(fresh, db) and db.can_post(fresh):
                ready.append(fresh)
    save_state()
    for deal in sorted(ready, key=ranking):
        if max(stats["posted"], stats["attempted"]) >= min(config.MAX_DEALS_PER_CYCLE, 2):
            break
        # Snapshots expire after five minutes; do not post stale queued products.
        if not eligible(deal, db) or not db.can_post(deal):
            continue
        caption = format_deal_message(deal)
        if test_mode:
            print("PREVIEW (not posted):\n" + caption)
            stats["posted"] += 1
            continue
        attempt = db.begin_attempt(deal)
        save_state()  # Durable reservation is mandatory BEFORE the network send.
        stats["attempted"] += 1
        try:
            message_id = post_deal(deal)
        except AmbiguousDelivery:
            stats["pending"] += 1
            print(f"Delivery uncertain for {deal.asin}; pending attempt retained for review")
            continue
        db.finish_attempt(attempt, deal, message_id)
        save_state()  # Failure here stops further sends; remote pending is safe.
        if message_id:
            stats["posted"] += 1
        time.sleep(3)
    if test_mode:
        stats["previewed"] = stats["posted"]
        stats["posted"] = 0
    print("CYCLE_RESULT " + json.dumps(stats, sort_keys=True))
    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", "--preview", action="store_true", dest="preview")
    parser.add_argument("--now", action="store_true", help="Run once (also the default)")
    parser.add_argument("--git-state", action="store_true", help="Restore/save the repository state branch")
    parser.add_argument("--require-verified", action="store_true", help="Fail a preview if no product can be verified")
    args = parser.parse_args()
    if not config.validate_config(production=not args.preview):
        return 1
    if os.getenv("GITHUB_ACTIONS") == "true" and not args.preview:
        if not args.git_state:
            raise RuntimeError("GitHub production requires durable Git state")
        if os.getenv("DEALS_APPROVED_SHA") != os.getenv("GITHUB_SHA"):
            raise RuntimeError("This commit has not passed the production preview gate")
    repo = Path(__file__).resolve().parent.parent
    legacy = [Path(config.DB_PATH), repo / "deals_bot.db", repo / "deals_bot" / "deals_bot.db"]
    with tempfile.TemporaryDirectory(prefix="deals-preview-") as directory:
        if args.preview:
            db = preview_database(Path(directory) / "deals_bot.db", config.DB_PATH)
        else:
            db = Database(config.DB_PATH)
        store = GitStateStore(repo, db) if args.git_state else None
        if store:
            store.restore(legacy)
        else:
            db.initialize()
            db.import_legacy(legacy)
        save = (lambda: None) if args.preview or not store else store.save
        save()  # Check durable writes before fetching or sending.
        stats = run_deal_cycle(db, save, test_mode=args.preview)
        if args.require_verified and stats["verified"] == 0:
            print("Preview failed: no product passed live verification")
            return 1
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # Request exceptions may include bot tokens in URLs: log type only.
        print(f"Bot stopped: {type(error).__name__}. Check state, source availability, and configuration.")
        sys.exit(1)
