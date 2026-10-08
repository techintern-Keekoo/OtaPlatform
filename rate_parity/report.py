"""Plain-text price table after each run: every room x every OTA next to Zen.

All prices are the search-page price BEFORE tax, so Zen and the OTAs are
compared like for like. Final prices incl. tax are in the CSV / Sheet.
ASCII only, so it prints in any Windows console.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .models import CheckRow, Status

TAGS = {
    Status.IN_PARITY: "ok",
    Status.FALSE_ALARM: "ok",
    Status.VIOLATION: "LOWER!",
    Status.COULD_NOT_CHECK: "n/a",
}


SHORT_OFFER = {"free cancellation": "", "cancellation policy": " policy", "non-refundable": " non-ref"}


def _cell(r) -> str:
    if r.status is Status.COULD_NOT_CHECK and r.search_price is not None:
        tag = "VERIFY"  # OTA price not above Zen, but checkout not confirmed
    else:
        tag = TAGS[r.status]
    return f"{_money(r.search_price)} {tag}{SHORT_OFFER.get(r.ota_offer.lower(), '')}"


def _money(value) -> str:
    return "-" if value is None else f"{value:,.0f}"


def build_table(rows: list[CheckRow], when: datetime) -> str:
    otas = list(dict.fromkeys(r.ota for r in rows))
    lines = [
        f"Rate parity check {when:%d-%b-%Y %H:%M} IST",
        "Rule: Zen (website) must be CHEAPER than every OTA. Prices per night, before tax.",
        "ok = Zen is cheaper | LOWER! = OTA equal/cheaper (confirmed) | VERIFY = OTA looks equal/cheaper, check by hand",
        "n/a = no price (sold out / not set up) | non-ref = only non-refundable offer | policy = 'cancellation policy' offer",
    ]
    nights = list(dict.fromkeys(r.checkin for r in rows))
    for night in nights:
        night_rows = [r for r in rows if r.checkin == night]
        rooms = list(dict.fromkeys(r.room for r in night_rows))
        header = f"{'Room':<38}{'Zen':>9}" + "".join(f"{ota:>22}" for ota in otas)
        lines += ["", f"Check-in {night}", header, "-" * len(header)]
        for room in rooms:
            cells = {r.ota: r for r in night_rows if r.room == room}
            zen = next((r.website_search_price for r in cells.values() if r.website_search_price is not None), None)
            line = f"{room[:37]:<38}{_money(zen):>9}"
            for ota in otas:
                r = cells.get(ota)
                line += f"{_cell(r) if r else '-':>22}"
            lines.append(line)
    problems = [r for r in rows if r.status is Status.VIOLATION]
    lines += ["", f"{len(problems)} problem(s): OTA equal to or cheaper than Zen."]
    lines += [f"  {r.ota} | {r.room} | {r.checkin} | OTA {_money(r.final_payable)} vs Zen {_money(r.website_final)} "
              f"incl. tax ({r.gap_pct}%)" for r in problems]
    not_set_up = sorted({r.ota for r in rows if r.status is Status.COULD_NOT_CHECK and "not set up" in r.note})
    if not_set_up:
        lines.append("Not set up yet: " + ", ".join(not_set_up))
    return "\n".join(lines)


def save_table(text: str, folder: Path, when: datetime) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"report_{when:%Y%m%d_%H%M}.txt"
    path.write_text(text + "\n", encoding="utf-8")
    return path
