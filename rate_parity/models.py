"""Plain data types shared across the package. Money is always Decimal."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date
from decimal import Decimal
from enum import Enum


class Status(str, Enum):
    VIOLATION = "VIOLATION"
    FALSE_ALARM = "FALSE_ALARM"
    IN_PARITY = "IN_PARITY"
    COULD_NOT_CHECK = "COULD_NOT_CHECK"


@dataclass(frozen=True)
class RoomKey:
    """A rate plan is only comparable if all three parts match."""
    room: str
    meal_plan: str
    cancellation: str


@dataclass(frozen=True)
class Stay:
    checkin: date
    checkout: date
    adults: int


@dataclass
class Summary:
    """Values READ from a booking summary page. Nothing here is computed."""
    final: Decimal
    room_price: Decimal | None = None
    gst: Decimal | None = None
    fees: Decimal | None = None
    discount: Decimal | None = None
    login_state: str = "unverified"
    screenshot_path: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class CheckRow:
    ota: str
    property: str
    room: str
    meal_plan: str
    cancellation: str
    checkin: str
    checkout: str
    adults: int
    login_state: str = ""
    search_price: Decimal | None = None
    checkout_room_price: Decimal | None = None
    gst: Decimal | None = None
    fees: Decimal | None = None
    discount: Decimal | None = None
    final_payable: Decimal | None = None
    website_final: Decimal | None = None
    gap_pct: Decimal | None = None
    status: Status = Status.COULD_NOT_CHECK
    note: str = ""
    screenshot_path: str = ""
    checked_at: str = ""


COLUMNS = [f.name for f in fields(CheckRow)]
