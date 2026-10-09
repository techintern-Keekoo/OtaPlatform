"""Health: exit codes, the last_run.json heartbeat, and the crash path of `run`."""
import json
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

import rate_parity.__main__ as cli_module
from rate_parity import alerts, health, runner
from rate_parity.__main__ import main
from rate_parity.alerts import wati
from rate_parity.config import ConfigError
from rate_parity.models import CheckRow, Status

ROOT = Path(__file__).resolve().parent.parent
START = datetime(2026, 10, 22, 10, 0, tzinfo=runner.IST)
END = datetime(2026, 10, 22, 10, 4, 30, tzinfo=runner.IST)
BROKEN = ("SITE BROKEN? 0 of 5 rooms read on a loaded page - layout may have changed; "
          "run: python -m rate_parity check --site agoda")
CONTRACT = {"status", "finished_at", "exit_code", "broken_sites", "error", "started_at", "counts",
            "violations", "dry_run"}
WATI_ENV = {"WATI_API_URL": "https://live-mt-server.wati.io/123", "WATI_ACCESS_TOKEN": "secret-token",
            "WATI_TEMPLATE_NAME": "rate_parity_daily", "WATI_RECIPIENTS": "919800000000"}


def row(ota="MakeMyTrip", status=Status.IN_PARITY, zen=D("1545.60"), note="", gap=None):
    r = CheckRow(ota=ota, property="Zen", room="Standard", meal_plan="Room only", cancellation="Free",
                 checkin="2026-10-22", checkout="2026-10-23", adults=2)
    r.website_search_price, r.status, r.note, r.gap_pct = zen, status, note, gap
    return r


def test_exit_code_ok_when_prices_were_read():
    rows = [row(), row(status=Status.VIOLATION, gap=D("-4.87")), row(zen=None, status=Status.COULD_NOT_CHECK)]
    assert health.exit_code(rows) == 0  # a violation is business news, not an agent fault
    assert health.health_line(rows) == "Health: ok (exit 0)"


def test_exit_code_3_when_no_website_price_at_all():
    rows = [row(zen=None, status=Status.COULD_NOT_CHECK, note="error: ConnectionError")] * 3
    assert health.exit_code(rows) == 3 and health.exit_code([]) == 3
    assert "no website (Zen) price" in health.health_line(rows)


def test_exit_code_4_when_a_set_up_site_is_broken():
    rows = [row(), row("Agoda", Status.COULD_NOT_CHECK, note=BROKEN)]
    assert health.exit_code(rows) == 4
    assert health.broken_sites(rows) == {"Agoda": "agoda"}
    assert health.health_line(rows) == "Health: degraded (exit 4) - site broken? Agoda"


def test_website_problem_wins_over_a_broken_site():
    rows = [row("Agoda", Status.COULD_NOT_CHECK, zen=None, note=BROKEN)]
    assert health.exit_code(rows) == 3


def test_last_run_has_the_shared_contract():
    rows = [row(), row("Agoda", Status.COULD_NOT_CHECK, note=BROKEN), row(status=Status.VIOLATION, gap=D("-1.50"))]
    data = health.last_run(rows, START, END, dry_run=True)
    assert CONTRACT <= set(data)
    assert data["status"] == "degraded" and data["exit_code"] == 4 and data["error"] is None
    assert data["broken_sites"] == ["Agoda"] and data["dry_run"] is True
    assert data["started_at"] == "2026-10-22T10:00:00+05:30" and data["finished_at"] == "2026-10-22T10:04:30+05:30"
    assert data["counts"] == {"VIOLATION": 1, "FALSE_ALARM": 0, "IN_PARITY": 1, "COULD_NOT_CHECK": 1}
    assert data["violations"] == [{"ota": "MakeMyTrip", "room": "Standard", "checkin": "2026-10-22",
                                   "gap_pct": "-1.50"}]
    json.dumps(data)  # plain JSON, no Decimals


def test_failed_run_is_one_line_and_short():
    data = health.failed_run(RuntimeError("boom\nsecond line " + "x" * 500), START, END, dry_run=False)
    assert CONTRACT <= set(data)
    assert data["status"] == "failed" and data["exit_code"] == 1
    assert data["error"].startswith("RuntimeError: boom second line") and len(data["error"]) <= 300


