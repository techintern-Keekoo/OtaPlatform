"""doctor and alert-test: every check with fakes. No network, no browser."""
from __future__ import annotations

import dataclasses
import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import requests

from rate_parity import __main__ as cli
from rate_parity import doctor
from rate_parity.alerts import wati
from rate_parity.config import load_config

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = str(ROOT / "config.example.yaml")
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=doctor.IST)
ENV = {
    "WATI_API_URL": "https://live-mt-server.wati.io/123",
    "WATI_ACCESS_TOKEN": "secret-token",
    "WATI_TEMPLATE_NAME": "rate_parity_daily",
    "WATI_RECIPIENTS": "919800000000,919811111111",
}


@pytest.fixture
def cfg():
    return load_config(EXAMPLE, check_placeholders=True)


# --- python and packages ------------------------------------------------------

def test_python_version():
    assert doctor.check_python((3, 11, 4)).level == doctor.PASS
    old = doctor.check_python((3, 9, 1))
    assert old.level == doctor.FAIL and "3.9" in old.reason and old.fix


def fake_importer(missing=(), broken=()):
    def importer(name):
        if name in missing:
            raise ImportError(name)
        if name in broken:
            raise RuntimeError("greenlet DLL load failed")
        return object()
    return importer


def test_packages_all_present():
    assert doctor.check_packages(fake_importer(), "win32").level == doctor.PASS


def test_packages_missing_required_fails_with_pip_fix():
    result = doctor.check_packages(fake_importer(missing={"yaml"}), "linux")
    assert result.level == doctor.FAIL and "yaml" in result.reason
    assert "pip install -r requirements.txt" in result.fix


def test_packages_broken_install_counts_as_missing():
    result = doctor.check_packages(fake_importer(broken={"playwright.sync_api"}), "linux")
    assert result.level == doctor.FAIL and "playwright" in result.reason


def test_tzdata_needed_on_windows_only():
    assert doctor.check_packages(fake_importer(missing={"tzdata"}), "linux").level == doctor.PASS
    result = doctor.check_packages(fake_importer(missing={"tzdata"}), "win32")
    assert result.level == doctor.FAIL and "tzdata" in result.reason


def test_gspread_is_optional():
    result = doctor.check_packages(fake_importer(missing={"gspread"}), "linux")
    assert result.level == doctor.WARN and "CSV" in result.reason


# --- config and sites ---------------------------------------------------------

def test_config_loads():
    result, loaded = doctor.check_config(EXAMPLE)
    assert result.level == doctor.PASS and loaded is not None


def test_config_missing_fails(tmp_path):
    result, loaded = doctor.check_config(str(tmp_path / "nope.yaml"))
    assert result.level == doctor.FAIL and "not found" in result.reason and loaded is None


def test_config_with_website_todo_fails(tmp_path):
    text = Path(EXAMPLE).read_text(encoding="utf-8").replace("ezee_hotel: zenmanalibykeekoostays",
                                                            "ezee_hotel: TODO")
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    result, _ = doctor.check_config(str(path))
    assert result.level == doctor.FAIL and "TODO" in result.reason


def test_sites_lists_not_set_up_and_checkout(cfg):
    result = doctor.check_sites(cfg)
    assert result.level == doctor.WARN
    assert "checked: MakeMyTrip, Agoda" in result.reason
    assert "not set up: Booking.com, Goibibo, Cleartrip" in result.reason
    assert "checkout check not set up: Agoda" in result.reason


def test_sites_all_ready_passes(cfg):
    sites = {k: dataclasses.replace(s, ready=True, deep_ready=True) for k, s in cfg.sites.items()}
    assert doctor.check_sites(dataclasses.replace(cfg, sites=sites)).level == doctor.PASS


def test_sites_none_ready_warns(cfg):
    sites = {k: dataclasses.replace(s, ready=(k == "website")) for k, s in cfg.sites.items()}
    result = doctor.check_sites(dataclasses.replace(cfg, sites=sites))
    assert result.level == doctor.WARN and "only the website" in result.reason


# --- folders and disk -----------------------------------------------------------

def test_folder_writable(tmp_path):
    result = doctor.check_folder("output", tmp_path)
    assert result.level == doctor.PASS and "writable" in result.reason


