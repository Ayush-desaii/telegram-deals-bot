"""
formatter.py - Enhanced deal message with discount as the hero element
"""
from fetcher import Deal


# ── Category Emoji Map ─────────────────────────────────────────────────────────

CATEGORY_EMOJI = {
    "electronics": "📱", "mobile": "📱", "smartphone": "📱", "phone": "📱",
    "laptop": "💻", "computer": "💻", "notebook": "💻",
    "camera": "📷", "dslr": "📷",
    "television": "📺", "tv": "📺", "monitor": "🖥️",
    "headphone": "🎧", "earphone": "🎧", "earbud": "🎧",
    "speaker": "🔊", "bluetooth": "🔊",
    "fashion": "👗", "clothing": "👕", "shirt": "👕", "dress": "👗",
    "shoes": "👟", "sneaker": "👟", "sandal": "👡",
    "watch": "⌚", "smartwatch": "⌚",
    "kitchen": "🍳", "cookware": "🍳", "pressure": "🍳",
    "home": "🏠", "furniture": "🪑",
    "appliance": "🔌", "refrigerator": "❄️", "washing": "🫧", "ac": "❄️",
    "beauty": "💄", "skincare": "🧴", "makeup": "💄",
    "health": "💊", "fitness": "💪", "gym": "💪",
    "sports": "⚽", "cricket": "🏏", "cycling": "🚴",
    "toys": "🧸", "kids": "🧒",
    "books": "📚", "stationery": "✏️",
    "gaming": "🎮", "xbox": "🎮", "playstation": "🎮",
    "tablet": "📟", "ipad": "📟",
    "charger": "🔋", "powerbank": "🔋", "cable": "🔌",
    "bag": "👜", "backpack": "🎒", "luggage": "🧳",
    "microwave": "📡", "oven": "🍕",
}

DEFAULT_EMOJI = "🔥"
FIRE_THRESHOLDS = {70: "🔥🔥🔥", 50: "🔥🔥", 30: "🔥"}


def get_emoji(deal: Deal) -> str:
    text = f"{deal.title or ''} {deal.category or ''}".lower()
    for kw, em in CATEGORY_EMOJI.items():
        if kw in text:
            return em
    return DEFAULT_EMOJI


def get_fire_badge(discount: int) -> str:
    for threshold, badge in FIRE_THRESHOLDS.items():
        if discount >= threshold:
            return badge
    return "🔥"


def fmt_price(price: float) -> str:
    return f"₹{int(price):,}"

def fmt_rating(rating_str: str) -> str:
    try:
        r = float(rating_str)
        full = int(r)
        half = 1 if (r - full) >= 0.5 else 0
        stars = "★" * full + ("½" if half else "") + "☆" * (5 - full - half)
        return f"{stars} {rating_str}/5"
    except Exception:
        return f"⭐ {rating_str}/5"


# ── Photo Caption (≤1024 chars) ────────────────────────────────────────────────

def format_deal_message(deal: Deal) -> str:
    """
    Caption for photo messages — discount is the HERO at the top.
    """
    emoji = get_emoji(deal)
    lines = []

    # ── Discount Badge (most prominent) ───────────────────
    if deal.discount_percent and deal.discount_percent >= 10:
        fire = get_fire_badge(deal.discount_percent)
        lines.append(f"<b>━━━  {deal.discount_percent}% OFF  ━━━</b> {fire}")
    else:
        lines.append(f"{emoji} <b>DEAL ALERT</b> {emoji}")
    lines.append("")

    # ── Title ─────────────────────────────────────────────
    title = (deal.title or "Amazon Deal")[:100]
    lines.append(f"<b>{title}</b>")
    lines.append("")

    # ── Price Block ───────────────────────────────────────
    if deal.deal_price and deal.original_price and deal.original_price > deal.deal_price:
        lines.append(f"💰 <b>{fmt_price(deal.deal_price)}</b>  "
                     f"<s>{fmt_price(deal.original_price)}</s>")
        saved = deal.original_price - deal.deal_price
        lines.append(f"💸 You save <b>{fmt_price(saved)}</b>")
    elif deal.deal_price:
        lines.append(f"💰 <b>{fmt_price(deal.deal_price)}</b>")
        if not deal.discount_percent:
            lines.append("🏆 <b>Amazon Best Seller</b>")

    lines.append("")

    # ── Rating ────────────────────────────────────────────
    if deal.rating:
        try:
            r_text = fmt_rating(deal.rating)
            if deal.rating_count:
                lines.append(f"{r_text}  ({deal.rating_count})")
            else:
                lines.append(r_text)
            lines.append("")
        except Exception:
            pass

    # ── CTA ───────────────────────────────────────────────
    lines.append(f'🛒 <a href="{deal.url}"><b>BUY NOW ON AMAZON →</b></a>')
    lines.append("")

    # ── Hashtags ──────────────────────────────────────────
    lines.append(build_hashtags(deal))

    msg = "\n".join(lines)
    # Truncate to Telegram caption limit
    return msg[:1024] if len(msg) > 1024 else msg


