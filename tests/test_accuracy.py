"""Pricing accuracy: no false LOWER! from a misread, no violation hidden by tax.

Misread guard (compare.implausible_reason + _check_room), the optional
`sanity` config block, per-site price_includes_tax, the website plan name in
row notes, and the Goibibo candidate in config.example.yaml.
"""
import copy
import dataclasses
from datetime import date
from decimal import Decimal as D

import pytest
import yaml

from rate_parity import runner
from rate_parity.compare import SanityLimits, implausible_reason
from rate_parity.config import ConfigError, load_config, parse_config
from rate_parity.models import Stay, Status, Summary
from tests.fakes import FakePage
from tests.test_config_and_storage import EXAMPLE

STAY = Stay(date(2026, 10, 22), date(2026, 10, 23), 2)
MISREAD = "possible violation, verify manually: implausible price ("


# --- the pure rule -------------------------------------------------------------

def test_normal_prices_are_plausible():
    assert implausible_reason(D("1901"), D("1545.60"), D("245")) is None
    assert implausible_reason(D("1700"), D("1787.10")) is None


def test_far_below_or_above_zen_is_a_probable_misread():
    assert implausible_reason(D("500"), D("1545.60")) == "OTA 500 is 32% of Zen 1,546; expected 40-300%"
    assert "323% of Zen" in implausible_reason(D("5000"), D("1545.60"))


def test_ratio_limits_are_inclusive():
    assert implausible_reason(D("400"), D("1000")) is None    # exactly 40%
    assert implausible_reason(D("3000"), D("1000")) is None   # exactly 300%
    assert implausible_reason(D("399.99"), D("1000")) is not None
    assert implausible_reason(D("3000.01"), D("1000")) is not None


def test_taxes_above_30_pct_of_the_price_are_a_misread():
    assert implausible_reason(D("1000"), D("1000"), D("300")) is None
    assert implausible_reason(D("1000"), D("1000"), D("0")) is None
    assert implausible_reason(D("1901"), D("1545.60"), D("900")) == (
        "OTA taxes 900 are 47% of the OTA price 1,901; expected 0-30%")


def test_zero_prices_are_never_plausible():
    assert "not positive" in implausible_reason(D("0"), D("1000"))
    assert "Zen price" in implausible_reason(D("1000"), D("0"))


def test_custom_limits_and_floats_rejected():
    loose = SanityLimits(min_ratio=D("0.2"), max_ratio=D("5"), max_tax_pct=D("50"))
    assert implausible_reason(D("300"), D("1000"), D("140"), loose) is None
    with pytest.raises(TypeError):
        implausible_reason(300.0, D("1000"))
    with pytest.raises(TypeError):
        implausible_reason(D("1000"), D("1000"), 50.0)


# --- config --------------------------------------------------------------------

SUMMARY = {"ready": "#summary", "room_name": "#room", "meal_plan": "#meal", "cancellation": "#cancel",
           "final": "#final", "room_price": "#price", "gst": "#gst", "fees": None, "discount": None}


def _site(domain, **extra):
    return {"enabled": True, "allowed_domains": [domain],
            "search_url": f"https://www.{domain}/h?ci={{checkin}}&co={{checkout}}&a={{adults}}",
            "rooms": {"r1": {"search_price": "#search-price", "steps": ["#select"], "summary": SUMMARY}},
            **extra}


RAW = {
    "property": {"name": "Zen Test"},
    "min_margin_pct": 0,
    "stay": {"days_ahead": [0], "nights": 1, "adults": 2},
    "rooms": [{"id": "r1", "room": "Deluxe", "meal_plan": "Room only", "cancellation": "Free cancellation"}],
    "browser": {"profile_dir": "chrome-profile", "delay_seconds": [0, 0]},
    "sites": {"website": _site("keekoo.test"), "agoda": _site("agoda.com", label="Agoda")},
}


def _raw(**changes):
    raw = copy.deepcopy(RAW)
    raw.update(changes)
    return raw


def test_sanity_defaults_when_absent():
    assert parse_config(_raw()).sanity == SanityLimits()
    assert SanityLimits() == SanityLimits(D("0.4"), D("3.0"), D("30"))


def test_sanity_values_are_read_as_decimal():
    cfg = parse_config(_raw(sanity={"min_ratio": 0.5, "max_ratio": 2, "max_tax_pct": 25}))
    assert cfg.sanity == SanityLimits(D("0.5"), D("2"), D("25"))
    assert parse_config(_raw(sanity={"max_ratio": 4})).sanity == SanityLimits(max_ratio=D("4"))


