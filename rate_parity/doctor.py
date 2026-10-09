"""Preflight check for the office PC, and a one-off WhatsApp test message.

    python -m rate_parity doctor --config config.example.yaml [--offline]
    python -m rate_parity alert-test --config config.example.yaml [--to 9198XXXXXXXX]

doctor only LOOKS: it changes no settings, never opens an OTA, never books.
It prints one line per check (PASS / WARN / FAIL / SKIP) with the fix in plain
words, and exits 1 if any check FAILed (0 otherwise).
"""
from __future__ import annotations

import ctypes
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .alerts import wati
from .config import Config, ConfigError, load_config
from .models import Stay

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
IST = timezone(timedelta(hours=5, minutes=30), "IST")  # India has no daylight saving

GB = 1024 ** 3
MIN_DISK_FREE = 1 * GB
MIN_MEMORY_FREE = 1.5 * GB
LAST_RUN_MAX_AGE = timedelta(hours=14)  # runs at 10:00, 18:00, 22:00: longest gap is 12 h
CHROME_LOCKS = ("lockfile", "SingletonLock")  # Windows, Linux: present while Chrome uses the profile
SCHEDULE_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "install_schedule.bat"
DEFAULT_TASKS = ("Keekoo OTA Parity 10AM", "Keekoo OTA Parity 6PM", "Keekoo OTA Parity 10PM")


@dataclass(frozen=True)
class Result:
    level: str  # PASS, WARN, FAIL or SKIP
    name: str
    reason: str
    fix: str = ""


def now_ist() -> datetime:
    return datetime.now(IST)


# --- checks: each returns one Result and never raises on a broken PC -------

def check_python(version=sys.version_info) -> Result:
    found = f"{version[0]}.{version[1]}"
    if tuple(version[:2]) >= (3, 10):
        return Result(PASS, "python", f"Python {found}")
    return Result(FAIL, "python", f"Python {found} is too old", "install Python 3.10 or newer, then re-create .venv")


def check_packages(importer=importlib.import_module, platform=sys.platform) -> Result:
    required = ["playwright.sync_api", "yaml", "requests", "dotenv"]
    if platform == "win32":
        required.append("tzdata")  # Windows has no time-zone database of its own
    missing = [name.split(".")[0] for name in required if not _imports(importer, name)]
    if missing:
        return Result(FAIL, "packages", "missing or broken: " + ", ".join(missing),
                      "with the venv active run: pip install -r requirements.txt")
    if not _imports(importer, "gspread"):
        return Result(WARN, "packages", "gspread missing: Google Sheets off, results go to the CSV only",
                      "pip install -r requirements.txt (only needed for Google Sheets)")
    return Result(PASS, "packages", "all required packages import")


def _imports(importer, name: str) -> bool:
    try:
        importer(name)
        return True
    except Exception:  # ImportError, or a broken install (e.g. greenlet) raising something else
        return False


def check_config(path: str) -> tuple[Result, Config | None]:
    try:
        cfg = load_config(path, check_placeholders=True)
    except ConfigError as exc:
        return Result(FAIL, "config", f"{path}: {exc}", "fix that line in the config file"), None
    return Result(PASS, "config", f"{path} loads"), cfg


def check_sites(cfg: Config) -> Result:
    otas = [site for key, site in cfg.sites.items() if key != "website" and site.enabled]
    live = [site.label for site in otas if site.ready]
    not_set_up = [site.label for site in otas if not site.ready]
    no_checkout = [site.label for site in otas if site.ready and not site.deep_ready]
    problems = []
    if not_set_up:
        problems.append("not set up: " + ", ".join(not_set_up))
    if no_checkout:
        problems.append("checkout check not set up: " + ", ".join(no_checkout))
    if not live:
        return Result(WARN, "sites", "no OTA is set up, only the website is read; " + "; ".join(problems),
                      "set up an OTA with: python -m rate_parity check --site <key>")
    reason = "checked: " + ", ".join(live)
    if problems:
        return Result(WARN, "sites", reason + "; " + "; ".join(problems),
                      "those show n/a or VERIFY in the report until their selectors are filled in")
    return Result(PASS, "sites", reason)


