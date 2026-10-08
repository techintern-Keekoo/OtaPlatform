"""Load config.yaml (yaml.safe_load) into plain dataclasses with clear errors.

Secrets are never read from here; they come from the environment (.env).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .models import RoomKey, Stay
from .safety import DEFAULT_COMMIT_PATH_PATTERNS, host_allowed

_MISSING = object()


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SummarySelectors:
    ready: str
    room_name: str
    meal_plan: str
    cancellation: str
    final: str
    room_price: str | None
    gst: str | None
    fees: str | None
    discount: str | None


@dataclass(frozen=True)
class SiteRoom:
    labels: RoomKey  # how this site words the room / meal plan / cancellation
    search_price: str
    steps: tuple[str, ...]
    summary: SummarySelectors


@dataclass(frozen=True)
class Site:
    key: str
    label: str
    enabled: bool
    allowed_domains: tuple[str, ...]
    search_url: str
    date_format: str
    logged_in_marker: str | None
    login_wall_selectors: tuple[str, ...]
    captcha_selectors: tuple[str, ...]
    block_texts: tuple[str, ...]
    rooms: dict[str, SiteRoom]

    def search_url_for(self, stay: Stay) -> str:
        return (
            self.search_url.replace("{checkin}", stay.checkin.strftime(self.date_format))
            .replace("{checkout}", stay.checkout.strftime(self.date_format))
            .replace("{adults}", str(stay.adults))
        )

    def step_selectors(self) -> set[str]:
        return {step for room in self.rooms.values() for step in room.steps}


@dataclass(frozen=True)
class StayPlan:
    days_ahead: tuple[int, ...]
    nights: int
    adults: int


@dataclass(frozen=True)
class BrowserSettings:
    profile_dir: Path
    channel: str | None
    headless: bool
    timeout_ms: int
    delay_seconds: tuple[float, float]


@dataclass(frozen=True)
class Config:
    property_name: str
    tolerance_pct: Decimal
    stay: StayPlan
    rooms: dict[str, RoomKey]
    sites: dict[str, Site]
    browser: BrowserSettings
    commit_path_patterns: tuple[str, ...]
    extra_payment_hosts: tuple[str, ...]
    screenshot_dir: Path
    csv_path: Path
    sheet_worksheet: str
    alert_param_name: str
    alert_include_could_not_check: bool
    alert_max_chars: int

    @property
    def website(self) -> Site:
        return self.sites["website"]

    def otas(self, only: str | None = None) -> list[Site]:
        if only is not None and (only == "website" or only not in self.sites):
            raise ConfigError(f"--only must be an OTA key from config, got {only!r}")
        return [
            site for key, site in self.sites.items()
            if key != "website" and site.enabled and (only is None or key == only)
        ]


def _get(data: dict, key: str, where: str, kind, default=_MISSING):
    if not isinstance(data, dict):
        raise ConfigError(f"{where} must be a mapping")
    value = data.get(key)
    if value is None:
        if default is _MISSING:
            raise ConfigError(f"{where}.{key} is required")
        return default
    if not isinstance(value, kind):
        raise ConfigError(f"{where}.{key} has the wrong type ({type(value).__name__})")
    return value


def _strings(data: dict, key: str, where: str) -> tuple[str, ...]:
    values = _get(data, key, where, list, [])
    if not all(isinstance(v, str) and v for v in values):
        raise ConfigError(f"{where}.{key} must be a list of non-empty strings")
    return tuple(values)


def _room_key(data: dict, where: str) -> RoomKey:
    return RoomKey(
        room=_get(data, "room", where, str),
        meal_plan=_get(data, "meal_plan", where, str),
        cancellation=_get(data, "cancellation", where, str),
    )


def _summary(data: dict, where: str) -> SummarySelectors:
    required = {k: _get(data, k, where, str) for k in ("ready", "room_name", "meal_plan", "cancellation", "final")}
    optional = {k: _get(data, k, where, str, None) for k in ("room_price", "gst", "fees", "discount")}
    return SummarySelectors(**required, **optional)


def _site_room(data: dict, where: str, canonical: RoomKey) -> SiteRoom:
    labels = data.get("labels")
    steps = _strings(data, "steps", where)
    if not steps:
        raise ConfigError(f"{where}.steps must list the clicks that reach the summary page")
    return SiteRoom(
        labels=_room_key(labels, f"{where}.labels") if labels else canonical,
        search_price=_get(data, "search_price", where, str),
        steps=steps,
        summary=_summary(_get(data, "summary", where, dict), f"{where}.summary"),
    )


def _site(key: str, data: dict, rooms: dict[str, RoomKey]) -> Site:
    where = f"sites.{key}"
    domains = _strings(data, "allowed_domains", where)
    url = _get(data, "search_url", where, str)
    parts = urlsplit(url.replace("{", "").replace("}", ""))
    if parts.scheme != "https" or not host_allowed(parts.hostname, domains):
        raise ConfigError(f"{where}.search_url must be https and inside allowed_domains")
    raw_rooms = _get(data, "rooms", where, dict, {})
    site_rooms = {}
    for room_id, room_data in raw_rooms.items():
        if room_id not in rooms:
            raise ConfigError(f"{where}.rooms.{room_id} is not defined in top-level rooms")
        site_rooms[room_id] = _site_room(room_data, f"{where}.rooms.{room_id}", rooms[room_id])
    return Site(
        key=key,
        label=_get(data, "label", where, str, key),
        enabled=_get(data, "enabled", where, bool, False),
        allowed_domains=domains,
        search_url=url,
        date_format=_get(data, "date_format", where, str, "%Y-%m-%d"),
        logged_in_marker=_get(data, "logged_in_marker", where, str, None),
        login_wall_selectors=_strings(data, "login_wall_selectors", where),
        captcha_selectors=_strings(data, "captcha_selectors", where),
        block_texts=_strings(data, "block_texts", where),
        rooms=site_rooms,
    )


def _find_todos(value, path: str):
    if isinstance(value, str) and "todo" in value.lower():
        yield path
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _find_todos(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _find_todos(v, f"{path}[{i}]")


def _check_placeholders(raw: dict) -> None:
    active = dict(raw)
    active["sites"] = {k: v for k, v in raw.get("sites", {}).items() if v.get("enabled")}
    todos = list(_find_todos(active, "config"))
    if todos:
        raise ConfigError("unfilled TODO placeholders: " + ", ".join(todos))


def _validate_sites(sites: dict[str, Site]) -> None:
    website = sites.get("website")
    if website is None or not website.enabled or not website.rooms:
        raise ConfigError("sites.website must exist, be enabled and map at least one room")
    for site in sites.values():
        if site.enabled and not site.rooms:
            raise ConfigError(f"sites.{site.key} is enabled but maps no rooms")
        missing = set(site.rooms) - set(website.rooms) if site.enabled else set()
        if missing:
            raise ConfigError(f"sites.{site.key} rooms not mapped on website: {sorted(missing)}")


def parse_config(raw: dict, check_placeholders: bool = True) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("config file must be a YAML mapping")
    if check_placeholders:
        _check_placeholders(raw)

    rooms = {}
    for i, item in enumerate(_get(raw, "rooms", "config", list)):
        room_id = _get(item, "id", f"rooms[{i}]", str)
        rooms[room_id] = _room_key(item, f"rooms[{i}]")

    sites = {k: _site(k, v, rooms) for k, v in _get(raw, "sites", "config", dict).items()}
    _validate_sites(sites)

    stay = _get(raw, "stay", "config", dict)
    days = tuple(_get(stay, "days_ahead", "stay", list))
    nights = _get(stay, "nights", "stay", int, 1)
    adults = _get(stay, "adults", "stay", int, 2)
    if not days or any(not isinstance(d, int) or d < 0 for d in days) or nights < 1 or adults < 1:
        raise ConfigError("stay: days_ahead must be non-negative ints; nights and adults >= 1")

    b = _get(raw, "browser", "config", dict)
    delay = _get(b, "delay_seconds", "browser", list, [5, 12])
    if len(delay) != 2 or not 0 <= delay[0] <= delay[1]:
        raise ConfigError("browser.delay_seconds must be [min, max] with 0 <= min <= max")
    browser = BrowserSettings(
        profile_dir=Path(_get(b, "profile_dir", "browser", str)),
        channel=_get(b, "channel", "browser", str, None),
        headless=_get(b, "headless", "browser", bool, False),
        timeout_ms=_get(b, "timeout_ms", "browser", int, 30000),
        delay_seconds=(float(delay[0]), float(delay[1])),
    )

    network = _get(raw, "network", "config", dict, {})
    commit = _strings(network, "commit_path_patterns", "network") or DEFAULT_COMMIT_PATH_PATTERNS
    output = _get(raw, "output", "config", dict, {})
    alerts = _get(raw, "alerts", "config", dict, {})

    return Config(
        property_name=_get(_get(raw, "property", "config", dict), "name", "property", str),
        tolerance_pct=Decimal(str(_get(raw, "tolerance_pct", "config", (int, float)))),
        stay=StayPlan(days, nights, adults),
        rooms=rooms,
        sites=sites,
        browser=browser,
        commit_path_patterns=commit,
        extra_payment_hosts=_strings(network, "extra_payment_host_keywords", "network"),
        screenshot_dir=Path(_get(output, "screenshot_dir", "output", str, "screenshots")),
        csv_path=Path(_get(output, "csv_path", "output", str, "output/rate_parity.csv")),
        sheet_worksheet=_get(output, "sheet_worksheet", "output", str, "checks"),
        alert_param_name=_get(alerts, "template_param_name", "alerts", str, "summary"),
        alert_include_could_not_check=_get(alerts, "include_could_not_check", "alerts", bool, True),
        alert_max_chars=_get(alerts, "max_chars", "alerts", int, 900),
    )


def load_config(path: str | Path, check_placeholders: bool = True) -> Config:
    try:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from None
    return parse_config(raw, check_placeholders)
