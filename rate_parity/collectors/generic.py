"""Collector driven entirely by per-site config. No site-specific code here."""
from __future__ import annotations

import logging
import re
from decimal import Decimal

from ..compare import room_matches
from ..models import RoomKey, Stay, Summary
from ..money import MoneyError, parse_money
from ..safety import SafePage, SelectorMissing
from .base import Collector, RoomMismatch

log = logging.getLogger(__name__)

RETRY_WAIT_S = 20  # seconds to wait before reloading a page whose room list did not appear


class GenericCollector(Collector):
    def load(self, page: SafePage, stay: Stay, attempts: int = 2) -> None:
        """Open the search page and wait until the room list is really there.

        OTAs load the room list late. Scroll, bring the list heading on screen,
        wait for a room box; if it never comes, reload once, then give up with
        a clear reason instead of reading an empty page.
        """
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                page.wait_seconds(RETRY_WAIT_S)  # a quick second visit can get the same empty page
            page.goto(self.site.search_url_for(stay))
            self.check_blocked(page)
            if self.site.scroll_to_load:
                page.scroll_through()
            if self.site.scroll_to:
                page.scroll_into_view(self.site.scroll_to)
            if not self.site.wait_for:
                return
            try:
                page.wait_for(self.site.wait_for)
                found = page.scroll_until_stable(self.site.wait_for)  # rooms render one by one
                log.info("%s: %d room boxes loaded", self.site.key, found)
                return
            except SelectorMissing:
                log.warning("%s: room list not loaded (attempt %d of %d)", self.site.key, attempt, attempts)
        raise SelectorMissing(f"{self.site.label}: room list did not load (tried {attempts} times); "
                              f"{self._failed_page_evidence(page, stay)}")

    def _failed_page_evidence(self, page: SafePage, stay: Stay) -> str:
        """Keep what the site showed instead of rooms (sold out? blocked? error?) so a person can see why."""
        name = f"load_failed_{stay.checkin.isoformat()}"
        try:
            shot, text_file = page.screenshot(name), page.save_text(name)
            seen = " ".join(page.body_text().split())[:160]
        except Exception as exc:  # the page may be gone; the failure itself is already reported
            return f"no evidence saved ({type(exc).__name__})"
        return f"page showed: '{seen}' - see {shot} and {text_file}"

    def quick_scan(self, page: SafePage, stay: Stay) -> dict[str, Decimal]:
        self.load(page, stay)
        prices, self.offer_types, self.card_taxes, self.evidence = {}, {}, {}, ""
        for room_id, room in self.site.rooms.items():
            if room.price_text:
                found = read_price_text(page, room.price_text)
                if found:
                    prices[room_id], tax, self.offer_types[room_id] = found
                    if tax is not None:  # price AND taxes on the list: final known, no second visit
                        self.card_taxes[room_id] = tax
                else:
                    log.info("%s %s: no price on page (sold out or not listed?)", self.site.key, room_id)
                continue
            for offer, selector in room.price_options or (("", room.search_price),):
                text = page.read_text(selector)
                if text is None:
                    continue  # this offer type is not on the page; try the next
                try:
                    prices[room_id] = parse_money(text)
                    self.offer_types[room_id] = offer
                    break
                except MoneyError as exc:
                    log.info("%s %s: unreadable price %r (%s)", self.site.key, room_id, text, exc)
            if room_id not in prices:
                log.info("%s %s: no price on page (sold out or not listed?)", self.site.key, room_id)
        if self.card_taxes:  # evidence for finals decided from this one page load
            self.evidence = page.screenshot(f"list_{stay.checkin.isoformat()}")
        # No price at all: a full house (Agoda prints "Sold out!") is not a broken site.
        self.page_says_sold_out = not prices and _says_sold_out(page.body_text(), len(self.site.rooms))
        return prices

    def deep_check(self, page: SafePage, stay: Stay, room_id: str) -> Summary:
        room = self.site.rooms[room_id]
        fields = room.summary
        self.load(page, stay)
        login_state = self.login_state(page)
        if room.price_text:  # price AND taxes are on the room card: no clicks needed
            found = read_price_text(page, room.price_text)
            if not found or found[1] is None:
                raise SelectorMissing(f"{self.site.label}: price and taxes not found on the {room_id} card")
            price, tax, offer = found
            return Summary(final=price + tax, room_price=price, gst=tax, login_state=login_state,
                           screenshot_path=page.screenshot(f"{room_id}_{stay.checkin.isoformat()}"),
                           notes=[f"final = price + taxes & fees, both read from the room card ({offer or 'offer type not shown'})"])
        for step in room.steps:
            page.click_step(step)
            self.check_blocked(page)

        page.wait_for(fields.ready)
        screenshot = page.screenshot(f"{room_id}_{stay.checkin.isoformat()}")
        observed = RoomKey(
            room=self._required_text(page, fields.room_name),
            meal_plan=self._required_text(page, fields.meal_plan),
            cancellation=self._required_text(page, fields.cancellation),
        )
        if not room_matches(room.labels, observed):
            raise RoomMismatch(f"summary shows {observed}, expected {room.labels}")

        summary = Summary(final=Decimal(0), login_state=login_state, screenshot_path=screenshot)
        for name in ("room_price", "gst", "fees", "discount"):
            selector = getattr(fields, name)
            if selector is None:
                continue
            text = page.read_text(selector)
            if text is None:
                summary.notes.append(f"{name} not shown")
                continue
            setattr(summary, name, parse_money(text))
        if fields.final is not None:
            summary.final = parse_money(self._required_text(page, fields.final))
        elif summary.room_price is None or summary.gst is None:
            raise SelectorMissing("page shows no total, and room price or taxes were not found")
        else:  # page shows price and taxes separately (e.g. Booking.com room table); add what it shows
            summary.final = (summary.room_price + summary.gst + (summary.fees or 0) - (summary.discount or 0))
            summary.notes.append("final = room price + taxes as shown on page")
        return summary

    @staticmethod
    def _required_text(page: SafePage, selector: str) -> str:
        text = page.read_text(selector)
        if text is None:
            raise SelectorMissing(f"summary selector not found: {selector}")
        return text


FULL_HOUSE_TEXTS = ("no rooms available", "fully booked", "no availability", "not available for your dates")


def _says_sold_out(text: str, rooms: int) -> bool:
    """Whole property sold out. Per-room "Sold out" must appear once per tracked
    room: two sold-out cards on a page whose prices we failed to read is still broken."""
    lowered = text.casefold()
    return any(phrase in lowered for phrase in FULL_HOUSE_TEXTS) or lowered.count("sold out") >= rooms


OFFER_WORDS = (("non-refundable", "non-refundable"), ("free cancellation", "free cancellation"),
               ("cancellation policy", "cancellation policy"))


def read_price_text(page: SafePage, spec: tuple[str, str]):
    """(price, tax or None, offer type) from a room card's visible text, or None.

    Only numbers that literally appear in the card text are used.
    """
    container, pattern = spec
    text = page.read_text(container)
    if not text:
        return None
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    price = parse_money(match.group("price"))
    tax = parse_money(match.group("tax")) if "tax" in match.re.groupindex and match.group("tax") else None
    lowered = text.casefold()
    offer = next((name for word, name in OFFER_WORDS if word in lowered), "")
    return price, tax, offer
