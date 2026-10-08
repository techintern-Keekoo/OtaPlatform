from datetime import datetime
from decimal import Decimal as D

from rate_parity.models import CheckRow, Status
from rate_parity.report import build_table, save_table


def row(ota, room, ota_price, zen_price, status, note=""):
    r = CheckRow(ota=ota, property="Zen", room=room, meal_plan="Room only", cancellation="Free cancellation",
                 checkin="2026-10-08", checkout="2026-10-09", adults=2)
    r.search_price, r.website_search_price, r.status, r.note = ota_price, zen_price, status, note
    return r


ROWS = [
    row("MakeMyTrip", "Standard garden view room", D("1901"), D("1545.60"), Status.IN_PARITY),
    row("Agoda", "Standard garden view room", D("1500"), D("1545.60"), Status.VIOLATION),
    row("Cleartrip", "Standard garden view room", None, D("1545.60"), Status.COULD_NOT_CHECK, "not set up yet"),
]


def test_table_shows_zen_and_every_ota_side_by_side():
    text = build_table(ROWS, datetime(2026, 10, 8, 10, 0))
    line = next(l for l in text.splitlines() if l.startswith("Standard garden"))
    assert "1,546" in line and "1,901 ok" in line and "1,500 LOWER!" in line and "- n/a" in line
    assert "1 problem(s)" in text and "Not set up yet: Cleartrip" in text
    text.encode("ascii")  # prints in any Windows console


def test_table_is_saved_per_run(tmp_path):
    path = save_table("x", tmp_path, datetime(2026, 10, 8, 18, 0))
    assert path.name == "report_20261008_1800.txt" and path.read_text(encoding="utf-8") == "x\n"
