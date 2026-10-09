"""Website (eZee) reader: picks the refundable 1-stay plan and reads its numbers."""
import base64
import json
from datetime import date
from decimal import Decimal as D

import pytest

from rate_parity.collectors import EzeeCollector, make_collector
from rate_parity.config import parse_config
from rate_parity.models import Stay
from rate_parity.safety import SafetyViolation, SelectorMissing

PAX2 = base64.b64encode(b'{"room_1":{"adult":2,"child":0}}').decode()
STAY = Stay(date(2026, 10, 22), date(2026, 10, 23), 2)


def record(room, plan, excl, tax, incl, nonref=0, minmsg=0, pax=PAX2):
    # Same field names as the live eZee reply (2026-10-08).
    return {"RateTypeId": "x", "Room_Name": plan, "RoomTypeUnkId": room, "default_pax": pax,
            "TaxRate": tax, "TotalPrice_ExclusiveAll": excl, "TotalPrice_InclusiveAll": incl,
            "Prepaid_Noncancel_Nonrefundable": nonref, "showMinmsg": minmsg}


def reply(*records):
    return "<html><script>var rooms = [" + ",".join(json.dumps(r) for r in records) + "];</script></html>"


LIVE_LIKE = reply(
    record("4757200000000000001", "Long Stay Packages - 3 Min Nights", 1435.2, 71.76, 1506.96, minmsg=2),
    record("4757200000000000001", "Book now! Save now! With Complimentary Wi-Fi", 1545.6, 77.28, 1622.88),
    record("4757200000000000001", "Non-refundable deal", 1400.0, 70.0, 1470.0, nonref=1),
    record("4757200000000000002", "Book now! Save now! With Complimentary Wi-Fi", 1787.1, 89.355, 1876.46),
)


class FakeResponse:
    def __init__(self, text=""):
        self.text = text

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self, text):
        self.text, self.headers, self.calls = text, {}, []

    def get(self, url, timeout=None):
        self.calls.append(("GET", url, None))
        return FakeResponse()

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, data))
        return FakeResponse(self.text)


RAW = {
    "property": {"name": "Zen Manali"},
    "min_margin_pct": 0,
    "stay": {"days_ahead": [14], "nights": 1, "adults": 2},
    "rooms": [
        {"id": "standard", "room": "Standard garden view room", "meal_plan": "Room only", "cancellation": "Free cancellation"},
        {"id": "valley", "room": "Deluxe valley facing room", "meal_plan": "Room only", "cancellation": "Free cancellation"},
    ],
    "browser": {"profile_dir": "chrome-profile"},
    "sites": {"website": {
        "kind": "ezee", "enabled": True, "allowed_domains": ["book.zenhotels.in"],
        "search_url": "https://book.zenhotels.in/booking/roomlist-zenmanalibykeekoostays-be",
        "ezee_hotel": "zenmanalibykeekoostays",
        "rooms": {"standard": {"ezee_room_type": "4757200000000000001"},
                  "valley": {"ezee_room_type": "4757200000000000002"}},
    }},
}
SITE = parse_config(RAW).website


def collector(text, tmp_path):
    session = FakeSession(text)
    return EzeeCollector(SITE, session=session, evidence_dir=tmp_path), session


def test_make_collector_picks_ezee_for_website():
    assert isinstance(make_collector(SITE), EzeeCollector)
    assert make_collector(SITE).needs_browser is False


def test_quick_scan_uses_refundable_one_night_plan_before_tax(tmp_path):
    c, _ = collector(LIVE_LIKE, tmp_path)
    # not the 3-night deal (1435.20) and not the non-refundable one (1400)
    assert c.quick_scan(None, STAY) == {"standard": D("1545.60"), "valley": D("1787.10")}


def test_deep_check_reads_total_and_tax_from_the_page(tmp_path):
    c, _ = collector(LIVE_LIKE, tmp_path)
    s = c.deep_check(None, STAY, "standard")
    assert (s.room_price, s.gst, s.final) == (D("1545.60"), D("77.28"), D("1622.88"))
    assert s.screenshot_path.endswith(".html") and (tmp_path / s.screenshot_path.split("/")[-1]).exists()


