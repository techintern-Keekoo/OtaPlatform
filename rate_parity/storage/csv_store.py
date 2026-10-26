"""Local CSV fallback. Cells must already be sanitized."""
from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path

from ..models import COLUMNS

log = logging.getLogger(__name__)


def append(cells: list[list[str]], path: Path) -> Path:
    """Append rows; returns the file written. If the CSV is locked (open in
    Excel on Windows), the rows go to a pending file next to it instead of
    being lost, and the run carries on."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write(cells, path)
        return path
    except PermissionError:
        pending = path.with_name(f"{path.stem}_pending_{datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
        _write(cells, pending)
        log.warning("%s is locked (open in Excel?): rows saved to %s - close it and paste them in", path, pending)
        return pending


def _write(cells: list[list[str]], path: Path) -> None:
    _retire_if_old_columns(path)
    new_file = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new_file:
            writer.writerow(COLUMNS)
        writer.writerows(cells)


def _retire_if_old_columns(path: Path) -> None:
    """A CSV written by an older version has other columns: appending would put
    new values under the wrong headings. Rename it (kept, nothing deleted) so a
    fresh file with the current header is started."""
    if not path.exists() or path.stat().st_size == 0:
        return
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
    except (UnicodeDecodeError, csv.Error):  # e.g. re-saved by Excel in another encoding
        header = []
    if header == list(COLUMNS):
        return
    old = path.with_name(f"{path.stem}_old_columns_{datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
    path.rename(old)  # PermissionError if open in Excel: caller saves to a pending file
    log.warning("%s had different columns (older version): renamed to %s, new file started", path, old.name)
