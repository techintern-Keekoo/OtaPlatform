"""Reliability: lazy logged-in profile, broken-site detection, and how both show up."""
import contextlib
import dataclasses
from datetime import date, datetime
from decimal import Decimal as D

import pytest

from rate_parity import browser, runner
from rate_parity.alerts import wati
from rate_parity.config import parse_config
from rate_parity.models import CheckRow, Stay, Status
from rate_parity.report import build_table
from rate_parity.safety import NetworkGuard, SelectorMissing
from tests.fakes import FakeContext, FakePage

SUMMARY = {
    "ready": "#summary", "room_name": "#room", "meal_plan": "#meal", "cancellation": "#cancel",
    "final": "#final", "room_price": "#price", "gst": "#gst", "fees": "#fees", "discount": None,
}


def site(domain, **extra):
    room = {"search_price": "#search-price", "steps": ["#select"], "summary": SUMMARY}
    return {
        "enabled": True, "allowed_domains": [domain],
        "search_url": f"https://www.{domain}/h?ci={{checkin}}&co={{checkout}}&a={{adults}}",
        "rooms": {"r1": room, "r2": room}, **extra,
    }


RAW = {
    "property": {"name": "Keekoo Test"},
    "min_margin_pct": 1,
    "stay": {"days_ahead": [0, 1], "nights": 1, "adults": 2},
    "rooms": [{"id": "r1", "room": "Deluxe", "meal_plan": "Breakfast", "cancellation": "Free cancellation"},
              {"id": "r2", "room": "Suite", "meal_plan": "Breakfast", "cancellation": "Free cancellation"}],
    "browser": {"profile_dir": "chrome-profile", "delay_seconds": [0, 0]},
    "sites": {"website": site("keekoo.test"), "booking_com": site("booking.com", label="Booking.com")},
}
CFG = parse_config(RAW)
OTA = CFG.sites["booking_com"]
STAY = Stay(date(2026, 10, 15), date(2026, 10, 16), 2)
LOCKED_NOTE = ("possible violation, verify manually: "
               "agent profile could not open (close any Chrome using chrome-profile)")
BROKEN_NOTE = ("SITE BROKEN? 0 of 2 rooms read on a loaded page - layout may have changed; "
               "run: python -m rate_parity check --site booking_com")


class FakeCollector:
    """Website: 10,000 for every room, read without a browser (like eZee).
    OTA: `ota_prices` from its search page; checkout total `ota_final`."""
    ota_prices = {"r1": D("12000"), "r2": D("12000")}  # well above Zen: no checkout check needed
    ota_final = D("9500")
    load_error = None

    def __init__(self, site, evidence_dir=None):
        self.site = site
        self.needs_browser = site.key != "website"

    def quick_scan(self, page, stay):
        if self.site.key == "website":
            return {"r1": D("10000"), "r2": D("10000")}
        if self.load_error:
            raise self.load_error
        return dict(self.ota_prices)

    def deep_check(self, page, stay, room_id):
        from rate_parity.models import Summary
        if self.site.key == "website":
            return Summary(final=D("10000"), screenshot_path="web.png")
        return Summary(final=self.ota_final, login_state="logged_in", screenshot_path="ota.png")


class FakeGuarded:
    """Context with the REAL network guard installed on a fake Playwright context."""

    def __init__(self, login_state="profile", guarded=True):
        self.login_state = login_state
        self.guard = NetworkGuard(CFG.commit_path_patterns, CFG.extra_payment_hosts, CFG.readonly_post_paths)
        if guarded:
            self.guard.install(FakeContext())
        self.pages, self.closed = 0, False

    def new_safe_page(self, site):
        self.pages += 1
        return FakePage()

    def close(self):
        self.closed = True


class Opener:
    """Counts how often the logged-in profile was opened; can fail like a locked profile."""

    def __init__(self, error=None, guarded=True):
        self.calls, self.error, self.guarded, self.context = 0, error, guarded, None

    def __call__(self):
        self.calls += 1
        if self.error:
            raise self.error
        self.context = FakeGuarded("profile", self.guarded)
        return self.context


@pytest.fixture(autouse=True)
def fake_collectors(monkeypatch):
    monkeypatch.setattr(runner, "make_collector", FakeCollector)
    monkeypatch.setattr(FakeCollector, "ota_prices", {"r1": D("12000"), "r2": D("12000")})
    monkeypatch.setattr(FakeCollector, "load_error", None)


def check(deep, ota=OTA):
    return runner.StayCheck(CFG, FakeGuarded("logged_out"), deep, STAY).check_ota(ota)


# --- R1: the logged-in profile opens only when a checkout check needs it -----

def test_profile_not_opened_when_no_checkout_check_is_needed():
    opener = Opener()
    rows = check(browser.LazyContext(opener))
    assert [r.status for r in rows] == [Status.IN_PARITY, Status.IN_PARITY]
    assert opener.calls == 0