@pytest.mark.parametrize("sanity", [
    {"min_ratio": 1.2}, {"min_ratio": 0}, {"max_ratio": 0.9}, {"max_ratio": 1},
    {"max_tax_pct": 0}, {"max_tax_pct": 150}, {"min_ratio": True}, {"min_ratio": "0.4"},
    {"max_ratio_pct": 3}, "strict",
])
def test_bad_sanity_values_are_refused(sanity):
    with pytest.raises(ConfigError, match="sanity"):
        parse_config(_raw(sanity=sanity))


def test_price_includes_tax_defaults_to_false_and_must_be_a_bool():
    assert parse_config(_raw()).sites["agoda"].price_includes_tax is False
    raw = _raw()
    raw["sites"]["agoda"]["price_includes_tax"] = True
    assert parse_config(raw).sites["agoda"].price_includes_tax is True
    raw["sites"]["agoda"]["price_includes_tax"] = "yes"
    with pytest.raises(ConfigError, match="price_includes_tax"):
        parse_config(raw)


def test_example_config_has_default_sanity_and_agoda_before_tax():
    cfg = load_config(EXAMPLE)
    assert cfg.sanity == SanityLimits()
    assert cfg.sites["agoda"].price_includes_tax is False


# --- _check_room: the misread guard and tax basis --------------------------------

class Fake:
    """Zen: 1000 before tax + 180 tax = 1180 (plan "Room Only Rate"). OTA set per test."""
    ota_price = D("950")
    ota_tax = None
    ota_summary = None
    deep_calls = 0

    def __init__(self, site):
        self.site = site
        self.needs_browser = site.key != "website"  # website read like eZee: no browser

    def quick_scan(self, page, stay):
        if self.site.key == "website":
            return {"r1": D("1000")}
        self.offer_types, self.evidence = {"r1": "free cancellation"}, "list.png"
        self.card_taxes = {} if self.ota_tax is None else {"r1": self.ota_tax}
        return {"r1": self.ota_price}

    def deep_check(self, page, stay, room_id):
        if self.site.key == "website":
            return Summary(final=D("1180"), room_price=D("1000"), gst=D("180"), screenshot_path="web.html",
                           notes=["website plan: Room Only Rate"])
        type(self).deep_calls += 1
        return self.ota_summary


class Context:
    login_state = "guest"

    def new_safe_page(self, site):
        return FakePage()


def check(monkeypatch, raw=None, site_changes=None, **fake):
    monkeypatch.setattr(Fake, "deep_calls", 0)
    for name, value in fake.items():
        monkeypatch.setattr(Fake, name, value)
    monkeypatch.setattr(runner, "make_collector", lambda site, *a: Fake(site))
    cfg = parse_config(raw or RAW)
    ota = dataclasses.replace(cfg.sites["agoda"], **(site_changes or {}))
    return runner.StayCheck(cfg, Context(), Context(), STAY).check_ota(ota)[0]


def test_ota_far_below_zen_is_never_a_violation(monkeypatch):
    row = check(monkeypatch, ota_price=D("300"), ota_summary=Summary(final=D("315")))
    assert row.status is Status.COULD_NOT_CHECK
    assert row.note == MISREAD + "OTA 300 is 30% of Zen 1,000; expected 40-300%) - possible misread"
    assert Fake.deep_calls == 0  # not even sent to the checkout check
    assert row.search_price == D("300") and row.website_final == D("1180")  # numbers kept for the human


def test_ota_far_above_zen_is_not_reported_in_parity(monkeypatch):
    # e.g. the struck-out "old" price read instead of the real one: a real violation could hide behind it
    row = check(monkeypatch, ota_price=D("3500"))
    assert row.status is Status.COULD_NOT_CHECK and row.note.startswith(MISREAD)


def test_card_taxes_above_limit_are_a_misread(monkeypatch):
    row = check(monkeypatch, ota_price=D("900"), ota_tax=D("300"))
    assert row.status is Status.COULD_NOT_CHECK
    assert "OTA taxes 300 are 33% of the OTA price 900" in row.note and row.final_payable is None


def test_card_taxes_normal_still_find_the_violation_and_name_the_website_plan(monkeypatch):
    row = check(monkeypatch, ota_price=D("900"), ota_tax=D("45"))
    assert row.status is Status.VIOLATION and row.final_payable == D("945")
    assert row.note == ("final = price + taxes & fees shown on the room list (one page load); "
                        "website plan: Room Only Rate")


def test_in_parity_note_names_the_website_plan(monkeypatch):
    row = check(monkeypatch, ota_price=D("1200"))
    assert row.status is Status.IN_PARITY
    assert row.note == "OTA search price above Zen; website plan: Room Only Rate"


def test_checkout_total_misread_is_never_a_violation(monkeypatch):
    row = check(monkeypatch, ota_price=D("950"), ota_summary=Summary(final=D("100"), screenshot_path="o.png"))
    assert Fake.deep_calls == 1
    assert row.status is Status.COULD_NOT_CHECK and row.note.startswith(MISREAD)
    assert row.final_payable == D("100") and row.gap_pct is None


