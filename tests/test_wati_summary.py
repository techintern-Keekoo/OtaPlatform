from datetime import date
from decimal import Decimal as D

import pytest
import requests

from rate_parity.alerts import wati
from rate_parity.models import CheckRow, Status


def row(status, ota="Booking.com", gap=None, note=""):
    return CheckRow(
        ota=ota, property="P", room="Deluxe", meal_plan="BB", cancellation="Free",
        checkin="2026-10-15", checkout="2026-10-16", adults=2,
        gap_pct=gap, status=status, note=note,
    )


ROWS = [
    row(Status.VIOLATION, gap=D("-6.20")),
    row(Status.FALSE_ALARM),
    row(Status.IN_PARITY),
    row(Status.IN_PARITY),
    row(Status.COULD_NOT_CHECK, ota="Agoda", note="re-login to Agoda"),
]


def test_summary_counts_and_details():
    text = wati.build_summary(ROWS, date(2026, 10, 8))
    assert text.startswith("Rate parity 08-Oct-2026: 5 checks")
    assert "1 VIOLATION" in text and "2 IN_PARITY" in text and "1 COULD_NOT_CHECK" in text
    assert "Booking.com Deluxe 2026-10-15 -6.20%" in text
    assert "Agoda: re-login to Agoda" in text


def test_summary_is_single_line():
    rows = ROWS + [row(Status.COULD_NOT_CHECK, note="error:\nline two\twith tab")]
    text = wati.build_summary(rows, date(2026, 10, 8))
    assert "\n" not in text and "\t" not in text and "    " not in text


def test_summary_can_omit_blocked_checks():
    text = wati.build_summary(ROWS, date(2026, 10, 8), include_could_not_check=False)
    assert "re-login" not in text


def test_summary_is_truncated():
    rows = [row(Status.VIOLATION, ota=f"OTA{i}", gap=D("-9.99")) for i in range(100)]
    text = wati.build_summary(rows, date(2026, 10, 8), max_chars=300)
    assert len(text) <= 300
    assert text.endswith("(see sheet)")


def test_summary_with_no_rows():
    assert wati.build_summary([], date(2026, 10, 8)) == "Rate parity 08-Oct-2026: 0 checks."


ENV = {
    "WATI_API_URL": "https://live-mt-server.wati.io/123/",
    "WATI_ACCESS_TOKEN": "secret-token",
    "WATI_TEMPLATE_NAME": "rate_parity_daily",
    "WATI_RECIPIENTS": "919800000000, 919811111111",
}


def test_settings_from_env_and_token_not_in_repr():
    settings = wati.load_settings(ENV)
    assert settings.recipients == ("919800000000", "919811111111")
    assert settings.api_url == "https://live-mt-server.wati.io/123"
    assert "secret-token" not in repr(settings)


@pytest.mark.parametrize("change", [
    {"WATI_ACCESS_TOKEN": ""},
    {"WATI_API_URL": "http://insecure.example"},
    {"WATI_RECIPIENTS": "+91 98000"},
])
def test_settings_rejects_bad_env(change):
    with pytest.raises(ValueError):
        wati.load_settings({**ENV, **change})


class FakeResponse:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def test_send_uses_template_endpoint_with_timeout(monkeypatch, caplog):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(wati.requests, "post", fake_post)
    sent = wati.send_summary("hello", wati.load_settings(ENV), "summary")
    assert sent == 2
    url, kwargs = calls[0]
    assert url == "https://live-mt-server.wati.io/123/api/v1/sendTemplateMessage"
    assert kwargs["params"] == {"whatsappNumber": "919800000000"}
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"
    assert kwargs["timeout"] and kwargs["verify"] is True
    assert kwargs["json"]["parameters"] == [{"name": "summary", "value": "hello"}]
    assert "secret-token" not in caplog.text


def test_send_raises_when_nobody_received_it(monkeypatch):
    monkeypatch.setattr(wati.requests, "post", lambda url, **kw: FakeResponse(401))
    with pytest.raises(RuntimeError):
        wati.send_summary("hello", wati.load_settings(ENV), "summary")
