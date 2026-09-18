"""
watermark.py - Adds professional branding and discount badges to deal images.
Makes every forwarded deal on WhatsApp & Telegram a billboard for your channel.
"""
import io
import re
import requests
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

import config


def get_channel_handle() -> str:
    """Returns the channel username without @ (e.g. 'ApexLootDealss')."""
    ch = getattr(config, "CHANNEL_ID", "")
    if ch and ch.startswith("@"):
        return ch.lstrip("@")
    return "ApexLootDealss"


def get_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
    """Safely loads scalable fonts across Windows and Linux."""
    font_names = (
        ["arialbd.ttf", "DejaVuSans-Bold.ttf", "arial.ttf", "DejaVuSans.ttf"]
        if bold
        else ["arial.ttf", "DejaVuSans.ttf"]
    )
    for name in font_names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default(size=size)


def add_watermark(image_url: str, deal) -> Optional[io.BytesIO]:
    """
    Downloads deal image, overlays branded top badge and bottom channel banner,
    and returns JPEG buffer in memory. Returns None if any step fails.
    """
    if not image_url:
        return None

    try:
        resp = requests.get(
            image_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        if resp.status_code != 200 or not resp.content:
            return None

        base = Image.open(io.BytesIO(resp.content)).convert("RGBA")
    except Exception as e:
        print(f"⚠️  Could not download image for watermarking: {e}")
        return None

    try:
        w, h = base.size

        # Minimum size check
        if w < 200 or h < 200:
            # Scale up small images for better banner rendering
            scale = max(400 / w, 400 / h)
            w, h = int(w * scale), int(h * scale)
            base = base.resize((w, h), Image.Resampling.LANCZOS)

        overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        handle = get_channel_handle()

        # ── 1. Top-Left Badge (Under ₹99/₹199, Discount %, or Loot) ───────
        disc = getattr(deal, "discount_percent", None)
        price = getattr(deal, "deal_price", None)

        if price and price <= 99:
            badge_text = " UNDER ₹99 LOOT "
            badge_bg = (124, 58, 237, 245)    # Purple Royal Loot
        elif price and price <= 199:
            badge_text = " UNDER ₹199 LOOT "
            badge_bg = (217, 119, 6, 245)     # Amber / Gold Loot
        elif disc and disc >= 10:
            badge_text = f" {disc}% OFF "
            badge_bg = (229, 9, 20, 245)      # Vibrant Red
        else:
            badge_text = " LOOT DEAL "
            badge_bg = (255, 87, 34, 245)     # Deep Orange

        badge_font_size = max(14, int(h * 0.045))
        badge_font = get_font(badge_font_size, bold=True)

        bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        pad_x = int(w * 0.025)
        pad_y = int(h * 0.012)
        b_x0 = int(w * 0.03)
        b_y0 = int(h * 0.03)
        b_x1 = b_x0 + text_w + pad_x * 2
        b_y1 = b_y0 + text_h + pad_y * 2
        radius = max(4, int((b_y1 - b_y0) * 0.3))

        draw.rounded_rectangle([(b_x0, b_y0), (b_x1, b_y1)], radius=radius, fill=badge_bg)
        draw.text((b_x0 + pad_x, b_y0 + pad_y), badge_text, fill=(255, 255, 255, 255), font=badge_font)

        # ── 2. Bottom Channel Branding Bar ─────────────────────────────────
        banner_h = max(38, int(h * 0.11))
        banner_top = h - banner_h

        # Dark sleek background
        draw.rectangle([(0, banner_top), (w, h)], fill=(15, 23, 42, 240))  # Slate dark #0F172A

        # Accent top border (Amazon Gold #FF9900)
        border_w = max(2, int(h * 0.006))
        draw.line([(0, banner_top), (w, banner_top)], fill=(255, 153, 0, 255), width=border_w)

        main_font_size = max(12, int(banner_h * 0.38))
        sub_font_size = max(10, int(banner_h * 0.30))

        main_font = get_font(main_font_size, bold=True)
        sub_font = get_font(sub_font_size, bold=False)

        left_text = f"JOIN @{handle}"
        right_text = f"t.me/{handle}"

        # Left Text: Channel Handle
        m_bbox = draw.textbbox((0, 0), left_text, font=main_font)
        m_th = m_bbox[3] - m_bbox[1]
        m_y = banner_top + (banner_h - m_th) // 2
        draw.text((int(w * 0.04), m_y), left_text, fill=(255, 255, 255, 255), font=main_font)

        # Right Text: Link
        s_bbox = draw.textbbox((0, 0), right_text, font=sub_font)
        s_tw = s_bbox[2] - s_bbox[0]
        s_th = s_bbox[3] - s_bbox[1]
        s_x = w - s_tw - int(w * 0.04)
        s_y = banner_top + (banner_h - s_th) // 2

        # Only draw right text if there's enough room
        if s_x > (int(w * 0.04) + m_bbox[2] - m_bbox[0] + 15):
            draw.text((s_x, s_y), right_text, fill=(255, 180, 0, 255), font=sub_font)

        # Composite & return JPEG
        result = Image.alpha_composite(base, overlay).convert("RGB")
        buf = io.BytesIO()
        result.save(buf, format="JPEG", quality=92)
        buf.seek(0)
        return buf

    except Exception as e:
        print(f"⚠️  Failed to apply watermark overlay: {e}")
        return None
