"""Eligibility and ranking after verification, separate from discovery scores."""
from datetime import datetime, timezone, timedelta
import config
from product import amazon_asin, meaningful_drop, paise


def eligible(deal, db, now=None):
    now = now or datetime.now(timezone.utc)
    price = paise(deal.deal_price)
    if (not price or not deal.title or not deal.title.strip() or not deal.asin
            or amazon_asin(deal.url) != deal.asin or deal.availability is not True
            or not deal.verified_at):
        return False
    try:
        age = now - datetime.fromisoformat(deal.verified_at).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    if not timedelta(0) <= age <= timedelta(minutes=5):
        return False
    baseline, days = db.baseline(deal.asin, now)
    deal.historical_price_paise, deal.historical_days = baseline, days
    if baseline is not None:
        deal.savings_paise = baseline - price
        deal.savings_percent = deal.savings_paise * 100 / baseline
        return meaningful_drop(baseline, price)
    original = paise(deal.original_price)
    if not original or original <= price:
        return False
    deal.savings_paise = original - price
    deal.savings_percent = deal.savings_paise * 100 / original
    deal.discount_percent = deal.savings_paise * 100 // original
    return deal.discount_percent >= config.MIN_DISCOUNT_PERCENT


def ranking(deal):
    return (-(deal.historical_price_paise is not None), -deal.savings_percent,
            -deal.savings_paise, -deal.deal_score, deal.asin)
