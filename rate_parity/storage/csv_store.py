"""Local CSV fallback. Cells must already be sanitized."""
from __future__ import annotations

import csv
from pathlib import Path

from ..models import COLUMNS


def append(cells: list[list[str]], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new_file:
            writer.writerow(COLUMNS)
        writer.writerows(cells)
