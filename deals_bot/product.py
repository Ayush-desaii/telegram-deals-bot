"""Product identity and exact money arithmetic shared by discovery and storage."""
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse, parse_qs, urlencode


def amazon_asin(url: str):
    if not isinstance(url, str):
        return None
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


def product_identity(url):
    """Store-qualified identity; preserve existing Amazon keys during migration."""
    if not isinstance(url, str):
        return None
    asin = amazon_asin(url)
    if asin:
        return ("amazon", asin, f"https://www.amazon.in/dp/{asin}")
    try:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.hostname not in ("flipkart.com", "www.flipkart.com")
                or parsed.port not in (None, 443)
                or not re.fullmatch(r"/(?:[a-zA-Z0-9_-]+/)?p/itm[a-zA-Z0-9]+/?", parsed.path)):
            return None
        values = parse_qs(parsed.query).get("pid", [])
        if len(values) != 1 or not re.fullmatch(r"[A-Z0-9]{16}", values[0]):
            return None
        return ("flipkart", values[0], "https://www.flipkart.com" + parsed.path.rstrip("/")
                + "?" + urlencode({"pid": values[0]}))
    except (ValueError, TypeError):
        return None


def product_key(url):
    identity = product_identity(url)
    if identity:
        store, identifier, _ = identity
        return identifier if store == "amazon" else f"{store}:{identifier}"
    return None


def retail_url(deal):
    return getattr(deal, "canonical_url", None) or deal.url


def store_name(deal):
    identity = product_identity(retail_url(deal))
    if not identity:
        raise ValueError("Unknown retailer")
    return {"amazon": "Amazon", "flipkart": "Flipkart"}[identity[0]]


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
