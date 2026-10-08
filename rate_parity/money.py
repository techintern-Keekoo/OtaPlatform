"""Parse prices shown on Indian booking pages into Decimal.

Accepted: "₹ 7,906.00", "Rs. 7906", "INR 7,906", "7,906", "1,23,456", "Rs 7906/-".
A leading minus ("- ₹500", as discounts are often shown) is dropped: we
store amounts as positive numbers and the column name says what they are.
Anything else (two numbers, other currencies, "7.906,00", "Free") is rejected.
"""
from __future__ import annotations

import re
from decimal import Decimal

_CURRENCY_PREFIX = re.compile(r"^(₹|rs\.?|inr)\s*", re.IGNORECASE)
_AMOUNT = re.compile(
    r"^(\d{1,3}(,\d{2})*,\d{3}"   # Indian grouping: 1,23,456
    r"|\d{1,3}(,\d{3})+"          # Western grouping: 123,456
    r"|\d+)"                      # no grouping
    r"(\.\d{1,2})?$"
)


class MoneyError(ValueError):
    pass


def parse_money(text: str | None) -> Decimal:
    if text is None:
        raise MoneyError("no price text")
    cleaned = " ".join(text.split())
    if cleaned[:1] in ("-", "−"):
        cleaned = cleaned[1:].strip()
    cleaned = _CURRENCY_PREFIX.sub("", cleaned, count=1)
    if cleaned.endswith("/-"):
        cleaned = cleaned[:-2].strip()
    if not _AMOUNT.match(cleaned):
        raise MoneyError(f"ambiguous or unsupported amount: {text!r}")
    return Decimal(cleaned.replace(",", ""))
