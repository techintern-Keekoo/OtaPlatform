"""Daily WhatsApp summary via the WATI template-message API.

UNVERIFIED: endpoint path and payload follow WATI's public v1 API
(POST {WATI_API_URL}/api/v1/sendTemplateMessage?whatsappNumber=...).
Confirm against the Keekoo WATI account before go-live.
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

import requests

from ..health import broken_sites
from ..models import SITE_BROKEN, CheckRow, Status

log = logging.getLogger(__name__)

TIMEOUT = (10, 30)  # connect, read seconds


@dataclass(frozen=True)
class WatiSettings:
    api_url: str
    token: str
    template_name: str
    recipients: tuple[str, ...]

    def __repr__(self) -> str:  # never show the token
        return f"WatiSettings(api_url={self.api_url!r}, template={self.template_name!r}, recipients={len(self.recipients)})"


def load_settings(env=os.environ) -> WatiSettings:
    api_url = env.get("WATI_API_URL", "").rstrip("/")
    token = env.get("WATI_ACCESS_TOKEN", "")
    template = env.get("WATI_TEMPLATE_NAME", "")
    recipients = tuple(r.strip() for r in env.get("WATI_RECIPIENTS", "").split(",") if r.strip())
    if not (api_url and token and template and recipients):
        raise ValueError("WATI_API_URL, WATI_ACCESS_TOKEN, WATI_TEMPLATE_NAME and WATI_RECIPIENTS must be set")
    if urlsplit(api_url).scheme != "https":
        raise ValueError("WATI_API_URL must be https")
    if not all(r.isdigit() for r in recipients):
        raise ValueError("WATI_RECIPIENTS must be digits only (country code + number)")
    return WatiSettings(api_url, token, template, recipients)


def build_summary(rows: list[CheckRow], day: date, include_could_not_check: bool = True, max_chars: int = 900) -> str:
    """One line of text. WhatsApp template parameters may not contain newlines."""
    counts = Counter(row.status for row in rows)
    parts = [f"Rate parity {day:%d-%b-%Y}: {len(rows)} checks"]
    parts += [f"{counts[s]} {s.value}" for s in Status if counts[s]]
    text = " | ".join(parts) + "."
    broken = broken_sites(rows)
    if broken:  # first and short: the agent itself needs fixing before its numbers can be trusted
        text += " SITE BROKEN?: " + ", ".join(broken) + " (0 rooms read, run check --site)."

    violations = [
        f"{r.ota} {r.room} {r.checkin} {r.gap_pct}%" for r in rows if r.status is Status.VIOLATION
    ]
    if violations:
        text += " VIOLATIONS: " + "; ".join(violations) + "."
    if include_could_not_check:
        actions = sorted({f"{r.ota}: {r.note}" for r in rows if r.status is Status.COULD_NOT_CHECK
                          and r.note and not r.note.startswith(SITE_BROKEN)})
        if actions:
            text += " NOT CHECKED: " + "; ".join(actions) + "."

    text = " ".join(text.split())  # strip newlines/tabs/runs of spaces
    if len(text) > max_chars:
        text = text[: max_chars - 16].rstrip() + " ...(see sheet)"
    return text


def send_summary(text: str, settings: WatiSettings, param_name: str) -> int:
    """Send to every recipient. Returns how many succeeded; raises if none did."""
    url = f"{settings.api_url}/api/v1/sendTemplateMessage"
    headers = {"Authorization": f"Bearer {settings.token}"}
    payload = {
        "template_name": settings.template_name,
        "broadcast_name": settings.template_name,
        "parameters": [{"name": param_name, "value": text}],
    }
    sent = 0
    for number in settings.recipients:
        try:
            response = requests.post(
                url, params={"whatsappNumber": number}, json=payload,
                headers=headers, timeout=TIMEOUT, verify=True,
            )
            response.raise_for_status()
            sent += 1
        except requests.RequestException as exc:
            status = getattr(exc.response, "status_code", "no response")
            log.error("WATI send failed for ...%s (%s)", number[-4:], status)
    if sent == 0:
        raise RuntimeError("WATI summary was not delivered to any recipient")
    log.info("WATI summary sent to %d of %d recipients", sent, len(settings.recipients))
    return sent
