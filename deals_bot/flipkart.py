"""Fail-closed Flipkart adapter using product-scoped structured retailer data."""
from datetime import datetime, timezone
import json
from decimal import Decimal
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import fetcher
from product import product_identity, product_key, paise


SEARCH_PAGES = [
    ("flipkart_search:Audio", "https://www.flipkart.com/search?q=headphones"),
    ("flipkart_search:Kitchen", "https://www.flipkart.com/search?q=kitchen+appliances"),
]


def schema_type(node, name):
    types = node.get("@type", [])
    types = [types] if isinstance(types, str) else types
    return isinstance(types, list) and any(t in (name, f"https://schema.org/{name}",
                                                   f"http://schema.org/{name}") for t in types)


def products_in(data):
    """Only top-level Product/@graph; never recommendation ItemLists."""
    if isinstance(data, list):
        for item in data:
            yield from products_in(item)
    elif isinstance(data, dict):
        if schema_type(data, "Product"):
            yield data
        elif "@graph" in data:
            yield from products_in(data["@graph"])


def parse_product(candidate, response):
    from discovery import VerificationResult, blocked_page
    if product_key(response.url) != candidate.asin:
        return VerificationResult("identity_mismatch")
    soup = BeautifulSoup(response.text, "html.parser")
    if blocked_page(soup):
        return VerificationResult("blocked")
    canonical = soup.select_one('link[rel="canonical"]')
    if canonical and product_key(canonical.get("href", "")) != candidate.asin:
        return VerificationResult("identity_mismatch")
    pid = product_identity(candidate.url)[1]
    matches = []
    identified_products = False
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            nodes = list(products_in(json.loads(script.get_text())))
        except (ValueError, TypeError):
            continue
        for node in nodes:
            identities = [node[k] for k in ("sku", "productID") if node.get(k)]
            urls = [node[k] for k in ("url",) if node.get(k)]
            if not identities and not urls:
                continue
            identified_products = True
            if any(x != pid for x in identities) or any(product_key(u) != candidate.asin for u in urls):
                continue
            matches.append(node)
    if len(matches) != 1:
        return VerificationResult("identity_mismatch" if identified_products else "parse_error")
    product = matches[0]
    offers = product.get("offers")
    offers = offers if isinstance(offers, list) else [offers]
    # Multiple sellers/variants and aggregate lowPrice cannot prove a single offer.
    if len(offers) != 1 or not isinstance(offers[0], dict) or not schema_type(offers[0], "Offer"):
        return VerificationResult("parse_error")
    offer = offers[0]
    if offer.get("url") and product_key(offer["url"]) != candidate.asin:
        return VerificationResult("identity_mismatch")
    available = offer.get("availability")
    if available in ("https://schema.org/OutOfStock", "http://schema.org/OutOfStock",
                     "https://schema.org/SoldOut", "http://schema.org/SoldOut",
                     "https://schema.org/Discontinued", "http://schema.org/Discontinued"):
        return VerificationResult("unavailable")
    if available not in ("https://schema.org/InStock", "http://schema.org/InStock"):
        return VerificationResult("unknown_availability")
    if offer.get("priceCurrency") != "INR" or not paise(offer.get("price")):
        return VerificationResult("invalid_price")
    if offer.get("priceValidUntil"):
        try:
            if datetime.fromisoformat(offer["priceValidUntil"]).date() < datetime.now(timezone.utc).date():
                return VerificationResult("stale_offer")
        except (ValueError, TypeError):
            return VerificationResult("parse_error")
    title = product.get("name")
    if not isinstance(title, str) or not title.strip():
        return VerificationResult("parse_error")
    # Only explicitly labelled MRP in this Product's price specifications counts.
    # Aggregate highPrice, crossed-out recommendation prices and % badges do not.
    original = None
    specs = offer.get("priceSpecification", [])
    specs = [specs] if isinstance(specs, dict) else specs
    if isinstance(specs, list):
        mrps = {paise(spec.get("price")) for spec in specs if isinstance(spec, dict)
                and str(spec.get("name", "")).strip().lower() in ("mrp", "maximum retail price")
                and spec.get("priceCurrency") == "INR"}
        mrps.discard(None)
        if len(mrps) == 1:
            original = Decimal(mrps.pop()) / 100
    rating = product.get("aggregateRating", {})
    rating = rating.get("ratingValue") if isinstance(rating, dict) else None
    rating = fetcher.parse_rating(str(rating)) if rating is not None else None
    image = product.get("image")
    image = image[0] if isinstance(image, list) and image else image
    # Only the retailer's static image host; no arbitrary metadata URL fetching.
    if (not isinstance(image, str) or urlparse(image).scheme != "https"
            or urlparse(image).hostname != "rukminim2.flixcart.com"):
        image = None
    deal = fetcher.Deal(title.strip(), candidate.url, "Flipkart", asin=candidate.asin,
                        canonical_url=candidate.url, availability=True,
                        verified_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                        deal_price=Decimal(paise(offer["price"])) / 100, original_price=original,
                        rating=rating, image_url=image)
    deal.discount_percent = fetcher.calc_discount(original, deal.deal_price)
    deal.deal_score = fetcher.score_deal(deal)
    return VerificationResult("verified", deal)


def discover_page(source_id, url, client):
    from discovery import SourceResult, ProductCandidate, blocked_page
    result = client.get(url)
    if result.status != "successful":
        return SourceResult(source_id, result.status)
    soup = BeautifulSoup(result.response.text, "html.parser")
    if blocked_page(soup):
        return SourceResult(source_id, "blocked")
    found = {}
    for anchor in soup.select('a[href]'):
        identity = product_identity(urljoin(url, anchor["href"]))
        if identity and identity[0] == "flipkart":
            key = product_key(identity[2])
            found.setdefault(key, ProductCandidate(key, identity[2], source_ids={source_id}))
        if len(found) >= 6:
            break
    if found:
        return SourceResult(source_id, "successful", list(found.values()))
    text = soup.get_text(" ", strip=True).lower()
    empty = bool(soup.select_one('input[name="q"]') and "sorry, no results found" in text)
    return SourceResult(source_id, "empty" if empty else "parse_error")


def discover(client, links):
    from discovery import ProductCandidate, SourceResult
    sources = [SourceResult("earnkaro:registered", "successful" if links.links else "disabled",
               [ProductCandidate(key, entry.product_url, source_ids={"earnkaro:registered"})
                for key, entry in sorted(links.links.items())],
               {} if links.links else {"affiliate_unconfigured": 1})]
    for source_id, url in SEARCH_PAGES:
        try:
            sources.append(discover_page(source_id, url, client))
        except Exception:
            sources.append(SourceResult(source_id, "parse_error"))
    return sources