def test_checkout_taxes_misread_is_never_a_violation(monkeypatch):
    summary = Summary(final=D("1150"), room_price=D("650"), gst=D("500"))
    row = check(monkeypatch, ota_price=D("950"), ota_summary=summary)
    assert row.status is Status.COULD_NOT_CHECK and "OTA taxes 500" in row.note


def test_checkout_violation_still_found_and_names_the_website_plan(monkeypatch):
    summary = Summary(final=D("1100"), room_price=D("950"), gst=D("150"), screenshot_path="o.png")
    row = check(monkeypatch, ota_price=D("950"), ota_summary=summary)
    assert row.status is Status.VIOLATION and row.gap_pct == D("-6.78")
    assert row.note.endswith("website screenshot: web.html; website plan: Room Only Rate")


def test_sanity_limits_come_from_config(monkeypatch):
    summary = Summary(final=D("315"), room_price=D("300"), gst=D("15"))
    row = check(monkeypatch, raw=_raw(sanity={"min_ratio": 0.2}), ota_price=D("300"), ota_summary=summary)
    assert row.status is Status.VIOLATION


def test_tax_inclusive_ota_price_hides_a_violation_unless_configured(monkeypatch):
    # OTA shows 1,100 incl. tax; Zen is 1,000 before tax but 1,180 incl. tax: the OTA IS cheaper.
    summary = Summary(final=D("1100"), screenshot_path="o.png")
    assert check(monkeypatch, ota_price=D("1100"), ota_summary=summary).status is Status.IN_PARITY  # wrong basis
    row = check(monkeypatch, site_changes={"price_includes_tax": True}, ota_price=D("1100"), ota_summary=summary)
    assert row.status is Status.VIOLATION and Fake.deep_calls == 1


def test_tax_inclusive_suspect_without_checkout_is_verify_manually(monkeypatch):
    row = check(monkeypatch, site_changes={"price_includes_tax": True, "deep_ready": False}, ota_price=D("1100"))
    assert row.status is Status.COULD_NOT_CHECK and row.note.startswith("possible violation, verify manually")


def test_tax_inclusive_ota_above_zen_final_is_in_parity(monkeypatch):
    row = check(monkeypatch, site_changes={"price_includes_tax": True}, ota_price=D("1200"))
    assert row.status is Status.IN_PARITY and row.note.startswith("OTA search price above Zen (both incl. tax)")


def test_tax_inclusive_price_ignores_card_taxes(monkeypatch):
    # the price already holds the tax: adding the card's tax again would hide a violation
    row = check(monkeypatch, site_changes={"price_includes_tax": True}, ota_price=D("1100"), ota_tax=D("100"),
                ota_summary=Summary(final=D("1100")))
    assert row.final_payable == D("1100") and row.status is Status.VIOLATION


def test_tax_inclusive_plausibility_uses_zen_final(monkeypatch):
    # 3,400 is 288% of Zen's 1,180 final (plausible) but 340% of Zen's 1,000 before tax
    assert check(monkeypatch, site_changes={"price_includes_tax": True},
                 ota_price=D("3400")).status is Status.IN_PARITY
    assert check(monkeypatch, ota_price=D("3400")).status is Status.COULD_NOT_CHECK


# --- Goibibo candidate in config.example.yaml ------------------------------------

def _goibibo_raw():
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))["sites"]["goibibo"]


def test_goibibo_candidate_is_not_set_up_until_verified():
    cfg = load_config(EXAMPLE)
    assert cfg.sites["goibibo"].enabled and not cfg.sites["goibibo"].ready


def test_every_goibibo_site_specific_value_is_marked_to_verify():
    raw = _goibibo_raw()
    values = [raw["wait_for"], *raw["room_template"]["price_text"].values()]
    values += [v for room in raw["rooms"].values() for v in room["labels"].values()]
    assert len(values) == 1 + 2 + 5 * 3
    assert all(v.startswith("TODO-verify: ") for v in values)


def test_goibibo_candidate_mirrors_makemytrip():
    from rate_parity.checker import candidate_site
    cfg = load_config(EXAMPLE, check_placeholders=False)
    goibibo, mmt = candidate_site(cfg.sites["goibibo"]), cfg.sites["makemytrip"]
    assert (goibibo.wait_for, goibibo.scroll_to_load) == (mmt.wait_for, mmt.scroll_to_load)
    for room_id, room in mmt.rooms.items():
        assert goibibo.rooms[room_id].price_text == room.price_text
        assert goibibo.rooms[room_id].labels == room.labels
    assert "mmtId=202004271355036572" in goibibo.search_url
