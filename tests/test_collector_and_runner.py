from datetime import date
from decimal import Decimal as D

import pytest

from rate_parity import runner
from rate_parity.collectors import RoomMismatch
from rate_parity.collectors.generic import GenericCollector
from rate_parity.config import parse_config
from rate_parity.models import Stay, Status, Summary
from rate_parity.safety import Blocked, SafePage
from tests.fakes import FakeElement, FakePage

SUMMARY = {
    "ready": "#summary", "room_name": "#room", "meal_plan": "#meal", "cancellation": "#cancel",
    "final": "#final", "room_price": "#price", "gst": "#gst", "fees": "#fees", "discount": None,
}


def site(domain, **extra):
    return {
        "enabled": True, "allowed_domains": [domain],
        "search_url": f"https://www.{domain}/h?ci={{checkin}}&co={{checkout}}&a={{adults}}",
        "rooms": {"r1": {"search_price": "#search-price", "steps": ["#select", "#next"], "summary": SUMMARY}},
        **extra,
    }


RAW = {
    "property": {"name": "Keekoo Test"},
    "min_margin_pct": 1,
    "stay": {"days_ahead": [7], "nights": 1, "adults": 2},
    "rooms": [{"id": "r1", "room": "Deluxe", "meal_plan": "Breakfast", "cancellation": "Free cancellation"}],
    "browser": {"profile_dir": "chrome-profile", "delay_seconds": [0, 0]},
    "sites": {
        "website": site("keekoo.test"),
        "booking_com": site("booking.com", label="Booking.com", captcha_selectors=["#captcha"]),
    },
}
CFG = parse_config(RAW)
STAY = Stay(date(2026, 10, 15), date(2026, 10, 16), 2)


def summary_page(**overrides):
    elements = {
        "#select": FakeElement("Select"), "#next": FakeElement("Continue"),
        "#summary": FakeElement("Summary"), "#room": FakeElement("Deluxe Room"),
        "#meal": FakeElement("Breakfast included"), "#cancel": FakeElement("Free cancellation till 13 Oct"),
        "#final": FakeElement("₹ 9,000"), "#price": FakeElement("₹ 8,036"), "#gst": FakeElement("₹ 964"),
        "body": FakeElement("Your booking summary"),
    }
    elements.update(overrides)
    fake = FakePage({k: v for k, v in elements.items() if v is not None})
    ota = CFG.sites["booking_com"]
    return fake, SafePage(fake, ota.key, ota.allowed_domains, ota.step_selectors(), "unused")


def test_deep_check_reads_summary_and_stops(tmp_path):
    fake, page = summary_page()
    page._screenshot_dir = tmp_path
    result = GenericCollector(CFG.sites["booking_com"]).deep_check(page, STAY, "r1")
    assert result.final == D("9000") and result.gst == D("964") and result.room_price == D("8036")
    assert result.fees is None and result.notes == ["fees not shown"]
    assert fake.visited == ["https://www.booking.com/h?ci=2026-10-15&co=2026-10-16&a=2"]
    assert len(fake.screenshots) == 1


def test_deep_check_rejects_different_meal_plan(tmp_path):
    _, page = summary_page(**{"#meal": FakeElement("Room only")})
    page._screenshot_dir = tmp_path
    with pytest.raises(RoomMismatch):
        GenericCollector(CFG.sites["booking_com"]).deep_check(page, STAY, "r1")


def test_captcha_is_blocked():
    _, page = summary_page(**{"#captcha": FakeElement("")})
    with pytest.raises(Blocked) as info:
        GenericCollector(CFG.sites["booking_com"]).deep_check(page, STAY, "r1")
    assert info.value.flag == "verify manually"


def test_login_text_is_blocked():
    _, page = summary_page(body=FakeElement("Please sign in to continue"))
    with pytest.raises(Blocked) as info:
        GenericCollector(CFG.sites["booking_com"]).quick_scan(page, STAY)
    assert info.value.flag == "re-login to Booking.com"


class FakeGuardedContext:
    def __init__(self, login_state):
        self.login_state = login_state

    def new_safe_page(self, site):
        return FakePage()


class FakeCollector:
    """Website search 10000; OTA search and checkout prices set per test."""
    ota_search = D("9000")
    ota_final = D("9500")
    ota_error = None

    def __init__(self, site):
        self.site = site

    def quick_scan(self, page, stay):
        return {"r1": D("10000") if self.site.key == "website" else self.ota_search}

    def deep_check(self, page, stay, room_id):
        if self.site.key == "website":
            return Summary(final=D("10000"), screenshot_path="web.png")
        if self.ota_error:
            raise self.ota_error
        return Summary(final=self.ota_final, login_state="logged_in", screenshot_path="ota.png")


