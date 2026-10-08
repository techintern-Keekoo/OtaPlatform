"""Daily alert entry point."""
from __future__ import annotations

import logging
from datetime import date

from ..config import Config
from ..models import CheckRow
from . import wati

log = logging.getLogger(__name__)


def send_daily_summary(rows: list[CheckRow], cfg: Config, day: date, dry_run: bool) -> str:
    text = wati.build_summary(rows, day, cfg.alert_include_could_not_check, cfg.alert_max_chars)
    if dry_run:
        log.info("[dry-run] WhatsApp summary not sent: %s", text)
        return text
    wati.send_summary(text, wati.load_settings(), cfg.alert_param_name)
    return text