def test_profile_opened_once_and_self_checked_when_needed(monkeypatch):
    monkeypatch.setattr(FakeCollector, "ota_prices", {"r1": D("9000"), "r2": D("9000")})  # suspects
    opener, checked = Opener(), []
    real_self_check = browser.self_check
    monkeypatch.setattr(browser, "self_check", lambda guard: (checked.append(guard), real_self_check(guard)))
    deep = browser.LazyContext(opener)
    rows = check(deep)
    assert [r.status for r in rows] == [Status.VIOLATION, Status.VIOLATION]
    assert opener.calls == 1 and deep.opened and opener.context.pages == 2
    assert checked == [opener.context.guard]  # checked before its first page
    deep.close()
    assert opener.context.closed


def test_locked_profile_only_affects_checks_that_need_it(monkeypatch):
    monkeypatch.setattr(FakeCollector, "ota_prices", {"r1": D("9000"), "r2": D("12000")})  # r1 suspect only
    opener = Opener(error=RuntimeError("user data directory is already in use"))
    deep = browser.LazyContext(opener)
    rows = check(deep) + check(deep)  # two nights
    assert [r.status for r in rows] == [Status.COULD_NOT_CHECK, Status.IN_PARITY] * 2
    assert rows[0].note == LOCKED_NOTE and rows[2].note == LOCKED_NOTE
    assert rows[0].search_price == D("9000")  # the OTA price is still shown (VERIFY in the table)
    assert opener.calls == 1  # not retried for every room
    deep.close()  # nothing to close, must not fail


def test_profile_without_its_safety_guard_is_never_used(monkeypatch):
    monkeypatch.setattr(FakeCollector, "ota_prices", {"r1": D("9000"), "r2": D("9000")})
    opener = Opener(guarded=False)
    rows = check(browser.LazyContext(opener))
    assert all(r.status is Status.COULD_NOT_CHECK for r in rows)
    assert rows[0].note.startswith("possible violation, verify manually: safety stop: network guard")
    assert opener.context.pages == 0 and opener.context.closed and opener.calls == 1


@contextlib.contextmanager
def fake_playwright():
    yield object()


def run_with_fakes(monkeypatch, tmp_path, deep_error=None):
    import playwright.sync_api
    cfg = dataclasses.replace(CFG, csv_path=tmp_path / "output" / "rate_parity.csv",
                              screenshot_dir=tmp_path / "screenshots")
    quick, deep_opens = FakeGuarded("logged_out"), []

    def open_deep(pw, cfg_):
        deep_opens.append(pw)
        if deep_error:
            raise deep_error
        return FakeGuarded("profile")

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", fake_playwright)
    monkeypatch.setattr(browser, "open_quick_context", lambda pw, cfg_: quick)
    monkeypatch.setattr(browser, "open_deep_context", open_deep)
    rows = runner.run(cfg, dry_run=True)
    return rows, quick, deep_opens, cfg


def test_run_never_opens_the_profile_when_no_site_needs_it(monkeypatch, tmp_path):
    rows, quick, deep_opens, cfg = run_with_fakes(monkeypatch, tmp_path)
    assert len(rows) == 4 and all(r.status is Status.IN_PARITY for r in rows)
    assert deep_opens == [] and quick.closed
    assert cfg.csv_path.is_file()


def test_run_carries_on_when_the_profile_is_locked(monkeypatch, tmp_path):
    monkeypatch.setattr(FakeCollector, "ota_prices", {"r1": D("9000"), "r2": D("12000")})
    rows, quick, deep_opens, cfg = run_with_fakes(monkeypatch, tmp_path, OSError("profile in use"))
    assert len(deep_opens) == 1 and quick.closed
    assert [r.note for r in rows if r.status is Status.COULD_NOT_CHECK] == [LOCKED_NOTE, LOCKED_NOTE]
    assert sum(r.status is Status.IN_PARITY for r in rows) == 2
    assert cfg.csv_path.is_file()  # results still saved


def test_save_failure_still_reports_then_raises(monkeypatch, tmp_path, capsys):
    from rate_parity import alerts, storage
    sent = []
    monkeypatch.setattr(storage, "save_rows", lambda *a: (_ for _ in ()).throw(PermissionError("csv is open")))
    monkeypatch.setattr(alerts, "send_daily_summary", lambda rows, *a: sent.append(len(rows)))
    cfg = dataclasses.replace(CFG, csv_path=tmp_path / "output" / "rate_parity.csv")
    rows = check(browser.LazyContext(Opener()))
    with pytest.raises(PermissionError):
        runner._store_and_report(cfg, rows, dry_run=False)
    assert sent == [2] and "Rate parity check" in capsys.readouterr().out
    assert list((tmp_path / "output").glob("report_*.txt"))


# --- R2: a page that loads but gives no price is flagged, not "sold out" -----

