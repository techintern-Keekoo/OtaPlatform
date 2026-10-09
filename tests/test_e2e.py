"""End-to-end: runner.run(cfg, dry_run=True) in a REAL Chromium, with no internet.

What is real: config.example.yaml (loaded with the real TODO/readiness logic),
the runner, both guarded browser contexts, SafePage, the NetworkGuard, the
collectors, compare rules, CSV storage and the report file.
What is fake: the web. Agoda and MakeMyTrip pages are served by a test route
(registered after the guard, so it runs first; everything else falls through
to the real guard), the eZee website reply is a fake HTTP session, and the
clock is frozen. DNS is disabled in the browser, so nothing can leave the
machine even if the guard let it through.

Skipped unless a Chromium binary is found (OTA_E2E_CHROMIUM, default the
sandbox path), so CI and the office PC are unaffected.
"""
from __future__ import annotations

import base64
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

CHROMIUM = os.environ.get("OTA_E2E_CHROMIUM", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
if not Path(CHROMIUM).is_file():
    pytest.skip(f"no Chromium binary at {CHROMIUM} (set OTA_E2E_CHROMIUM)", allow_module_level=True)
sync_api = pytest.importorskip("playwright.sync_api")

import dataclasses  # noqa: E402

from rate_parity import browser, runner, safety  # noqa: E402
from rate_parity.alerts import wati  # noqa: E402
from rate_parity.collectors import ezee  # noqa: E402
from rate_parity.config import load_config  # noqa: E402
from rate_parity.models import COLUMNS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 22, 10, 0, tzinfo=runner.IST)  # nights checked: 22 Oct and 23 Oct
NIGHT1, NIGHT2 = "2026-10-22", "2026-10-23"

# --- the Keekoo website (eZee): before tax / tax / final ---------------------
ZEN = {
    "4757200000000000001": (1545.60, 77.28, 1622.88),   # standard_garden
    "4757200000000000002": (1787.10, 89.36, 1876.46),   # deluxe_valley
    "4757200000000000003": (2000.00, 100.00, 2100.00),  # deluxe_mountain
    "4757200000000000005": (3000.00, 150.00, 3150.00),  # family_suite
    "4757200000000000006": (2500.00, 125.00, 2625.00),  # premium_cottage
}
PAX2 = base64.b64encode(b'{"room_1":{"adult":2,"child":0}}').decode()


def ezee_reply() -> str:
    records = [{"RateTypeId": "x", "Room_Name": "Book now! Save now!", "RoomTypeUnkId": type_id,
                "default_pax": PAX2, "TaxRate": tax, "TotalPrice_ExclusiveAll": excl,
                "TotalPrice_InclusiveAll": incl, "Prepaid_Noncancel_Nonrefundable": 0, "showMinmsg": 0}
               for type_id, (excl, tax, incl) in ZEN.items()]
    return "<html><script>var rooms = " + json.dumps(records) + ";</script></html>"


class FakeEzeeSession:
    calls: list = []

    def __init__(self):
        self.headers = {}

    def get(self, url, timeout=None):
        FakeEzeeSession.calls.append(("GET", url, None))
        return _Reply("")

    def post(self, url, data=None, timeout=None):
        FakeEzeeSession.calls.append(("POST", url, data))
        return _Reply(ezee_reply())


class _Reply:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


# --- Agoda: (offer text, price) per room, same both nights -------------------
AGODA = {
    # cheaper non-refundable offer listed FIRST: free cancellation must still win
    "Standard Garden View Room": [("Non-refundable", "1,200"), ("Free Cancellation", "2,049")],  # > Zen: ok
    "Deluxe Valley View Room": [("Free Cancellation", "1,700")],     # <= Zen 1,787.10: verify manually
    "Deluxe Mountain View": [("Non-refundable", "2,300")],           # only non-ref, > Zen: ok non-ref
    "Premium Cottage": [("Free Cancellation", "2,600")],             # > Zen 2,500: ok
    "Family Suite": [("Free Cancellation", "3,000")],                # == Zen: verify manually
}


