"""Is the agent itself healthy? Exit code and heartbeat file (output/last_run.json).

Price problems (violations) are business news, not agent faults: they do not
change the exit code. These do:
    0  all fine
    1  crash (set by __main__)
    2  config error (set by __main__)
    3  no website (Zen) price at all, so nothing could be compared
    4  a set-up OTA gave no price for a night: page loaded but nothing read
       (layout changed?), or never checked (room list did not load, CAPTCHA,
       safety stop, error). "Not set up" and "sold out" are not faults.
The website problem wins over a broken OTA: without Zen nothing is compared.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from .models import SITE_BROKEN, CheckRow, Status

log = logging.getLogger(__name__)

OK, CRASH, CONFIG_ERROR, NO_WEBSITE, SITE_BROKEN_EXIT = 0, 1, 2, 3, 4
LAST_RUN_FILE = "last_run.json"
_SITE_KEY = re.compile(r"--site (\S+)")


def broken_sites(rows: list[CheckRow]) -> dict[str, str]:
    """OTA label -> site key, for sites whose page loaded but gave 0 prices."""
    found = {}
    for row in rows:
        if row.note.startswith(SITE_BROKEN) and row.ota not in found:
            key = _SITE_KEY.search(row.note)
            found[row.ota] = key.group(1) if key else row.ota
    return dict(sorted(found.items()))


# Notes that explain a missing OTA price without any agent fault.
_BENIGN = ("not set up yet", "no OTA price")


def unchecked_sites(rows: list[CheckRow]) -> list[str]:
    """OTA labels with a night where no room got a price for a reason other than
    "not set up" / "sold out" (and not already flagged SITE BROKEN?)."""
    nights: dict[tuple[str, str], list[CheckRow]] = {}
    for row in rows:
        nights.setdefault((row.ota, row.checkin), []).append(row)
    found = set()
    for (ota, _), night in nights.items():
        if any(r.search_price is not None for r in night):
            continue
        if any(r.note and not r.note.startswith(_BENIGN + (SITE_BROKEN,)) for r in night):
            found.add(ota)
    return sorted(found)


def website_missing(rows: list[CheckRow]) -> bool:
    """True when not one website (Zen) price was read, for any room or night."""
    return not any(row.website_search_price is not None for row in rows)


def exit_code(rows: list[CheckRow]) -> int:
    if website_missing(rows):
        return NO_WEBSITE
    if broken_sites(rows) or unchecked_sites(rows):
        return SITE_BROKEN_EXIT
    return OK


def status_for(code: int) -> str:
    return {OK: "ok", CRASH: "failed", CONFIG_ERROR: "failed"}.get(code, "degraded")


def run_health(rows: list[CheckRow]) -> dict:
    code = exit_code(rows)
    counts = Counter(row.status for row in rows)
    return {
        "status": status_for(code),
        "exit_code": code,
        "checks": len(rows),
        "counts": {status.value: counts[status] for status in Status},
        "website_ok": not website_missing(rows),
        "broken_sites": list(broken_sites(rows)),
        "unchecked_sites": unchecked_sites(rows),
        "violations": [
            {"ota": r.ota, "room": r.room, "checkin": r.checkin,
             "gap_pct": None if r.gap_pct is None else str(r.gap_pct)}
            for r in rows if r.status is Status.VIOLATION
        ],
    }


def health_line(rows: list[CheckRow]) -> str:
    """One line for the console / log, e.g. 'Health: degraded (exit 4) - site broken? Agoda'."""
    code = exit_code(rows)
    line = f"Health: {status_for(code)} (exit {code})"
    if code == NO_WEBSITE:
        line += " - no website (Zen) price was read, nothing could be compared"
    elif code == SITE_BROKEN_EXIT:
        if broken_sites(rows):
            line += " - site broken? " + ", ".join(broken_sites(rows))
        if unchecked_sites(rows):
            line += " - not checked (see report notes): " + ", ".join(unchecked_sites(rows))
    return line


def last_run(rows: list[CheckRow], started_at: datetime, finished_at: datetime, dry_run: bool) -> dict:
    """Heartbeat after a normal run."""
    return {**_times(started_at, finished_at, dry_run), **run_health(rows), "error": None}


def failed_run(error: BaseException, started_at: datetime, finished_at: datetime, dry_run: bool) -> dict:
    """Heartbeat after a crash: no rows to count."""
    return {
        **_times(started_at, finished_at, dry_run),
        "status": "failed", "exit_code": CRASH, "checks": 0,
        "counts": {status.value: 0 for status in Status}, "website_ok": False,
        "broken_sites": [], "unchecked_sites": [], "violations": [], "error": error_text(error),
    }


def error_text(error: BaseException, limit: int = 300) -> str:
    """'ExcType: message' on one line, short enough for a WhatsApp message."""
    text = " ".join(f"{type(error).__name__}: {error}".split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _times(started_at: datetime, finished_at: datetime, dry_run: bool) -> dict:
    return {
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "dry_run": dry_run,
    }


def write_last_run(folder: Path, data: dict) -> Path | None:
    """Write the heartbeat atomically (tmp file, then replace), so a reader never
    sees half a file. Never raises: the run's own result matters more."""
    path = Path(folder) / LAST_RUN_FILE
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.error("could not write %s: %s", path, exc)
        return None
    return path
