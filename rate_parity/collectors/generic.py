"""Collector driven entirely by per-site config. No site-specific code here."""
from __future__ import annotations

import logging
from decimal import Decimal

from ..compare import room_matches
from ..models import RoomKey, Stay, Summary
from ..money import MoneyError, parse_money
from ..safety import SafePage, SelectorMissing
from .base import Collector, RoomMismatch

log = logging.getLogger(__name__)


class GenericCollector(Collector):
    def quick_scan(self, page: SafePage, stay: Stay) -> dict[str, Decimal]:
        page.goto(self.site.search_url_for(stay))
        self.check_blocked(page)
        if self.site.scroll_to_load:
            page.scroll_through()
        prices, self.offer_types = {}, {}
        for room_id, room in self.site.rooms.items():
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
        return prices

    def deep_check(self, page: SafePage, stay: Stay, room_id: str) -> Summary:
        room = self.site.rooms[room_id]
        fields = room.summary
        page.goto(self.site.search_url_for(stay))
        self.check_blocked(page)
        if self.site.scroll_to_load:
            page.scroll_through()
        login_state = self.login_state(page)
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
