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
