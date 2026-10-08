"""Append rows to Google Sheets with a service account. Cells must already be sanitized."""
from __future__ import annotations

import gspread

from ..models import COLUMNS


def append(cells: list[list[str]], sheet_id: str, key_file: str, worksheet_title: str) -> None:
    client = gspread.service_account(filename=key_file)
    sheet = client.open_by_key(sheet_id)
    try:
        worksheet = sheet.worksheet(worksheet_title)
    except gspread.WorksheetNotFound:
        worksheet = sheet.add_worksheet(worksheet_title, rows=1000, cols=len(COLUMNS))
    if not worksheet.row_values(1):
        worksheet.append_row(COLUMNS, value_input_option="RAW")
    # USER_ENTERED so numbers stay numbers; sanitize_cell already neutralised formulas.
    worksheet.append_rows(cells, value_input_option="USER_ENTERED")
