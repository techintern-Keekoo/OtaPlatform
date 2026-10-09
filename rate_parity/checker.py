"""Selector checker: open one site's page READ-ONLY and report which configured
selectors are found and what text they read. No clicks, no typing.

    python -m rate_parity check --config config.example.yaml --site agoda

Candidates written as "TODO-verify: <selector>" are tried without the prefix,
so a human can confirm them before deleting the prefix in config. The prefix
is also removed where a room label was filled into a selector template
(e.g. Goibibo's candidate room names). Only this checker strips it: a real
run still refuses every site that has a TODO left (see config.py).
"""
from __future__ import annotations

import dataclasses
import re
from datetime import timedelta
from pathlib import Path

from .models import Stay
from .safety import SelectorMissing

PREFIX = "TODO-verify:"
_MARKER = re.compile(re.escape(PREFIX) + r"\s*")

# Words OTAs use to say whether a price includes tax. Shown by the checker so a
# human can set price_includes_tax for the site (Agoda: "before taxes & fees", seen 9 Oct 2026).
TAX_BASIS_PHRASES = ("incl. taxes", "including taxes", "excl. taxes", "excluding taxes", "+ taxes",
                     "taxes and fees", "taxes & fees", "price per night")


def candidate(selector: str | None) -> str | None:
    """The selector to try, or None when it is still an unfilled TODO."""
    if not selector:
        return None
    text = _MARKER.sub("", selector).strip()
    return None if not text or "todo" in text.lower() else text


def _keep(selector: str | None) -> str | None:
    """Candidate without its prefix; an unfilled TODO stays as it is, so it is reported as TODO."""
    return candidate(selector) or selector


def candidate_site(site):
    """The site with every "TODO-verify: " prefix removed, to test candidates on the page.

    For the checker only. Pure TODOs stay (reported TODO) except where the page
    loading would trip over them (wait/scroll targets, login/CAPTCHA checks).
    """
    rooms = {}
    for room_id, room in site.rooms.items():
        summary = room.summary and dataclasses.replace(room.summary, **{
            f.name: _keep(getattr(room.summary, f.name)) for f in dataclasses.fields(room.summary)})
        price_text = room.price_text and (_keep(room.price_text[0]), _MARKER.sub("", room.price_text[1]).strip())
        labels = dataclasses.replace(room.labels, **{
            f.name: _MARKER.sub("", getattr(room.labels, f.name)) for f in dataclasses.fields(room.labels)})
        rooms[room_id] = dataclasses.replace(
            room, labels=labels, search_price=_keep(room.search_price), steps=tuple(_keep(s) for s in room.steps),
            summary=summary, price_options=tuple((offer, _keep(s)) for offer, s in room.price_options),
            price_text=price_text)
    return dataclasses.replace(
        site, rooms=rooms, logged_in_marker=_keep(site.logged_in_marker),
        wait_for=candidate(site.wait_for), scroll_to=candidate(site.scroll_to),
        login_wall_selectors=tuple(filter(None, map(candidate, site.login_wall_selectors))),
        captcha_selectors=tuple(filter(None, map(candidate, site.captcha_selectors))))


def tax_basis_hint(text: str) -> list[str]:
    """Which tax-basis phrases the page text contains (case-insensitive)."""
    lowered = " ".join(text.casefold().split())
    return [phrase for phrase in TAX_BASIS_PHRASES if phrase in lowered]


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
        for offer, selector in room.price_options or (("", room.search_price),):
            rows.append((room_id, f"price[{offer}]" if offer else "search_price", selector))
        for i, step in enumerate(room.steps):
            rows.append((room_id, f"step[{i}] (exists only)", step))
        if not room.steps and room.summary:  # no-click mode: summary is on this page
            for name in ("room_name", "meal_plan", "cancellation", "room_price", "gst", "final"):
                rows.append((room_id, name, getattr(room.summary, name)))
    return rows


def report(page, site) -> list[tuple[str, str, str, str]]:
    from .collectors.generic import read_price_text
    site = candidate_site(site)
    results = []
    for room_id, room in site.rooms.items():
        if room.price_text:  # card text + pattern (MakeMyTrip style)
            if candidate(room.price_text[0]) is None:
                results.append((room_id, "price (card text)", "TODO", "not filled yet"))
                continue
            try:
                found = read_price_text(page, room.price_text)
            except Exception as exc:
                results.append((room_id, "price (card text)", "ERROR", f"{type(exc).__name__}: {str(exc)[:80]}"))
                continue
            detail = (f"price {found[0]} + taxes {found[1]} ({found[2] or 'offer type not shown'})"
                      if found else "room card or price pattern not found")
            results.append((room_id, "price (card text)", "OK" if found else "MISSING", detail))
    for room_id, field, selector in fields_to_check(site):
        if site.rooms.get(room_id) is not None and site.rooms[room_id].price_text:
            continue
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
        from collections import Counter
        import re
        labels = Counter(re.sub(r"^-?\d+", "*", v) for v in page.attribute_values(  # "7083...-selectRoom" -> "*-selectRoom"
            "[data-testid*='oom'], [data-testid*='rice'], [data-testid*='ate'], [data-testid*='ancel']", "data-testid"))
        lines.append("page labels (data-testid) about rooms/prices: " + ", ".join(
            f"{name} x{n}" for name, n in sorted(labels.items()))[:1200])
        lines.append("room names seen: " + " | ".join(page.texts("[data-testid='room-name']")))
        for i, text in enumerate(page.texts("[data-testid='room-offer']", limit=16, width=110)):
            lines.append(f"offer {i + 1}: {text}")
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
    test_site = candidate_site(site)  # "TODO-verify: " candidates tried as written
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
            page = context.new_safe_page(site)  # click allow-list from config as is (the checker never clicks)
            from .collectors import GenericCollector
            try:
                GenericCollector(test_site).load(page, stay)  # same loading + waiting as the real run
            except SelectorMissing as exc:
                print(f"\nNOTE: {exc}")
            shot = page.screenshot(f"check_{site_key}")
            html = page.save_html(f"check_{site_key}")
            text_file = Path(html).with_suffix(".txt")
            page_text = page.body_text()
            text_file.write_text(page_text, encoding="utf-8")  # the page text exactly as a guest sees it
            print(f"\n{site.label} - stay {stay.checkin} to {stay.checkout}, {stay.adults} adults")
            print(f"Screenshot: {shot}\nPage HTML: {html}\nPage text: {text_file}\n")
            print("What the browser got:")
            for line in diagnose(page):
                print("  " + line)
            found = tax_basis_hint(page_text)
            print(f"  tax basis hint (config price_includes_tax: {str(site.price_includes_tax).lower()}): page says "
                  + (", ".join(f"'{p}'" for p in found) if found else "none of: " + ", ".join(TAX_BASIS_PHRASES)))
            print()
            for room_id, field, status, detail in report(page, test_site):
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
