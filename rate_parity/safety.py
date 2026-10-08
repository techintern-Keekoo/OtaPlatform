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
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

BOOKING_ALLOWED = False  # Never change. A test asserts this.

DENY_LABEL = re.compile(
    r"\b(pay|payment|confirm|complete (your )?(booking|reservation)"
    r"|finish (your )?(booking|reservation)|place (your )?order"
    r"|book (now )?(and|&) pay|submit|purchase|checkout now|proceed to pay"
    r"|card|upi|wallet)"
    r"|भुगतान|पेमेंट|पुष्टि|कार्ड",  # Hindi: payment, payment, confirm, card
    re.IGNORECASE,
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9_:.-]+$")
# Nearest ancestor that actually receives the click (a child span may say "Continue"
# while the button around it says "Pay now").
_CLICKABLE_ANCESTOR = "xpath=ancestor::*[self::button or self::a or self::label or @role='button'][1]"

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


# Always checked on non-GET request bodies (letters only, lower-case), so a booking
# sent through a generic endpoint such as /graphql is still blocked.
COMMIT_BODY_KEYWORDS = (
    "createbooking", "bookingcreate", "confirmbooking", "bookingconfirm",
    "completebooking", "makebooking", "submitbooking", "createreservation",
    "reservationcreate", "confirmreservation", "completereservation",
    "placeorder", "initiatepayment", "makepayment",
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


def _normalize_label(text: str) -> str:
    """NFKC (full-width letters) and drop invisible format chars (zero-width space, soft hyphen)."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return " ".join(text.split())


def is_denied_label(text: str | None) -> bool:
    return bool(text) and bool(DENY_LABEL.search(_normalize_label(text)))


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
        page.on("popup", self._close_popup)  # new tabs/windows escape the allowlist: close them

    def _close_popup(self, popup) -> None:
        log.warning("%s: closed a popup/new tab the agent did not open", self._site)
        popup.close()

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
        # Pin ONE element so the element we checked is the element we click.
        element = self._page.locator(selector).first.element_handle(timeout=self._timeout_ms)
        labels = self._labels(element)
        if not labels:  # icon-only / CSS-only text: we cannot tell what it does
            raise SafetyViolation(f"{self._site}: refusing to click an element with no readable label ({selector})")
        for label in labels:
            if is_denied_label(label):
                raise SafetyViolation(f"{self._site}: refusing to click {label!r} ({selector})")
        self._pause()
        element.click(timeout=self._timeout_ms)
        self._page.wait_for_load_state("domcontentloaded", timeout=self._timeout_ms)
        check_url_allowed(self._page.url, self._domains)

    def count(self, selector: str) -> int:
        return self._page.locator(selector).count()

    def texts(self, selector: str, limit: int = 12, width: int = 60) -> list[str]:
        return [" ".join(t.split())[:width] for t in self._page.locator(selector).all_inner_texts()[:limit]]

    def title(self) -> str:
        return self._page.title()

    @property
    def url(self) -> str:
        return self._page.url

    def save_html(self, name: str) -> str:
        """Save the page's HTML next to the screenshots (for debugging selectors)."""
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{self._site}_{name}")
        path = self._screenshot_dir / f"{safe_name}.html"
        path.write_text(self._page.content(), encoding="utf-8")
        return str(path)

    def scroll_through(self, steps: int = 10, pause_ms: int = 600) -> None:
        """Scroll down with the mouse wheel so lazy-loaded room lists appear.

        Read-only: a wheel event cannot click, type or submit anything.
        """
        for _ in range(steps):
            self._page.mouse.wheel(0, 1200)
            self._page.wait_for_timeout(pause_ms)

    def close(self) -> None:
        self._page.close()

    def _labels(self, element) -> list[str]:
        """Every label of the element and of the clickable ancestor around it."""
        targets = [element]
        ancestor = element.query_selector(_CLICKABLE_ANCESTOR)
        if ancestor is not None:
            targets.append(ancestor)
        labels = []
        for target in targets:
            labels.append(target.inner_text(timeout=self._timeout_ms))
            for attribute in ("aria-label", "value", "title", "alt", "name"):
                labels.append(target.get_attribute(attribute))
            for ref in (target.get_attribute("aria-labelledby") or "").split():
                if not _SAFE_ID.match(ref):
                    raise SafetyViolation(f"{self._site}: unreadable aria-labelledby {ref!r}; refusing to click")
                labelled = self._page.query_selector(f'[id="{ref}"]')
                if labelled is not None:
                    labels.append(labelled.inner_text(timeout=self._timeout_ms))
        return [label for label in labels if label and label.strip()]


class NetworkGuard:
    """Aborts payment-gateway traffic and non-GET booking-commit requests."""

    def __init__(
        self,
        commit_path_patterns: Iterable[str],
        extra_payment_hosts: Iterable[str] = (),
        readonly_post_paths: Iterable[str] = (),
    ):
        self.payment_hosts = PAYMENT_HOST_KEYWORDS + tuple(h.lower() for h in extra_payment_hosts)
        self.commit_paths = tuple(p.lower() for p in commit_path_patterns)
        # EXACT paths of search endpoints verified to be read-only (e.g. eZee's
        # /booking/multibox.php room search). Exempt from the path rule only:
        # payment hosts and commit-body keywords still apply to them.
        self.readonly_paths = frozenset(p.lower() for p in readonly_post_paths)
        self.installed = False
        self.aborted: list[str] = []

    def block_reason(self, url: str, method: str, body: str | None = None) -> str | None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        for keyword in self.payment_hosts:
            if keyword in host:
                return f"payment gateway ({keyword})"
        if method.upper() != "GET":
            path = parts.path.lower()
            for pattern in () if path in self.readonly_paths else self.commit_paths:
                if pattern in path:
                    return f"booking-commit request ({pattern})"
            compact = re.sub(r"[^a-z]", "", (body or "").lower())
            for keyword in COMMIT_BODY_KEYWORDS:
                if keyword in compact:
                    return f"booking-commit request body ({keyword})"
        return None

    def handle(self, route) -> None:
        request = route.request
        try:
            body = request.post_data
        except Exception:  # binary body: path rules still apply
            body = None
        reason = self.block_reason(request.url, request.method, body)
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
    if guard.block_reason("https://x.example/graphql", "POST", '{"operationName":"CreateBooking"}') is None:
        raise SafetyViolation("booking-commit body blocking is broken")
    if not guard.commit_paths:
        raise SafetyViolation("no booking-commit patterns configured")
