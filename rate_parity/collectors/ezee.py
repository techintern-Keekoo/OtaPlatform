"""Keekoo website prices from the eZee booking engine, without a browser.

The website's own "Search" button sends one read-only request:
    POST <base>/roomlisting.php  HotelId=<hotel>&checkIn=DD_MM_YYYY&checkOut=DD_MM_YYYY&isroomsearch=1
and the reply holds one JSON record per room + rate plan, with the price
before tax, the tax and the total incl. tax. We only READ those numbers:
nothing is added to a cart, no guest details, no booking.

Plan chosen per room: refundable (Prepaid_Noncancel_Nonrefundable == 0),
bookable for this stay (showMinmsg == 0, i.e. no "need N more nights"),
lowest total. Verified against the live site on 2026-10-08.

Like for like: every candidate plan's numbers must add up (before tax + tax
= total, within Rs 1), else the reply is refused: if they do not, we no
longer know which number is which. No meal-plan field has been seen in the
reply, so the chosen plan's name goes into the row note for a human to audit.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

import requests

from ..models import Stay, Summary
from ..safety import DEFAULT_COMMIT_PATH_PATTERNS, NetworkGuard, SafetyViolation, SelectorMissing, check_url_allowed
from .base import Collector

log = logging.getLogger(__name__)

TIMEOUT = (10, 30)  # connect, read seconds
SEARCH_PATH = "/booking/roomlisting.php"
_RECORD_START = re.compile(r'\{"RateTypeId"')
ADD_UP_TOLERANCE = Decimal("1")  # rupees: before tax + tax may miss the total by rounding only


class EzeeCollector(Collector):
    needs_browser = False

    def __init__(self, site, session=None, evidence_dir: Path = Path("screenshots")):
        super().__init__(site)
        self._session = session
        self._evidence_dir = Path(evidence_dir)
        self._cache: dict[Stay, list[dict]] = {}
        self._evidence: dict[Stay, str] = {}
        self.room_errors: dict[str, str] = {}  # room id -> why its price was refused (shown on its row)
        # Same rules as the browser guard; the search path is the only exemption.
        self._guard = NetworkGuard(DEFAULT_COMMIT_PATH_PATTERNS, (), [SEARCH_PATH])

    def quick_scan(self, page, stay: Stay) -> dict[str, Decimal]:
        prices = {}
        self._records(stay)  # a failed search or a wrong guest count still stops the whole stay
        self.room_errors = {}
        for room_id in self.site.rooms:
            try:
                plan = self._best_plan(stay, room_id)
            except SelectorMissing as exc:  # one odd room must not black out the other four
                log.error("%s", exc)
                self.room_errors[room_id] = str(exc)
                continue
            if plan is not None:
                prices[room_id] = _money(plan["TotalPrice_ExclusiveAll"])
        return prices

    def deep_check(self, page, stay: Stay, room_id: str) -> Summary:
        plan = self._best_plan(stay, room_id)
        if plan is None:
            raise SelectorMissing(f"website: no refundable 1-stay plan for {room_id} (sold out?)")
        return Summary(
            final=_money(plan["TotalPrice_InclusiveAll"]),
            room_price=_money(plan["TotalPrice_ExclusiveAll"]),
            gst=_money(plan["TaxRate"]),
            login_state="guest",
            screenshot_path=self._evidence.get(stay, ""),
            notes=[_plan_note(plan)],
        )

    # --- internals ---------------------------------------------------------

    def _best_plan(self, stay: Stay, room_id: str) -> dict | None:
        type_id = self.site.rooms[room_id].ezee_room_type
        plans = [
            r for r in self._records(stay)
            if str(r.get("RoomTypeUnkId")) == type_id
            and int(r.get("Prepaid_Noncancel_Nonrefundable", 1)) == 0
            and int(r.get("showMinmsg", 1)) == 0
        ]
        for plan in plans:
            _check_adds_up(plan, room_id)
        return min(plans, key=lambda r: _money(r["TotalPrice_InclusiveAll"]), default=None)

    def _records(self, stay: Stay) -> list[dict]:
        if stay not in self._cache:
            html = self._search(stay)
            records = _parse_records(html)
            if not records:
                raise SelectorMissing("website: eZee rate data not found in search reply")
            _check_adults(records, stay)
            self._cache[stay] = records
            self._evidence[stay] = self._save_evidence(stay, html)
        return self._cache[stay]

    def _search(self, stay: Stay) -> str:
        session = self._session or requests.Session()
        session.headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129 Safari/537.36")
        base = self.site.search_url.split("/booking/")[0] + "/booking"
        page_url, search_url = self.site.search_url, base + "/roomlisting.php"
        body = {
            "HotelId": self.site.ezee_hotel,
            "checkIn": stay.checkin.strftime("%d_%m_%Y"),
            "checkOut": stay.checkout.strftime("%d_%m_%Y"),
            "isroomsearch": "1",
        }
        for url in (page_url, search_url):
            check_url_allowed(url, self.site.allowed_domains)
        reason = self._guard.block_reason(search_url, "POST", urlencode(body))
        if reason:
            raise SafetyViolation(f"website search refused: {reason}")
        session.get(page_url, timeout=TIMEOUT).raise_for_status()  # starts the engine's session
        reply = session.post(search_url, data=body, timeout=TIMEOUT)
        reply.raise_for_status()
        return reply.text

    def _save_evidence(self, stay: Stay, html: str) -> str:
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self._evidence_dir / f"website_{stay.checkin.isoformat()}_{stamp}.html"
        path.write_text(html, encoding="utf-8")
        return str(path)


def _plan_note(plan: dict) -> str:
    """Plan name plus the room description, which ends in the meal plan (live, 9 Oct 2026:
    "Standard garden view room EP", EP = room only), so a person can audit like-for-like."""
    description = " ".join(str(plan.get("Room_Description") or "").split())
    name = f"website plan: {plan.get('Room_Name', '?')}"
    return f"{name} ({description})" if description else name


def _parse_records(html: str) -> list[dict]:
    decoder, records = json.JSONDecoder(), []
    for match in _RECORD_START.finditer(html):
        try:
            record, _ = decoder.raw_decode(html, match.start())
        except ValueError:
            continue
        if isinstance(record, dict) and "TotalPrice_InclusiveAll" in record:
            records.append(record)
    return records


def _check_adults(records: list[dict], stay: Stay) -> None:
    """The engine searches with its default guests; refuse if that is not our stay."""
    raw = records[0].get("default_pax", "")
    try:
        adults = json.loads(base64.b64decode(raw))["room_1"]["adult"]
    except (ValueError, KeyError, TypeError):
        raise SelectorMissing("website: cannot read the guest count the engine searched for") from None
    if int(adults) != stay.adults:
        raise SelectorMissing(f"website searched for {adults} adults, stay needs {stay.adults}")


def _check_adds_up(plan: dict, room_id: str) -> None:
    """Before tax + tax must equal the total (within Rs 1), else refuse to guess."""
    name = plan.get("Room_Name", "?")
    try:
        before, tax, total = (_money(plan[k]) for k in ("TotalPrice_ExclusiveAll", "TaxRate", "TotalPrice_InclusiveAll"))
    except (KeyError, ArithmeticError, TypeError, ValueError):
        raise SelectorMissing(f"website numbers do not add up for {room_id}: price, tax or total "
                              f"missing in plan {name!r}") from None
    if abs(before + tax - total) > ADD_UP_TOLERANCE:
        raise SelectorMissing(f"website numbers do not add up for {room_id}: {before} + tax {tax} "
                              f"!= total {total} (plan {name!r}); the engine reply may have changed")


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))
