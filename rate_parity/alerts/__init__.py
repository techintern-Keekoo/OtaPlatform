"""Daily alert entry point."""
from __future__ import annotations

import logging
from datetime import date, datetime

from ..config import Config
from ..health import error_text
from ..models import CheckRow
from . import wati

log = logging.getLogger(__name__)


def send_daily_summary(rows: list[CheckRow], cfg: Config, day: date, dry_run: bool) -> str:
    text = wati.build_summary(rows, day, cfg.alert_include_could_not_check, cfg.alert_max_chars)
    if dry_run:
        log.info("[dry-run] WhatsApp summary not sent: %s", text)
        return text
    try:
        settings = wati.load_settings()
    except ValueError as exc:  # WATI not set up yet: results are still saved
        log.warning("WhatsApp summary skipped: %s", exc)
        return text
    wati.send_summary(text, settings, cfg.alert_param_name)
    return text


def send_failure(error: BaseException, cfg: Config, when: datetime, dry_run: bool) -> str:
    """One line to WhatsApp when the run crashed. Never raises: it runs while
    handling a crash already."""
    text = f"Rate parity agent FAILED {when:%d-%b-%Y %H:%M} IST: {error_text(error)} - see output/agent.log"
    text = text[: cfg.alert_max_chars]
    if dry_run:
        log.info("[dry-run] WhatsApp failure alert not sent: %s", text)
        return text
    try:
        settings = wati.load_settings()
    except ValueError as exc:
        log.warning("WhatsApp failure alert skipped: %s", exc)
        return text
    try:
        wati.send_summary(text, settings, cfg.alert_param_name)
    except Exception as exc:
        log.error("WhatsApp failure alert not delivered: %s: %s", type(exc).__name__, exc)
    return text
