"""Selector checker: open one site's page READ-ONLY and report which configured
selectors are found and what text they read. No clicks, no typing.

    python -m rate_parity check --config config.example.yaml --site agoda

Candidates written as "TODO-verify: <selector>" are tried without the prefix,
so a human can confirm them before deleting the prefix in config.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from .models import Stay
from .safety import SelectorMissing

PREFIX = "TODO-verify:"


def candidate(selector: str | None) -> str | None:
    """The selector to try, or None when it is still an unfilled TODO."""
    if not selector:
        return None
    text = selector.strip()
    if text.startswith(PREFIX):
        text = text[len(PREFIX):].strip()
    return None if "todo" in text.lower() else text


def probe(page, selector: str | None) -> tuple[str, str]:
    """(status, detail) for one selector: OK / MISSING / ERROR / TODO."""
    sel = candidate(selector)
    if sel is None:
        return "TODO", "not filled yet"
    try:
        text = page.read_text(sel)
    except Exception as exc:  # bad selector syntax, page closed, ...
        return "ERROR", f"{type(exc).__name__}: {str(exc)[:80]}"
    if text is None:
        return "MISSING", "not on page"
    return "OK", " ".join(text.split())[:70]


def fields_to_check(site) -> list[tuple[str, str, str | None]]:
    """(room_id, field, selector) for everything readable on the search page."""
    rows = [("-", "logged_in_marker", site.logged_in_marker)]
    for room_id, room in site.rooms.items():
        rows.append((room_id, "search_price", room.search_price))
        for i, step in enumerate(room.steps):
            rows.append((room_id, f"step[{i}] (exists only)", step))
        if not room.steps and room.summary:  # no-click mode: summary is on this page
            for name in ("room_name", "meal_plan", "cancellation", "room_price", "gst", "final"):
                rows.append((room_id, name, getattr(room.summary, name)))
    return rows


def report(page, site) -> list[tuple[str, str, str, str]]:
    results = []
    for room_id, field, selector in fields_to_check(site):
        if field.startswith("step["):  # never click, only check it is there
            sel = candidate(selector)
            status = "TODO" if sel is None else ("OK" if _exists(page, sel) else "MISSING")
            results.append((room_id, field, status, sel or "not filled yet"))
        else:
            results.append((room_id, field, *probe(page, selector)))
    return results


DIAGNOSTIC_SELECTORS = (
    "[data-testid='room-item']", "[data-testid='room-name']", "[data-testid='room-offer']",
    "[data-testid='room-offer-final-price']", "[data-testid='book-button']",
    "[data-testid*='room']", "[data-element-name*='room']",
)


def diagnose(page) -> list[str]:
    """What the browser actually got: title, URL, counts and room-name texts."""
    lines = []
    for label, get in (("title", page.title), ("url", lambda: page.url)):
        try:
            lines.append(f"{label}: {get()[:120]}")
        except Exception as exc:
            lines.append(f"{label}: ERROR {type(exc).__name__}")
    for sel in DIAGNOSTIC_SELECTORS:
        try:
            lines.append(f"{page.count(sel):4}  {sel}")
        except Exception as exc:
            lines.append(f"ERROR {sel}: {type(exc).__name__}")
    try:
        lines.append("room names seen: " + " | ".join(page.texts("[data-testid='room-name']")))
    except Exception as exc:
        lines.append(f"room names seen: ERROR {type(exc).__name__}")
    return lines


def _exists(page, selector: str) -> bool:
    try:
        return page.exists(selector)
    except Exception:
        return False


def check_site(cfg, site_key: str, days: int = 14, use_profile: bool = False) -> int:
    from playwright.sync_api import sync_playwright

    from . import browser
    from .runner import now_ist
    from .safety import self_check

    if site_key not in cfg.sites:
        print(f"Unknown site {site_key!r}. Known: {', '.join(cfg.sites)}")
        return 2
    site = cfg.sites[site_key]
    checkin = now_ist().date() + timedelta(days=days)
    stay = Stay(checkin, checkin + timedelta(days=cfg.stay.nights), cfg.stay.adults)
    if site.kind == "ezee":
        from .collectors import make_collector
        prices = make_collector(site, cfg.screenshot_dir).quick_scan(None, stay)
        for room_id in site.rooms:
            print(f"{'OK' if room_id in prices else 'MISSING':8} {room_id:18} {prices.get(room_id, '')}")
        return 0

    with sync_playwright() as pw:
        context = (browser.open_deep_context if use_profile else browser.open_quick_context)(pw, cfg)
        try:
            self_check(context.guard)
            page = context.new_safe_page(site)
            page.goto(site.search_url_for(stay))
            if site.scroll_to_load:
                page.scroll_through()  # lazy-loaded room lists only appear after scrolling
            first = next((candidate(r.search_price) for r in site.rooms.values() if candidate(r.search_price)), None)
            if first:
                try:
                    page.wait_for(first)  # let the page render its room list
                except SelectorMissing:
                    pass
            shot = page.screenshot(f"check_{site_key}")
            html = page.save_html(f"check_{site_key}")
            text_file = Path(html).with_suffix(".txt")
            text_file.write_text(page.body_text(), encoding="utf-8")  # the page text exactly as a guest sees it
            print(f"\n{site.label} - stay {stay.checkin} to {stay.checkout}, {stay.adults} adults")
            print(f"Screenshot: {shot}\nPage HTML: {html}\nPage text: {text_file}\n")
            print("What the browser got:")
            for line in diagnose(page):
                print("  " + line)
            print()
            for room_id, field, status, detail in report(page, site):
                print(f"{status:8} {room_id:18} {field:24} {detail}")
            blocked = context.guard.aborted
            print(f"\nRequests blocked by the no-booking network guard: {len(blocked)}")
            for where in blocked:
                print("  BLOCKED  " + where)
            print("(If the room list is empty and something is blocked here, send this list.)")
            print("\nOK = found (check the text is right, then delete 'TODO-verify: ' in config).")
            print("MISSING/ERROR = selector needs fixing. TODO = not filled yet.")
        finally:
            context.close()
    return 0