def test_loaded_page_with_no_price_marks_every_room_site_broken(monkeypatch):
    monkeypatch.setattr(FakeCollector, "ota_prices", {})
    rows = check(browser.LazyContext(Opener()))
    assert [(r.status, r.note) for r in rows] == [(Status.COULD_NOT_CHECK, BROKEN_NOTE)] * 2
    assert all(r.website_search_price == D("10000") for r in rows)  # Zen still shown


def test_page_that_says_sold_out_is_a_full_house_not_a_broken_site(monkeypatch):
    monkeypatch.setattr(FakeCollector, "ota_prices", {})
    monkeypatch.setattr(FakeCollector, "page_says_sold_out", True, raising=False)
    rows = check(browser.LazyContext(Opener()))
    assert [r.note for r in rows] == ["no OTA price (room sold out or not listed)"] * 2


@pytest.mark.parametrize("text, rooms, full_house", [
    ("Premium Cottage Sold out! Deluxe Mountain View Sold out!", 5, False),  # Agoda, 10 Oct: 2 of 5
    ("Sold out! " * 5, 5, True),
    ("Sorry, no rooms available for your dates", 5, True),
    ("Deluxe Room 3,400 +193 taxes and fees", 5, False),
])
def test_only_a_whole_sold_out_page_counts_as_a_full_house(text, rooms, full_house):
    from rate_parity.collectors.generic import _says_sold_out
    assert _says_sold_out(text, rooms) is full_house


def test_one_room_read_is_not_a_broken_site(monkeypatch):
    monkeypatch.setattr(FakeCollector, "ota_prices", {"r1": D("12000")})
    rows = check(browser.LazyContext(Opener()))
    assert rows[0].status is Status.IN_PARITY
    assert rows[1].note == "no OTA price (room sold out or not listed)"


def test_page_that_did_not_load_keeps_its_own_reason(monkeypatch):
    monkeypatch.setattr(FakeCollector, "load_error", SelectorMissing("room list did not load (page showed: 'x')"))
    rows = check(browser.LazyContext(Opener()))
    assert all(r.note.startswith("error: SelectorMissing: room list did not load") for r in rows)


def test_site_not_set_up_is_not_broken():
    rows = check(browser.LazyContext(Opener()), dataclasses.replace(OTA, ready=False))
    assert all(r.note.startswith("not set up yet") for r in rows)


def row(ota, room, status, note="", ota_price=None, gap=None):
    r = CheckRow(ota=ota, property="Zen", room=room, meal_plan="Room only", cancellation="Free cancellation",
                 checkin="2026-10-08", checkout="2026-10-09", adults=2)
    r.search_price, r.website_search_price, r.status, r.note, r.gap_pct = ota_price, D("1545.60"), status, note, gap
    return r


AGODA_BROKEN = ("SITE BROKEN? 0 of 5 rooms read on a loaded page - layout may have changed; "
                "run: python -m rate_parity check --site agoda")
SHOWN = [
    row("MakeMyTrip", "Standard", Status.IN_PARITY, ota_price=D("1901")),
    row("Agoda", "Standard", Status.COULD_NOT_CHECK, AGODA_BROKEN),
    row("Cleartrip", "Standard", Status.COULD_NOT_CHECK, "not set up yet (selectors still TODO in config)"),
]


def test_report_shows_broken_cell_and_lists_broken_sites_in_footer():
    text = build_table(SHOWN, datetime(2026, 10, 8, 10, 0))
    line = next(l for l in text.splitlines() if l.startswith("Standard"))
    assert "- BROKEN?" in line and "- n/a" in line
    assert "SITE BROKEN? (page loaded, 0 rooms read - layout may have changed): Agoda" in text
    assert "python -m rate_parity check --config config.example.yaml --site agoda" in text
    text.encode("ascii")


def test_report_without_broken_sites_has_no_broken_footer():
    assert "SITE BROKEN" not in build_table(SHOWN[:1], datetime(2026, 10, 8, 10, 0))


def test_whatsapp_lists_broken_sites_first_and_short():
    rows = SHOWN + [row("Goibibo", "Standard", Status.VIOLATION, gap=D("-3.00")),
                    row("Booking.com", "Standard", Status.COULD_NOT_CHECK, "re-login to Booking.com")]
    text = wati.build_summary(rows, date(2026, 10, 8))
    broken = text.index("SITE BROKEN?: Agoda")
    assert broken < text.index("VIOLATIONS") < text.index("NOT CHECKED")
    assert "layout may have changed" not in text  # the long note is not repeated
    assert "Booking.com: re-login to Booking.com" in text


def test_whatsapp_broken_sites_survive_truncation():
    rows = [row("Agoda", "Standard", Status.COULD_NOT_CHECK, AGODA_BROKEN)]
    rows += [row(f"OTA{i}", "Standard", Status.VIOLATION, gap=D("-9.99")) for i in range(100)]
    text = wati.build_summary(rows, date(2026, 10, 8), max_chars=300)
    assert len(text) <= 300 and "SITE BROKEN?: Agoda" in text and text.endswith("(see sheet)")
