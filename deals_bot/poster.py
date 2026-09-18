"""
poster.py - Posts Amazon deals to Telegram channel with watermarked image, metadata, and inline buttons
"""
import io
import json
import requests
from typing import Union, Optional
from urllib.parse import quote
from fetcher import Deal
from formatter import format_deal_message, format_text_only_message
from watermark import add_watermark
import config

TELEGRAM_API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"


def build_deal_keyboard(deal: Deal) -> dict:
    """
    Builds attractive Telegram inline keyboard buttons:
    1. Direct 'BUY NOW ON AMAZON' button with affiliate link
    2. 1-Click 'SHARE DEAL' button to trigger Telegram's native share sheet
    """
    ch = getattr(config, "CHANNEL_ID", "")
    handle = ch.lstrip("@") if ch and ch.startswith("@") else "ApexLootDealss"
    channel_link = f"https://t.me/{handle}"

    disc_str = f"🔥 {deal.discount_percent}% OFF" if deal.discount_percent else "🔥 LOOT DEAL"
    price_str = f"₹{int(deal.deal_price):,}" if deal.deal_price else ""
    price_info = f" at {price_str}" if price_str else ""
    share_text = f"{disc_str}! {deal.title[:60]}...{price_info}\n\n👉 Join @{handle} for more daily loot deals!"

    share_url = f"https://t.me/share/url?url={quote(channel_link, safe='')}&text={quote(share_text, safe='')}"

    return {
        "inline_keyboard": [
            [
                {"text": "🛒 BUY NOW ON AMAZON →", "url": deal.url}
            ],
            [
                {"text": "📢 SHARE DEAL WITH FRIENDS", "url": share_url}
            ]
        ]
    }


def send_photo_with_caption(
    photo: Union[str, io.BytesIO, bytes],
    caption: str,
    reply_markup: Optional[dict] = None
) -> bool:
    """
    Send product photo with deal caption and optional inline buttons.
    Supports either direct image URL (str) or in-memory watermarked image (io.BytesIO).
    """
    if len(caption) > 1024:
        caption = caption[:1020] + "..."

    url = f"{TELEGRAM_API}/sendPhoto"

    try:
        if isinstance(photo, (io.BytesIO, bytes)):
            if isinstance(photo, io.BytesIO):
                photo.seek(0)
                file_bytes = photo.read()
            else:
                file_bytes = photo

            files = {"photo": ("deal.jpg", file_bytes, "image/jpeg")}
            data = {
                "chat_id": config.CHANNEL_ID,
                "caption": caption,
                "parse_mode": "HTML",
            }
            if reply_markup:
                data["reply_markup"] = json.dumps(reply_markup)

            resp = requests.post(url, data=data, files=files, timeout=30)
        else:
            payload = {
                "chat_id": config.CHANNEL_ID,
                "photo": photo,
                "caption": caption,
                "parse_mode": "HTML",
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup

            resp = requests.post(url, json=payload, timeout=20)

        data = resp.json()
        if data.get("ok"):
            return True

        print(f"⚠️  sendPhoto failed: {data.get('description')}")
        return False

    except Exception as e:
        print(f"❌ sendPhoto error: {e}")
        return False


def send_message(text: str, reply_markup: Optional[dict] = None) -> bool:
    """Send a text-only message with optional inline buttons."""
    if len(text) > 4096:
        text = text[:4090] + "..."

    url = f"{TELEGRAM_API}/sendMessage"
    payload = {
        "chat_id": config.CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

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
    Post a deal to the channel with branded image and inline action buttons.
    1. Try sending branded watermarked image with buttons
    2. If watermarking fails, fallback to raw Amazon image with buttons
    3. If image send fails, fallback to rich text message with buttons
    """
    caption = format_deal_message(deal)
    keyboard = build_deal_keyboard(deal)

    if deal.image_url:
        # Tier 1: Branded Watermarked Photo
        watermarked_buf = add_watermark(deal.image_url, deal)
        if watermarked_buf:
            success = send_photo_with_caption(watermarked_buf, caption, reply_markup=keyboard)
            if success:
                print(f"✅ Posted with branded watermark & buttons: {deal.title[:55]}...")
                return True
            print("⚠️  Branded photo upload failed, attempting raw image URL fallback...")

        # Tier 2: Raw Image URL Fallback
        success = send_photo_with_caption(deal.image_url, caption, reply_markup=keyboard)
        if success:
            print(f"✅ Posted with raw image & buttons: {deal.title[:55]}...")
            return True

    # Tier 3: Rich Text Fallback
    message = format_text_only_message(deal)
    success = send_message(message, reply_markup=keyboard)
    if success:
        print(f"✅ Posted as text & buttons: {deal.title[:55]}...")
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
