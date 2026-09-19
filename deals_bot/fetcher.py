"""
fetcher.py - Enhanced Amazon deal finder
- Scrapes product pages for REAL discount % and MRP
- Uses Amazon's sale/deal search pages as primary source
- Scores deals by discount % to post only the best ones
"""
import re
import time
import random
import feedparser
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse, urlencode, parse_qs, urlunparse, urljoin
from datetime import datetime, timezone
from product import amazon_asin, paise, discount_percent as computed_discount

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
    rating_count: Optional[str] = None   # e.g. "12,345 ratings"
    deal_score: int = 0                  # computed quality score
    tags: list = field(default_factory=list)
    asin: Optional[str] = None
    availability: Optional[bool] = None
    verified_at: Optional[str] = None
    historical_price_paise: Optional[int] = None
    historical_days: int = 0
    savings_percent: float = 0
    savings_paise: int = 0


# ── HTTP Session ───────────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
    })
    return s

SESSION = make_session()

def safe_get(url: str, timeout: int = 15) -> Optional[requests.Response]:
    for attempt in range(2):
        try:
            # Rotate user agent on each call
            SESSION.headers["User-Agent"] = random.choice(USER_AGENTS)
            resp = SESSION.get(url, timeout=timeout)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (503, 429):
                time.sleep(4)
        except Exception:
            time.sleep(2)
    return None


# ── Affiliate Tag ──────────────────────────────────────────────────────────────

def add_affiliate_tag(url: str) -> str:
    asin = amazon_asin(url)
    if not asin:
        raise ValueError("Expected an Amazon India product URL")
    url = f"https://www.amazon.in/dp/{asin}"
    tag = config.AMAZON_AFFILIATE_TAG
    if not tag or tag == "yourtag-21":
        return url
    url = url.strip().rstrip(").,\"'")
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        qs["tag"] = [tag]
        for k in ["ref", "psc", "smid", "th", "ref_", "sprefix", "crid"]:
            qs.pop(k, None)
        new_query = urlencode({k: v[0] for k, v in qs.items()})
        return urlunparse(parsed._replace(query=new_query))
    except Exception:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}tag={tag}"

def extract_asin(url: str) -> Optional[str]:
    return amazon_asin(url)

def make_affiliate_url(asin_or_url: str) -> str:
    """
    Converts product links into affiliate links:
    - Amazon links get Amazon Associates tag
    - Flipkart, Myntra, Ajio links get EarnKaro affiliate redirect
    """
    url = asin_or_url
    if re.match(r"^[A-Z0-9]{10}$", url):
        return add_affiliate_tag(f"https://www.amazon.in/dp/{url}")

    if amazon_asin(url):
        return add_affiliate_tag(url)

    # Multi-store EarnKaro routing (Flipkart, Myntra, Ajio, Nykaa)
    ek_id = getattr(config, "EARNKARO_USER_ID", "")
    if ek_id and any(store in url for store in ["flipkart.com", "myntra.com", "ajio.com", "nykaa.com"]):
        from urllib.parse import quote
        return f"https://earnkaro.com/deal?url={quote(url, safe='')}&r={ek_id}"

    return url

def resolve_short_url(url: str) -> str:
    if urlparse(url).hostname in ("amzn.to", "amzn.in"):
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENTS[0]},
                             timeout=8, allow_redirects=True)
            return r.url
        except Exception:
            pass
    return url


# ── Price / Text Helpers ───────────────────────────────────────────────────────

def parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    text = re.sub(r"[₹,\s]", "", text.replace("Rs.", "").replace("INR", ""))
    m = re.fullmatch(r"\d+(?:\.\d{1,2})?", text)
    try:
        return float(m.group()) if m and paise(m.group()) else None
    except Exception:
        return None

def parse_rating(text: str) -> Optional[str]:
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

def calc_discount(original: Optional[float], deal: Optional[float]) -> Optional[int]:
    return computed_discount(original, deal)

def clean_html(html: str) -> str:
    return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)


# ── Deal Scoring ───────────────────────────────────────────────────────────────

