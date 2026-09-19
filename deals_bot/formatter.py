"""Evidence-based, bounded Telegram HTML assembled from escaped values."""
from html import escape
from decimal import Decimal
from fetcher import Deal


def fmt_price(price):
    return f"₹{Decimal(str(price)):,.2f}"


def format_deal_message(deal: Deal) -> str:
    if deal.historical_price_paise is not None:
        heading = f"{deal.savings_percent:.1f}% below observed price"
        comparison = (f"Observed comparison: {fmt_price(Decimal(deal.historical_price_paise) / 100)}"
                      f" ({deal.historical_days} days sampled in the prior 30 days)")
    else:
        heading = f"{deal.discount_percent}% off MRP"
        comparison = f"MRP: {fmt_price(deal.original_price)}"
    # Bound each external field before escaping; never cut serialized HTML.
    title = escape(deal.title[:60])
    lines = [f"<b>{heading}</b>", "", f"<b>{title}</b>",
             f"Price: <b>{fmt_price(deal.deal_price)}</b>", comparison,
             f"Save {fmt_price(Decimal(deal.savings_paise) / 100)}"]
    if deal.rating:
        lines.append(f"Rating: {escape(str(deal.rating)[:5])}/5")
    lines.extend(["", f'<a href="{escape(deal.url, quote=True)}">Buy on Amazon →</a>',
                  f"Checked: {escape(str(deal.verified_at)[:19])} UTC",
                  "Price and availability may change.",
                  "Affiliate link: we may earn from qualifying purchases.", "#AmazonIndia #Deals"])
    result = "\n".join(lines)
    if len(result.encode("utf-16-le")) // 2 > 1024:
        raise ValueError("Caption exceeds Telegram limit")
    return result


def format_text_only_message(deal: Deal) -> str:
    return format_deal_message(deal)
