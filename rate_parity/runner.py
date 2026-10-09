"""Orchestration: quick scan -> deep check for suspects -> compare -> save -> alert.

Each site/room check is isolated: one failure becomes a COULD_NOT_CHECK row
and the run carries on.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import alerts, browser, report, storage
from .collectors import make_collector
from .compare import decide_status, gap_pct, implausible_reason, is_suspect
from .config import Config, Site
from .models import SITE_BROKEN, CheckRow, Stay, Status, Summary
from .safety import Blocked, SafetyViolation, self_check

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def build_stays(cfg: Config, today: date) -> list[Stay]:
    stays = []
    for days in cfg.stay.days_ahead:
        checkin = today + timedelta(days=days)
        stays.append(Stay(checkin, checkin + timedelta(days=cfg.stay.nights), cfg.stay.adults))
    return stays


def _attempt(context: browser.GuardedContext, site: Site, action, collector=None):
    """Run action(collector, page) on a fresh SafePage (None if the collector
    needs no browser). Returns (result, note)."""
    page = None
    try:
        collector = collector or make_collector(site)
        if getattr(collector, "needs_browser", True):
            page = context.new_safe_page(site)
        return action(collector, page), ""
    except Blocked as exc:
        log.warning("%s", exc)
        return None, exc.flag
    except SafetyViolation as exc:
        log.error("SAFETY STOP on %s: %s", site.key, exc)
        return None, f"safety stop: {exc}"
    except Exception as exc:
        log.exception("%s failed", site.key)
        return None, f"error: {type(exc).__name__}: {exc}"[:200]
    finally:
        if page is not None:
            _close_quietly(page)


def _close_quietly(page) -> None:
    try:
        page.close()
    except Exception as exc:
        log.warning("could not close page: %s", exc)


class StayCheck:
    """All checks for one stay. Website results are fetched once and reused."""

    def __init__(self, cfg: Config, quick, deep, stay: Stay):
        self.cfg, self.quick, self.deep, self.stay = cfg, quick, deep, stay
        # Reused for every room, so the website (eZee) search runs once per stay.
        web = cfg.website
        self.web_collector = make_collector(web, cfg.screenshot_dir) if web.kind == "ezee" else make_collector(web)
        self.web_prices, self.web_note = _attempt(
            quick, cfg.website, lambda c, p: c.quick_scan(p, stay), self.web_collector)
        self._web_summaries: dict[str, tuple[Summary | None, str]] = {}

    def website_summary(self, room_id: str) -> tuple[Summary | None, str]:
        if room_id not in self._web_summaries:
            self._web_summaries[room_id] = _attempt(
                self.deep, self.cfg.website, lambda c, p: c.deep_check(p, self.stay, room_id), self.web_collector
            )
        return self._web_summaries[room_id]

    def check_ota(self, ota: Site) -> list[CheckRow]:
        loaded = sold_out = False
        if not ota.ready:
            prices, note = None, "not set up yet (selectors still TODO in config)"
            self._offers, self._taxes, self._evidence = {}, {}, ""
        else:
            result, note = _attempt(self.quick, ota, lambda c, p: (
                c.quick_scan(p, self.stay), getattr(c, "offer_types", {}),
                getattr(c, "card_taxes", {}), getattr(c, "evidence", ""), getattr(c, "page_says_sold_out", False)))
            loaded = result is not None
            prices, self._offers, self._taxes, self._evidence, sold_out = result if result else (None, {}, {}, "", False)
        rows = [self._check_room(ota, room_id, prices, note) for room_id in ota.rooms]
        if loaded and rows and not any((prices or {}).get(room_id) is not None for room_id in ota.rooms):
            if sold_out:
                log.info("%s: no price and the page says sold out: treated as a full house", ota.label)
            else:
                _mark_site_broken(rows, ota)
        return rows

    _offers: dict = {}
    _taxes: dict = {}
    _evidence: str = ""

    def _check_room(self, ota: Site, room_id: str, ota_prices, ota_note: str) -> CheckRow:
        key = self.cfg.rooms[room_id]
        row = CheckRow(
            ota=ota.label, property=self.cfg.property_name, room=key.room,
            meal_plan=key.meal_plan, cancellation=key.cancellation,
            checkin=self.stay.checkin.isoformat(), checkout=self.stay.checkout.isoformat(),
            adults=self.stay.adults, login_state=self.quick.login_state,
        )
        web_search = (self.web_prices or {}).get(room_id)
        row.website_search_price = web_search
        row.search_price = (ota_prices or {}).get(room_id)
        row.ota_offer = self._offers.get(room_id, "") if row.search_price is not None else ""
        web_plan = []  # which website rate plan Zen's price is, so a human can audit it (meal plan etc.)
        if web_search is not None and not getattr(self.web_collector, "needs_browser", True):
            web, _ = self.website_summary(room_id)  # eZee: same cached search, no extra request
            row.website_final = web.final if web else None
            web_plan = web.notes if web else []
        if web_search is None:
            return _finish(row, Status.COULD_NOT_CHECK, self.web_note or "website search price not found")
        if row.search_price is None:
            return _finish(row, Status.COULD_NOT_CHECK, ota_note or "no OTA price (room sold out or not listed)")

        def verdict(status: Status, note: str) -> CheckRow:
            return _finish(row, status, "; ".join([note, *web_plan]))

        def misread(reason: str) -> CheckRow:  # numbers that look misread are never a VIOLATION
            log.warning("%s %s %s: implausible price (%s)", ota.key, room_id, row.checkin, reason)
            return _finish(row, Status.COULD_NOT_CHECK,
                           f"possible violation, verify manually: implausible price ({reason}) - possible misread")

        # Like for like: a price shown incl. tax is held against Zen's final, not
        # Zen's before-tax price (else the OTA's tax would hide a violation).
        zen_basis = web_search
        if ota.price_includes_tax:
            if row.website_final is None:
                web, _ = self.website_summary(room_id)
                row.website_final = web.final if web else None
            if row.website_final is None:
                return _finish(row, Status.COULD_NOT_CHECK, "possible violation, verify manually: "
                               "OTA price includes tax but Zen's final (incl. tax) was not found")
            zen_basis = row.website_final
        tax = None if ota.price_includes_tax else self._taxes.get(room_id)
        reason = implausible_reason(row.search_price, zen_basis, tax, self.cfg.sanity)
        if reason:
            return misread(reason)
        suspect = is_suspect(row.search_price, zen_basis, self.cfg.min_margin_pct)
        if tax is not None and row.website_final is not None:
            # The room list shows price AND taxes (MakeMyTrip): final is known from the
            # same page load, so no second visit. Both numbers were read from the page.
            row.checkout_room_price, row.gst = row.search_price, tax
            row.final_payable = row.search_price + tax
            row.gap_pct = gap_pct(row.final_payable, row.website_final)
            row.screenshot_path = self._evidence
            status = decide_status(row.final_payable, row.website_final, self.cfg.min_margin_pct, suspect=suspect)
            return verdict(status, "final = price + taxes & fees shown on the room list (one page load)")
        if not suspect:
            basis = " (both incl. tax)" if ota.price_includes_tax else ""
            return verdict(Status.IN_PARITY, f"OTA search price above Zen{basis}")

        if not ota.deep_ready:
            return _finish(row, Status.COULD_NOT_CHECK,
                           "possible violation, verify manually: OTA price not above Zen (checkout check not configured yet)")
        web, web_note = self.website_summary(room_id)
        if web is None or web.final <= 0:
            return _finish(row, Status.COULD_NOT_CHECK, f"website checkout: {web_note or 'no valid total'}")
        row.website_final = web.final
        summary, note = _attempt(self.deep, ota, lambda c, p: c.deep_check(p, self.stay, room_id))
        if summary is None:
            return _finish(row, Status.COULD_NOT_CHECK, f"possible violation, verify manually: {note}")

        row.login_state = summary.login_state
        row.checkout_room_price, row.gst = summary.room_price, summary.gst
        row.fees, row.discount, row.final_payable = summary.fees, summary.discount, summary.final
        row.screenshot_path = summary.screenshot_path
        reason = implausible_reason(summary.final, web.final, None, self.cfg.sanity)
        if reason is None and summary.room_price is not None and summary.gst is not None:
            reason = implausible_reason(summary.room_price, web_search, summary.gst, self.cfg.sanity)
        if reason:
            return misread(reason)
        summary.notes.extend(web.notes)  # the website plan's name, for a human to audit
        row.gap_pct = gap_pct(summary.final, web.final)
        status = decide_status(summary.final, web.final, self.cfg.min_margin_pct, suspect=True)
        notes = summary.notes + [f"website screenshot: {web.screenshot_path}"]
        return _finish(row, status, "; ".join(notes))


def _mark_site_broken(rows: list[CheckRow], ota: Site) -> None:
    """The page loaded but not one room price was read: most likely the site
    changed its layout, not "everything sold out". Say so loudly."""
    note = (f"{SITE_BROKEN} 0 of {len(rows)} rooms read on a loaded page - layout may have changed; "
            f"run: python -m rate_parity check --site {ota.key}")
    log.error("%s: %s", ota.label, note)
    for row in rows:
        _finish(row, Status.COULD_NOT_CHECK, note)


def _finish(row: CheckRow, status: Status, note: str) -> CheckRow:
    row.status, row.note, row.checked_at = status, note, now_ist().isoformat(timespec="seconds")
    return row


def check_all(cfg: Config, quick, deep, only: str | None = None) -> list[CheckRow]:
    otas = cfg.otas(only)
    rows = []
    for stay in build_stays(cfg, now_ist().date()):
        stay_check = StayCheck(cfg, quick, deep, stay)
        for ota in otas:
            rows.extend(stay_check.check_ota(ota))
    return rows


class NoBrowser:
    """Stand-in when no OTA is ready: the website (eZee) needs no browser."""
    login_state = "not opened"

    def new_safe_page(self, site):
        raise RuntimeError(f"{site.key} needs a browser but none was opened")


def run(cfg: Config, only: str | None = None, dry_run: bool = False) -> list[CheckRow]:
    otas = cfg.otas(only)  # validate --only before opening a browser
    if not any(site.ready for site in otas) and cfg.website.kind == "ezee":
        log.info("no OTA is set up yet: checking the website only, no browser")
        rows = check_all(cfg, NoBrowser(), NoBrowser(), only)
        return _store_and_report(cfg, rows, dry_run)

    from playwright.sync_api import sync_playwright  # imported here so tests need no browser
    with sync_playwright() as pw:
        quick = browser.open_quick_context(pw, cfg)
        try:
            # The logged-in profile costs memory and fails if another Chrome holds it:
            # open it only when a checkout check really needs it (self_check runs then).
            deep = browser.LazyContext(lambda: browser.open_deep_context(pw, cfg))
            try:
                self_check(quick.guard)
                rows = check_all(cfg, quick, deep, only)
            finally:
                log.info("agent profile opened this run: %s", "yes" if deep.opened else "no")
                deep.close()
        finally:
            quick.close()

    return _store_and_report(cfg, rows, dry_run)


def _store_and_report(cfg: Config, rows: list[CheckRow], dry_run: bool) -> list[CheckRow]:
    """Save, show and send. If saving fails (e.g. the CSV is open in Excel) the
    table and the WhatsApp summary still go out, then the error is raised."""
    error = None
    try:
        destination = storage.save_rows(rows, cfg, dry_run)
        log.info("saved %d rows to %s", len(rows), destination)
    except Exception as exc:
        log.exception("could not save the %d rows", len(rows))
        error = exc
    when = now_ist()
    table = report.build_table(rows, when)
    print(table)
    try:
        path = report.save_table(table, cfg.csv_path.parent, when)
        log.info("price table saved to %s", path)
    except OSError as exc:
        log.error("could not save the price table: %s", exc)
        error = error or exc
    alerts.send_daily_summary(rows, cfg, when.date(), dry_run)
    if error is not None:
        raise error
    return rows
