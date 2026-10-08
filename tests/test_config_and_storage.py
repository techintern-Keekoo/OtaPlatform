from decimal import Decimal as D
from pathlib import Path

import pytest

from rate_parity.config import ConfigError, load_config
from rate_parity.models import COLUMNS, CheckRow, Status
from rate_parity.storage import csv_store, row_to_cells, sanitize_cell

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.yaml"


def test_example_config_parses():
    cfg = load_config(EXAMPLE, check_placeholders=False)
    assert cfg.website.enabled
    assert [s.key for s in cfg.otas()] == ["booking_com", "makemytrip", "goibibo", "agoda", "cleartrip"]
    assert cfg.min_margin_pct == D("0")
    assert cfg.stay.days_ahead == (0, 1)
    assert "/book" in cfg.commit_path_patterns


def test_otas_with_todos_are_not_set_up_but_run_continues():
    cfg = load_config(EXAMPLE)  # website has no TODOs, so this loads
    assert cfg.website.ready
    assert [s.key for s in cfg.otas() if not s.ready] == ["booking_com", "makemytrip", "goibibo", "agoda", "cleartrip"]


def test_website_with_todos_refuses_to_run(tmp_path):
    text = EXAMPLE.read_text(encoding="utf-8").replace("ezee_hotel: zenmanalibykeekoostays", "ezee_hotel: TODO")
    bad = tmp_path / "config.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="sites.website"):
        load_config(bad)


def test_old_tolerance_key_is_rejected_with_a_clear_message():
    import yaml
    from rate_parity.config import parse_config
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw["tolerance_pct"] = 1
    with pytest.raises(ConfigError, match="min_margin_pct"):
        parse_config(raw, check_placeholders=False)


def test_only_must_be_an_ota():
    cfg = load_config(EXAMPLE, check_placeholders=False)
    with pytest.raises(ConfigError):
        cfg.otas("website")
    with pytest.raises(ConfigError):
        cfg.otas("nope")


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.yaml")


@pytest.mark.parametrize("value, expected", [
    ("=HYPERLINK(\"x\")", "'=HYPERLINK(\"x\")"),
    ("+1", "'+1"),
    ("-cmd", "'-cmd"),
    ("@SUM(A1)", "'@SUM(A1)"),
    ("\t=1", "'\t=1"),
    ("normal", "normal"),
    (None, ""),
    (D("-5.25"), "-5.25"),
    (Status.VIOLATION, "VIOLATION"),
])
def test_sanitize_cell(value, expected):
    assert sanitize_cell(value) == expected


def test_csv_append_writes_header_once(tmp_path):
    row = CheckRow("Booking.com", "P", "R", "BB", "Free", "2026-10-15", "2026-10-16", 2,
                   note="=evil()", status=Status.IN_PARITY)
    path = tmp_path / "out" / "rows.csv"
    csv_store.append([row_to_cells(row)], path)
    csv_store.append([row_to_cells(row)], path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(COLUMNS)
    assert len(lines) == 3
    assert "'=evil()" in lines[1]


# --- reviewer fixes -------------------------------------------------------------

def _raw_example():
    import yaml
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def test_malformed_site_is_a_config_error_not_a_crash():
    from rate_parity.config import parse_config
    raw = _raw_example()
    raw["sites"]["makemytrip"] = "oops"
    with pytest.raises(ConfigError):
        parse_config(raw)


@pytest.mark.parametrize("tolerance", [-1, True])
def test_tolerance_must_be_a_non_negative_number(tolerance):
    from rate_parity.config import parse_config
    raw = _raw_example()
    raw["min_margin_pct"] = tolerance
    with pytest.raises(ConfigError, match="min_margin_pct"):
        parse_config(raw, check_placeholders=False)


def test_sheets_client_gets_a_timeout(monkeypatch):
    from rate_parity.storage import sheets

    class FakeWorksheet:
        def row_values(self, n):
            return ["ota"]

        def append_rows(self, cells, value_input_option=None):
            self.cells = cells

    class FakeClient:
        timeout = None

        def set_timeout(self, seconds):
            self.timeout = seconds

        def open_by_key(self, key):
            return self

        def worksheet(self, title):
            return FakeWorksheet()

    client = FakeClient()
    monkeypatch.setattr(sheets.gspread, "service_account", lambda filename: client)
    sheets.append([["x"]], "sheet-id", "key.json", "checks")
    assert client.timeout == sheets.TIMEOUT_SECONDS


def test_gitignore_covers_secrets_and_evidence():
    ignored = (EXAMPLE.parent / ".gitignore").read_text(encoding="utf-8").split()
    for pattern in (".env", ".env.*", "config.yaml", "*.json", "*.pem", "chrome-profile/", "screenshots/", "output/"):
        assert pattern in ignored


@pytest.mark.parametrize("site, checkin, expected", [
    ("makemytrip", (2026, 11, 8), "checkin=11082026&checkout=11092026"),
    ("goibibo", (2026, 10, 8), "checkin=20261008&checkout=20261009&roomString=1-2-0"),
    ("agoda", (2026, 10, 8), "checkIn=2026-10-08&los=1&adults=2"),
    ("cleartrip", (2026, 10, 8), "c=081026%7C091026&r=2%2C0"),
    ("booking_com", (2026, 10, 8), "checkin=2026-10-08&checkout=2026-10-09&group_adults=2"),
])
def test_example_ota_urls_match_real_listing_links(site, checkin, expected):
    from datetime import date, timedelta
    from rate_parity.models import Stay
    start = date(*checkin)
    url = load_config(EXAMPLE, check_placeholders=False).sites[site].search_url_for(Stay(start, start + timedelta(days=1), 2))
    assert expected in url


def test_search_url_without_checkin_placeholder_is_rejected():
    import yaml
    from rate_parity.config import parse_config
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw["sites"]["booking_com"]["search_url"] = "https://www.booking.com/hotel/in/keekoo-manali-manali.html"
    with pytest.raises(ConfigError, match="checkin"):
        parse_config(raw, check_placeholders=False)


def _raw_with_template(label):
    import yaml
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    site = raw["sites"]["agoda"]
    site["room_template"] = {
        "search_price": "[data-testid='room-name']:text-is('{room}')",
        "steps": ["[data-testid='book-button'] >> nth=0 /* {room} */"],
        "summary": {"ready": "#s", "room_name": "#r", "meal_plan": "#m", "cancellation": "#c", "final": "#f"},
    }
    room_id = raw["rooms"][0]["id"]
    site["rooms"] = {room_id: {"labels": {"room": label, "meal_plan": "Room only", "cancellation": "Free cancellation"}}}
    return raw, room_id


def test_room_template_fills_each_rooms_labels():
    from rate_parity.config import parse_config
    raw, room_id = _raw_with_template("Premium Cottage")
    room = parse_config(raw, check_placeholders=False).sites["agoda"].rooms[room_id]
    assert room.search_price == "[data-testid='room-name']:text-is('Premium Cottage')"
    assert room.steps == ("[data-testid='book-button'] >> nth=0 /* Premium Cottage */",)
    assert room.labels.room == "Premium Cottage"


def test_room_template_rejects_quotes_in_labels():
    from rate_parity.config import parse_config
    raw, _ = _raw_with_template("Bob's Room')")
    with pytest.raises(ConfigError, match="quotes"):
        parse_config(raw, check_placeholders=False)
