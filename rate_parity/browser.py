"""Open guarded Playwright contexts. Every context gets the NetworkGuard first."""
from __future__ import annotations

import logging
import random
import time

from .config import Config, Site
from .safety import NetworkGuard, SafePage

log = logging.getLogger(__name__)

_COMMON = {
    "service_workers": "block",   # service workers would bypass context.route
    "accept_downloads": False,
    "locale": "en-IN",
    "timezone_id": "Asia/Kolkata",
}


class GuardedContext:
    def __init__(self, context, cfg: Config, login_state: str, closers: list):
        self.cfg = cfg
        self.login_state = login_state
        self.guard = NetworkGuard(cfg.commit_path_patterns, cfg.extra_payment_hosts)
        self.guard.install(context)
        context.set_default_timeout(cfg.browser.timeout_ms)
        self._context = context
        self._closers = closers

    def new_safe_page(self, site: Site) -> SafePage:
        return SafePage(
            self._context.new_page(),
            site=site.key,
            allowed_domains=site.allowed_domains,
            allowed_steps=site.step_selectors(),
            screenshot_dir=self.cfg.screenshot_dir,
            timeout_ms=self.cfg.browser.timeout_ms,
            pause=self._pause,
        )

    def open_page_for_human(self):
        """Raw page for manual login only. Collectors never get this."""
        return self._context.new_page()

    def _pause(self) -> None:
        low, high = self.cfg.browser.delay_seconds
        time.sleep(random.uniform(low, high))

    def close(self) -> None:
        for close in self._closers:
            try:
                close()
            except Exception as exc:  # closing must never hide the real error
                log.warning("error while closing browser: %s", exc)


def open_quick_context(pw, cfg: Config) -> GuardedContext:
    """Fresh, logged-out context for stage-1 search prices."""
    browser = pw.chromium.launch(headless=cfg.browser.headless, channel=cfg.browser.channel)
    context = browser.new_context(**_COMMON)
    return GuardedContext(context, cfg, "logged_out", [context.close, browser.close])


def open_deep_context(pw, cfg: Config, headless: bool | None = None) -> GuardedContext:
    """Persistent profile (already logged in by a human) for stage-2 checks."""
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(cfg.browser.profile_dir),
        headless=cfg.browser.headless if headless is None else headless,
        channel=cfg.browser.channel,
        **_COMMON,
    )
    return GuardedContext(context, cfg, "profile", [context.close])


def manual_login(cfg: Config) -> None:
    """Open the profile visibly so a HUMAN can log in. Nothing is automated."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        guarded = open_deep_context(pw, cfg, headless=False)
        try:
            guarded.open_page_for_human()
            input("Log in to each OTA in the browser window yourself, then press Enter here to close it... ")
        finally:
            guarded.close()
