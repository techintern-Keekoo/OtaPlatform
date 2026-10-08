# OTA Rate Parity Agent (Keekoo Hospitality)

Checks whether Keekoo's rooms are sold cheaper on OTAs (Booking.com, MakeMyTrip,
Goibibo, Agoda, Expedia) than on Keekoo's own website, and reports once a day.

## What it does

1. **Quick scan.** Opens a fresh, logged-out browser and reads the search-page
   price for each tracked room on the website and each enabled OTA. If an OTA
   price is below the website price, that room is a **suspect**.
2. **Deep check (suspects only).** Uses a dedicated Chrome profile that a human
   has already logged in to. It opens the same room, clicks through to the
   booking **summary** page, and reads room price, GST, fees, discount and the
   final payable amount from the page (GST is never calculated). It takes a
   screenshot, then stops and closes the page. The same check runs on the Keekoo
   website.
3. **Compare.** `gap% = (ota_final - website_final) / website_final`. Prices are
   compared only when the room, meal plan and cancellation policy all match.
   - `VIOLATION`: the OTA is cheaper by more than `tolerance_pct`.
   - `FALSE_ALARM`: the room was a suspect in step 1 but is not cheaper (beyond tolerance) at checkout.
   - `IN_PARITY`: the OTA search price was not below the website's.
   - `COULD_NOT_CHECK`: login wall, CAPTCHA, missing selector, room mismatch or another error. The note says what to do, for example "re-login to Booking.com" or "verify manually".
4. **Report.** Rows go to Google Sheets. If Sheets fails or is not configured,
   they go to a local CSV file. One WhatsApp summary is sent each day through
   a WATI template message.

## The NO-BOOKING rule

The agent must never book, reserve-and-pay or pay. Several separate layers
enforce this, and any one of them is enough to stop a booking:

| Layer | Where | What it does |
|---|---|---|
| No typing at all | `safety.SafePage` | Collectors can only `goto`, read text, take screenshots and `click_step`. They have no fill, type, press, keyboard or evaluate methods. Guest and card details are never entered, so a booking form can never be completed. |
| Click allowlist + deny words | `SafePage.click_step` | Only selectors listed as `steps` in config can be clicked. Before each click the element's text, aria-label, value and title are read. The click is refused (`SafetyViolation`) if any of them matches *pay, payment, confirm, complete (your) booking, place order, book (now) and/& pay, submit, purchase, checkout now, proceed to pay, card, upi, wallet*. The match is case-insensitive and also catches longer words that start with these, such as "Payable" or "Confirmation". |
| Navigation allowlist | `check_url_allowed` | Only https URLs on the site's `allowed_domains` are allowed. The URL is checked again after redirects and after every click. |
| Network guard | `safety.NetworkGuard` via `context.route("**/*")` | Every request to a payment gateway is aborted: razorpay, payu, paytm, stripe, adyen, juspay, ccavenue, billdesk, cashfree, checkout.com, braintree and paypal. Config can add gateways but cannot remove these. Every non-GET request to a booking-commit path (`/book`, `/confirm`, `/payment`, ... set in config) is also aborted, and each abort is logged. Service workers are blocked so they cannot get around the guard. |
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

## Open questions before go-live

- [ ] **Booking engine:** which engine runs Keekoo's website, and what are its domain(s) and booking flow?
- [ ] **Office PC 24/7:** will a dedicated office PC stay on with Chrome and the logged-in profile? It must not be someone's personal profile.
- [ ] **WATI API access:** the tenant API URL and token, an approved template name, and the parameter name of the template placeholder.
- [ ] **Alert recipients:** who gets the daily WhatsApp summary?
- [ ] **OTA logins:** which company email will the OTA accounts use? Manager approval is needed before logging the agent's profile in.
- [ ] **Blocked checks in WhatsApp:** should `COULD_NOT_CHECK` items be included in the WhatsApp summary? Currently `alerts.include_could_not_check: true`.
- [ ] **Links:** the property listing link on each OTA, and the website booking link.
- [ ] **OTAs:** which OTAs is Keekoo actually listed on?
- [ ] **Tolerance:** what tolerance % does management want? The example uses 1.0, which is a placeholder.
