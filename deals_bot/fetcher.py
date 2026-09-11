"""
fetcher.py - Fetches REAL Amazon deals with product images, prices & affiliate tags

Sources (in order of reliability):
  1. Amazon PA API (official, best) — requires API keys
  2. Amazon Bestsellers pages (scraper-friendly, no JS needed)
  3. Reddit r/IndiaDeals → extract Amazon product links → scrape product page
"""
import re
import time
import random
import feedparser
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse

import config


# ── Deal Data Model ────────────────────────────────────────────────────────────

@dataclass
class Deal:
    title: str
    url: str
    source: str
    image_url: Optional[str] = None
    original_price: Optional[float] = None
    deal_price: Optional[float] = None
    discount_percent: Optional[int] = None
    description: Optional[str] = None
    category: Optional[str] = None
    rating: Optional[str] = None
    tags: list = field(default_factory=list)


# ── HTTP Session (shared, mimics real browser) ─────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
    })
    return s


SESSION = make_session()


def safe_get(url: str, timeout: int = 15) -> Optional[requests.Response]:
    """Safe GET with retry on failure."""
    for attempt in range(2):
        try:
            resp = SESSION.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp
            if resp.status_code == 503:
                time.sleep(3)
        except Exception:
            time.sleep(2)
    return None


# ── Affiliate Link Builder ─────────────────────────────────────────────────────

def add_amazon_affiliate_tag(url: str) -> str:
    """Append Amazon affiliate tag to any Amazon URL."""
    tag = config.AMAZON_AFFILIATE_TAG
    if not tag or tag == "yourtag-21":
        return url
    url = url.strip().rstrip(").,\"'")
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        qs["tag"] = [tag]
        # Remove amazon tracking noise
        for k in ["ref", "psc", "smid", "th", "ref_"]:
            qs.pop(k, None)
        new_query = urlencode({k: v[0] for k, v in qs.items()})
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}tag={tag}"


def extract_asin(url: str) -> Optional[str]:
    """Extract Amazon ASIN from any Amazon URL."""
    match = re.search(r"/(?:dp|gp/product|product)/([A-Z0-9]{10})", url)
    return match.group(1) if match else None


def make_clean_amazon_url(asin: str) -> str:
    """Build clean affiliate URL from ASIN."""
    base = f"https://www.amazon.in/dp/{asin}"
    return add_amazon_affiliate_tag(base)


def resolve_short_link(url: str) -> str:
    """Resolve amzn.to / amzn.in short links to full amazon.in URL."""
    if "amzn.to" in url or ("amzn.in" in url and "/dp/" not in url):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENTS[0]},
                             timeout=10, allow_redirects=True)
            return r.url
        except Exception:
            pass
    return url


# ── Price / Text Helpers ───────────────────────────────────────────────────────

def parse_price(text: str) -> Optional[float]:
    """Extract a numeric price from strings like '₹1,299', 'Rs. 999', etc."""
    if not text:
        return None
    text = text.replace(",", "").replace("₹", "").replace("Rs.", "").strip()
    m = re.search(r"[\d]+(?:\.\d+)?", text)
    if m:
        try:
            return float(m.group())
        except Exception:
            return None
    return None


def parse_rating(text: str) -> Optional[str]:
    """Safely extract numeric rating like '4.3' from any rating string."""
    if not text:
        return None
    m = re.search(r"([\d]+\.?\d*)\s*(?:out of|/)?", text)
    if m:
        try:
            val = float(m.group(1))
            if 1.0 <= val <= 5.0:
                return str(round(val, 1))
        except Exception:
            pass
    return None


def calc_discount(original: float, deal: float) -> Optional[int]:
    if original and deal and original > deal > 0:
        return int(((original - deal) / original) * 100)
    return None


def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text(separator=" ", strip=True)


