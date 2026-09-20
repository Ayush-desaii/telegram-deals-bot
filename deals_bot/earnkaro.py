"""Use account-generated Profit Links; never manufacture affiliate URLs.

The registry is operator-owned, not populated from untrusted discovery pages.
A redirect check establishes product identity, not proof of commission credit.
"""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from product import product_identity, product_key, retail_url


def valid_profit_url(url):
    if not isinstance(url, str):
        return False
    try:
        p = urlparse(url)
        return bool(len(url) <= 300 and p.scheme == "https"
                    and p.hostname == "ekaro.in" and p.port in (None, 443)
                    and not p.username and not p.password and not p.query and not p.fragment
                    and re.fullmatch(r"/[A-Za-z0-9_-]{5,200}", p.path))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class ProfitLink:
    product_url: str
    profit_url: str


class EarnKaroLinks:
    def __init__(self, entries=()):
        self.links = {}
        for entry in entries:
            identity = product_identity(entry.get("product_url")) if isinstance(entry, dict) else None
            if not identity or identity[0] != "flipkart" or not valid_profit_url(entry.get("profit_url")):
                raise ValueError("Invalid EarnKaro entry: expected a Flipkart product and generated ekaro.in link")
            key = product_key(identity[2])
            link = ProfitLink(identity[2], entry["profit_url"])
            if key in self.links and self.links[key] != link:
                raise ValueError("Conflicting EarnKaro links for the same product")
            self.links[key] = link
        if len(self.links) > 100:
            raise ValueError("EarnKaro registry is limited to 100 products")

    @classmethod
    def load(cls, path, raw=None):
        try:
            data = json.loads(raw) if raw else json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(data, list):
                raise ValueError
            return cls(data)
        except (ValueError, TypeError, OSError):
            raise ValueError("Invalid EarnKaro link configuration; values withheld") from None

    def prepare(self, deal, client):
        """Check the registered link again on every final product refresh."""
        deal.affiliate_url = None
        deal.affiliate_verified_at = None
        entry = self.links.get(deal.asin)
        if not entry:
            return "affiliate_missing"
        result = client.get(entry.profit_url, method="HEAD")
        if result.status != "successful":
            return "budget_skipped" if result.status == "budget_skipped" else "affiliate_unavailable"
        if product_key(result.response.url) != product_key(retail_url(deal)):
            return "affiliate_identity_mismatch"
        deal.affiliate_url = entry.profit_url
        deal.affiliate_verified_at = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        return "ready"


def purchase_url(deal):
    identity = product_identity(retail_url(deal))
    if not identity or product_key(retail_url(deal)) != deal.asin:
        raise ValueError("Invalid purchase identity")
    if identity[0] == "amazon":
        if product_key(deal.url) != deal.asin:
            raise ValueError("Invalid Amazon purchase link")
        return deal.url
    try:
        checked = datetime.fromisoformat(deal.affiliate_verified_at).replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - checked
    except (ValueError, TypeError):
        raise ValueError("EarnKaro link has not been verified") from None
    if not valid_profit_url(deal.affiliate_url) or not timedelta(0) <= age <= timedelta(minutes=5):
        raise ValueError("EarnKaro link is missing or stale")
    return deal.affiliate_url