@pytest.fixture
def check(monkeypatch):
    monkeypatch.setattr(runner, "make_collector", FakeCollector)
    monkeypatch.setattr(FakeCollector, "ota_error", None)

    def _run():
        stay_check = runner.StayCheck(CFG, FakeGuardedContext("logged_out"), FakeGuardedContext("profile"), STAY)
        return stay_check.check_ota(CFG.sites["booking_com"])[0]
    return _run


def test_violation(check):
    row = check()
    assert row.status is Status.VIOLATION and row.gap_pct == D("-5.00")
    assert row.website_final == D("10000") and row.screenshot_path == "ota.png"


def test_false_alarm(check, monkeypatch):
    monkeypatch.setattr(FakeCollector, "ota_final", D("10200"))  # 2% above Zen, margin is 1%
    assert check().status is Status.FALSE_ALARM


def test_not_suspect_skips_deep_check(check, monkeypatch):
    monkeypatch.setattr(FakeCollector, "ota_search", D("10200"))  # above Zen by more than the 1% margin
    row = check()
    assert row.status is Status.IN_PARITY and row.final_payable is None
    assert row.login_state == "logged_out"


def test_blocked_becomes_could_not_check(check, monkeypatch):
    monkeypatch.setattr(FakeCollector, "ota_error", Blocked("login wall", "re-login to Booking.com"))
    row = check()
    assert row.status is Status.COULD_NOT_CHECK
    assert row.note == "possible violation, verify manually: re-login to Booking.com"


def test_crash_becomes_could_not_check(check, monkeypatch):
    monkeypatch.setattr(FakeCollector, "ota_error", RuntimeError("boom"))
    row = check()
    assert row.status is Status.COULD_NOT_CHECK and "boom" in row.note


def _no_click_cfg():
    import copy
    raw = copy.deepcopy(RAW)
    room = raw["sites"]["booking_com"]["rooms"]["r1"]
    room["steps"] = []
    room["summary"] = {**SUMMARY, "final": None, "fees": None}
    return parse_config(raw)


def test_no_click_deep_check_adds_shown_price_and_taxes(tmp_path):
    cfg = _no_click_cfg()
    fake, page = summary_page()
    page._screenshot_dir = tmp_path
    result = GenericCollector(cfg.sites["booking_com"]).deep_check(page, STAY, "r1")
    assert result.final == D("9000")  # 8,036 + 964, both read from the page
    assert "final = room price + taxes as shown on page" in result.notes
    assert fake.visited == ["https://www.booking.com/h?ci=2026-10-15&co=2026-10-16&a=2"]  # no clicks needed


def test_no_click_deep_check_without_taxes_cannot_check(tmp_path):
    from rate_parity.safety import SelectorMissing
    cfg = _no_click_cfg()
    _, page = summary_page(**{"#gst": None})
    page._screenshot_dir = tmp_path
    with pytest.raises(SelectorMissing):
        GenericCollector(cfg.sites["booking_com"]).deep_check(page, STAY, "r1")


def test_config_needs_final_or_price_and_taxes():
    import copy
    from rate_parity.config import ConfigError
    raw = copy.deepcopy(RAW)
    raw["sites"]["booking_com"]["rooms"]["r1"]["summary"] = {**SUMMARY, "final": None, "gst": None}
    with pytest.raises(ConfigError, match="room_price and gst"):
        parse_config(raw)


def test_scroll_to_load_scrolls_before_reading(tmp_path):
    import dataclasses
    site = dataclasses.replace(CFG.sites["booking_com"], scroll_to_load=True)
    fake = FakePage({"#search-price": FakeElement("₹ 9,000"), "body": FakeElement("rooms")})
    page = SafePage(fake, site.key, site.allowed_domains, site.step_selectors(), tmp_path)
    assert GenericCollector(site).quick_scan(page, STAY) == {"r1": D("9000")}
    assert fake.mouse.wheel_calls > 0


def test_price_options_fall_back_and_remember_offer_type(tmp_path):
    import copy
    raw = copy.deepcopy(RAW)
    raw["sites"]["booking_com"]["rooms"]["r1"]["search_price"] = [
        {"offer": "free cancellation", "selector": "#fc"},
        {"offer": "non-refundable", "selector": "#nr"},
    ]
    site = parse_config(raw).sites["booking_com"]
    fake = FakePage({"#nr": FakeElement("₹ 2,336"), "body": FakeElement("rooms")})
    page = SafePage(fake, site.key, site.allowed_domains, site.step_selectors(), tmp_path)
    collector = GenericCollector(site)
    assert collector.quick_scan(page, STAY) == {"r1": D("2336")}
    assert collector.offer_types == {"r1": "non-refundable"}