def test_heartbeat_is_written_atomically(tmp_path):
    path = health.write_last_run(tmp_path / "output", {"status": "ok"})
    assert path == tmp_path / "output" / "last_run.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "ok"}
    health.write_last_run(tmp_path / "output", {"status": "failed"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "failed"}
    assert [p.name for p in path.parent.iterdir()] == ["last_run.json"]  # no tmp file left behind


def test_heartbeat_write_failure_never_raises(tmp_path):
    blocker = tmp_path / "output"
    blocker.write_text("a file, not a folder")
    assert health.write_last_run(blocker, {"status": "ok"}) is None


# --- `python -m rate_parity run`: exit code, heartbeat, crash alert ----------

@pytest.fixture
def cli(monkeypatch, tmp_path):
    """main(["run", ...]) on the real example config, inside tmp_path, with run() faked."""
    monkeypatch.chdir(tmp_path)  # output/ is relative to the working folder
    monkeypatch.setattr(cli_module, "load_dotenv", lambda: None)  # never the office PC's real .env
    for name in WATI_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(runner, "now_ist", lambda: END)
    sent = []
    monkeypatch.setattr(wati, "send_summary", lambda text, settings, param: sent.append(text) or 1)

    def _main(outcome, *extra):
        def fake_run(cfg, only=None, dry_run=False):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        monkeypatch.setattr(runner, "run", fake_run)
        code = main(["run", "--config", str(ROOT / "config.example.yaml"), *extra])
        heartbeat = tmp_path / "output" / "last_run.json"
        return code, (json.loads(heartbeat.read_text(encoding="utf-8")) if heartbeat.exists() else None), sent
    return _main


def test_normal_run_returns_health_exit_code_and_writes_heartbeat(cli, capsys):
    code, beat, sent = cli([row(), row("Agoda", Status.COULD_NOT_CHECK, note=BROKEN)], "--dry-run")
    assert code == 4 and beat["status"] == "degraded" and beat["broken_sites"] == ["Agoda"]
    assert beat["dry_run"] is True and beat["exit_code"] == 4
    assert "Health: degraded (exit 4) - site broken? Agoda" in capsys.readouterr().out
    assert sent == []


def test_all_fine_returns_0(cli):
    code, beat, _ = cli([row()], "--dry-run")
    assert code == 0 and beat["status"] == "ok" and beat["error"] is None


def test_crash_returns_1_writes_failed_heartbeat_and_alerts(cli, monkeypatch, caplog):
    for name, value in WATI_ENV.items():
        monkeypatch.setenv(name, value)
    code, beat, sent = cli(PermissionError("[Errno 13] Permission denied: 'output/rate_parity.csv'"))
    assert code == 1
    assert beat["status"] == "failed" and beat["exit_code"] == 1 and beat["dry_run"] is False
    assert beat["error"] == "PermissionError: [Errno 13] Permission denied: 'output/rate_parity.csv'"
    assert sent == ["Rate parity agent FAILED 22-Oct-2026 10:04 IST: PermissionError: [Errno 13] "
                    "Permission denied: 'output/rate_parity.csv' - see output/agent.log"]
    assert "Traceback" in caplog.text  # the full traceback goes to agent.log


def test_crash_in_dry_run_sends_nothing(cli, monkeypatch):
    for name, value in WATI_ENV.items():
        monkeypatch.setenv(name, value)
    code, beat, sent = cli(RuntimeError("boom"), "--dry-run")
    assert code == 1 and beat["status"] == "failed" and sent == []


def test_crash_without_wati_set_up_still_returns_1(cli):
    code, beat, sent = cli(RuntimeError("boom"))
    assert code == 1 and beat["error"] == "RuntimeError: boom" and sent == []


def test_bad_only_key_is_still_a_config_error(cli):
    code, beat, _ = cli(ConfigError("--only must be an OTA key from config, got 'x'"))
    assert code == 2 and beat is None


def test_failure_alert_never_raises(monkeypatch):
    from rate_parity.config import load_config
    cfg = load_config(ROOT / "config.example.yaml", check_placeholders=True)

    def broken_send(*a):
        raise RuntimeError("WATI summary was not delivered to any recipient")
    monkeypatch.setattr(wati, "send_summary", broken_send)
    for name, value in WATI_ENV.items():
        monkeypatch.setenv(name, value)
    text = alerts.send_failure(ValueError("x"), cfg, END, dry_run=False)
    assert text.startswith("Rate parity agent FAILED") and "\n" not in text


# --- an OTA that was never checked is a fault, "not set up" and "sold out" are not --------

def _night(ota, note, checkin="2026-10-08", price=None):
    r = CheckRow(ota=ota, property="Zen", room="Standard", meal_plan="Room only", cancellation="Free cancellation",
                 checkin=checkin, checkout="2026-10-09", adults=2)
    r.website_search_price, r.search_price, r.status, r.note = D("1545.60"), price, Status.COULD_NOT_CHECK, note
    return r


@pytest.mark.parametrize("note", [
    "error: SelectorMissing: Booking.com: room list did not load (tried 2 times)",
    "verify manually",              # CAPTCHA (Blocked flag)
    "re-login to Agoda",            # login wall
    "safety stop: refusing to click 'Pay now'",
])
def test_a_night_with_no_price_and_a_fault_is_degraded(note):
    rows = [_night("MakeMyTrip", "", price=D("1901")), _night("MakeMyTrip", note, checkin="2026-10-09")]
    assert health.exit_code(rows) == 4 and health.unchecked_sites(rows) == ["MakeMyTrip"]
    assert "not checked" in health.health_line(rows)


@pytest.mark.parametrize("note", ["not set up yet (selectors still TODO in config)",
                                  "no OTA price (room sold out or not listed)"])
def test_not_set_up_and_sold_out_are_not_faults(note):
    rows = [_night("Cleartrip", note), _night("Cleartrip", note)]
    assert health.exit_code(rows) == 0 and health.unchecked_sites(rows) == []


def test_one_priced_room_means_the_night_was_checked():
    rows = [_night("Agoda", "", price=D("2233")), _night("Agoda", "error: TimeoutError: slow card")]
    assert health.exit_code(rows) == 0


def test_a_one_site_test_run_keeps_the_scheduled_heartbeat(cli):
    code, beat, _ = cli([row()], "--dry-run", "--only", "agoda")
    assert code == 0 and beat is None  # --only never writes output/last_run.json