def score_deal(deal: Deal) -> int:
    """
    Score a deal 0-100 based on discount, rating, and price.
    Higher = better deal to post.
    """
    score = 0

    # Discount is king (up to 60 pts)
    if deal.discount_percent:
        score += min(deal.discount_percent, 60)

    # Rating bonus (up to 20 pts)
    if deal.rating:
        try:
            r = float(deal.rating)
            score += int((r / 5.0) * 20)
        except Exception:
            pass

    # Price sweetspot bonus (₹200–₹5000 = impulse buy range)
    if deal.deal_price:
        if 200 <= deal.deal_price <= 5000:
            score += 10
        elif deal.deal_price <= 200:
            score += 15   # Very cheap = great loot deal
        elif deal.deal_price <= 15000:
            score += 5

    # Has image bonus
    if deal.image_url:
        score += 5

    return score


# ── Amazon Product Page Scraper ────────────────────────────────────────────────

def scrape_product_page(url: str) -> Optional[dict]:
    """
    Visit an Amazon product page and extract full deal metadata.
    This is called for bestseller items to get their real MRP + discount.
    """
    url = resolve_short_url(url)
    asin = extract_asin(url)
    if not asin:
        return None
    clean_url = f"https://www.amazon.in/dp/{asin}"

    resp = safe_get(clean_url)
    if not resp:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    if amazon_asin(resp.url) != asin or soup.select_one("#captchacharacters, form[action*='validateCaptcha']"):
        return None
    page_asin = soup.select_one("input#ASIN")
    canonical = soup.select_one("link[rel='canonical']")
    identities = []
    if page_asin:
        identities.append(page_asin.get("value", ""))
    if canonical:
        identities.append(amazon_asin(canonical.get("href", "")))
    if not identities or any(identity != asin for identity in identities):
        return None
    availability_el = soup.select_one("#availability")
    availability_text = availability_el.get_text(" ", strip=True).lower() if availability_el else ""
    unavailable = any(word in availability_text for word in
                      ("unavailable", "out of stock", "not in stock", "temporarily"))
    availability = (not unavailable and bool(re.search(r"\bin stock\b|only \d+ left in stock", availability_text))) if availability_text else None

    # ── Title ──────────────────────────────────────────────
    title = None
    for sel in ["#productTitle", "span#productTitle", "#title span"]:
        el = soup.select_one(sel)
        if el and el.get_text(strip=True):
            title = el.get_text(strip=True)
            break
    if not title:
        return None

    # ── Image ──────────────────────────────────────────────
    image_url = None
    for sel in ["#landingImage", "#imgBlkFront", "img#main-image", "#ebooksImgBlkFront"]:
        img = soup.select_one(sel)
        if img:
            image_url = img.get("data-old-hires") or img.get("src")
            if not image_url and img.get("data-a-dynamic-image"):
                m = re.search(r'"(https://[^"]+)"', img.get("data-a-dynamic-image", ""))
                image_url = m.group(1) if m else None
            if image_url:
                image_url = re.sub(r"_SL\d+_", "_SL500_", image_url)
                break

    # ── Deal Price ─────────────────────────────────────────
    deal_price = None
    for sel in [
        "#corePriceDisplay_desktop_feature_div span.priceToPay .a-offscreen",
        "#corePrice_feature_div span.priceToPay .a-offscreen",
        "#priceblock_dealprice",
        "#priceblock_ourprice",
        "#corePriceDisplay_desktop_feature_div span.a-price:not(.a-text-price) .a-offscreen",
        "#corePrice_feature_div span.a-price:not(.a-text-price) .a-offscreen",
    ]:
        el = soup.select_one(sel)
        if el:
            p = parse_price(el.get_text(strip=True))
            if p and p > 0:
                deal_price = p
                break

    # ── MRP / Original Price ───────────────────────────────
    original_price = None
    for sel in [
        "#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen",
        "#corePriceDisplay_desktop_feature_div span.a-text-price .a-offscreen",
        "#corePrice_feature_div span.a-text-price .a-offscreen",
        "#listPrice",
    ]:
        el = soup.select_one(sel)
        if el:
            p = parse_price(el.get_text(strip=True))
            if p and p > 0 and p != deal_price:
                original_price = p
                break

    # ── Discount % ─────────────────────────────────────────
    discount_percent = None
    for sel in ["span.savingsPercentage", "td.a-span12.a-color-price"]:
        el = soup.select_one(sel)
        if el:
            m = re.search(r"(\d+)%", el.get_text())
            if m:
                discount_percent = int(m.group(1))
                break
    if not discount_percent:
        discount_percent = calc_discount(original_price, deal_price)

    # ── Rating ─────────────────────────────────────────────
    rating = None
    rating_count = None
    for sel in ["span[data-hook='rating-out-of-text']",
                "i[data-hook='average-star-rating'] span.a-icon-alt",
                "#acrPopover span.a-icon-alt"]:
        el = soup.select_one(sel)
        if el:
            rating = parse_rating(el.get_text(strip=True))
            if rating:
                break

    count_el = soup.select_one("span[data-hook='total-review-count']") or \
               soup.select_one("#acrCustomerReviewText")
    if count_el:
        rating_count = count_el.get_text(strip=True)

    # ── Category ───────────────────────────────────────────
    category = None
    for sel in ["#wayfinding-breadcrumbs_feature_div li:nth-child(3) a",
                "#wayfinding-breadcrumbs_feature_div li:nth-child(2) a",
                "#wayfinding-breadcrumbs_feature_div a"]:
        el = soup.select_one(sel)
        if el:
            category = el.get_text(strip=True)
            break

    return {
        "title": title,
        "image_url": image_url,
        "deal_price": deal_price,
        "original_price": original_price,
        "discount_percent": discount_percent,
        "rating": rating,
        "rating_count": rating_count,
        "category": category,
        "asin": asin,
        "clean_url": clean_url,
        "availability": availability,
        "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── Source 1: Amazon Sale Search Pages ────────────────────────────────────────

DEAL_SEARCH_PAGES = [
    # Daily-changing sale keywords — varied enough to find fresh deals every day
    ("https://www.amazon.in/s?k=electronics+offer&s=discount-rank", "Electronics"),
    ("https://www.amazon.in/s?k=mobile+phone+sale+india&s=discount-rank", "Mobiles"),
    ("https://www.amazon.in/s?k=bluetooth+headphones+offer&s=discount-rank", "Audio"),
    ("https://www.amazon.in/s?k=smart+tv+sale&s=discount-rank", "TVs"),
    ("https://www.amazon.in/s?k=kitchen+appliances+offer&s=discount-rank", "Kitchen"),
    ("https://www.amazon.in/s?k=fitness+gym+equipment+offer&s=discount-rank", "Sports"),
    ("https://www.amazon.in/s?k=laptop+offer+india&s=discount-rank", "Laptops"),
    ("https://www.amazon.in/s?k=fashion+clothing+sale&s=discount-rank", "Fashion"),
    ("https://www.amazon.in/s?k=usb+cable+charger+offer&s=discount-rank", "Accessories"),
    ("https://www.amazon.in/s?k=home+decor+offer&s=discount-rank", "Home Decor"),
]

def fetch_amazon_sale_page(page_url: str, category: str, max_items: int = 8) -> list[Deal]:
    """
    Scrape Amazon search results for discounted products.
    Uses multiple robust selector strategies to handle HTML differences across regions/UAs.
    """
    deals = []
    resp = safe_get(page_url)
    if not resp:
        return deals

    soup = BeautifulSoup(resp.text, "html.parser")

    # Strategy: find all divs that have a 10-char ASIN — works across all Amazon page layouts
    all_asin_divs = soup.find_all("div", attrs={"data-asin": True})
    products = [
        d for d in all_asin_divs
        if d.get("data-asin") and len(d.get("data-asin", "")) == 10
        and d.get("data-component-type") in ("s-search-result", "s-search-results", None)
    ]

    # De-duplicate by ASIN (avoid nested div double-counting)
    seen_asins = set()
    unique_products = []
    for p in products:
        asin = p.get("data-asin", "")
        if asin not in seen_asins:
            seen_asins.add(asin)
            unique_products.append(p)
    products = unique_products

    if products:
        print(f"   📦 {category}: {len(products)} results")
    else:
        print(f"   📦 {category}: 0 results (page may be JS-rendered or blocked)")
        return deals

    for product in products[:max_items]:
        asin = product.get("data-asin", "")
        try:
            # Title — try multiple selectors
            title = None
            for sel in [
                "h2 a span",
                "h2 span",
                "span.a-size-medium.a-color-base.a-text-normal",
                "span.a-size-base-plus.a-color-base.a-text-normal",
                "span.a-text-normal",
            ]:
                el = product.select_one(sel)
                if el and el.get_text(strip=True):
                    title = el.get_text(strip=True)
                    break
            if not title:
                continue

            # Image
            img_el = product.select_one("img.s-image") or product.select_one("img[src*='amazon']")
            image_url = None
            if img_el:
                image_url = img_el.get("src", "")
                image_url = re.sub(r"\._[A-Z]+\d+_\.", "._SL500_.", image_url) if image_url else None

            # Deal price — try multiple selectors
            deal_price = None
            for sel in [
                "span.a-price:not(.a-text-price) .a-offscreen",
                "span.a-price .a-offscreen",
            ]:
                el = product.select_one(sel)
                if el:
                    p = parse_price(el.get_text())
                    if p and p > 0:
                        deal_price = p
                        break

            # MRP / Original price
            orig_el = product.select_one("span.a-price.a-text-price .a-offscreen")
            original_price = parse_price(orig_el.get_text()) if orig_el else None

            # Discount %
            discount_percent = None
            for sel in [
                "span[class*='savingPriceOverride']",
                "span.a-color-price",
                "span[class*='saving']",
            ]:
                el = product.select_one(sel)
                if el:
                    m = re.search(r"(\d+)%", el.get_text())
                    if m:
                        discount_percent = int(m.group(1))
                        break
            if not discount_percent:
                discount_percent = calc_discount(original_price, deal_price)

            # Rating
            rating_el = product.select_one("span.a-icon-alt")
            rating = parse_rating(rating_el.get_text()) if rating_el else None

            # Rating count
            rc_el = product.select_one("span.a-size-base.s-underline-text")
            rating_count = rc_el.get_text(strip=True) if rc_el else None

            url = make_affiliate_url(asin)
            d = Deal(
                title=title, url=url, source="Amazon India",
                image_url=image_url, deal_price=deal_price,
                original_price=original_price, discount_percent=discount_percent,
                rating=rating, rating_count=rating_count, category=category,
            )
            d.deal_score = score_deal(d)
            deals.append(d)

        except Exception:
            continue

    return deals


# ── Source 2: Amazon Bestsellers + Product Page Enrichment ────────────────────

BESTSELLER_PAGES = [
    ("https://www.amazon.in/gp/bestsellers/electronics/", "Electronics"),
    ("https://www.amazon.in/gp/bestsellers/computers/", "Computers"),
    ("https://www.amazon.in/gp/bestsellers/kitchen/", "Kitchen & Home"),
    ("https://www.amazon.in/gp/bestsellers/sports/", "Sports & Fitness"),
    ("https://www.amazon.in/gp/bestsellers/apparel/", "Fashion"),
    ("https://www.amazon.in/gp/movers-and-shakers/electronics/", "Trending Electronics"),
    ("https://www.amazon.in/gp/movers-and-shakers/kitchen/", "Trending Kitchen"),
]

def fetch_amazon_bestsellers_enriched(page_url: str, category: str,
                                       max_items: int = 5) -> list[Deal]:
    """
    Scrape bestsellers page, then visit each product page to get real price/discount.
    Slower but gives accurate discount data.
    """
    deals = []
    resp = safe_get(page_url)
    if not resp:
        return deals

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = (soup.select("div.zg-item-immersion") or
             soup.select("li.zg-item") or
             soup.select("div[class*='p13n-sc-uncoverable-faceout']"))

    print(f"   📦 {category}: {len(cards)} bestsellers found")

    asin_list = []
    for card in cards[:max_items * 2]:   # fetch extra to compensate for failures
        asin = card.get("data-asin")
        if not asin:
            a_el = card.select_one("a[href*='/dp/']")
            if a_el:
                asin = extract_asin(urljoin(page_url, a_el.get("href", "")))
        if asin and asin not in asin_list:
            asin_list.append(asin)

    for asin in asin_list[:max_items]:
        product_url = f"https://www.amazon.in/dp/{asin}"
        details = scrape_product_page(product_url)
        if not details or not details.get("title"):
            continue

        url = make_affiliate_url(asin)
        d = Deal(
            title=details["title"],
            url=url,
            source="Amazon India",
            image_url=details.get("image_url"),
            deal_price=details.get("deal_price"),
            original_price=details.get("original_price"),
            discount_percent=details.get("discount_percent"),
            rating=details.get("rating"),
            rating_count=details.get("rating_count"),
            category=details.get("category") or category,
        )
        d.deal_score = score_deal(d)
        deals.append(d)
        time.sleep(1.2)   # Be nice to Amazon

    return deals


# ── Source 3: Reddit → Amazon Link Extractor ───────────────────────────────────

REDDIT_HEADERS = {"User-Agent": "Mozilla/5.0 DealBot/3.0 (India deals aggregator)"}
AMAZON_LINK_RE = re.compile(
    r"https?://(?:www\.)?(?:amazon\.in|amzn\.to|amzn\.in)/\S+",
    re.IGNORECASE,
)

RSS_FEEDS_LIST = [
    ("https://www.reddit.com/r/IndiaDeals/new/.rss", "r/IndiaDeals"),
    ("https://www.reddit.com/r/IndiaDealsExchange/new/.rss", "r/IndiaDealsExchange"),
]

def fetch_reddit_amazon_deals() -> list[Deal]:
    """
    Scan Reddit India deal subreddits for posts with Amazon.in links.
    Visits each Amazon product page for real data.
    """
    deals = []
    for feed_url, feed_name in RSS_FEEDS_LIST:
        try:
            response = safe_get(feed_url)
            if response is None:
                continue
            feed = feedparser.parse(response.content)
            print(f"   📡 {feed_name}: {len(feed.entries)} posts")

            for entry in feed.entries[:10]:
                title = entry.get("title", "").strip()
                if any(w in title.lower() for w in
                       ["weekly", "megathread", "discussion", "help", "question", "where to"]):
                    continue

                full_text = entry.get("summary", "") + \
                            (entry.get("content") or [{}])[0].get("value", "")
                links = [a.get("href", "") for a in BeautifulSoup(full_text, "html.parser").select("a[href]")
                         if amazon_asin(a.get("href", "")) or urlparse(a.get("href", "")).hostname in ("amzn.to", "amzn.in")]
                if not links:
                    continue

                raw_url = links[0].rstrip(").,\"'")
                details = scrape_product_page(raw_url)
                if not details or not details.get("asin"):
                    continue

                url = make_affiliate_url(details["asin"])
                d = Deal(
                    title=details["title"],
                    url=url,
                    source="Amazon India",
                    image_url=details.get("image_url"),
                    deal_price=details.get("deal_price"),
                    original_price=details.get("original_price"),
                    discount_percent=details.get("discount_percent"),
                    rating=details.get("rating"),
                    rating_count=details.get("rating_count"),
                    category=details.get("category") or "Amazon Deals",
                )
                d.deal_score = score_deal(d)
                deals.append(d)
                time.sleep(1.5)

        except Exception as e:
            print(f"   ⚠️  {feed_name}: {e}")

    return deals


# ── Source 4: Amazon PA API (Official) ────────────────────────────────────────

def fetch_via_pa_api(max_per_keyword: int = 5) -> list[Deal]:
    if not config.AMAZON_PA_API_KEY or config.AMAZON_PA_API_KEY == "your_pa_api_key_here":
        return []
    try:
        from amazon_paapi import AmazonApi
    except ImportError:
        return []

    keywords = ["electronics offer", "smartphone deal", "laptop sale",
                "headphones offer", "home appliance sale"]
    all_deals = []

    try:
        api = AmazonApi(config.AMAZON_PA_API_KEY, config.AMAZON_PA_API_SECRET,
                        config.AMAZON_AFFILIATE_TAG, "IN")
        for kw in keywords:
            try:
                results = api.search_items(keywords=kw, search_index="All",
                                           min_saving_percent=config.MIN_DISCOUNT_PERCENT,
                                           sort_by="Featured", item_count=max_per_keyword)
                for item in (results.items or []):
                    try:
                        title = item.item_info.title.display_value if item.item_info and item.item_info.title else None
                        if not title:
                            continue
                        deal_price = orig = disc = None
                        if item.offers and item.offers.listings:
                            lst = item.offers.listings[0]
                            deal_price = lst.price.amount if lst.price else None
                            orig = lst.saving_basis.amount if lst.saving_basis else None
                            disc = int(lst.price.savings.percentage or 0) if lst.price and lst.price.savings else None
                        img = None
                        if item.images and item.images.primary and item.images.primary.large:
                            img = item.images.primary.large.url
                        rating = str(item.customer_reviews.star_rating.value) \
                            if item.customer_reviews and item.customer_reviews.star_rating else None
                        asin = item.asin or ""
                        url = make_affiliate_url(asin) if asin else (item.detail_page_url or "")
                        d = Deal(title=title, url=url, source="Amazon India",
                                 image_url=img, deal_price=deal_price, original_price=orig,
                                 discount_percent=disc, rating=rating, category=kw)
                        d.deal_score = score_deal(d)
                        all_deals.append(d)
                    except Exception:
                        continue
                time.sleep(1)
            except Exception as e:
                print(f"   ⚠️  PA API [{kw}]: {e}")
    except Exception as e:
        print(f"   ❌ PA API: {e}")

    return all_deals


# ── Main Fetch Function ────────────────────────────────────────────────────────

def fetch_all_deals() -> list[Deal]:
    """
    Fetch deals from all sources and return scored, deduplicated list.
    Order: PA API → Sale search pages → Bestsellers (enriched) → Reddit
    """
    all_deals: list[Deal] = []

    # ── PA API (official, best quality) ───────────────────────────────────────
    if config.AMAZON_PA_API_KEY and config.AMAZON_PA_API_KEY != "your_pa_api_key_here":
        print("🔍 [1/4] Amazon PA API...")
        pa = fetch_via_pa_api()
        print(f"   ✅ PA API: {len(pa)} deals")
        all_deals.extend(pa)
    else:
        print("ℹ️  PA API not configured (add keys to .env for best results)")

    # ── Sale search pages (has real discount %) ────────────────────────────────
    print("🔍 [2/4] Amazon Sale Search pages (with discount data)...")
    for page_url, cat in DEAL_SEARCH_PAGES:
        try:
            deals = fetch_amazon_sale_page(page_url, cat, max_items=6)
            all_deals.extend(deals)
            time.sleep(1.5)
        except Exception as e:
            print(f"   ⚠️  {cat}: {e}")

    # ── Bestsellers + product page enrichment ─────────────────────────────────
    print("🔍 [3/4] Amazon Bestsellers (with product page enrichment)...")
    for page_url, cat in BESTSELLER_PAGES[:3]:   # limit to 3 to save time
        try:
            deals = fetch_amazon_bestsellers_enriched(page_url, cat, max_items=3)
            all_deals.extend(deals)
            time.sleep(1)
        except Exception as e:
            print(f"   ⚠️  {cat}: {e}")

    # ── Reddit → Amazon ────────────────────────────────────────────────────────
    print("🔍 [4/4] Reddit India deal posts...")
    try:
        reddit_deals = fetch_reddit_amazon_deals()
        print(f"   ✅ Reddit: {len(reddit_deals)} Amazon deals")
        all_deals.extend(reddit_deals)
    except Exception as e:
        print(f"   ⚠️  Reddit: {e}")

    # ── Deduplicate by ASIN ────────────────────────────────────────────────────
    seen = set()
    unique = []
    for d in all_deals:
        asin = extract_asin(d.url)
        key = asin or d.url
        if key not in seen:
            seen.add(key)
            unique.append(d)

    # ── Sort by deal score (best deals first) ──────────────────────────────────
    unique.sort(key=lambda d: d.deal_score, reverse=True)

    print(f"\n🏆 Total unique deals: {len(unique)}")
    if unique:
        top = unique[0]
        print(f"   🥇 Best deal: {top.title[:60]} | Score:{top.deal_score} | "
              f"{top.discount_percent or '?'}% off | ₹{top.deal_price or '?'}")

    return unique


def filter_deals(deals: list[Deal]) -> list[Deal]:
    """Validate discovery data; eligibility is decided after fresh verification."""
    filtered = []
    for deal in deals:
        asin = amazon_asin(deal.url)
        if not deal.title or not deal.title.strip() or not asin or not paise(deal.deal_price):
            continue
        deal.asin = asin
        deal.discount_percent = calc_discount(deal.original_price, deal.deal_price)
        filtered.append(deal)
    return filtered


def verify_deal(deal: Deal) -> Optional[Deal]:
    """Return a new snapshot: never retain stale price/image/rating fields."""
    asin = amazon_asin(deal.url)
    if not asin:
        return None
    details = scrape_product_page(f"https://www.amazon.in/dp/{asin}")
    if (not details or details.get("asin") != asin or details.get("availability") is not True
            or not paise(details.get("deal_price")) or not details.get("title", "").strip()):
        reason = ("page or identity not verified" if not details else
                  "availability not confirmed" if details.get("availability") is not True else
                  "missing valid product data")
        print(f"   Skipped {asin}: {reason}")
        return None
    fresh = Deal(
        title=details["title"], url=make_affiliate_url(asin), source=deal.source,
        asin=asin, availability=True, verified_at=details["verified_at"],
        **{key: details.get(key) for key in ("deal_price", "original_price", "image_url",
                                           "rating", "rating_count", "category")},
    )
    fresh.discount_percent = calc_discount(fresh.original_price, fresh.deal_price)
    fresh.deal_score = score_deal(fresh)
    return fresh
