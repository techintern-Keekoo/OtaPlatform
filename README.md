# OTA Rate Parity Agent (Keekoo Hospitality)

Checks whether Keekoo's rooms are sold cheaper on OTAs (Booking.com, MakeMyTrip,
Goibibo, Agoda, Cleartrip) than on Keekoo's own website, and reports once a day.

## What it does

1. **Quick scan.** Opens a fresh, logged-out browser and reads the search-page
   price for each tracked room on the website and each enabled OTA. If an OTA
   price is not above the website (Zen) price, that room is a **suspect**.
2. **Deep check (suspects only).** Uses a dedicated Chrome profile that a human
   has already logged in to. It opens the same room, clicks through to the
   booking **summary** page, and reads room price, GST, fees, discount and the
   final payable amount from the page (GST is never calculated). It takes a
   screenshot, then stops and closes the page. The same check runs on the Keekoo
   website.
3. **Compare.** `gap% = (ota_final - website_final) / website_final`. Prices are
   compared only when the room, meal plan and cancellation policy all match.
   Keekoo's rule: **Zen (the website) must be cheaper than every OTA.**
   - `VIOLATION`: the OTA final is equal to or below Zen's final (or not above it by more than `min_margin_pct`, default 0).
   - `FALSE_ALARM`: the room was a suspect in step 1 but the OTA is more expensive at checkout.
   - `IN_PARITY`: the OTA search price was above Zen's.
   - `COULD_NOT_CHECK`: login wall, CAPTCHA, missing selector, room mismatch or another error. The note says what to do, for example "re-login to Booking.com" or "verify manually".
4. **Report.** After every run a table of every room x every OTA next to Zen is
   printed and saved as `output/report_<date>_<time>.txt`. Rows go to Google Sheets. If Sheets fails or is not configured,
   they go to a local CSV file. One WhatsApp summary is sent each day through
   a WATI template message.

## The NO-BOOKING rule

The agent must never book, reserve-and-pay or pay. Several separate layers
enforce this, and any one of them is enough to stop a booking:

| Layer | Where | What it does |
|---|---|---|
| No typing at all | `safety.SafePage` | Collectors can only `goto`, read text, take screenshots and `click_step`. They have no fill, type, press, keyboard or evaluate methods. Guest and card details are never entered, so a booking form can never be completed. |
| Click allowlist + deny words | `SafePage.click_step` | Only selectors listed as `steps` in config can be clicked. Before each click the element is pinned, and its text, aria-label, aria-labelledby text, value, title, alt and name are read, together with those of the button/link/label around it. The click is refused (`SafetyViolation`) if the element has no readable label at all, or if any label matches *pay, payment, confirm, complete/finish (your) booking/reservation, place (your) order, book (now) and/& pay, submit, purchase, checkout now, proceed to pay, card, upi, wallet* or the Hindi words for payment, confirm and card. The match is case-insensitive, ignores invisible characters and full-width letters, and also catches longer words that start with these, such as "Payable" or "Confirmation". |
| Navigation allowlist | `check_url_allowed` | Only https URLs on the site's `allowed_domains` are allowed. The URL is checked again after redirects and after every click. |
| Network guard | `safety.NetworkGuard` via `context.route("**/*")` | Every request to a payment gateway is aborted: razorpay, payu, paytm, stripe, adyen, juspay, ccavenue, billdesk, cashfree, checkout.com, braintree and paypal. Config can add gateways but cannot remove these. Every non-GET request to a booking-commit path (`/book`, `/confirm`, `/payment`, ... set in config) is also aborted. So is any non-GET request whose body names a booking or payment action (e.g. `CreateBooking`, `placeOrder`), even on a generic endpoint such as `/graphql`. Each abort is logged. Service workers are blocked so they cannot get around the guard. |
| Popup closer | `SafePage._close_popup` | Any new tab or popup the page opens is closed at once, so the agent cannot be led outside the site's allowlist. |
| Kill switch + self-check | `safety.BOOKING_ALLOWED = False`, `safety.self_check` | A test asserts the constant is `False`. If it were ever `True`, every click would be refused. The run refuses to start unless the network guard is installed on both browser contexts. |
| No login automation | `collectors/base.py` | The agent never logs in, never creates accounts and never solves a CAPTCHA. If it sees a login wall or CAPTCHA (from configured selectors or text on the page), the check becomes `COULD_NOT_CHECK` with a note for a human. |

## Languages and tools

