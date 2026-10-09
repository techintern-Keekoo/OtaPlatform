"""Turn CheckRows into safe cells and save them (Google Sheets, CSV fallback)."""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from enum import Enum

from ..config import Config
from ..models import COLUMNS, CheckRow

log = logging.getLogger(__name__)

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value) -> str:
    """Text that a spreadsheet could run as a formula gets a leading quote."""
    if value is None:
        return ""
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
        return str(value)  # our own numbers; "-5.25" must stay a number
    text = str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def row_to_cells(row: CheckRow) -> list[str]:
    return [sanitize_cell(getattr(row, column)) for column in COLUMNS]


def save_rows(rows: list[CheckRow], cfg: Config, dry_run: bool) -> str:
    """Returns where the rows went: "sheets" or the CSV path."""
    from . import csv_store, sheets

    cells = [row_to_cells(row) for row in rows]
    sheet_id = os.environ.get("SHEET_ID", "")
    key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "")
    if not dry_run and sheet_id and key_file:
        try:
            sheets.append(cells, sheet_id, key_file, cfg.sheet_worksheet)
            return "sheets"
        except Exception as exc:
            log.error("Google Sheets write failed, using CSV fallback: %s", type(exc).__name__)
    return str(csv_store.append(cells, cfg.csv_path))
