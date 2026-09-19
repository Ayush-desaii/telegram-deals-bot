"""Identity-only discovery and reasoned verification within one network budget."""
from dataclasses import dataclass, field
import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import feedparser
import requests

import fetcher
from product import amazon_asin, paise

SOURCE_ERRORS = {"blocked", "timeout", "network_error", "http_error", "parse_error",
                 "identity_mismatch", "invalid_price", "unknown_availability"}


@dataclass
class ProductCandidate:
    asin: str
    url: str
    metadata: dict = field(default_factory=dict)
    source_ids: set[str] = field(default_factory=set)

    def __post_init__(self):
        if not self.asin or amazon_asin(self.url) != self.asin:
            raise ValueError("Invalid product identity")
        self.url = f"https://www.amazon.in/dp/{self.asin}"

    @classmethod
    def from_deal(cls, deal, source_id="legacy"):
        return cls(amazon_asin(deal.url), deal.url,
                   {"title": deal.title, "deal_price": deal.deal_price,
                    "original_price": deal.original_price}, {source_id})


@dataclass
class SourceResult:
    source_id: str
    status: str
    candidates: list[ProductCandidate] = field(default_factory=list)
    issues: dict[str, int] = field(default_factory=dict)


@dataclass
class VerificationResult:
    reason: str
    deal: fetcher.Deal | None = None


@dataclass
class HttpResult:
    status: str
    response: object = None


class Budget:
    def __init__(self, seconds=900, clock=None, sleeper=None):
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        self.deadline = self.clock() + seconds

    def remaining(self):
        return max(0, self.deadline - self.clock())

    def sleep(self, seconds):
        self.sleeper(min(seconds, self.remaining()))


class HttpClient:
    def __init__(self, budget, session=None):
        self.budget = budget
        self.session = session or fetcher.make_session()

    def get(self, url, method="GET"):
        status = "network_error"
        for attempt in range(2):
            remaining = self.budget.remaining()
            if remaining < 0.1:
                return HttpResult("budget_skipped")
            timeout = min(10, remaining / 2)
            response = None
            try:
                response = self.session.request(method, url, timeout=(timeout, timeout),
                                                stream=True, allow_redirects=True)
                if self.budget.remaining() <= 0:
                    return HttpResult("budget_skipped")
                if response.status_code in (403, 429):
                    return HttpResult("blocked")
                if response.status_code != 200:
                    status = "http_error"
                else:
                    body = bytearray()
                    if method != "HEAD":
                        for chunk in response.iter_content(16384):
                            if self.budget.remaining() <= 0:
                                return HttpResult("budget_skipped")
                            body.extend(chunk)
                            if len(body) > 8 * 1024 * 1024:
                                return HttpResult("parse_error")
                    response._content = bytes(body)
                    response._content_consumed = True
                    return HttpResult("successful", response)
            except requests.Timeout:
                status = "timeout"
            except requests.RequestException:
                status = "network_error"
            finally:
                if response is not None:
                    response.close()
            if attempt == 0:
                self.budget.sleep(1)
        return HttpResult("budget_skipped" if self.budget.remaining() <= 0 else status)


def blocked_page(soup):
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    return bool(soup.select_one("#captchacharacters, form[action*='validateCaptcha']")
                or "robot check" in title or "access denied" in title)


def discover_source(source_id, url, kind, client):
    result = client.get(url)
    if result.status != "successful":
        return SourceResult(source_id, result.status)
    response = result.response
    soup = BeautifulSoup(response.text, "html.parser")
    if blocked_page(soup):
        return SourceResult(source_id, "blocked")
    candidates = {}
    issues = {}

    def add(link, metadata=None):
        asin = amazon_asin(urljoin(url, link))
        if asin:
            candidates.setdefault(asin, ProductCandidate(asin, f"https://www.amazon.in/dp/{asin}",
                                                         metadata or {}, {source_id}))

    if kind == "search":
        cards = soup.select('div[data-asin]')
        for card in cards:
            asin = card.get("data-asin", "")
            if not re.fullmatch(r"[A-Z0-9]{10}", asin):
                continue
            title = card.select_one("h2")
            price = card.select_one(".a-price:not(.a-text-price) .a-offscreen")
            original = card.select_one(".a-price.a-text-price .a-offscreen")
            add(f"https://www.amazon.in/dp/{asin}", {
                "title": title.get_text(" ", strip=True) if title else "",
                "deal_price": fetcher.parse_price(price.get_text()) if price else None,
                "original_price": fetcher.parse_price(original.get_text()) if original else None,
            })
            if len(candidates) >= 6:
                break
        text = soup.get_text(" ", strip=True).lower()
        empty = bool(soup.select_one(".s-main-slot, #search") and
                     ("no results for" in text or re.search(r"\b0 results\b", text)))
    elif kind == "bestseller":
        cards = (soup.select("div.zg-item-immersion") or soup.select("li.zg-item") or
                 soup.select("div[class*='p13n-sc-uncoverable-faceout']"))
        for card in cards:
            asin = card.get("data-asin", "")
            if re.fullmatch(r"[A-Z0-9]{10}", asin):
                add(f"https://www.amazon.in/dp/{asin}")
            else:
                link = card.select_one("a[href*='/dp/']")
                if link:
                    add(link["href"])
            if len(candidates) >= 3:
                break
        empty = False  # Missing bestseller cards is not evidence of an empty catalogue.
    else:
        feed = feedparser.parse(response.content)
        if not feed.get("version") or (feed.bozo and not feed.entries):
            return SourceResult(source_id, "parse_error")
        for entry in feed.entries[:10]:
            content = entry.get("summary", "") + (entry.get("content") or [{}])[0].get("value", "")
            for link in BeautifulSoup(content, "html.parser").select("a[href]"):
                href = link["href"]
                if urlparse(href).hostname in ("amzn.to", "amzn.in"):
                    resolved = client.get(href, method="HEAD")
                    if resolved.status != "successful":
                        issues[resolved.status] = issues.get(resolved.status, 0) + 1
                        continue
                    href = resolved.response.url
                add(href)
        empty = not issues
    status = "successful" if candidates else "empty" if empty else "parse_error"
    if not candidates and issues:
        status = next(iter(issues))
    return SourceResult(source_id, status, list(candidates.values()), issues)


