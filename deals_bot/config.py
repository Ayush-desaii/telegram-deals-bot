"""
config.py - Loads all settings from .env file
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID: str = os.getenv("TELEGRAM_CHANNEL_ID", "")

# ── Affiliate ─────────────────────────────────────────────
AMAZON_AFFILIATE_TAG: str = os.getenv("AMAZON_AFFILIATE_TAG", "")

# ── Amazon PA API (optional but recommended for best results) ──
# Sign up: https://affiliate-program.amazon.in/assoc_credentials/home
# Required: Must have made 3+ qualifying sales in Amazon Associates first
AMAZON_PA_API_KEY: str = os.getenv("AMAZON_PA_API_KEY", "")
AMAZON_PA_API_SECRET: str = os.getenv("AMAZON_PA_API_SECRET", "")

# ── Scheduler ─────────────────────────────────────────────
FETCH_INTERVAL_MINUTES: int = int(os.getenv("FETCH_INTERVAL_MINUTES", "60"))

# ── Deal Filters ──────────────────────────────────────────
MIN_DISCOUNT_PERCENT: int = int(os.getenv("MIN_DISCOUNT_PERCENT", "40"))
MAX_DEALS_PER_CYCLE: int = int(os.getenv("MAX_DEALS_PER_CYCLE", "3"))

# ── Deal Sources (RSS Feeds) ───────────────────────────────
# Reddit subreddits — /new/ gives freshest posts with active Amazon links
RSS_FEEDS = [
    {
        "name": "r/IndiaDeals",
        "url": "https://www.reddit.com/r/IndiaDeals/new/.rss",
        "source": "reddit",
    },
    {
        "name": "r/IndiaDealsExchange",
        "url": "https://www.reddit.com/r/IndiaDealsExchange/new/.rss",
        "source": "reddit",
    },
    {
        "name": "r/indianfrugalliving",
        "url": "https://www.reddit.com/r/indianfrugalliving/new/.rss",
        "source": "reddit",
    },
    {
        "name": "r/India_eCommerce",
        "url": "https://www.reddit.com/r/India_eCommerce/new/.rss",
        "source": "reddit",
    },
]

# ── Database ──────────────────────────────────────────────
DB_PATH: str = "deals_bot.db"

# ── Validation ────────────────────────────────────────────
def validate_config() -> bool:
    """Check all required config values are set."""
    errors = []
    if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
        errors.append("❌ TELEGRAM_BOT_TOKEN is not set in .env")
    if not CHANNEL_ID or CHANNEL_ID == "@YourChannelUsername":
        errors.append("❌ TELEGRAM_CHANNEL_ID is not set in .env")
    if not AMAZON_AFFILIATE_TAG or AMAZON_AFFILIATE_TAG == "yourtag-21":
        print("⚠️  AMAZON_AFFILIATE_TAG not set — affiliate links will be plain links")

    if errors:
        for e in errors:
            print(e)
        return False
    return True
