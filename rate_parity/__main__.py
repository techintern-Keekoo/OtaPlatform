"""CLI.

    python -m rate_parity run --config config.yaml [--dry-run] [--only booking_com]
    python -m rate_parity login --config config.yaml   # a human logs in to the OTAs
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
    args = parser.parse_args(argv)

    load_dotenv()
    _setup_logging()
    try:
        # login only needs browser settings, so unfilled site TODOs are fine there
        cfg = load_config(args.config, check_placeholders=(args.command == "run"))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.command == "login":
        from .browser import manual_login
        manual_login(cfg)
        return 0

    from .runner import run
    rows = run(cfg, only=args.only, dry_run=args.dry_run)
    counts = Counter(row.status.value for row in rows)
    print(f"{len(rows)} checks: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