def agoda_html() -> str:
    boxes = []
    for name, offers in AGODA.items():
        offer_html = "".join(
            f"""<div data-testid="room-offer"><div>{text}</div><div>Room only</div>
                <div data-testid="room-offer-final-price">₹ {price}</div>
                <button data-testid="book-button" onclick="window.__booked=1;
                  fetch('https://www.agoda.com/__clicked__/book');
                  fetch('https://api.razorpay.com/pay', {{method: 'POST', body: 'amount={price}'}})">Book</button>
               </div>""" for text, price in offers)
        boxes.append(f"""<div data-testid="room-item">
            <div data-testid="room-header"><span data-testid="room-name">{name}</span></div>{offer_html}</div>""")
    # the page itself pings a payment host on load: the guard must abort it
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Zen Manali - Agoda</title></head>
        <body><h1>Zen Manali by Keekoo Stays</h1><div style="height:1500px">Photos and reviews</div>
        <img src="https://checkout.razorpay.com/v1/preload.png" alt="">
        <h2>Select your room</h2>{''.join(boxes)}</body></html>"""


# --- MakeMyTrip: (old price, price, taxes) per room, per night ---------------
MMT = {
    NIGHT1: {
        "Standard Garden View Room": ("3,680", "1,901", "245"),       # 2,146 > Zen 1,622.88: IN_PARITY
        "Deluxe Valley Facing Room": ("3,100", "1,700", "85"),        # 1,785 < Zen 1,876.46: VIOLATION
        "Deluxe Mountain View Room": ("3,900", "1,990", "200"),       # suspect, but 2,190 > 2,100: FALSE_ALARM
        "Family Suite with Mountain View": ("5,500", "3,000", "150"),  # 3,150 == Zen 3,150: VIOLATION
        # Premium Cottage Room: not listed (sold out)
    },
    NIGHT2: {
        "Standard Garden View Room": ("3,680", "1,901", "245"),
        "Deluxe Valley Facing Room": ("3,100", "2,000", "100"),       # 2,100 > Zen: IN_PARITY tonight+1
        "Deluxe Mountain View Room": ("3,900", "1,990", "200"),
        "Family Suite with Mountain View": ("5,500", "3,000", "150"),
    },
}


def mmt_html(night: str) -> str:
    cards = "".join(f"""
        <div class="rmCard"><div class="rmHead"><span data-testid="rmType__roomName">{name}</span></div>
          <div class="rmPlan"><div>Room With Free Cancellation</div><div>Free Cancellation before 20 Oct</div>
            <div><s>₹ {old}</s></div><div>₹ {price}</div><div>+₹ {tax} Taxes &amp; Fees Per Night</div>
            <button data-testid="{i}23-selectRoom" onclick="window.__booked=1;
              fetch('https://www.makemytrip.com/__clicked__/book');
              fetch('https://api.razorpay.com/pay', {{method: 'POST', body: 'x=1'}})">BOOK NOW</button>
          </div></div>""" for i, (name, (old, price, tax)) in enumerate(MMT[night].items()))
    # The "Upgrade" box comes first and repeats a room name with a decoy price.
    upgrade = """<div class="upgrade"><div>Upgrade to a better room</div>
        <span data-testid="rmType__roomName">Deluxe Valley Facing Room</span>
        <div>₹ 999</div><div>+₹ 11 Taxes &amp; Fees Per Night</div>
        <button data-testid="999-selectRoom">UPGRADE</button></div>"""
    # a booking-commit POST sent by the page on load: the guard must abort it
    beacon = """<script>fetch('https://www.makemytrip.com/api/v2/order/create',
        {method: 'POST', body: '{"placeOrder": true}'}).catch(() => {});</script>"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Zen Manali - MakeMyTrip</title></head>
        <body><h1>Zen Manali by Keekoo Stays</h1>{upgrade}<div class="rooms">{cards}</div>{beacon}</body></html>"""


class FakeWeb:
    """Route registered on each guarded context AFTER the guard, so it runs first."""

    def __init__(self):
        self.seen: list[tuple[str, str, str]] = []   # (method, url, resource type) of every request
        self.documents: dict[tuple[str, str], int] = {}

    def handle(self, route):
        request = route.request
        parts = urlsplit(request.url)
        host = parts.hostname or ""
        self.seen.append((request.method, request.url, request.resource_type))
        if request.method != "GET":  # never answer a POST ourselves: the real guard must see it
            route.fallback()
            return
        query = parse_qs(parts.query)
        if host == "www.agoda.com" and request.resource_type == "document":
            self._count("agoda", query["checkIn"][0])
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=agoda_html())
        elif host == "www.makemytrip.com" and request.resource_type == "document":
            ci = query["checkin"][0]  # MMDDYYYY
            night = f"{ci[4:]}-{ci[:2]}-{ci[2:4]}"
            self._count("makemytrip", night)
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=mmt_html(night))
        elif host.endswith(("agoda.com", "makemytrip.com")):
            route.fulfill(status=204, body="")  # favicon etc.
        else:
            route.fallback()  # e.g. payment hosts: the real NetworkGuard decides

    def _count(self, site, night):
        self.documents[(site, night)] = self.documents.get((site, night), 0) + 1


class RouteSpy:
    """Wraps the route the guard gets, to record what it let through or aborted."""

    def __init__(self, route, log):
        self._route, self._log, self.request = route, log, route.request

    def continue_(self, *args, **kwargs):
        self._log.append(("continued", self.request.method, self.request.url))
        return self._route.continue_(*args, **kwargs)

    def abort(self, *args, **kwargs):
        self._log.append(("aborted", self.request.method, self.request.url))
        return self._route.abort(*args, **kwargs)


@pytest.fixture(scope="module")
def e2e(tmp_path_factory):
    """One full run, shared by the tests below."""
    with pytest.MonkeyPatch.context() as monkeypatch:
        yield _run(tmp_path_factory.mktemp("e2e"), monkeypatch)


def _run(tmp_path, monkeypatch):
    cfg = load_config(ROOT / "config.example.yaml", check_placeholders=True)
    cfg = dataclasses.replace(
        cfg,
        browser=dataclasses.replace(cfg.browser, profile_dir=tmp_path / "profile", channel=None, headless=True,
                                    timeout_ms=15000, delay_seconds=(0.0, 0.0)),
        screenshot_dir=tmp_path / "screenshots",
        csv_path=tmp_path / "output" / "rate_parity.csv",
    )
    # readiness comes from the real TODO logic in config.example.yaml
    assert cfg.website.kind == "ezee"
    assert (cfg.sites["agoda"].ready, cfg.sites["agoda"].deep_ready) == (True, False)
    assert (cfg.sites["makemytrip"].ready, cfg.sites["makemytrip"].deep_ready) == (True, True)
    assert not any(cfg.sites[k].ready for k in ("booking_com", "goibibo", "cleartrip"))

    monkeypatch.setattr(runner, "now_ist", lambda: NOW)
    FakeEzeeSession.calls = []
    monkeypatch.setattr(ezee.requests, "Session", FakeEzeeSession)

    # Launch the local Chromium, headless, with DNS switched off: no internet at all.
    no_dns = "--host-resolver-rules=MAP * ~NOTFOUND"
    BrowserType = sync_api.BrowserType
    real_launch, real_persistent = BrowserType.launch, BrowserType.launch_persistent_context

    def launch(self, **kwargs):
        return real_launch(self, **{**kwargs, "executable_path": CHROMIUM, "channel": None,
                                    "headless": True, "args": [no_dns]})

    def launch_persistent_context(self, **kwargs):
        return real_persistent(self, **{**kwargs, "executable_path": CHROMIUM, "channel": None,
                                        "headless": True, "args": [no_dns]})

    monkeypatch.setattr(BrowserType, "launch", launch)
    monkeypatch.setattr(BrowserType, "launch_persistent_context", launch_persistent_context)

    guard_log: list = []
    real_handle = safety.NetworkGuard.handle
    monkeypatch.setattr(safety.NetworkGuard, "handle",
                        lambda self, route: real_handle(self, RouteSpy(route, guard_log)))

    web, contexts = FakeWeb(), []
    real_init = browser.GuardedContext.__init__

    def init(self, context, cfg_, login_state, closers):
        real_init(self, context, cfg_, login_state, closers)  # installs the real guard first
        context.route("**/*", web.handle)                      # later route = runs first
        contexts.append(self)

    monkeypatch.setattr(browser.GuardedContext, "__init__", init)

    clicks: list = []
    real_click = safety.SafePage.click_step
    monkeypatch.setattr(safety.SafePage, "click_step",
                        lambda self, selector: (clicks.append(selector), real_click(self, selector))[1])

    # Speed only: same scrolling, shorter pauses between wheel steps.
    real_through, real_stable = safety.SafePage.scroll_through, safety.SafePage.scroll_until_stable
    monkeypatch.setattr(safety.SafePage, "scroll_through", lambda self: real_through(self, steps=3, pause_ms=50))
    monkeypatch.setattr(safety.SafePage, "scroll_until_stable",
                        lambda self, selector: real_stable(self, selector, pause_ms=50))

    sent: list = []
    monkeypatch.setattr(wati, "send_summary", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(wati.requests, "post", lambda *a, **k: sent.append(a))

    rows = runner.run(cfg, dry_run=True)
    return {"cfg": cfg, "rows": rows, "web": web, "guard_log": guard_log, "contexts": contexts,
            "clicks": clicks, "sent": sent}


def csv_rows(cfg):
    with open(cfg.csv_path, encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def find(table, ota, room, night):
    matches = [r for r in table if (r["ota"], r["room"], r["checkin"]) == (ota, room, night)]
    assert len(matches) == 1, (ota, room, night)
    return matches[0]


def test_full_run_reads_prices_applies_rule_and_stores_results(e2e):
    cfg = e2e["cfg"]
    raw = csv_rows(cfg)
    assert raw[0] == COLUMNS
    assert len(raw) == 1 + 50  # 2 nights x 5 rooms x 5 OTAs
    table = [dict(zip(raw[0], r)) for r in raw[1:]]
    assert len(e2e["rows"]) == 50

    def check(ota, room, night, **expected):
        row = find(table, ota, room, night)
        assert {k: row[k] for k in expected} == expected, row

    std, valley, mountain = "Standard garden view room", "Deluxe valley facing room", "Deluxe mountain view room"
    family, cottage = "Family Suite with Mountain View Room", "Premium cottage Mountain View Room"

    # MakeMyTrip: final = price + taxes, read from the room list (one page load)
    check("MakeMyTrip", std, NIGHT1, status="IN_PARITY", search_price="1901", gst="245",
          final_payable="2146", website_search_price="1545.60", website_final="1622.88", gap_pct="32.23")
    check("MakeMyTrip", valley, NIGHT1, status="VIOLATION", search_price="1700", gst="85",
          final_payable="1785", website_final="1876.46", gap_pct="-4.87")  # not the 999 "Upgrade" decoy
    check("MakeMyTrip", mountain, NIGHT1, status="FALSE_ALARM", search_price="1990",
          final_payable="2190", website_final="2100.00", gap_pct="4.29")
    check("MakeMyTrip", family, NIGHT1, status="VIOLATION", final_payable="3150",
          website_final="3150.00", gap_pct="0.00")  # equal is a violation: Zen must be cheaper
    check("MakeMyTrip", cottage, NIGHT1, status="COULD_NOT_CHECK", search_price="", final_payable="",
          note="no OTA price (room sold out or not listed)")
    check("MakeMyTrip", valley, NIGHT2, status="IN_PARITY", search_price="2000",
          final_payable="2100", gap_pct="11.91")  # the second night's page, not the first's
    mmt = find(table, "MakeMyTrip", valley, NIGHT1)
    assert mmt["ota_offer"] == "free cancellation" and Path(mmt["screenshot_path"]).is_file()

    # Agoda: no checkout check set up, so suspects become "verify manually"
    check("Agoda", std, NIGHT1, status="IN_PARITY", search_price="2049", ota_offer="free cancellation",
          final_payable="", website_final="1622.88")
    check("Agoda", valley, NIGHT1, status="COULD_NOT_CHECK", search_price="1700", final_payable="", gap_pct="")
    assert find(table, "Agoda", valley, NIGHT1)["note"].startswith("possible violation, verify manually")
    check("Agoda", family, NIGHT2, status="COULD_NOT_CHECK", search_price="3000")
    check("Agoda", mountain, NIGHT1, status="IN_PARITY", search_price="2300", ota_offer="non-refundable")
    check("Agoda", cottage, NIGHT2, status="IN_PARITY", search_price="2600")

    # Not set up: never opened, reported as such
    for ota in ("Booking.com", "Goibibo", "Cleartrip"):
        for row in (r for r in table if r["ota"] == ota):
            assert row["status"] == "COULD_NOT_CHECK" and row["search_price"] == ""
            assert row["note"].startswith("not set up yet")
            assert row["website_search_price"] != ""  # Zen still shown next to it

    statuses = {s: sum(r["status"] == s for r in table) for s in
                ("VIOLATION", "FALSE_ALARM", "IN_PARITY", "COULD_NOT_CHECK")}
    assert statuses == {"VIOLATION": 3, "FALSE_ALARM": 2, "IN_PARITY": 9, "COULD_NOT_CHECK": 36}

    # The website (eZee) is searched once per night, with the engine's date format
    posts = [c for c in FakeEzeeSession.calls if c[0] == "POST"]
    assert [p[2]["checkIn"] for p in posts] == ["22_10_2026", "23_10_2026"]

    # The report file sits next to the CSV and shows Zen next to each OTA
    reports = list(cfg.csv_path.parent.glob("report_*.txt"))
    assert [p.name for p in reports] == ["report_20261022_1000.txt"]
    text = reports[0].read_text(encoding="utf-8")
    text.encode("ascii")
    assert "Check-in 2026-10-22" in text and "Check-in 2026-10-23" in text
    line = next(l for l in text.splitlines() if l.startswith("Deluxe valley facing room"))  # first night
    assert "1,787 (1,876)" in line         # Zen: before tax (final)
    assert "1,700 (1,785) LOWER!" in line  # MakeMyTrip
    assert "1,700 VERIFY" in line          # Agoda
    assert "3 problem(s)" in text
    assert "Not set up yet: Booking.com, Cleartrip, Goibibo" in text

    assert e2e["sent"] == []  # dry run: no WhatsApp


def test_each_ota_page_is_loaded_once_per_night(e2e):
    assert e2e["web"].documents == {
        ("agoda", NIGHT1): 1, ("agoda", NIGHT2): 1,
        ("makemytrip", NIGHT1): 1, ("makemytrip", NIGHT2): 1,
    }
    hosts = {urlsplit(url).hostname for _, url, kind in e2e["web"].seen if kind == "document"}
    assert hosts == {"www.agoda.com", "www.makemytrip.com"}  # not-set-up OTAs were never opened


def test_never_clicks_book_and_nothing_reaches_a_payment_host(e2e):
    assert e2e["clicks"] == []
    seen_urls = [url for _, url, _ in e2e["web"].seen]
    assert not any("__clicked__" in u or "api.razorpay.com/pay" in u for u in seen_urls)  # no button fired

    # the search context had the real guard and passed self_check; no checkout check was
    # needed (MakeMyTrip finals come from its room list), so the logged-in profile never opened
    assert len(e2e["contexts"]) == 1 and all(c.guard.installed for c in e2e["contexts"])
    assert not e2e["cfg"].browser.profile_dir.exists()
    log = e2e["guard_log"]
    # what the pages tried on their own was stopped by the real guard
    aborted = [url for kind, _, url in log if kind == "aborted"]
    assert any(u.startswith("https://checkout.razorpay.com/") for u in aborted)
    assert any(u.endswith("/api/v2/order/create") for u in aborted)
    # and nothing at all was let through to the network
    continued = [url for kind, _, url in log if kind == "continued"]
    assert not any(k in (urlsplit(u).hostname or "") for u in continued for k in safety.PAYMENT_HOST_KEYWORDS)
    assert continued == []
