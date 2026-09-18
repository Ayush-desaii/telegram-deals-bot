"""
main.py - Entry point. Runs the deal bot with automatic scheduling.

Usage:
    python main.py          -> Start the bot (runs forever)
    python main.py --test   -> Fetch & preview 3 deals WITHOUT posting
    python main.py --now    -> Fetch & post deals once immediately, then exit
"""
import sys
import time
import random
from datetime import datetime

# Fix Windows terminal encoding for emoji support
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

import config
from config import validate_config
from database import init_db, is_already_posted, mark_as_posted, get_total_posted, cleanup_old_records
from fetcher import fetch_all_deals, filter_deals, Deal
from formatter import format_deal_message
from poster import post_deal, post_startup_message, test_connection, pin_deal_message


# ── Core Job ──────────────────────────────────────────────────────────────────

def run_deal_cycle(test_mode: bool = False) -> None:
    """
    Main cycle:
    1. Fetch deals from all sources
    2. Filter by discount threshold
    3. Skip already-posted deals
    4. Post top N new deals ranked by score
    5. Auto-pin the hottest deal of the day
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*50}")
    print(f"⏰ Cycle started at {now}")
    print(f"{'='*50}")

    # Step 1: Fetch
    all_deals = fetch_all_deals()
    print(f"\n📦 Total deals fetched: {len(all_deals)}")

    # Step 2: Filter by discount %
    filtered = filter_deals(all_deals)
    print(f"🔍 After discount filter (≥{config.MIN_DISCOUNT_PERCENT}%): {len(filtered)} deals")

    if not filtered:
        print("⚠️  No qualifying deals found this cycle.")
        return

    # Step 3: Remove already-posted deals
    new_deals = [d for d in filtered if not is_already_posted(d.url)]
    print(f"🆕 New (not yet posted): {len(new_deals)} deals")

    if not new_deals:
        print("ℹ️  All qualifying deals already posted. Nothing new to post.")
        return

    # Step 4: Sort by deal_score (best discount, rating, & price first)
    new_deals.sort(key=lambda d: getattr(d, "deal_score", 0), reverse=True)
    to_post: list[Deal] = new_deals[:config.MAX_DEALS_PER_CYCLE]

    # Step 5: Post (or preview in test mode)
    posted_count = 0
    top_deal_pinned = False

    for i, deal in enumerate(to_post):
        if test_mode:
            print("\n" + "─" * 50)
            print("📋 PREVIEW (not posting):")
            print(format_deal_message(deal))
            print(f"   🔗 URL: {deal.url}")
            print(f"   🖼️  Image: {deal.image_url or 'None'}")
        else:
            msg_id = post_deal(deal)
            if msg_id:
                mark_as_posted(deal.url, deal.title, deal.source)
                posted_count += 1

                # Auto-pin the single hottest deal of the cycle (>=60% off)
                if not top_deal_pinned and deal.discount_percent and deal.discount_percent >= 60:
                    pin_deal_message(msg_id)
                    top_deal_pinned = True

                # Small delay between posts to avoid flooding
                time.sleep(3)

    if not test_mode:
        total = get_total_posted()
        print(f"\n✅ Posted {posted_count} deal(s) | Total ever posted: {total}")


# ── Startup ───────────────────────────────────────────────────────────────────

def startup() -> None:
    print("\n" + "🛍️ " * 15)
    print("   LOOT DEALS BOT - Starting Up")
    print("🛍️ " * 15 + "\n")

    # Validate config
    if not validate_config():
        print("\n❌ Please fix your .env file and restart.")
        sys.exit(1)

    # Test Telegram connection
    if not test_connection():
        print("\n❌ Cannot connect to Telegram. Check your BOT_TOKEN.")
        sys.exit(1)

    # Initialize database
    init_db()

    # Cleanup old DB records (keep 14 days of history)
    cleanup_old_records(days=14)

    print(f"\n⚙️  Settings:")
    print(f"   Channel     : {config.CHANNEL_ID}")
    print(f"   Interval    : Every {config.FETCH_INTERVAL_MINUTES} minute(s)")
    print(f"   Min Discount: {config.MIN_DISCOUNT_PERCENT}%")
    print(f"   Max Per Cycle: {config.MAX_DEALS_PER_CYCLE} deals")
    print(f"   DB Path     : {config.DB_PATH}")
    print(f"   Total Posted: {get_total_posted()} deals so far")


# ── Main Entry ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]

    # ── Test mode: preview deals without posting ──────────
    if "--test" in args:
        print("🧪 TEST MODE — No messages will be sent to Telegram\n")
        validate_config()
        init_db()
        run_deal_cycle(test_mode=True)
        sys.exit(0)

    # ── One-shot mode: post now and exit ──────────────────
    if "--now" in args:
        startup()
        run_deal_cycle(test_mode=False)
        print("\n✅ One-shot run complete. Exiting.")
        sys.exit(0)

    # ── Normal mode: run forever on schedule ──────────────
    startup()

    # Run once immediately
    print("\n🚀 Running first cycle now...")
    run_deal_cycle()

    # Schedule recurring runs
    scheduler = BlockingScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        func=run_deal_cycle,
        trigger=IntervalTrigger(minutes=config.FETCH_INTERVAL_MINUTES),
        id="deals_job",
        name="Fetch & Post Deals",
        replace_existing=True,
    )

    print(f"\n⏰ Scheduler started! Next run in {config.FETCH_INTERVAL_MINUTES} minute(s).")
    print("   Press Ctrl+C to stop.\n")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\n\n👋 Bot stopped by user. Goodbye!")
