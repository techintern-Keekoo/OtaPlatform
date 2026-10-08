"""Orchestration: quick scan -> deep check for suspects -> compare -> save -> alert.

Each site/room check is isolated: one failure becomes a COULD_NOT_CHECK row
and the run carries on.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import alerts, browser, storage
from .collectors import make_collector
from .compare import decide_status, gap_pct, is_suspect
from .config import Config, Site
from .models import CheckRow, Stay, Status, Summary
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


def _attempt(context: browser.GuardedContext, site: Site, action):
    """Run action(collector, page) on a fresh SafePage. Returns (result, note)."""
    page = None
    try:
        page = context.new_safe_page(site)
        return action(make_collector(site), page), ""
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
        self.web_prices, self.web_note = _attempt(quick, cfg.website, lambda c, p: c.quick_scan(p, stay))
        self._web_summaries: dict[str, tuple[Summary | None, str]] = {}

    def website_summary(self, room_id: str) -> tuple[Summary | None, str]:
        if room_id not in self._web_summaries:
            self._web_summaries[room_id] = _attempt(
                self.deep, self.cfg.website, lambda c, p: c.deep_check(p, self.stay, room_id)
            )
        return self._web_summaries[room_id]

    def check_ota(self, ota: Site) -> list[CheckRow]:
        prices, note = _attempt(self.quick, ota, lambda c, p: c.quick_scan(p, self.stay))
        return [self._check_room(ota, room_id, prices, note) for room_id in ota.rooms]

    def _check_room(self, ota: Site, room_id: str, ota_prices, ota_note: str) -> CheckRow:
        key = self.cfg.rooms[room_id]
        row = CheckRow(
            ota=ota.label, property=self.cfg.property_name, room=key.room,
            meal_plan=key.meal_plan, cancellation=key.cancellation,
            checkin=self.stay.checkin.isoformat(), checkout=self.stay.checkout.isoformat(),
            adults=self.stay.adults, login_state=self.quick.login_state,
        )
        web_search = (self.web_prices or {}).get(room_id)
        row.search_price = (ota_prices or {}).get(room_id)
        if web_search is None:
            return _finish(row, Status.COULD_NOT_CHECK, self.web_note or "website search price not found")
        if row.search_price is None:
            return _finish(row, Status.COULD_NOT_CHECK, ota_note or "OTA search price not found (sold out or selector)")
        if not is_suspect(row.search_price, web_search):
            return _finish(row, Status.IN_PARITY, "search price not below website")

        web, web_note = self.website_summary(room_id)
        if web is None or web.final <= 0:
            return _finish(row, Status.COULD_NOT_CHECK, f"website checkout: {web_note or 'no valid total'}")
        row.website_final = web.final
        summary, note = _attempt(self.deep, ota, lambda c, p: c.deep_check(p, self.stay, room_id))
        if summary is None:
            return _finish(row, Status.COULD_NOT_CHECK, note)

        row.login_state = summary.login_state
        row.checkout_room_price, row.gst = summary.room_price, summary.gst
        row.fees, row.discount, row.final_payable = summary.fees, summary.discount, summary.final
        row.screenshot_path = summary.screenshot_path
        row.gap_pct = gap_pct(summary.final, web.final)
        status = decide_status(summary.final, web.final, self.cfg.tolerance_pct, suspect=True)
        notes = summary.notes + [f"website screenshot: {web.screenshot_path}"]
        return _finish(row, status, "; ".join(notes))


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


def run(cfg: Config, only: str | None = None, dry_run: bool = False) -> list[CheckRow]:
    from playwright.sync_api import sync_playwright  # imported here so tests need no browser

    cfg.otas(only)  # validate --only before opening a browser
    with sync_playwright() as pw:
        quick = browser.open_quick_context(pw, cfg)
        try:
            deep = browser.open_deep_context(pw, cfg)
            try:
                self_check(quick.guard)
                self_check(deep.guard)
                rows = check_all(cfg, quick, deep, only)
            finally:
                deep.close()
        finally:
            quick.close()

    destination = storage.save_rows(rows, cfg, dry_run)
    log.info("saved %d rows to %s", len(rows), destination)
    alerts.send_daily_summary(rows, cfg, now_ist().date(), dry_run)
    return rows
