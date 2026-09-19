"""Product identity and exact money arithmetic shared by discovery and storage."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse


def amazon_asin(url: str):
    try:
        parsed = urlparse(url)
        if (parsed.scheme not in ("http", "https") or parsed.username or parsed.password
                or parsed.hostname not in ("amazon.in", "www.amazon.in")
                or parsed.port not in (None, 80, 443)):
            return None
        match = re.search(r"/(?:dp|gp/product|product)/([A-Z0-9]{10})(?:/|$)", parsed.path)
        return match.group(1) if match else None
    except (ValueError, TypeError):
        return None


def paise(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount > Decimal("100000000"):
            return None
        result = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return result if result > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def discount_percent(original, current):
    original, current = paise(original), paise(current)
    if original and current and original > current:
        return (original - current) * 100 // original
    return None


def meaningful_drop(baseline: int, current: int) -> bool:
    return baseline - current >= 5000 and (baseline - current) * 10 >= baseline