def extract_image_url(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    img = soup.find("img")
    return img.get("src") if img else None


# ── Amazon Product Page Scraper ────────────────────────────────────────────────

def get_amazon_product_details(url: str) -> Optional[dict]:
    """
    Visit an Amazon product page and extract all deal metadata.
    Returns None if the page can't be scraped or it's not a product page.
    """
    # Resolve short links first
    url = resolve_short_link(url)
    asin = extract_asin(url)

    # Build a clean product URL
    clean_url = f"https://www.amazon.in/dp/{asin}" if asin else url

    resp = safe_get(clean_url)
    if not resp:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # ── Title ──────────────────────────────────────────────
    title = None
    for sel in ["#productTitle", "span#productTitle", "#title span"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            title = el.get_text(strip=True)
            break
    if not title:
        return None  # Not a product page

    # ── Image ──────────────────────────────────────────────
    image_url = None
    img_el = soup.select_one("#landingImage") or \
             soup.select_one("#imgBlkFront") or \
             soup.select_one("img#main-image")
    if img_el:
        # data-old-hires has highest quality
        image_url = (img_el.get("data-old-hires") or
                     img_el.get("src") or None)
        # Upgrade to SL500 resolution
        if image_url and "_SL" in image_url:
            image_url = re.sub(r"_SL\d+_", "_SL500_", image_url)
        # Fix dynamic image JSON
        if not image_url and img_el.get("data-a-dynamic-image"):
            m = re.search(r'"(https://[^"]+)"', img_el.get("data-a-dynamic-image", ""))
            image_url = m.group(1) if m else None

    # ── Deal Price ─────────────────────────────────────────
    deal_price = None
    for sel in [
        "span.priceToPay .a-offscreen",
        "#priceblock_dealprice",
        "#priceblock_ourprice",
        ".a-price .a-offscreen",
        "span.a-price-whole",
    ]:
        el = soup.select_one(sel)
        if el:
            p = parse_price(el.get_text(strip=True))
            if p and p > 0:
                deal_price = p
                break

    # ── Original Price (MRP) ───────────────────────────────
    original_price = None
    for sel in [
        "span.a-price.a-text-price .a-offscreen",
        ".basisPrice .a-offscreen",
        "span.a-text-price .a-offscreen",
    ]:
        el = soup.select_one(sel)
        if el:
            p = parse_price(el.get_text(strip=True))
            if p and p > 0:
                original_price = p
                break

    # ── Discount % ─────────────────────────────────────────
    discount_percent = None
    disc_el = soup.select_one("span.savingsPercentage") or \
              soup.select_one("span.a-color-price")
    if disc_el:
        m = re.search(r"(\d+)%", disc_el.get_text())
        if m:
            discount_percent = int(m.group(1))
    if not discount_percent:
        discount_percent = calc_discount(original_price, deal_price)

    # ── Rating ─────────────────────────────────────────────
    rating = None
    for sel in [
        "span[data-hook='rating-out-of-text']",
        "i[data-hook='average-star-rating'] span.a-icon-alt",
        "span.a-icon-alt",
    ]:
        el = soup.select_one(sel)
        if el:
            rating = parse_rating(el.get_text(strip=True))
            if rating:
                break

    # ── Category ───────────────────────────────────────────
    category = None
    cat_el = soup.select_one("#wayfinding-breadcrumbs_feature_div li:nth-child(3) a") or \
             soup.select_one("#wayfinding-breadcrumbs_feature_div a")
    if cat_el:
        category = cat_el.get_text(strip=True)

    return {
        "title": title,
        "image_url": image_url,
        "deal_price": deal_price,
        "original_price": original_price,
        "discount_percent": discount_percent,
        "rating": rating,
        "category": category,
        "clean_url": clean_url,
        "asin": asin,
    }


# ── Amazon Bestsellers Scraper (Most Reliable Free Source) ────────────────────
# Amazon bestseller pages load fine without JS — great for scraping

BESTSELLER_PAGES = [
    ("https://www.amazon.in/gp/bestsellers/electronics/", "Electronics"),
    ("https://www.amazon.in/gp/bestsellers/computers/", "Computers"),
    ("https://www.amazon.in/gp/movers-and-shakers/electronics/", "Trending Electronics"),
    ("https://www.amazon.in/gp/bestsellers/kitchen/", "Kitchen & Home"),
    ("https://www.amazon.in/gp/bestsellers/sports/", "Sports & Fitness"),
    ("https://www.amazon.in/gp/movers-and-shakers/sports/", "Trending Sports"),
]


def fetch_amazon_bestsellers(page_url: str, category: str, max_items: int = 8) -> list[Deal]:
    """Scrape Amazon bestsellers / movers-and-shakers page for deals."""
    deals = []
    resp = safe_get(page_url)
    if not resp:
        return deals

    soup = BeautifulSoup(resp.text, "html.parser")

    # Amazon bestseller cards
    cards = (soup.select("div.zg-item-immersion") or
             soup.select("li.zg-item") or
             soup.select("div[class*='p13n-sc-uncoverable-faceout']"))

    print(f"   📦 {category}: {len(cards)} cards found")

    for card in cards[:max_items]:
        try:
            # ASIN
            asin = (card.get("data-asin") or
                    card.find(attrs={"data-asin": True}) and
                    card.find(attrs={"data-asin": True}).get("data-asin"))
            if not asin:
                a_el = card.select_one("a[href*='/dp/']")
                if a_el:
                    asin = extract_asin(a_el.get("href", ""))
            if not asin:
                continue

            # Title
            title_el = (card.select_one("div._cDEzb_p13n-sc-css-line-clamp-3_g3dy1") or
                        card.select_one("span.zg-item-link-title") or
                        card.select_one("a.a-link-normal span") or
                        card.select_one("div[class*='_truncationWrapper']") or
                        card.select_one("span[class*='p13n-sc-line-clamp']"))
            title = title_el.get_text(strip=True) if title_el else None
            if not title:
                continue

            # Image
            img_el = card.select_one("img")
            image_url = None
            if img_el:
                image_url = img_el.get("data-old-hires") or img_el.get("src")
                if image_url and "_SL" in image_url:
                    image_url = re.sub(r"_SL\d+_", "_SL500_", image_url)

            # Price
            price_el = (card.select_one("span.p13n-sc-price") or
                        card.select_one("span._cDEzb_p13n-sc-price_3mJ9Z") or
                        card.select_one("span.a-color-price"))
            deal_price = parse_price(price_el.get_text()) if price_el else None

            # Rating
            rating_el = card.select_one("span.a-icon-alt")
            rating = parse_rating(rating_el.get_text()) if rating_el else None

            affiliate_url = make_clean_amazon_url(asin)

            deals.append(Deal(
                title=title,
                url=affiliate_url,
                source="Amazon India",
                image_url=image_url,
                deal_price=deal_price,
                category=category,
                rating=rating,
            ))

        except Exception:
            continue

    return deals


# ── Reddit → Amazon Link Extractor ────────────────────────────────────────────

REDDIT_HEADERS = {"User-Agent": "Mozilla/5.0 DealBot/2.0"}

AMAZON_LINK_RE = re.compile(
    r"https?://(?:www\.)?(?:amazon\.in|amzn\.to|amzn\.in)/\S+",
    re.IGNORECASE,
)


def fetch_reddit_amazon_deals(feed_url: str, feed_name: str) -> list[Deal]:
    """
    Parse Reddit subreddit RSS for posts containing Amazon.in links.
    Visit each Amazon link to get real product data + image.
    """
    deals = []
    try:
        feed = feedparser.parse(feed_url, request_headers=REDDIT_HEADERS)
        print(f"   📡 {feed_name}: {len(feed.entries)} posts")

        for entry in feed.entries:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "")
            content = (entry.get("content") or [{}])[0].get("value", "")
            full_text = summary + content

            # Skip non-deal posts
            if any(w in title.lower() for w in ["weekly", "megathread", "discussion", "help", "question"]):
                continue

            # Find Amazon links in post body
            links = AMAZON_LINK_RE.findall(full_text)
            if not links:
                continue

            raw_url = links[0].rstrip(").,\"'")
            print(f"   🔗 Checking: {title[:55]}...")

            details = get_amazon_product_details(raw_url)
            if not details or not details.get("asin"):
                continue

            final_url = make_clean_amazon_url(details["asin"])

            deals.append(Deal(
                title=details["title"],
                url=final_url,
                source="Amazon India",
                image_url=details.get("image_url"),
                original_price=details.get("original_price"),
                deal_price=details.get("deal_price"),
                discount_percent=details.get("discount_percent"),
                rating=details.get("rating"),
                category=details.get("category") or "Amazon Deals",
            ))

            time.sleep(1.5)

    except Exception as e:
        print(f"   ⚠️  Reddit feed error ({feed_url}): {e}")
    return deals


# ── Amazon PA API (Official, Best Quality) ────────────────────────────────────

PA_API_KEYWORDS = [
    "smartphone offer india",
    "laptop deal india",
    "wireless headphones sale",
    "home appliances offer",
    "kitchen appliances deal",
]


def fetch_via_pa_api(max_per_keyword: int = 5) -> list[Deal]:
    """
    Fetch deals using Amazon's official PA API 5.0.
    Requires AMAZON_PA_API_KEY + AMAZON_PA_API_SECRET in .env
    Gives 100% accurate data: real prices, images, ratings.
    """
    if not config.AMAZON_PA_API_KEY or config.AMAZON_PA_API_KEY == "your_pa_api_key_here":
        return []

    try:
        from amazon_paapi import AmazonApi
    except ImportError:
        print("   ⚠️  Run: pip install amazon-paapi5")
        return []

    all_deals = []
    try:
        api = AmazonApi(
            config.AMAZON_PA_API_KEY,
            config.AMAZON_PA_API_SECRET,
            config.AMAZON_AFFILIATE_TAG,
            "IN",
        )

        for keyword in PA_API_KEYWORDS:
            try:
                results = api.search_items(
                    keywords=keyword,
                    search_index="All",
                    min_saving_percent=config.MIN_DISCOUNT_PERCENT,
                    sort_by="Featured",
                    item_count=max_per_keyword,
                )

                for item in (results.items or []):
                    try:
                        title = (item.item_info.title.display_value
                                 if item.item_info and item.item_info.title else None)
                        if not title:
                            continue

                        deal_price = orig_price = discount = None
                        if item.offers and item.offers.listings:
                            lst = item.offers.listings[0]
                            if lst.price:
                                deal_price = lst.price.amount
                            if lst.saving_basis:
                                orig_price = lst.saving_basis.amount
                            if lst.price and lst.price.savings:
                                discount = int(lst.price.savings.percentage or 0)

                        image_url = None
                        if item.images and item.images.primary and item.images.primary.large:
                            image_url = item.images.primary.large.url

                        rating = None
                        if item.customer_reviews and item.customer_reviews.star_rating:
                            rating = str(item.customer_reviews.star_rating.value)

                        asin = item.asin or ""
                        url = make_clean_amazon_url(asin) if asin else (item.detail_page_url or "")

                        all_deals.append(Deal(
                            title=title, url=url,
                            source="Amazon India",
                            image_url=image_url,
                            deal_price=deal_price,
                            original_price=orig_price,
                            discount_percent=discount,
                            rating=rating,
                            category=keyword,
                        ))
                    except Exception:
                        continue

                time.sleep(1)

            except Exception as e:
                print(f"   ⚠️  PA API [{keyword}]: {e}")

    except Exception as e:
        print(f"   ❌ PA API failed: {e}")

    return all_deals


# ── Main Fetch Function ────────────────────────────────────────────────────────

def fetch_all_deals() -> list[Deal]:
    """Fetch deals from all sources. PA API → Bestsellers → Reddit."""
    all_deals: list[Deal] = []

    # ── Source 1: Amazon PA API (official, most reliable) ─────────────────────
    if (config.AMAZON_PA_API_KEY and
            config.AMAZON_PA_API_KEY != "your_pa_api_key_here"):
        print("🔍 [1/3] Amazon PA API...")
        pa = fetch_via_pa_api()
        print(f"   ✅ PA API: {len(pa)} deals")
        all_deals.extend(pa)
    else:
        print("ℹ️  PA API not set — using free scraping (add keys to .env for better results)")

    # ── Source 2: Amazon Bestsellers (scraper-friendly, no JS needed) ──────────
    print("🔍 [2/3] Amazon Bestsellers pages...")
    for page_url, cat in BESTSELLER_PAGES:
        try:
            deals = fetch_amazon_bestsellers(page_url, cat, max_items=6)
            if deals:
                print(f"   ✅ {cat}: {len(deals)} products")
            all_deals.extend(deals)
            time.sleep(1.5)
        except Exception as e:
            print(f"   ⚠️  {cat}: {e}")

    # ── Source 3: Reddit → Amazon product page scraping ────────────────────────
    print("🔍 [3/3] Reddit India deal posts...")
    for feed_cfg in config.RSS_FEEDS:
        if feed_cfg["source"] == "reddit":
            try:
                deals = fetch_reddit_amazon_deals(feed_cfg["url"], feed_cfg["name"])
                print(f"   ✅ {feed_cfg['name']}: {len(deals)} Amazon deals")
                all_deals.extend(deals)
            except Exception as e:
                print(f"   ⚠️  {feed_cfg['name']}: {e}")

    # ── Deduplicate by ASIN ─────────────────────────────────────────────────────
    seen = set()
    unique = []
    for d in all_deals:
        asin = extract_asin(d.url)
        key = asin or d.url
        if key not in seen:
            seen.add(key)
            unique.append(d)

    return unique


def filter_deals(deals: list[Deal]) -> list[Deal]:
    """
    Keep only Amazon links with sufficient discount.
    Bestseller products without discount data are ALWAYS allowed through
    (they are highly-rated products worth recommending).
    """
    filtered = []
    for deal in deals:
        if not deal.title or not deal.url:
            continue
        if "amazon.in" not in deal.url:
            continue
        # Only apply discount filter if we have discount data
        # Products with no discount data (bestsellers) are allowed through
        if deal.discount_percent is not None:
            if deal.discount_percent < config.MIN_DISCOUNT_PERCENT:
                continue
        filtered.append(deal)
    return filtered

