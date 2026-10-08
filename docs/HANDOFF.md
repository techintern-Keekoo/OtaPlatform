# Handoff - OTA Rate Parity Agent (Zen Manali by Keekoo Stays)

Rule: Zen website price must be LOWER than every OTA. The agent only READS prices;
it never books (no typing, no guest details, no Pay/Book/Confirm, payment hosts blocked).

## Status (8 Oct 2026)
| Site | State |
|---|---|
| Website (eZee) | Live - read straight from the booking engine, any date |
| Agoda | Live - 5 rooms, before-tax price; checkout steps not set up (cheaper cases show VERIFY) |
| MakeMyTrip | Live - price + taxes from the room list (one page load per night) |
| Booking.com | Selectors still `TODO-verify` |
| Goibibo, Cleartrip | Not set up (`TODO`) |
| WhatsApp (WATI) | Waiting for link, token (`.env` only), template, recipients |

Runs: today + tomorrow, at 10:00, 18:00, 22:00 (`scripts\install_schedule.bat`).
Output: `output\rate_parity.csv` (all rows), `output\report_*.txt` (table), `output\agent.log`.

## Known issue
MakeMyTrip night 2 once returned no room list (twice). Now the agent waits 20 s before
the second try, and if it still fails it saves a screenshot + page text
(`screenshots\makemytrip_load_failed_<date>_*.png/.txt`) and puts what the page said in the report.
Next step: read that text - sold out, blocked, or slow page - and adjust.

## Commands (office PC, `C:\otaPlatform\OtaPlatform`, venv active)
    git pull
    python -m pytest -q                                   # all tests must pass
    python -m rate_parity check --config config.example.yaml --site <site> --days 1
    python -m rate_parity run --config config.example.yaml --dry-run

## Next
1. Set up Cleartrip, Goibibo and Booking.com with `check --site ...` (guide given in chat).
2. WATI details into `.env`, then run without `--dry-run`.
3. Run 7 days; compare with manual checks (target: 95% or more match).

Never commit `.env`, `chrome-profile/`, `output/` or `screenshots/`.
