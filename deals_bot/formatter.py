"""
formatter.py - Formats Amazon deal data into beautiful Telegram messages
"""
from fetcher import Deal


# ── Category Emoji Map ─────────────────────────────────────────────────────────

CATEGORY_EMOJI = {
    "electronics":    "📱",
    "mobile":         "📱",
    "smartphone":     "📱",
    "laptop":         "💻",
    "computer":       "💻",
    "camera":         "📷",
    "television":     "📺",
    "tv":             "📺",
    "headphone":      "🎧",
    "earphone":       "🎧",
    "speaker":        "🔊",
    "fashion":        "👗",
    "clothing":       "👕",
    "shirt":          "👕",
    "shoes":          "👟",
    "watch":          "⌚",
    "kitchen":        "🍳",
    "home":           "🏠",
    "appliance":      "🔌",
    "grocery":        "🛒",
    "food":           "🍔",
    "beauty":         "💄",
    "health":         "💊",
    "fitness":        "💪",
    "sports":         "⚽",
    "toys":           "🧸",
    "books":          "📚",
    "gaming":         "🎮",
    "tablet":         "📟",
    "charger":        "🔋",
    "bag":            "👜",
    "backpack":       "🎒",
    "refrigerator":   "❄️",
    "washing":        "🫧",
    "air conditioner": "❄️",
    "microwave":      "📡",
}

DEFAULT_EMOJI = "🔥"


def get_category_emoji(deal: Deal) -> str:
    search_text = " ".join([
        deal.title or "",
        deal.category or "",
    ]).lower()
    for keyword, emoji in CATEGORY_EMOJI.items():
        if keyword in search_text:
            return emoji
    return DEFAULT_EMOJI


def format_price(price: float) -> str:
    return f"₹{int(price):,}"


def format_deal_message(deal: Deal) -> str:
    """
    Format a Deal into a rich Telegram HTML message.
    Designed to be used as photo caption (max ~1024 chars for caption).
    """
    emoji = get_category_emoji(deal)
    lines = []

    # ── Header ──────────────────────────────────────
    lines.append(f"{emoji} <b>LOOT DEAL ALERT!</b> {emoji}")
    lines.append("")

    # ── Title ───────────────────────────────────────
    title = (deal.title or "Hot Amazon Deal")[:120]
    lines.append(f"📦 <b>{title}</b>")
    lines.append("")

    # ── Price Info ──────────────────────────────────────
    if deal.deal_price:
        lines.append(f"💰 <b>Price: {format_price(deal.deal_price)}</b>")
    if deal.original_price and deal.original_price != deal.deal_price:
        lines.append(f"🏷️ MRP: <s>{format_price(deal.original_price)}</s>")
    if deal.discount_percent:
        lines.append(f"📉 <b>{deal.discount_percent}% OFF</b> 🔥")
    if deal.deal_price and deal.original_price and deal.original_price > deal.deal_price:
        you_save = deal.original_price - deal.deal_price
        lines.append(f"💸 You Save: {format_price(you_save)}")
    if not deal.discount_percent and not deal.original_price:
        lines.append("🏆 <b>Amazon Best Seller</b>")

    lines.append("")

    # ── Rating ──────────────────────────────────────
    if deal.rating:
        try:
            star_count = min(round(float(deal.rating)), 5)
            stars = "⭐" * star_count
            lines.append(f"{stars} {deal.rating}/5")
            lines.append("")
        except Exception:
            pass  # Skip rating if it can't be parsed

    # ── Category ────────────────────────────────────
    if deal.category:
        lines.append(f"📂 {deal.category}")
        lines.append("")

    # ── CTA ─────────────────────────────────────────
    lines.append(f'🛒 <a href="{deal.url}"><b>BUY NOW ON AMAZON →</b></a>')
    lines.append("")

    # ── Hashtags ────────────────────────────────────
    lines.append(build_hashtags(deal))

    return "\n".join(lines)


def format_text_only_message(deal: Deal) -> str:
    """
    Longer version for text-only messages (no image).
    Shows more details since there's no photo taking up space.
    """
    emoji = get_category_emoji(deal)
    lines = []

    lines.append(f"{'─' * 28}")
    lines.append(f"{emoji} <b>LOOT DEAL ALERT!</b> {emoji}")
    lines.append(f"{'─' * 28}")
    lines.append("")

    title = (deal.title or "Hot Amazon Deal")[:150]
    lines.append(f"📦 <b>{title}</b>")
    lines.append("")

    if deal.deal_price:
        lines.append(f"💰 <b>Deal Price: {format_price(deal.deal_price)}</b>")
    if deal.original_price and deal.original_price != deal.deal_price:
        lines.append(f"🏷️ MRP: <s>{format_price(deal.original_price)}</s>")
    if deal.discount_percent:
        lines.append(f"📉 <b>Discount: {deal.discount_percent}% OFF</b> 🔥")
    if deal.deal_price and deal.original_price and deal.original_price > deal.deal_price:
        you_save = deal.original_price - deal.deal_price
        lines.append(f"💸 You Save: {format_price(you_save)}")
    if not deal.discount_percent and not deal.original_price:
        lines.append("🏆 <b>Amazon Best Seller</b>")

    lines.append("")

    if deal.rating:
        try:
            star_count = min(round(float(deal.rating)), 5)
            stars = "⭐" * star_count
            lines.append(f"Rating: {stars} {deal.rating}/5")
            lines.append("")
        except Exception:
            pass

    if deal.category:
        lines.append(f"📂 Category: {deal.category}")
        lines.append("")

    lines.append(f'🛒 <a href="{deal.url}"><b>BUY NOW ON AMAZON →</b></a>')
    lines.append("")
    lines.append(build_hashtags(deal))

    return "\n".join(lines)


def build_hashtags(deal: Deal) -> str:
    tags = ["#LootDeal", "#AmazonIndia", "#Deal"]

    if deal.discount_percent and deal.discount_percent >= 70:
        tags.append("#SuperLoot")
    elif deal.discount_percent and deal.discount_percent >= 50:
        tags.append("#BigOff")

    text = (deal.title or "").lower()
    if any(w in text for w in ["phone", "mobile", "samsung", "realme", "redmi", "poco", "oneplus"]):
        tags.append("#Smartphones")
    elif any(w in text for w in ["laptop", "notebook", "macbook"]):
        tags.append("#Laptops")
    elif any(w in text for w in ["headphone", "earphone", "boat", "jbl", "sony", "bose"]):
        tags.append("#AudioDeals")
    elif any(w in text for w in ["tv", "television", "led"]):
        tags.append("#TVDeals")
    elif any(w in text for w in ["shoe", "sneaker", "nike", "adidas"]):
        tags.append("#Fashion")
    elif any(w in text for w in ["kitchen", "cookware", "pressure cooker"]):
        tags.append("#Kitchen")

    return " ".join(tags[:5])