def test_folder_not_created_yet_checks_parent(tmp_path):
    result = doctor.check_folder("output", tmp_path / "output" / "deeper")
    assert result.level == doctor.PASS and "created on first run" in result.reason
    assert not (tmp_path / "output").exists()  # read-only: nothing was created


def test_folder_not_writable_fails(tmp_path, monkeypatch):
    def refuse(**kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(doctor.tempfile, "TemporaryFile", refuse)
    result = doctor.check_folder("output", tmp_path)
    assert result.level == doctor.FAIL and "PermissionError" in result.reason and result.fix


def test_low_disk_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda p: _Usage(500 * 1024 ** 2))
    result = doctor.check_folder("screenshots", tmp_path)
    assert result.level == doctor.WARN and "0.5 GB disk free" in result.reason


class _Usage:
    def __init__(self, free):
        self.free = free


# --- memory -----------------------------------------------------------------------

def test_parse_meminfo():
    text = "MemTotal:       16000000 kB\nMemFree:  100 kB\nMemAvailable:    2048 kB\n"
    assert doctor.parse_meminfo(text) == 2048 * 1024
    assert doctor.parse_meminfo("MemTotal: 1 kB\n") is None


def test_memory_levels():
    gb = 1024 ** 3
    assert doctor.check_memory(lambda: 4 * gb).level == doctor.PASS
    low = doctor.check_memory(lambda: gb // 2)
    assert low.level == doctor.WARN and "close other apps" in low.fix
    assert doctor.check_memory(lambda: None).level == doctor.WARN


def test_available_memory_reads_this_machine():
    if not Path("/proc/meminfo").exists():
        pytest.skip("no /proc/meminfo here")
    assert doctor.available_memory() > 0


# --- Chrome profile lock ----------------------------------------------------------

def test_profile_missing_and_free(tmp_path):
    assert doctor.check_profile(tmp_path / "chrome-profile").level == doctor.PASS
    assert doctor.check_profile(tmp_path).level == doctor.PASS


def test_profile_windows_lockfile_warns(tmp_path):
    (tmp_path / "lockfile").write_text("")
    result = doctor.check_profile(tmp_path)
    assert result.level == doctor.WARN and "degraded" in result.reason and "close" in result.fix


def test_profile_linux_singleton_link_warns(tmp_path):
    os.symlink("office-pc-1234", tmp_path / "SingletonLock")  # a link to nothing, as Chrome makes it
    assert doctor.check_profile(tmp_path).level == doctor.WARN


# --- clock --------------------------------------------------------------------------

def test_clock():
    assert doctor.check_clock(timedelta(hours=5, minutes=30)).level == doctor.PASS
    utc = doctor.check_clock(timedelta(0))
    assert utc.level == doctor.WARN and "UTC+00:00" in utc.reason and "Kolkata" in utc.fix
    assert "UTC-04:00" in doctor.check_clock(timedelta(hours=-4)).reason


# --- WATI ------------------------------------------------------------------------------

def test_wati_set_up():
    result = doctor.check_wati(ENV)
    assert result.level == doctor.PASS and "2 recipient" in result.reason
    assert "secret-token" not in str(result)


def test_wati_missing_warns_without_token():
    result = doctor.check_wati({**ENV, "WATI_RECIPIENTS": ""})
    assert result.level == doctor.WARN and "alerts will be skipped" in result.reason
    assert "secret-token" not in str(result)


# --- schedule -------------------------------------------------------------------------

def test_task_names_come_from_install_script():
    assert doctor.schedule_task_names() == doctor.DEFAULT_TASKS


def test_task_names_fallback(tmp_path):
    assert doctor.schedule_task_names(tmp_path / "missing.bat") == doctor.DEFAULT_TASKS


def test_schedule_skipped_off_windows():
    assert doctor.check_schedule("linux").level == doctor.SKIP


def test_schedule_missing_task_warns():
    result = doctor.check_schedule("win32", query=lambda name: "6PM" not in name)
    assert result.level == doctor.WARN and "Keekoo OTA Parity 6PM" in result.reason
    assert "install_schedule.bat" in result.fix


def test_schedule_all_present():
    assert doctor.check_schedule("win32", query=lambda name: True).level == doctor.PASS


def test_schtasks_query_uses_task_name(monkeypatch):
    calls = []

    class Done:
        returncode = 1

    def fake_run(args, **kwargs):
        calls.append(args)
        return Done()
    monkeypatch.setattr(doctor.subprocess, "run", fake_run)
    assert doctor._schtasks_has("Keekoo OTA Parity 10AM") is False
    assert calls == [["schtasks", "/Query", "/TN", "Keekoo OTA Parity 10AM"]]


# --- last run ---------------------------------------------------------------------------

def write_last_run(tmp_path, **values) -> Path:
    data = {"status": "ok", "finished_at": "2026-10-09T10:05:00+05:30", "exit_code": 0,
            "broken_sites": [], "error": None, **values}
    path = tmp_path / "last_run.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_last_run_ok(tmp_path):
    result = doctor.check_last_run(write_last_run(tmp_path), NOW)
    assert result.level == doctor.PASS and "09-Oct 10:05" in result.reason


def test_last_run_missing(tmp_path):
    result = doctor.check_last_run(tmp_path / "last_run.json", NOW)
    assert result.level == doctor.WARN and "run_agent.bat" in result.fix


@pytest.mark.parametrize("text", ["{not json", "[]", '{"status": "ok"}', '{"finished_at": "yesterday"}'])
def test_last_run_garbled(tmp_path, text):
    path = tmp_path / "last_run.json"
    path.write_text(text, encoding="utf-8")
    result = doctor.check_last_run(path, NOW)
    assert result.level == doctor.WARN and "unreadable" in result.reason


def test_last_run_failed(tmp_path):
    path = write_last_run(tmp_path, status="failed", exit_code=1, error="Chrome\ncrashed")
    result = doctor.check_last_run(path, NOW)
    assert result.level == doctor.WARN and "FAILED: Chrome crashed" in result.reason


def test_last_run_too_old(tmp_path):
    path = write_last_run(tmp_path, finished_at="2026-10-08T18:05:00+05:30")
    result = doctor.check_last_run(path, NOW)
    assert result.level == doctor.WARN and "18 h ago" in result.reason


def test_last_run_naive_time_is_ist(tmp_path):
    path = write_last_run(tmp_path, finished_at="2026-10-09T10:05:00")
    assert doctor.check_last_run(path, NOW).level == doctor.PASS


def test_last_run_degraded_names_sites(tmp_path):
    path = write_last_run(tmp_path, status="degraded", broken_sites=["MakeMyTrip"])
    result = doctor.check_last_run(path, NOW)
    assert result.level == doctor.WARN and "MakeMyTrip" in result.reason


# --- browser ------------------------------------------------------------------------------

def test_browser_offline_skipped(cfg):
    assert doctor.check_browser(cfg, offline=True, launcher=pytest.fail).level == doctor.SKIP


def test_browser_starts(cfg):
    seen = []
    result = doctor.check_browser(cfg, offline=False, launcher=seen.append)
    assert result.level == doctor.PASS and seen == ["chrome"]


def test_browser_missing_chrome_fails_with_fix(cfg):
    def launcher(channel):
        raise RuntimeError("Chromium distribution 'chrome' is not found\nmore lines")
    result = doctor.check_browser(cfg, offline=False, launcher=launcher)
    assert result.level == doctor.FAIL and "not found" in result.reason and "\n" not in result.reason
    assert result.fix == "install Google Chrome or set browser.channel: null"


def test_bundled_chromium_fix(cfg):
    bundled = dataclasses.replace(cfg, browser=dataclasses.replace(cfg.browser, channel=None))

    def launcher(channel):
        raise RuntimeError("Executable doesn't exist")
    result = doctor.check_browser(bundled, offline=False, launcher=launcher)
    assert "playwright install chromium" in result.fix


# --- website ----------------------------------------------------------------------------------

class FakeCollector:
    def __init__(self, prices=None, error=None):
        self.prices, self.error, self.stays = prices or {}, error, []

    def quick_scan(self, page, stay):
        self.stays.append(stay)
        if self.error:
            raise self.error
        return self.prices


def test_website_offline_skipped(cfg):
    assert doctor.check_website(cfg, offline=True, collector_factory=pytest.fail).level == doctor.SKIP


def test_website_all_rooms_priced_for_tomorrow(cfg):
    fake = FakeCollector({room: Decimal("1500.00") for room in cfg.website.rooms})
    result = doctor.check_website(cfg, False, lambda site, evidence: fake, today=date(2026, 10, 9))
    assert result.level == doctor.PASS and result.reason == "5 of 5 rooms priced for 10-Oct"
    assert fake.stays[0].checkin == date(2026, 10, 10) and fake.stays[0].checkout == date(2026, 10, 11)
    assert fake.stays[0].adults == 2


def test_website_some_rooms_priced(cfg):
    fake = FakeCollector({"standard_garden": Decimal("1500.00")})
    result = doctor.check_website(cfg, False, lambda site, evidence: fake, today=date(2026, 10, 9))
    assert result.level == doctor.PASS and result.reason.startswith("1 of 5 rooms priced")


def test_website_no_rooms_warns(cfg):
    result = doctor.check_website(cfg, False, lambda site, evidence: FakeCollector({}))
    assert result.level == doctor.WARN and "0 of 5" in result.reason


def test_website_unreachable_fails(cfg):
    fake = FakeCollector(error=requests.ConnectionError("no route\nto host"))
    result = doctor.check_website(cfg, False, lambda site, evidence: fake)
    assert result.level == doctor.FAIL and "no route to host" in result.reason
    assert "book.zenhotels.in" in result.fix


def test_website_evidence_goes_to_temp_folder(cfg):
    folders = []

    def factory(site, evidence):
        folders.append(evidence)
        assert evidence.is_dir()
        return FakeCollector({})
    doctor.check_website(cfg, False, factory)
    assert folders and not folders[0].exists()  # removed again; screenshots\ untouched
    assert folders[0] != cfg.screenshot_dir


def test_real_ezee_collector_is_built(cfg, tmp_path):
    collector = doctor._ezee_collector(cfg.website, tmp_path)
    assert collector.site is cfg.website and collector.needs_browser is False


# --- the doctor as a whole --------------------------------------------------------------------

def test_doctor_offline_ready(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(doctor, "check_memory", lambda: doctor.Result(doctor.PASS, "memory", "8 GB free"))
    lines = []
    code = doctor.doctor(EXAMPLE, offline=True, out=lines.append, env=ENV)
    text = "\n".join(lines)
    assert code == 0, text
    names = [line[6:17].strip() for line in lines if line[:4] in ("PASS", "WARN", "FAIL", "SKIP")]
    assert names == ["python", "packages", "config", "clock", "whatsapp", "schedule", "sites", "output",
                     "screenshots", "memory", "profile", "last run", "browser", "website"]
    assert "SKIP  browser     skipped (--offline)" in text
    assert "secret-token" not in text
    assert lines[-1].startswith("Ready to run")


def test_doctor_bad_config_fails_but_runs_other_checks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lines = []
    code = doctor.doctor(str(tmp_path / "missing.yaml"), offline=True, out=lines.append, env={})
    text = "\n".join(lines)
    assert code == 1
    assert "FAIL  config" in text and "PASS  python" in text and "WARN  whatsapp" in text
    assert "SKIP  website     needs a config that loads" in text
    assert "NOT READY" in lines[-1]


def test_crashing_check_is_one_fail_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def boom():
        raise OSError("disk on fire")
    monkeypatch.setattr(doctor, "check_memory", boom)
    results = doctor.run_checks(EXAMPLE, offline=True, env=ENV)
    memory = [r for r in results if r.name == "memory"][0]
    assert memory.level == doctor.FAIL and "disk on fire" in memory.reason
    assert any(r.name == "website" for r in results)  # later checks still ran


def test_format_shows_fix_only_when_not_pass():
    assert "fix:" not in doctor.format_result(doctor.Result(doctor.PASS, "x", "fine", "do this"))
    assert "fix: do this" in doctor.format_result(doctor.Result(doctor.WARN, "x", "hmm", "do this"))


def test_cli_doctor_command(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    code = cli.main(["doctor", "--config", EXAMPLE, "--offline"])
    out = capsys.readouterr().out
    assert code in (0, 1) and "Rate parity doctor" in out and "PASS  config" in out


def test_cli_doctor_bad_config_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["doctor", "--config", "missing.yaml", "--offline"]) == 1
    assert "FAIL  config" in capsys.readouterr().out


# --- alert-test ---------------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


@pytest.fixture
def posts(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()
    monkeypatch.setattr(wati.requests, "post", fake_post)
    return calls


def test_alert_test_sends_to_all(cfg, posts, caplog):
    lines = []
    assert doctor.alert_test(cfg, env=ENV, out=lines.append, now=NOW) == 0
    assert lines == ["sent to 2 of 2"]
    assert [kw["params"]["whatsappNumber"] for _, kw in posts] == ["919800000000", "919811111111"]
    parameters = posts[0][1]["json"]["parameters"]
    assert parameters == [{"name": cfg.alert_param_name,
                           "value": "Rate parity agent test 09-Oct-2026 12:00 IST - alerts work"}]
    assert "secret-token" not in " ".join(lines) + caplog.text


def test_alert_test_only_to_one_number(cfg, posts):
    lines = []
    assert doctor.alert_test(cfg, to="919822222222", env=ENV, out=lines.append, now=NOW) == 0
    assert lines == ["sent to 1 of 1"]
    assert [kw["params"]["whatsappNumber"] for _, kw in posts] == ["919822222222"]


def test_alert_test_rejects_bad_number(cfg, posts):
    lines = []
    assert doctor.alert_test(cfg, to="+91 98222", env=ENV, out=lines.append) == 1
    assert "digits only" in lines[0] and posts == []


def test_alert_test_not_configured(cfg, posts):
    lines = []
    assert doctor.alert_test(cfg, env={**ENV, "WATI_API_URL": ""}, out=lines.append) == 1
    assert lines[0].startswith("WhatsApp (WATI) is not set up") and ".env" in lines[1]
    assert posts == [] and "secret-token" not in " ".join(lines)


def test_alert_test_failure_reports_counts(cfg, monkeypatch, caplog):
    replies = iter([FakeResponse(200), FakeResponse(401)])
    monkeypatch.setattr(wati.requests, "post", lambda url, **kw: next(replies))
    lines = []
    assert doctor.alert_test(cfg, env=ENV, out=lines.append, now=NOW) == 1
    assert lines[0] == "sent to 1 of 2"
    assert "secret-token" not in " ".join(lines) + caplog.text


def test_alert_test_nobody_received(cfg, monkeypatch):
    monkeypatch.setattr(wati.requests, "post", lambda url, **kw: FakeResponse(500))
    lines = []
    assert doctor.alert_test(cfg, env=ENV, out=lines.append, now=NOW) == 1
    assert lines[0] == "sent to 0 of 2"


def test_cli_alert_test_command(tmp_path, monkeypatch, posts, capsys):
    monkeypatch.chdir(tmp_path)  # no .env here
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    assert cli.main(["alert-test", "--config", EXAMPLE, "--to", "919833333333"]) == 0
    assert "sent to 1 of 1" in capsys.readouterr().out
    assert [kw["params"]["whatsappNumber"] for _, kw in posts] == ["919833333333"]


# --- Windows scripts --------------------------------------------------------------------------

SCRIPTS = ROOT / "scripts"


def test_uninstall_removes_the_tasks_install_creates():
    removed = doctor.schedule_task_names(SCRIPTS / "uninstall_schedule.bat")
    assert removed == doctor.schedule_task_names() == doctor.DEFAULT_TASKS


def test_run_agent_rotates_log_and_passes_exit_code_on():
    text = (SCRIPTS / "run_agent.bat").read_text(encoding="ascii")
    assert "GTR 5242880 move /Y output\\agent.log output\\agent.log.1" in text
    assert "python -m rate_parity run --config config.example.yaml >> output\\agent.log 2>&1" in text
    assert text.rstrip().splitlines()[-1] == "exit /b %RESULT%"
    assert "set RESULT=%ERRORLEVEL%" in text


def test_doctor_bat_runs_doctor_and_pauses():
    text = (SCRIPTS / "doctor.bat").read_text(encoding="ascii")
    assert "call .venv\\Scripts\\activate.bat" in text
    assert "python -m rate_parity doctor --config config.example.yaml" in text
    assert "pause" in text
