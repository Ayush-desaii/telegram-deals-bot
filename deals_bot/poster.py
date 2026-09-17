"""
poster.py - Posts Amazon deals to Telegram channel with watermarked image + metadata
"""
import io
import requests
from typing import Union
from fetcher import Deal
from formatter import format_deal_message, format_text_only_message
from watermark import add_watermark
import config

TELEGRAM_API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"


def send_photo_with_caption(photo: Union[str, io.BytesIO, bytes], caption: str) -> bool:
    """
    Send product photo with deal caption.
    Supports either direct image URL (str) or in-memory watermarked image (io.BytesIO).
    """
    # Telegram caption limit is 1024 characters
    if len(caption) > 1024:
        caption = caption[:1020] + "..."

    url = f"{TELEGRAM_API}/sendPhoto"

    try:
        if isinstance(photo, (io.BytesIO, bytes)):
            # Sending watermarked image buffer as multipart file
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
            resp = requests.post(url, data=data, files=files, timeout=30)
        else:
            # Sending by remote image URL string
            payload = {
                "chat_id": config.CHANNEL_ID,
                "photo": photo,
                "caption": caption,
                "parse_mode": "HTML",
            }
            resp = requests.post(url, json=payload, timeout=20)

        data = resp.json()
        if data.get("ok"):
            return True

        print(f"⚠️  sendPhoto failed: {data.get('description')}")
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
    1. Try sending branded watermarked image with caption
    2. If watermarking fails, fallback to raw Amazon image with caption
    3. If image send fails, fallback to rich text message with link preview
    """
    caption = format_deal_message(deal)

    if deal.image_url:
        # Tier 1: Branded Watermarked Photo
        watermarked_buf = add_watermark(deal.image_url, deal)
        if watermarked_buf:
            success = send_photo_with_caption(watermarked_buf, caption)
            if success:
                print(f"✅ Posted with branded watermark: {deal.title[:55]}...")
                return True
            print("⚠️  Branded photo upload failed, attempting raw image URL fallback...")

        # Tier 2: Raw Image URL Fallback
        success = send_photo_with_caption(deal.image_url, caption)
        if success:
            print(f"✅ Posted with raw image: {deal.title[:55]}...")
            return True

    # Tier 3: Rich Text Fallback
    message = format_text_only_message(deal)
    success = send_message(message)
    if success:
        print(f"✅ Posted as text: {deal.title[:55]}...")
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
