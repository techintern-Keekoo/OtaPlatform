"""CLI.

    python -m rate_parity run --config config.yaml [--dry-run] [--only booking_com]
    python -m rate_parity login --config config.yaml   # a human logs in to the OTAs
    python -m rate_parity check --config config.yaml --site agoda   # read-only selector check
    python -m rate_parity doctor --config config.yaml [--offline]  # is this PC ready? (read-only)
    python -m rate_parity alert-test --config config.yaml [--to 919800000000]  # one WhatsApp test
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter

from dotenv import load_dotenv

from .config import ConfigError, load_config

SECRET_ENV_VARS = ("WATI_ACCESS_TOKEN",)


class RedactSecrets(logging.Filter):
    """Belt and braces: we never log secrets, but scrub them if one slips in."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for name in SECRET_ENV_VARS:
            secret = os.environ.get(name)
            if secret and secret in message:
                message = message.replace(secret, "***")
                record.msg, record.args = message, ()
        return True


def _setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactSecrets())
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rate_parity", description="Keekoo OTA rate parity checker (never books).")
    commands = parser.add_subparsers(dest="command", required=True)
    run_cmd = commands.add_parser("run", help="run all checks once")
    run_cmd.add_argument("--config", required=True)
    run_cmd.add_argument("--dry-run", action="store_true", help="no Google Sheets / WhatsApp writes")
    run_cmd.add_argument("--only", help="check one OTA key, e.g. booking_com")
    login_cmd = commands.add_parser("login", help="open the agent's browser profile so a human can log in")
    login_cmd.add_argument("--config", required=True)
    check_cmd = commands.add_parser("check", help="read-only: which selectors does one site's page match?")
    check_cmd.add_argument("--config", required=True)
    check_cmd.add_argument("--site", required=True, help="site key, e.g. agoda, makemytrip, booking_com, website")
    check_cmd.add_argument("--days", type=int, default=14, help="check-in this many days from today")
    check_cmd.add_argument("--profile", action="store_true", help="use the logged-in agent profile")
    doctor_cmd = commands.add_parser("doctor", help="read-only: is this PC ready for the scheduled runs?")
    doctor_cmd.add_argument("--config", required=True)
    doctor_cmd.add_argument("--offline", action="store_true", help="skip the browser start and the website search")
    alert_cmd = commands.add_parser("alert-test", help="send one WhatsApp test message through WATI")
    alert_cmd.add_argument("--config", required=True)
    alert_cmd.add_argument("--to", help="send only to this number (country code + number, digits only)")
    args = parser.parse_args(argv)

    load_dotenv()
    _setup_logging()
    if args.command == "doctor":  # loads the config itself, so a bad config is one FAIL line
        from .doctor import doctor
        return doctor(args.config, offline=args.offline)
    try:
        # login only needs browser settings, so unfilled site TODOs are fine there
        cfg = load_config(args.config, check_placeholders=(args.command == "run"))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.command == "check":
        from .checker import check_site
        return check_site(cfg, args.site, args.days, args.profile)

    if args.command == "alert-test":
        from .doctor import alert_test
        return alert_test(cfg, to=args.to)

    if args.command == "login":
        from .browser import manual_login
        manual_login(cfg)
        return 0

    from .runner import run
    return _run_and_record(run, cfg, args)


def _run_and_record(run, cfg, args) -> int:
    """Run once, write output/last_run.json either way, and turn the result into
    an exit code (0 ok, 1 crash, 2 config, 3 no website price, 4 site broken)."""
    from . import health
    from .alerts import send_failure
    from .runner import now_ist

    started = now_ist()
    try:
        rows = run(cfg, only=args.only, dry_run=args.dry_run)
    except ConfigError as exc:  # e.g. a wrong --only key
        print(f"Config error: {exc}", file=sys.stderr)
        return health.CONFIG_ERROR
    except Exception as exc:
        logging.getLogger(__name__).exception("run FAILED")
        finished = now_ist()
        health.write_last_run(cfg.csv_path.parent, health.failed_run(exc, started, finished, args.dry_run))
        send_failure(exc, cfg, finished, args.dry_run)
        print(f"Run FAILED: {health.error_text(exc)}", file=sys.stderr)
        return health.CRASH
    if args.only:  # a one-site test is not a full run: keep the scheduled run's heartbeat
        logging.getLogger(__name__).info("--only run: %s not updated", health.LAST_RUN_FILE)
    else:
        health.write_last_run(cfg.csv_path.parent, health.last_run(rows, started, now_ist(), args.dry_run))
    counts = Counter(row.status.value for row in rows)
    print(f"{len(rows)} checks: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(health.health_line(rows))
    return health.exit_code(rows)


if __name__ == "__main__":
    sys.exit(main())