# ── Text-Only Message (≤4096 chars) ───────────────────────────────────────────

def format_text_only_message(deal: Deal) -> str:
    """
    Full text message when no image is available.
    More detail since there's no photo.
    """
    emoji = get_emoji(deal)
    lines = []

    # ── Big Discount Banner ────────────────────────────────
    if deal.discount_percent and deal.discount_percent >= 10:
        fire = get_fire_badge(deal.discount_percent)
        lines.append(f"{'▬' * 20}")
        lines.append(f"  <b>{deal.discount_percent}% OFF</b>  {fire}")
        lines.append(f"{'▬' * 20}")
    else:
        lines.append(f"{'─' * 25}")
        lines.append(f"{emoji} <b>DEAL ALERT</b> {emoji}")
        lines.append(f"{'─' * 25}")
    lines.append("")

    # ── Title ─────────────────────────────────────────────
    title = (deal.title or "Amazon Deal")[:150]
    lines.append(f"📦 <b>{title}</b>")
    lines.append("")

    # ── Full Price Block ──────────────────────────────────
    if deal.deal_price:
        lines.append(f"💰 <b>Price: {fmt_price(deal.deal_price)}</b>")
    if deal.original_price and deal.original_price != deal.deal_price:
        lines.append(f"🏷️ MRP: <s>{fmt_price(deal.original_price)}</s>")
    if deal.discount_percent:
        lines.append(f"📉 <b>Discount: {deal.discount_percent}% OFF</b>")
    if deal.deal_price and deal.original_price and deal.original_price > deal.deal_price:
        saved = deal.original_price - deal.deal_price
        lines.append(f"💸 <b>You Save: {fmt_price(saved)}</b>")
    if not deal.discount_percent and not deal.original_price:
        lines.append("🏆 <b>Amazon Best Seller</b>")

    lines.append("")

    # ── Rating ────────────────────────────────────────────
    if deal.rating:
        try:
            r_text = fmt_rating(deal.rating)
            if deal.rating_count:
                lines.append(f"⭐ {r_text}  |  {deal.rating_count}")
            else:
                lines.append(f"⭐ {r_text}")
            lines.append("")
        except Exception:
            pass

    # ── Category ──────────────────────────────────────────
    if deal.category:
        lines.append(f"📂 {deal.category}")
        lines.append("")

    # ── CTA ───────────────────────────────────────────────
    lines.append(f'🛒 <a href="{deal.url}"><b>BUY NOW ON AMAZON →</b></a>')
    lines.append("")
    lines.append(build_hashtags(deal))

    return "\n".join(lines)


# ── Hashtag Builder ────────────────────────────────────────────────────────────

def build_hashtags(deal: Deal) -> str:
    tags = ["#LootDeal", "#AmazonIndia"]

    disc = deal.discount_percent or 0
    if disc >= 70:
        tags.append("#SuperLoot")
    elif disc >= 50:
        tags.append("#BigOff")
    elif disc >= 30:
        tags.append("#GoodDeal")

    text = f"{deal.title or ''} {deal.category or ''}".lower()

    brand_tags = {
        "samsung": "#Samsung", "apple": "#Apple", "oneplus": "#OnePlus",
        "redmi": "#Redmi", "realme": "#Realme", "poco": "#POCO",
        "boat": "#boAt", "jbl": "#JBL", "sony": "#Sony", "lg": "#LG",
        "philips": "#Philips", "prestige": "#Prestige", "nike": "#Nike",
        "adidas": "#Adidas",
    }
    for brand, tag in brand_tags.items():
        if brand in text:
            tags.append(tag)
            break

    cat_tags = {
        ("phone", "mobile", "smartphone"): "#Smartphones",
        ("laptop", "notebook"): "#Laptops",
        ("headphone", "earphone", "earbud", "boat", "jbl"): "#AudioDeals",
        ("tv", "television"): "#TVDeals",
        ("shoe", "sneaker", "sandal"): "#Footwear",
        ("kitchen", "cookware", "appliance"): "#HomeAppliances",
        ("watch", "smartwatch"): "#Watches",
        ("gaming", "playstation", "xbox"): "#Gaming",
    }
    for keywords, tag in cat_tags.items():
        if any(kw in text for kw in keywords):
            tags.append(tag)
            break

    return " ".join(tags[:5])
