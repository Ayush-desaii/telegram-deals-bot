"""
poster.py - Posts Amazon deals to Telegram channel with product image + metadata
"""
import requests
from fetcher import Deal
from formatter import format_deal_message, format_text_only_message
import config

TELEGRAM_API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"


def send_photo_with_caption(image_url: str, caption: str) -> bool:
    """Send product photo with deal caption."""
    # Telegram caption limit is 1024 characters
    if len(caption) > 1024:
        caption = caption[:1020] + "..."

    url = f"{TELEGRAM_API}/sendPhoto"
    payload = {
        "chat_id": config.CHANNEL_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=20)
        data = resp.json()
        if data.get("ok"):
            return True
        print(f"⚠️  sendPhoto failed: {data.get('description')} — trying text only")
        return False
    except Exception as e:
        print(f"❌ sendPhoto error: {e}")
        return False


def send_message(text: str) -> bool:
    """Send a text-only message."""
    if len(text) > 4096:
        text = text[:4090] + "..."

    url = f"{TELEGRAM_API}/sendMessage"
    payload = {
        "chat_id": config.CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        if data.get("ok"):
            return True
        print(f"⚠️  sendMessage failed: {data.get('description')}")
        return False
    except Exception as e:
        print(f"❌ sendMessage error: {e}")
        return False


def post_deal(deal: Deal) -> bool:
    """
    Post a deal to the channel.
    - If image available: send photo + caption (shows product image)
    - Fallback: send rich text message with link preview (shows Amazon card)
    """
    if deal.image_url:
        caption = format_deal_message(deal)
        success = send_photo_with_caption(deal.image_url, caption)
        if success:
            print(f"✅ Posted with image: {deal.title[:60]}...")
            return True

    # Fallback to text (link preview will show Amazon product card)
    message = format_text_only_message(deal)
    success = send_message(message)
    if success:
        print(f"✅ Posted as text: {deal.title[:60]}...")
    return success


def post_startup_message() -> None:
    msg = (
        "🤖 <b>Amazon Deals Bot Started!</b>\n\n"
        "I'll be fetching the best Amazon India deals automatically.\n"
        "All links include affiliate tracking — every purchase earns! 💰\n\n"
        "#AmazonIndia #LootDeals #Offers"
    )
    send_message(msg)


def test_connection() -> bool:
    url = f"{TELEGRAM_API}/getMe"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get("ok"):
            bot_name = data["result"]["username"]
            print(f"✅ Bot connected: @{bot_name}")
            return True
        print(f"❌ Bot token invalid: {data.get('description')}")
        return False
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False