def check_folder(label: str, folder: Path) -> Result:
    existing = _nearest_existing(folder)
    try:
        with tempfile.TemporaryFile(dir=existing):
            pass
    except OSError as exc:
        return Result(FAIL, label, f"cannot write to {existing} ({exc.__class__.__name__})",
                      "move the project to a folder you can write to, e.g. C:\\otaPlatform")
    free = shutil.disk_usage(existing).free
    where = str(folder) if folder.exists() else f"{folder} (created on first run)"
    if free < MIN_DISK_FREE:
        return Result(WARN, label, f"{where} writable, only {free / GB:.1f} GB disk free",
                      "free up disk space (empty the Recycle Bin, delete old screenshots)")
    return Result(PASS, label, f"{where} writable, {free / GB:.0f} GB free")


def _nearest_existing(folder: Path) -> Path:
    folder = folder.resolve()
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    return folder


def available_memory() -> int | None:
    """Bytes of memory free for new programs, or None if this PC cannot tell us."""
    if sys.platform == "win32":
        return _windows_available_memory()
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            return parse_meminfo(handle.read())
    except OSError:
        return None


def parse_meminfo(text: str) -> int | None:
    match = re.search(r"^MemAvailable:\s+(\d+)\s*kB", text, re.MULTILINE)
    return int(match.group(1)) * 1024 if match else None


def _windows_available_memory() -> int | None:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys)


def check_memory(reader=available_memory) -> Result:
    free = reader()
    if free is None:
        return Result(WARN, "memory", "could not read free memory", "close other apps before the runs")
    if free < MIN_MEMORY_FREE:
        return Result(WARN, "memory", f"only {free / GB:.1f} GB memory free (Chrome needs about 1.5 GB)",
                      "close other apps (extra Chrome windows, Excel, WhatsApp desktop) before 10 AM, 6 PM, 10 PM")
    return Result(PASS, "memory", f"{free / GB:.1f} GB memory free")


def check_profile(profile_dir: Path) -> Result:
    if not profile_dir.exists():
        return Result(PASS, "profile", f"{profile_dir} not created yet (made on the first browser run)")
    # lexists: on Linux SingletonLock is a link to "host-pid", which is not a real file
    locks = [name for name in CHROME_LOCKS if os.path.lexists(profile_dir / name)]
    if locks:
        return Result(WARN, "profile", f"Chrome is using {profile_dir} right now ({locks[0]}): runs will be degraded",
                      "close the agent's Chrome window (the one opened by 'login' or a run), then run doctor again")
    return Result(PASS, "profile", f"{profile_dir} is free")


