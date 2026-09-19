"""
config.py - Loads all settings from .env file
"""
import os
import re
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ── Telegram ──────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID: str = os.getenv("TELEGRAM_CHANNEL_ID", "")

# ── Affiliate: Amazon ─────────────────────────────────────
AMAZON_AFFILIATE_TAG: str = os.getenv("AMAZON_AFFILIATE_TAG", "")

# ── Affiliate: Multi-Store via EarnKaro (Flipkart, Myntra, Ajio) ──
# Sign up free: https://earnkaro.com/
# Use your Referral code/ID from your profile (e.g. 1234567)
EARNKARO_USER_ID: str = os.getenv("EARNKARO_USER_ID", "")

# ── Amazon PA API (optional but recommended for best results) ──
# Sign up: https://affiliate-program.amazon.in/assoc_credentials/home
# Required: Must have made 3+ qualifying sales in Amazon Associates first
AMAZON_PA_API_KEY: str = os.getenv("AMAZON_PA_API_KEY", "")
AMAZON_PA_API_SECRET: str = os.getenv("AMAZON_PA_API_SECRET", "")

# ── Scheduler ─────────────────────────────────────────────
FETCH_INTERVAL_MINUTES: int = int(os.getenv("FETCH_INTERVAL_MINUTES", "60"))

# ── Deal Filters ──────────────────────────────────────────
MIN_DISCOUNT_PERCENT: int = int(os.getenv("MIN_DISCOUNT_PERCENT", "40"))
MAX_DEALS_PER_CYCLE: int = int(os.getenv("MAX_DEALS_PER_CYCLE", "2"))

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
DB_PATH: str = str(Path(os.getenv("DB_PATH", str(BASE_DIR / "deals_bot.db"))).resolve())
STATE_BRANCH = "codex/deals-state"

# ── Validation ────────────────────────────────────────────
def validate_config(production: bool = True) -> bool:
    """Check all required config values are set."""
    errors = []
    if production and (not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here"):
        errors.append("❌ TELEGRAM_BOT_TOKEN is not set in .env")
    if production and (not CHANNEL_ID or CHANNEL_ID == "@YourChannelUsername"):
        errors.append("❌ TELEGRAM_CHANNEL_ID is not set in .env")
    if production and (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", AMAZON_AFFILIATE_TAG) or
                       AMAZON_AFFILIATE_TAG.lower().startswith(("your", "placeholder"))):
        errors.append("AMAZON_AFFILIATE_TAG must be configured before posting")
    if MIN_DISCOUNT_PERCENT < 40 or MIN_DISCOUNT_PERCENT > 99 or MAX_DEALS_PER_CYCLE < 1:
        errors.append("Require MIN_DISCOUNT_PERCENT=40..99 and MAX_DEALS_PER_CYCLE >= 1")

    if errors:
        for e in errors:
            print(e)
        return False
    return True
