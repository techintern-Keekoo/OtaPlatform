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
    new_file = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new_file:
            writer.writerow(COLUMNS)
        writer.writerows(cells)
