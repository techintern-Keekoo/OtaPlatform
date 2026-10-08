"""The NO-BOOKING guard. Every collector touches the browser only through SafePage.

Layers (any one of them is enough to stop a booking):
1. SafePage exposes no fill/type/press/keyboard/evaluate, so no guest or card
   details can ever be entered and no booking form can be completed.
2. click_step only clicks selectors explicitly configured as navigation steps,
   and refuses any element whose label looks like pay/confirm/submit/etc.
3. Navigation is https-only and limited to the site's domain allowlist.
4. NetworkGuard aborts every request to payment gateways and every non-GET
   request to booking-commit URLs.
5. self_check() refuses to run unless the guard is installed and
   BOOKING_ALLOWED is False.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

BOOKING_ALLOWED = False  # Never change. A test asserts this.

DENY_LABEL = re.compile(
    r"\b(pay|payment|confirm|complete (your )?booking|place order"
    r"|book (now )?(and|&) pay|submit|purchase|checkout now|proceed to pay"
    r"|card|upi|wallet)",
    re.IGNORECASE,
)

# Always blocked, whatever config says. Config can only add to this list.
PAYMENT_HOST_KEYWORDS = (
    "razorpay", "payu", "paytm", "stripe", "adyen", "juspay", "ccavenue",
    "billdesk", "cashfree", "checkout.com", "braintree", "paypal",
)

# Default for config `network.commit_path_patterns` (lower-case path substrings).
DEFAULT_COMMIT_PATH_PATTERNS = (
    "/book", "/confirm", "/payment", "/pay/", "/reservation/create",
    "/checkout/complete", "/order",
)


class SafetyViolation(Exception):
    """The agent was about to do something that could book or pay. Stop."""


class Blocked(Exception):
    """Login wall or CAPTCHA. A human must act; `flag` says what to do."""

    def __init__(self, message: str, flag: str):
        super().__init__(message)
        self.flag = flag


class SelectorMissing(Exception):
    """A configured selector was not found on the page."""


def is_denied_label(text: str | None) -> bool:
    return bool(text) and bool(DENY_LABEL.search(" ".join(text.split())))


def host_allowed(host: str | None, allowed_domains: Iterable[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    for domain in allowed_domains:
        domain = domain.lower()
        if host == domain or host.endswith("." + domain):
            return True
    return False


def check_url_allowed(url: str, allowed_domains: Iterable[str]) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise SafetyViolation(f"refusing non-https URL: {parts.scheme}://{parts.hostname}")
    if not host_allowed(parts.hostname, allowed_domains):
        raise SafetyViolation(f"refusing URL outside allowlist: {parts.hostname}")


class SafePage:
    """Read-only-ish view of a Playwright page: goto, read, screenshot, click_step."""

    def __init__(
        self,
        page,
        site: str,
        allowed_domains: Iterable[str],
        allowed_steps: Iterable[str],
        screenshot_dir: Path,
        timeout_ms: int = 30000,
        pause: Callable[[], None] = lambda: None,
    ):
        self._page = page
        self._site = site
        self._domains = tuple(allowed_domains)
        self._steps = frozenset(allowed_steps)
        self._screenshot_dir = Path(screenshot_dir)
        self._timeout_ms = timeout_ms
        self._pause = pause

    def goto(self, url: str) -> None:
        check_url_allowed(url, self._domains)
        self._pause()
        self._page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        check_url_allowed(self._page.url, self._domains)  # catch redirects

    def exists(self, selector: str) -> bool:
        return self._page.locator(selector).count() > 0

    def wait_for(self, selector: str) -> None:
        try:
            self._page.locator(selector).first.wait_for(state="visible", timeout=self._timeout_ms)
        except Exception as exc:
            raise SelectorMissing(f"{self._site}: selector not found: {selector}") from exc

    def read_text(self, selector: str) -> str | None:
        locator = self._page.locator(selector)
        if locator.count() == 0:
            return None
        return locator.first.inner_text(timeout=self._timeout_ms)

    def body_text(self) -> str:
        return self.read_text("body") or ""

    def screenshot(self, name: str) -> str:
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{self._site}_{name}_{stamp}")
        path = self._screenshot_dir / f"{safe_name}.png"
        self._page.screenshot(path=str(path), full_page=True)
        return str(path)

    def click_step(self, selector: str) -> None:
        if BOOKING_ALLOWED:
            raise SafetyViolation("BOOKING_ALLOWED is set; refusing to click anything")
        if selector not in self._steps:
            raise SafetyViolation(f"{self._site}: selector is not a configured step: {selector}")
        self.wait_for(selector)
        element = self._page.locator(selector).first
        for label in self._labels(element):
            if is_denied_label(label):
                raise SafetyViolation(f"{self._site}: refusing to click {label!r} ({selector})")
        self._pause()
        element.click(timeout=self._timeout_ms)
        self._page.wait_for_load_state("domcontentloaded", timeout=self._timeout_ms)
        check_url_allowed(self._page.url, self._domains)

    def close(self) -> None:
        self._page.close()

    def _labels(self, element) -> list[str]:
        labels = [element.inner_text(timeout=self._timeout_ms)]
        for attribute in ("aria-label", "value", "title"):
            labels.append(element.get_attribute(attribute))
        return [label for label in labels if label]


class NetworkGuard:
    """Aborts payment-gateway traffic and non-GET booking-commit requests."""

    def __init__(self, commit_path_patterns: Iterable[str], extra_payment_hosts: Iterable[str] = ()):
        self.payment_hosts = PAYMENT_HOST_KEYWORDS + tuple(h.lower() for h in extra_payment_hosts)
        self.commit_paths = tuple(p.lower() for p in commit_path_patterns)
        self.installed = False
        self.aborted: list[str] = []

    def block_reason(self, url: str, method: str) -> str | None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        for keyword in self.payment_hosts:
            if keyword in host:
                return f"payment gateway ({keyword})"
        if method.upper() != "GET":
            path = parts.path.lower()
            for pattern in self.commit_paths:
                if pattern in path:
                    return f"booking-commit request ({pattern})"
        return None

    def handle(self, route) -> None:
        request = route.request
        reason = self.block_reason(request.url, request.method)
        if reason is None:
            route.continue_()
            return
        parts = urlsplit(request.url)
        where = f"{request.method} {parts.scheme}://{parts.hostname}{parts.path}"  # no query: may hold PII
        log.warning("NETWORK GUARD aborted %s: %s", where, reason)
        self.aborted.append(where)
        route.abort("blockedbyclient")

    def install(self, context) -> None:
        context.route("**/*", self.handle)
        self.installed = True


def self_check(guard: NetworkGuard | None) -> None:
    """Refuse to run unless every safety layer is in place."""
    if BOOKING_ALLOWED:
        raise SafetyViolation("BOOKING_ALLOWED must be False")
    if guard is None or not guard.installed:
        raise SafetyViolation("network guard is not installed; refusing to run")
    if not (is_denied_label("Pay Now") and is_denied_label("Complete booking")):
        raise SafetyViolation("deny-label rule is broken")
    if guard.block_reason("https://api.razorpay.com/v1/payments", "GET") is None:
        raise SafetyViolation("payment gateway blocking is broken")
    if not guard.commit_paths:
        raise SafetyViolation("no booking-commit patterns configured")
