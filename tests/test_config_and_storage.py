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
    assert [s.key for s in cfg.otas()] == ["booking_com"]
    assert cfg.tolerance_pct == D("1.0")
    assert "/book" in cfg.commit_path_patterns


def test_example_config_refuses_to_run_with_todos():
    with pytest.raises(ConfigError, match="TODO"):
        load_config(EXAMPLE)


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
    raw["tolerance_pct"] = tolerance
    with pytest.raises(ConfigError, match="tolerance_pct"):
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
