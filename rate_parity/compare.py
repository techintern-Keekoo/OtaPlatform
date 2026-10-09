"""Pure comparison rules. Decimal in, Decimal/Status out. No I/O."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .models import RoomKey, Status

_HUNDRED = Decimal("100")
_CENT = Decimal("0.01")


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def room_matches(expected: RoomKey, observed: RoomKey) -> bool:
    """True only if room, meal plan AND cancellation policy all match.

    Each expected label must appear in the text read from the page
    (case/whitespace-insensitive). An empty label never matches.
    """
    pairs = [
        (expected.room, observed.room),
        (expected.meal_plan, observed.meal_plan),
        (expected.cancellation, observed.cancellation),
    ]
    for want, seen in pairs:
        want, seen = _normalize(want or ""), _normalize(seen or "")
        if not want or want not in seen:
            return False
    return True


def _require_decimal(*values: Decimal) -> None:
    for value in values:
        if not isinstance(value, Decimal):
            raise TypeError(f"expected Decimal, got {type(value).__name__}")


def is_suspect(ota_search: Decimal, website_search: Decimal, min_margin_pct: Decimal = Decimal(0)) -> bool:
    """Stage 1: the OTA search price is NOT above the website's by more than the margin."""
    _require_decimal(ota_search, website_search, min_margin_pct)
    return ota_search <= website_search * (1 + min_margin_pct / _HUNDRED)


def _raw_gap_pct(ota_final: Decimal, website_final: Decimal) -> Decimal:
    _require_decimal(ota_final, website_final)
    if website_final <= 0:
        raise ValueError("website_final must be positive")
    return (ota_final - website_final) / website_final * _HUNDRED


def gap_pct(ota_final: Decimal, website_final: Decimal) -> Decimal:
    """(ota - website) / website, in percent, rounded to 2 places. Negative = OTA cheaper."""
    return _raw_gap_pct(ota_final, website_final).quantize(_CENT, rounding=ROUND_HALF_UP)


def decide_status(
    ota_final: Decimal | None,
    website_final: Decimal | None,
    min_margin_pct: Decimal,
    suspect: bool = True,
) -> Status:
    """Keekoo's rule: the website (Zen) must be CHEAPER than every OTA.

    VIOLATION if the OTA final is not above the website final by more than
    min_margin_pct (0 = any OTA price equal to or below Zen's is a violation).
    A suspect that turns out fine at checkout is a FALSE_ALARM.
    """
    if ota_final is None or website_final is None:
        return Status.COULD_NOT_CHECK
    _require_decimal(min_margin_pct)
    if _raw_gap_pct(ota_final, website_final) <= min_margin_pct:
        return Status.VIOLATION
    return Status.FALSE_ALARM if suspect else Status.IN_PARITY


@dataclass(frozen=True)
class SanityLimits:
    """How far an OTA price may sit from Zen's before we suspect a misread.

    A real OTA price is rarely below 40% or above 3x Zen's for the same room:
    numbers outside that are more likely an old (struck-out) price, a per-stay
    total, a "from" price or a bad parse. Taxes above 30% of the price are
    not Indian hotel GST (5-18%) plus normal fees.
    """
    min_ratio: Decimal = Decimal("0.4")
    max_ratio: Decimal = Decimal("3.0")
    max_tax_pct: Decimal = Decimal("30")


def implausible_reason(
    ota_price: Decimal,
    zen_price: Decimal,
    ota_tax: Decimal | None = None,
    limits: SanityLimits = SanityLimits(),
) -> str | None:
    """Why these numbers look like a misread, or None if they look normal.

    ota_price and zen_price must be on the same basis (both before tax, or
    both incl. tax). ota_tax, when given, is checked against ota_price.
    """
    _require_decimal(ota_price, zen_price)
    if zen_price <= 0:
        return f"Zen price {zen_price} is not positive"
    if ota_price <= 0:
        return f"OTA price {ota_price} is not positive"
    ratio = ota_price / zen_price
    if not limits.min_ratio <= ratio <= limits.max_ratio:
        return (f"OTA {ota_price:,.0f} is {ratio * _HUNDRED:.0f}% of Zen {zen_price:,.0f}; "
                f"expected {limits.min_ratio * _HUNDRED:.0f}-{limits.max_ratio * _HUNDRED:.0f}%")
    if ota_tax is not None:
        _require_decimal(ota_tax)
        tax_pct = ota_tax / ota_price * _HUNDRED
        if not 0 <= tax_pct <= limits.max_tax_pct:
            return (f"OTA taxes {ota_tax:,.0f} are {tax_pct:.0f}% of the OTA price {ota_price:,.0f}; "
                    f"expected 0-{limits.max_tax_pct:.0f}%")
    return None