def discover(client):
    results = []
    sources = [(f"amazon_search:{category}", url, "search") for url, category in fetcher.DEAL_SEARCH_PAGES]
    sources += [(f"amazon_bestseller:{category}", url, "bestseller") for url, category in fetcher.BESTSELLER_PAGES[:3]]
    sources += [(f"reddit:{name}", url, "reddit") for url, name in fetcher.RSS_FEEDS_LIST]
    for source_id, url, kind in sources:
        try:
            result = discover_source(source_id, url, kind, client)
        except Exception:
            # No raw exception text, URLs, headers or credentials in health reports.
            result = SourceResult(source_id, "parse_error")
        results.append(result)
        print(f"SOURCE {source_id}: {result.status} ({len(result.candidates)} products)")
    # The optional legacy API is not part of the active, bounded discovery pipeline.
    results.append(SourceResult("amazon_api", "disabled"))
    return results


def verify_candidate(candidate, client):
    response = client.get(candidate.url)
    if response.status != "successful":
        return VerificationResult(response.status)
    soup = BeautifulSoup(response.response.text, "html.parser")
    if blocked_page(soup):
        return VerificationResult("blocked")
    if amazon_asin(response.response.url) != candidate.asin:
        return VerificationResult("identity_mismatch")
    identities = []
    node = soup.select_one("input#ASIN")
    if node:
        identities.append(node.get("value"))
    node = soup.select_one("link[rel='canonical']")
    if node:
        identities.append(amazon_asin(node.get("href", "")))
    if not identities:
        return VerificationResult("parse_error")
    if any(value != candidate.asin for value in identities):
        return VerificationResult("identity_mismatch")
    availability = soup.select_one("#availability")
    text = availability.get_text(" ", strip=True).lower() if availability else ""
    if any(value in text for value in ("unavailable", "out of stock", "not in stock", "temporarily")):
        return VerificationResult("unavailable")
    if not re.search(r"\bin stock\b", text):
        return VerificationResult("unknown_availability")
    details = fetcher.scrape_product_page(candidate.url, response=response.response)
    if not details:
        return VerificationResult("parse_error")
    if not paise(details.get("deal_price")):
        return VerificationResult("invalid_price")
    fresh = fetcher.Deal(
        title=details["title"], url=fetcher.make_affiliate_url(candidate.asin), source="Amazon India",
        asin=candidate.asin, availability=True, verified_at=details["verified_at"],
        **{key: details.get(key) for key in ("deal_price", "original_price", "image_url",
                                           "rating", "rating_count", "category")},
    )
    fresh.discount_percent = fetcher.calc_discount(fresh.original_price, fresh.deal_price)
    fresh.deal_score = fetcher.score_deal(fresh)
    return VerificationResult("verified", fresh)


def select_candidates(due, discoveries, tracked):
    """Preserve due ordering, share provenance, and honour not-due backoff."""
    merged = {}
    for row in due:
        merged[row["asin"]] = ProductCandidate(row["asin"], row["url"],
                                                {"title": row["title"]}, {"watchlist"})
    discovered_order = []
    for candidate in discoveries:
        if candidate.asin not in discovered_order:
            discovered_order.append(candidate.asin)
        if candidate.asin in merged:
            merged[candidate.asin].source_ids.update(candidate.source_ids)
            merged[candidate.asin].metadata.update(candidate.metadata)
        else:
            merged[candidate.asin] = candidate
    watched = [merged[row["asin"]] for row in due]
    new = [merged[asin] for asin in discovered_order if asin not in tracked]
    selected = watched[:20] + new[:10]
    selected += (watched[20:] + new[10:])[:30 - len(selected)]
    chosen = {candidate.asin for candidate in selected}
    deferred = {asin: ("not_due" if asin in tracked and asin not in {r["asin"] for r in due}
                       else "budget_skipped") for asin in merged if asin not in chosen}
    return selected, merged, deferred
