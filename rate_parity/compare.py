"""Pure comparison rules. Decimal in, Decimal/Status out. No I/O."""
from __future__ import annotations

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


def is_suspect(ota_search: Decimal, website_search: Decimal) -> bool:
    """Stage 1: the OTA search price is strictly below the website's."""
    _require_decimal(ota_search, website_search)
    return ota_search < website_search


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
    tolerance_pct: Decimal,
    suspect: bool = True,
) -> Status:
    """VIOLATION if the OTA is cheaper by MORE than tolerance_pct.

    A suspect that is not a violation at checkout is a FALSE_ALARM.
    """
    if ota_final is None or website_final is None:
        return Status.COULD_NOT_CHECK
    _require_decimal(tolerance_pct)
    if _raw_gap_pct(ota_final, website_final) < -tolerance_pct:
        return Status.VIOLATION
    return Status.FALSE_ALARM if suspect else Status.IN_PARITY