def test_option_b_compares_first_offer_type_only():
    import copy
    raw = copy.deepcopy(RAW)
    raw["compare_non_refundable"] = False
    raw["sites"]["booking_com"]["rooms"]["r1"]["search_price"] = [
        {"offer": "free cancellation", "selector": "#fc"}, {"offer": "non-refundable", "selector": "#nr"}]
    assert parse_config(raw).sites["booking_com"].rooms["r1"].price_options == (("free cancellation", "#fc"),)


def test_suspect_without_checkout_setup_is_verify_manually(check, monkeypatch):
    import dataclasses
    stay_check = runner.StayCheck(CFG, FakeGuardedContext("logged_out"), FakeGuardedContext("profile"), STAY)
    monkeypatch.setattr(runner, "make_collector", FakeCollector)
    ota = dataclasses.replace(CFG.sites["booking_com"], deep_ready=False)
    row = stay_check.check_ota(ota)[0]
    assert row.status is Status.COULD_NOT_CHECK and row.note.startswith("possible violation, verify manually")


def _loading_site(**kw):
    import dataclasses
    return dataclasses.replace(CFG.sites["booking_com"], scroll_to="text=Select your room", wait_for=".room-box", **kw)


def test_load_waits_for_room_list(tmp_path):
    site = _loading_site()
    fake = FakePage({".room-box": FakeElement("Deluxe"), "text=Select your room": FakeElement("Select your room"),
                     "body": FakeElement("rooms")})
    page = SafePage(fake, site.key, site.allowed_domains, site.step_selectors(), tmp_path)
    GenericCollector(site).load(page, STAY)
    assert len(fake.visited) == 1


def test_load_reloads_once_then_gives_a_clear_reason(tmp_path):
    from rate_parity.safety import SelectorMissing
    site = _loading_site()
    fake = FakePage({"body": FakeElement("Select your room About us")})  # empty room list
    page = SafePage(fake, site.key, site.allowed_domains, site.step_selectors(), tmp_path)
    with pytest.raises(SelectorMissing, match="room list did not load"):
        GenericCollector(site).load(page, STAY)
    assert len(fake.visited) == 2  # tried, reloaded once, stopped


def test_failed_load_waits_before_retry_and_keeps_what_the_page_showed(tmp_path):
    from rate_parity.safety import SelectorMissing
    site = _loading_site()
    fake = FakePage({"body": FakeElement("Sold Out for your dates")})
    waits = []
    fake.wait_for_timeout = waits.append
    page = SafePage(fake, site.key, site.allowed_domains, site.step_selectors(), tmp_path)
    with pytest.raises(SelectorMissing, match="page showed: 'Sold Out for your dates'") as err:
        GenericCollector(site).load(page, STAY)
    assert 20000 in waits  # polite pause before the second visit
    assert fake.screenshots and "load_failed_" in fake.screenshots[-1]
    text_file = str(err.value).rsplit(" and ", 1)[1]
    assert open(text_file, encoding="utf-8").read() == "Sold Out for your dates"


MMT_CARD = ("Deluxe Valley Facing Room\nRoom With Free Cancellation\nFree Cancellation before 19 Oct\n"
            "₹ 4,255\n₹ 2,199\n+₹ 283 Taxes & Fees Per Night\nBOOK NOW\nLogin Now and get this for ₹2,157 or less")
MMT_PATTERN = r"₹\s*(?P<price>[\d,]+)\s*\+\s*₹\s*(?P<tax>[\d,]+)\s*Taxes\s*&\s*Fees"


def test_read_price_text_takes_price_before_taxes_not_the_old_price(tmp_path):
    from rate_parity.collectors.generic import read_price_text
    page = SafePage(FakePage({"#card": FakeElement(MMT_CARD)}), "makemytrip", ["makemytrip.com"], [], tmp_path)
    assert read_price_text(page, ("#card", MMT_PATTERN)) == (D("2199"), D("283"), "free cancellation")
    assert read_price_text(page, ("#missing", MMT_PATTERN)) is None


def test_text_room_config_needs_a_price_group():
    import copy
    from rate_parity.config import ConfigError
    raw = copy.deepcopy(RAW)
    raw["sites"]["booking_com"]["rooms"]["r1"] = {"price_text": {"container": "#card", "pattern": r"₹\s*([\d,]+)"}}
    with pytest.raises(ConfigError, match="price"):
        parse_config(raw)