Python 3.11+, Playwright (Chrome), PyYAML, gspread with google-auth (service
account), requests (WATI API), python-dotenv and pytest.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium        # only needed for real runs, not for tests
cp config.example.yaml config.yaml # then fill every TODO (see below)
cp .env.example .env               # then fill secrets
python -m pytest -q
```

Log in to the OTAs once in the agent's own browser profile. A human types the
password; the agent only opens the window:

```bash
python -m rate_parity login --config config.yaml
```

## Config

* `config.yaml` holds everything site-specific: domains, the search URL template
  (`{checkin}`, `{checkout}`, `{adults}`), the price selector, the click `steps`
  that reach the summary page, and the summary field selectors. **None of the
  selectors are known yet.** Every value marked `TODO ... UNVERIFIED` must be
  filled in after inspecting the live page. The agent refuses to run while an
  enabled site still has a TODO.
* For Week 1, only `website` and `booking_com` are enabled. The other OTAs are
  present with `enabled: false`.
* Secrets live only in `.env`: `WATI_API_URL`, `WATI_ACCESS_TOKEN`,
  `WATI_TEMPLATE_NAME`, `WATI_RECIPIENTS`, `GOOGLE_SERVICE_ACCOUNT_FILE` and
  `SHEET_ID`. They are never put in YAML and never logged.
* The browser profile, screenshots, output CSV, `.env`, `config.yaml` and JSON
  key files are all gitignored.

## Run

```bash
python -m rate_parity run --config config.yaml --dry-run          # writes local CSV only, no Sheets/WhatsApp
python -m rate_parity run --config config.yaml --only booking_com  # one OTA
python -m rate_parity run --config config.yaml                     # full run: Sheets + one WhatsApp summary
```

To run it daily, schedule the full command once a day (Windows Task Scheduler or cron).

Saved columns: ota, property, room, meal_plan, cancellation, checkin, checkout,
adults, login_state, search_price, checkout_room_price, gst, fees, discount,
final_payable, website_final, gap_pct, status, note, screenshot_path, and
checked_at (ISO time, Asia/Kolkata).

## Daily schedule (Windows)

Runs 3 times a day: **10:00, 18:00 and 22:00** (the PC's clock, keep it on IST).

1. Set up the project once (`.venv`, `pip install -r requirements.txt`).
2. Double-click `scripts\install_schedule.bat`. It creates 3 Windows Task Scheduler tasks.
3. The PC must be on and you must be logged in at those times. The OTA check uses a visible Chrome window.
4. Results go to `output\`: `rate_parity.csv` (every check), `report_*.txt` (the price table per run) and `agent.log`.
5. To stop it, double-click `scripts\uninstall_schedule.bat`.

An OTA that still has `TODO` in the config shows as "not set up" in the table and is never opened, so the website and the ready OTAs keep running. Use `python -m rate_parity check --config config.yaml --site <key>` to verify an OTA's selectors.

## Before go-live

On the office PC, in the project folder with the venv active:

1. `git pull`, then `python -m pytest -q` (all tests must pass).
2. Double-click `scripts\doctor.bat` (or run
   `python -m rate_parity doctor --config config.example.yaml`). It only looks:
   it changes nothing and never books. Fix every `FAIL` line and read every
   `WARN` line; each line says the fix. It exits 1 while any check FAILs.
   `--offline` skips the browser start and the website search.
3. `python -m rate_parity alert-test --config config.example.yaml --to 9198XXXXXXXX`
   sends one WhatsApp test message to that number ("sent to 1 of 1"). Without
   `--to` it goes to everyone in `WATI_RECIPIENTS`.
4. `python -m rate_parity run --config config.example.yaml --dry-run` once and
   read the price table.
5. Double-click `scripts\install_schedule.bat`. After the next scheduled time,
   run doctor again: the `last run` and `schedule` lines should PASS, and Task
   Scheduler's "Last Run Result" should be `0x0` (anything else = the run failed;
   see `output\agent.log`, which is moved to `agent.log.1` when it passes 5 MB).

## Open questions before go-live

- [x] **Booking engine:** eZee (Yanolja) at `book.zenhotels.in`. Dates cannot be set from the URL; see `docs/site-findings.md`. Still needed: the eZee API key or a deep-link parameter.
- [x] **Office PC 24/7:** confirmed by Keekoo on 2026-10-08. Original question: will a dedicated office PC stay on with Chrome and the logged-in profile? It must not be someone's personal profile.
- [ ] **WATI API access:** the tenant API URL and token, an approved template name, and the parameter name of the template placeholder.
- [ ] **Alert recipients:** who gets the daily WhatsApp summary?
- [ ] **OTA logins:** which company email will the OTA accounts use? Manager approval is needed before logging the agent's profile in.
- [ ] **Blocked checks in WhatsApp:** should `COULD_NOT_CHECK` items be included in the WhatsApp summary? Currently `alerts.include_could_not_check: true`.
- [x] **Links:** the OTA listing links (Booking.com, MakeMyTrip, Goibibo, Agoda, Cleartrip) are in `config.example.yaml`. The website booking-page link is still TODO.
- [x] **OTAs:** Booking.com, MakeMyTrip, Goibibo, Agoda and Cleartrip. Expedia was removed because it was not in the list provided; say if it should come back.
- [ ] **Selectors:** inspect each live page from the office PC and fill in the `TODO` selectors. The cloud build machine could not reach these sites.
- [x] **Rule:** Zen must be cheaper than every OTA (`min_margin_pct: 0`). Checks tonight and tomorrow night (`days_ahead: [0, 1]`).