def check_clock(offset: timedelta | None = None) -> Result:
    offset = datetime.now().astimezone().utcoffset() if offset is None else offset
    if offset == timedelta(hours=5, minutes=30):
        return Result(PASS, "clock", "PC time zone is IST (UTC+05:30)")
    minutes = int(offset.total_seconds() // 60) if offset is not None else 0
    shown = f"{'+' if minutes >= 0 else '-'}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
    return Result(WARN, "clock", f"PC time zone is UTC{shown}, not IST: runs would start at the wrong hours",
                  "Settings > Time & language > Time zone: (UTC+05:30) Chennai, Kolkata, Mumbai, New Delhi")


def check_wati(env=os.environ) -> Result:
    try:
        settings = wati.load_settings(env)
    except ValueError as exc:  # the message names the variables, never their values
        return Result(WARN, "whatsapp", f"alerts will be skipped: {exc}",
                      "fill the WATI_ lines in .env, then run: python -m rate_parity alert-test")
    return Result(PASS, "whatsapp", f"WATI set up for {len(settings.recipients)} recipient(s)")


def schedule_task_names(script: Path = SCHEDULE_SCRIPT) -> tuple[str, ...]:
    try:
        names = re.findall(r'/TN\s+"([^"]+)"', script.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        names = []
    return tuple(dict.fromkeys(names)) or DEFAULT_TASKS


def check_schedule(platform=sys.platform, query=None, names: tuple[str, ...] | None = None) -> Result:
    if platform != "win32":
        return Result(SKIP, "schedule", "Task Scheduler is only checked on Windows")
    query = query or _schtasks_has
    missing = [name for name in names or schedule_task_names() if not query(name)]
    if missing:
        return Result(WARN, "schedule", "missing tasks: " + ", ".join(missing),
                      "double-click scripts\\install_schedule.bat")
    return Result(PASS, "schedule", "3 daily runs are in Task Scheduler")


def _schtasks_has(name: str) -> bool:
    try:
        done = subprocess.run(["schtasks", "/Query", "/TN", name], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def check_last_run(path: Path, now: datetime | None = None) -> Result:
    now = now or now_ist()
    fix_run = "double-click scripts\\run_agent.bat once, then read output\\agent.log"
    if not path.exists():
        return Result(WARN, "last run", f"no {path} yet (no run has finished)", fix_run)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        status = str(data.get("status", "?"))
        finished = datetime.fromisoformat(str(data["finished_at"]))
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return Result(WARN, "last run", f"{path} is unreadable", fix_run)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=IST)
    age = now - finished
    when = finished.astimezone(IST).strftime("%d-%b %H:%M")
    if status == "failed":
        error = " ".join(str(data.get("error") or "no error text").split())[:200]
        return Result(WARN, "last run", f"run at {when} FAILED: {error}", "read output\\agent.log for the cause")
    if age > LAST_RUN_MAX_AGE:
        return Result(WARN, "last run", f"last run finished {age.total_seconds() / 3600:.0f} h ago ({when})",
                      "check the PC was on and logged in; see Task Scheduler > Last Run Result")
    broken = data.get("broken_sites") or []
    if status == "degraded":
        sites = ", ".join(str(s) for s in broken) if isinstance(broken, list) and broken else "see report"
        return Result(WARN, "last run", f"run at {when} was degraded (broken: {sites})",
                      "run: python -m rate_parity check --site <key> for each broken site")
    return Result(PASS, "last run", f"run at {when}: {status}")


def check_browser(cfg: Config, offline: bool, launcher=None) -> Result:
    if offline:
        return Result(SKIP, "browser", "skipped (--offline)")
    channel = cfg.browser.channel
    name = f"Chrome (channel {channel})" if channel else "bundled Chromium"
    fix = ("install Google Chrome or set browser.channel: null" if channel
           else "run: python -m playwright install chromium")
    try:
        (launcher or _launch_and_close)(channel)
    except Exception as exc:
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
        return Result(FAIL, "browser", f"{name} did not start: {first_line[:160]}", fix)
    return Result(PASS, "browser", f"{name} starts and closes")


def _launch_and_close(channel: str | None) -> None:
    """Start the browser with no page and close it: nothing is loaded."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        pw.chromium.launch(headless=True, channel=channel).close()


def check_website(cfg: Config, offline: bool, collector_factory=None, today=None) -> Result:
    site = cfg.website
    if offline:
        return Result(SKIP, "website", "skipped (--offline)")
    if site.kind != "ezee":
        return Result(SKIP, "website", "only the eZee booking engine is checked here")
    checkin = (today or now_ist().date()) + timedelta(days=1)
    stay = Stay(checkin, checkin + timedelta(days=cfg.stay.nights), cfg.stay.adults)
    fix = "check the internet, then open https://" + site.allowed_domains[0] + " in Chrome"
    with tempfile.TemporaryDirectory() as evidence:  # keep the doctor's reply out of screenshots\
        try:
            collector = (collector_factory or _ezee_collector)(site, Path(evidence))
            prices = collector.quick_scan(None, stay)
        except Exception as exc:
            return Result(FAIL, "website", f"website search failed: {' '.join(str(exc).split())[:160]}", fix)
    total = len(site.rooms)
    reason = f"{len(prices)} of {total} rooms priced for {checkin:%d-%b}"
    if not prices:
        return Result(WARN, "website", reason + " (sold out, or the booking engine changed)",
                      "open the website for tomorrow in Chrome; if rooms show there, tell the developer")
    return Result(PASS, "website", reason)


def _ezee_collector(site, evidence_dir: Path):
    from .collectors.ezee import EzeeCollector
    return EzeeCollector(site, evidence_dir=evidence_dir)


# --- doctor -----------------------------------------------------------------

def run_checks(config_path: str, offline: bool = False, env=os.environ) -> list[Result]:
    results = [_safe("python", check_python), _safe("packages", check_packages)]
    config_result, cfg = _safe_config(config_path)
    results.append(config_result)
    results += [_safe("clock", check_clock), _safe("whatsapp", lambda: check_wati(env)),
                _safe("schedule", check_schedule)]
    if cfg is None:
        needs_config = ("sites", "output", "screenshots", "memory", "profile", "last run", "browser", "website")
        return results + [Result(SKIP, name, "needs a config that loads") for name in needs_config]
    return results + [
        _safe("sites", lambda: check_sites(cfg)),
        _safe("output", lambda: check_folder("output", cfg.csv_path.parent)),
        _safe("screenshots", lambda: check_folder("screenshots", cfg.screenshot_dir)),
        _safe("memory", check_memory),
        _safe("profile", lambda: check_profile(cfg.browser.profile_dir)),
        _safe("last run", lambda: check_last_run(cfg.csv_path.parent / "last_run.json")),
        _safe("browser", lambda: check_browser(cfg, offline)),
        _safe("website", lambda: check_website(cfg, offline)),
    ]


def _safe(name: str, check) -> Result:
    """A check that crashes is reported as FAIL; the other checks still run."""
    try:
        return check()
    except Exception as exc:
        return Result(FAIL, name, f"check crashed: {exc.__class__.__name__}: {' '.join(str(exc).split())[:160]}",
                      "send this line to the developer")


def _safe_config(path: str) -> tuple[Result, Config | None]:
    try:
        return check_config(path)
    except Exception as exc:
        return Result(FAIL, "config", f"{path}: {exc.__class__.__name__}: {exc}", "fix the config file"), None


def format_result(result: Result) -> str:
    line = f"{result.level:<4}  {result.name:<11} {result.reason}"
    if result.fix and result.level != PASS:
        line += f"\n      {'':<11} fix: {result.fix}"
    return line


def doctor(config_path: str, offline: bool = False, out=print, env=os.environ) -> int:
    out(f"Rate parity doctor - {now_ist():%d-%b-%Y %H:%M} IST - read-only, changes nothing")
    results = run_checks(config_path, offline, env)
    for result in results:
        out(format_result(result))
    counts = {level: sum(r.level == level for r in results) for level in (PASS, WARN, FAIL, SKIP)}
    out(f"{counts[PASS]} PASS, {counts[WARN]} WARN, {counts[FAIL]} FAIL, {counts[SKIP]} SKIP")
    if counts[FAIL]:
        out("NOT READY: fix every FAIL line, then run doctor again.")
        return 1
    out("Ready to run." + (" Read the WARN lines too." if counts[WARN] else ""))
    return 0


# --- alert-test -------------------------------------------------------------

def alert_test(cfg: Config, to: str | None = None, env=os.environ, out=print, now: datetime | None = None) -> int:
    """Send ONE WhatsApp test message to every WATI recipient (or only --to)."""
    if to is not None:
        env = {**env, "WATI_RECIPIENTS": to.strip()}  # load_settings checks it is digits only
    try:
        settings = wati.load_settings(env)
    except ValueError as exc:  # names the missing variables, never the token
        out(f"WhatsApp (WATI) is not set up: {exc}.")
        out("Fill the WATI_ lines in .env (see .env.example), then run alert-test again.")
        return 1
    text = f"Rate parity agent test {(now or now_ist()):%d-%b-%Y %H:%M} IST - alerts work"
    try:
        sent = wati.send_summary(text, settings, cfg.alert_param_name)
    except RuntimeError:  # nobody received it; each failure was logged without the token
        sent = 0
    total = len(settings.recipients)
    out(f"sent to {sent} of {total}")
    if sent < total:
        out("Not every number got it: check WATI_API_URL, the token, that the template is approved, "
            "and that alerts.template_param_name matches its placeholder. The lines above show which number failed.")
        return 1
    return 0
