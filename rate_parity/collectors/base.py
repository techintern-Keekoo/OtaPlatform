"""Collector interface plus login-wall / CAPTCHA detection shared by all sites."""
from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from ..config import Site
from ..models import RoomKey, Stay, Summary
from ..safety import Blocked, SafePage

CAPTCHA_TEXTS = ("captcha", "verify you are human", "are you a robot", "unusual traffic", "press and hold")
LOGIN_TEXTS = ("sign in to continue", "log in to continue", "login to continue", "please sign in")


class RoomMismatch(Exception):
    """Summary page shows a different room, meal plan or cancellation policy."""


class Collector(ABC):
    def __init__(self, site: Site):
        self.site = site

    @abstractmethod
    def quick_scan(self, page: SafePage, stay: Stay) -> dict[str, Decimal]:
        """Search-page price per room id. Rooms with no price are left out."""

    @abstractmethod
    def deep_check(self, page: SafePage, stay: Stay, room_id: str) -> Summary:
        """Navigate to the booking summary, read it, screenshot, stop."""

    def check_blocked(self, page: SafePage) -> None:
        relogin = f"re-login to {self.site.label}"
        for selector in self.site.captcha_selectors:
            if page.exists(selector):
                raise Blocked(f"{self.site.label}: CAPTCHA shown", "verify manually")
        for selector in self.site.login_wall_selectors:
            if page.exists(selector):
                raise Blocked(f"{self.site.label}: login wall", relogin)
        text = page.body_text().casefold()
        if any(t in text for t in CAPTCHA_TEXTS + tuple(x.casefold() for x in self.site.block_texts)):
            raise Blocked(f"{self.site.label}: CAPTCHA/block text on page", "verify manually")
        if any(t in text for t in LOGIN_TEXTS):
            raise Blocked(f"{self.site.label}: login wall text on page", relogin)

    def login_state(self, page: SafePage) -> str:
        marker = self.site.logged_in_marker
        if marker is None:
            return "unverified"
        if not page.exists(marker):
            raise Blocked(f"{self.site.label}: not logged in", f"re-login to {self.site.label}")
        return "logged_in"