def test_one_search_per_stay_with_engine_date_format(tmp_path):
    c, session = collector(LIVE_LIKE, tmp_path)
    c.quick_scan(None, STAY)
    c.deep_check(None, STAY, "standard")
    c.deep_check(None, STAY, "valley")
    posts = [call for call in session.calls if call[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][1] == "https://book.zenhotels.in/booking/roomlisting.php"
    assert posts[0][2] == {"HotelId": "zenmanalibykeekoostays", "checkIn": "22_10_2026",
                           "checkOut": "23_10_2026", "isroomsearch": "1"}


def test_sold_out_room_is_missing_not_guessed(tmp_path):
    c, _ = collector(reply(record("4757200000000000002", "Plan", 1787.1, 89.36, 1876.46)), tmp_path)
    assert c.quick_scan(None, STAY) == {"valley": D("1787.10")}
    with pytest.raises(SelectorMissing):
        c.deep_check(None, STAY, "standard")


def test_no_rate_data_cannot_check(tmp_path):
    c, _ = collector("<html>maintenance</html>", tmp_path)
    with pytest.raises(SelectorMissing):
        c.quick_scan(None, STAY)


def test_wrong_guest_count_is_refused(tmp_path):
    pax3 = base64.b64encode(b'{"room_1":{"adult":3,"child":0}}').decode()
    c, _ = collector(reply(record("4757200000000000001", "Plan", 1545.6, 77.28, 1622.88, pax=pax3)), tmp_path)
    with pytest.raises(SelectorMissing, match="3 adults"):
        c.quick_scan(None, STAY)


def test_search_outside_allowlist_is_refused(tmp_path):
    import dataclasses
    site = dataclasses.replace(SITE, allowed_domains=("other.example",))
    c = EzeeCollector(site, session=FakeSession(LIVE_LIKE), evidence_dir=tmp_path)
    with pytest.raises(SafetyViolation):
        c.quick_scan(None, STAY)


# --- like for like: the website's own numbers must add up ----------------------

def test_plan_numbers_that_do_not_add_up_are_refused(tmp_path):
    # 1,545.60 + 77.28 = 1,622.88, but the total says 1,700: which number is the price?
    c, _ = collector(reply(record("4757200000000000001", "Plan", 1545.6, 77.28, 1700.0)), tmp_path)
    assert c.quick_scan(None, STAY) == {}  # that room has no price; the reason is kept for its row
    assert c.room_errors["standard"].startswith("website numbers do not add up for standard")
    with pytest.raises(SelectorMissing, match="do not add up"):
        c.deep_check(None, STAY, "standard")


def test_rounding_within_one_rupee_is_accepted(tmp_path):
    c, _ = collector(reply(record("4757200000000000001", "Plan", 1545.6, 77.28, 1623.5)), tmp_path)
    assert c.deep_check(None, STAY, "standard").final == D("1623.50")


def test_missing_tax_is_refused_not_guessed(tmp_path):
    bad = record("4757200000000000001", "Plan", 1545.6, 77.28, 1622.88)
    del bad["TaxRate"]
    c, _ = collector(reply(bad), tmp_path)
    assert c.quick_scan(None, STAY) == {}
    assert "price, tax or total missing" in c.room_errors["standard"]


def test_every_candidate_plan_must_add_up_not_only_the_cheapest(tmp_path):
    c, _ = collector(reply(
        record("4757200000000000001", "Plan A", 1545.6, 77.28, 1622.88),
        record("4757200000000000001", "Plan B", 1600.0, 80.0, 1900.0),  # broken: the reply format may have changed
    ), tmp_path)
    # A corrupt total on any candidate could hide the real cheapest plan: refuse the room.
    assert c.quick_scan(None, STAY) == {} and "Plan B" in c.room_errors["standard"]


def test_plans_never_chosen_are_not_checked(tmp_path):
    c, _ = collector(reply(
        record("4757200000000000001", "Plan A", 1545.6, 77.28, 1622.88),
        record("4757200000000000001", "Non-refundable", 1400.0, 70.0, 9999.0, nonref=1),
        record("4757200000000000001", "3 Min Nights", 1435.2, 71.76, 1.0, minmsg=2),
    ), tmp_path)
    assert c.quick_scan(None, STAY) == {"standard": D("1545.60")}


def test_chosen_plan_name_is_given_for_audit(tmp_path):
    # The reply has no meal-plan field we have seen, so the plan's name is shown instead.
    c, _ = collector(LIVE_LIKE, tmp_path)
    assert c.deep_check(None, STAY, "standard").notes == ["website plan: Book now! Save now! With Complimentary Wi-Fi"]


def test_plan_note_shows_the_room_description_with_its_meal_plan():
    from rate_parity.collectors.ezee import _plan_note
    plan = {"Room_Name": "Book now! Save now!", "Room_Description": "Standard garden view room  EP"}
    assert _plan_note(plan) == "website plan: Book now! Save now! (Standard garden view room EP)"


def test_one_odd_room_does_not_black_out_the_others(tmp_path):
    c, _ = collector(reply(
        record("4757200000000000001", "Plan", 1545.6, 77.28, 1700.0),      # standard: does not add up
        record("4757200000000000002", "Plan", 1876.56, 93.83, 1970.39),    # valley: fine
    ), tmp_path)
    assert c.quick_scan(None, STAY) == {"valley": D("1876.56")}
    assert "do not add up" in c.room_errors["standard"]
