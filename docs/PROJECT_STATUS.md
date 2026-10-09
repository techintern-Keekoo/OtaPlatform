# OTA Rate Parity Agent - Project Status

**Property:** Zen Manali by Keekoo Stays
**Status as of:** 9 Oct 2026
**Code:** branch `claude/festive-tesla-1av7qt`, pull request https://github.com/techintern-Keekoo/OtaPlatform/pull/1

> **Update, 9 Oct (later):** production hardening is on branch `claude/loving-cannon-s37n4o`
> (silent-failure detection, exit codes, `doctor`, misread guard, Booking.com fix).
> The plan to launch, with the office-PC steps and the 7-day go/no-go gate, is in
> [`docs/GO_LIVE_PLAN.md`](GO_LIVE_PLAN.md). Live evidence from 9 Oct is in `docs/site-findings.md`.

---

## 1. What we are making

A small program that runs on the office PC and checks room prices.

- **Goal:** the Zen website price must always be **lower** than on every OTA.
- **OTAs checked:** Booking.com, MakeMyTrip, Goibibo, Agoda, Cleartrip.
- **Rooms checked (all 5):**
  - Standard Garden View Room
  - Deluxe Valley Facing Room
  - Deluxe Mountain Facing Room
  - Family Suite
  - Premium Cottage
- **Nights checked:** tonight and tomorrow night (check-in today and check-in tomorrow).
- **When it runs:** every day at **10 AM, 6 PM and 10 PM**, through Windows Task Scheduler.
- **What it saves:**
  - Every price goes into `output\rate_parity.csv`.
  - A readable table goes into `output\report_YYYYMMDD_HHMM.txt`, with the Zen price beside every OTA price.
- **Alerts:** a WhatsApp alert through WATI. It will work once WATI is set up.
- **Cost:** ₹0. It uses Python and Playwright, and needs no paid AI. Ollama was dropped because the PC has too little memory.

### Hard rule: the program never books

- It only reads prices.
- It never types anything and never fills guest details.
- It never clicks Pay, Book, Confirm or "Final step".
- It never logs in by itself and never gets around a CAPTCHA.
- Payment websites are blocked at the network level.
- Tests check every one of these points.

---

## 2. What is made and working

| Part | Status | Notes |
|---|---|---|
| Zen website price | ✅ Working | Read directly from the eZee booking engine, for any date, with no browser. Takes the refundable plan, with price before tax, tax and total. |
| Agoda | ✅ Working | All 5 rooms read. Offer order: Free cancellation, then Cancellation policy, then Non-refundable. Price is before tax. |
| MakeMyTrip | ✅ Working (night 1) | Price and taxes are read from each room card, so we get the final price with no clicks. Night 2 has a problem (see section 3). |
| Booking.com | ⚠️ Not finished | Room names are matched. The price selectors still need checking (`TODO-verify`). |
| Goibibo | ❌ Not set up | |
| Cleartrip | ❌ Not set up | A setup guide was given. You can set it up yourself. |
| Rule check | ✅ Working | `LOWER!` means the OTA is equal to or cheaper than Zen (a problem). `ok` means Zen is cheaper. `VERIFY` means "looks cheaper, check by hand". `n/a` means no price. |
| CSV and report table | ✅ Working | 50 rows per run. The end-to-end test proves they are saved. |
| Daily schedule | ✅ Made | Start it once by running `scripts\install_schedule.bat`. |
| Checker tool | ✅ Working | `python -m rate_parity check --config config.example.yaml --site <name>` shows what the browser sees, saves a screenshot and the page text, and marks each room OK or MISSING. |
| Safety guard | ✅ Working | No typing, only allowed clicks, payment hosts blocked, pop-ups closed. |
| Tests | ✅ 229 passing | Includes a real-browser test: prices read, results stored, no booking. |
| Dashboard design | ✅ Made | https://claude.ai/artifact/11uW7LumMpMARXUJcPNtYo |
| WhatsApp (WATI) | ⏳ Waiting | Needs the link, the token (in `.env` only), the template and the phone numbers. |
| Google Sheets | ⏳ Optional | Not set up. |

### Example of a real result (8 Oct)

```
Room              Zen              MakeMyTrip            Agoda
Standard Garden   1,468 (1,542)    1,359 (1,557) ok      ...
```

The number in brackets is the final price including tax and fees.

---

## 3. Problems we are facing now

1. **MakeMyTrip night 2 does not load.**
   - For tomorrow's date the page came back with no room list, even after two tries. Night 1 works.
   - We don't know the cause yet: it could be sold out, the site blocking us, or a slow page.
   - My cloud computer cannot open MakeMyTrip (it is blocked), so this must be tested on the office PC.
2. **Booking.com, Goibibo and Cleartrip are not set up.**
   - Until they are, they show `n/a, not set up`.
3. **Agoda checkout check is not set up.**
   - When Agoda looks cheaper, the agent shows `VERIFY` instead of a confirmed `LOWER!`.
4. **WhatsApp alerts are not on yet.**
   - We are waiting for the WATI details.
5. **The office PC is low on memory** (7.9 GB, 94% used).
   - Close other apps before runs.

---

## 4. What we tried so far (and the result)

| Problem | What we tried | Result |
|---|---|---|
| The website ignored the dates in the link | Asked the booking engine directly for the price (`roomlisting.php`) | ✅ Fixed |
| The safety guard blocked the website's own search | Allowed only that exact read-only address | ✅ Fixed |
| Install error on Windows (greenlet) | Switched to newer package versions | ✅ Fixed |
| The copied zip had no git; `git pull` was pasted into the code | Used `git clone`, then `git checkout -- .` | ✅ Fixed |
| Agoda showed MISSING | Tried the 3 offer types in order; scrolled, waited, reloaded once, and scrolled until all rooms appeared | ✅ Fixed, all 5 rooms read |
| MakeMyTrip settings threw ERROR | New "read the card text" method, built from the page text you pasted | ✅ Fixed |
| MakeMyTrip reloaded once per room (9 minutes, failures) | Read price and taxes from one page load | ✅ Fixed, the run now takes about 4 minutes |
| Your own Chrome reached Agoda's booking form | Not caused by the agent (your profile, logged in). Still added "Final step / Next step / Continue to payment" to the never-click list | ✅ Extra safety |
| MakeMyTrip night 2 empty | Added a 20-second wait before the retry. If it still fails, a screenshot and the page text are saved, and the report says what the page showed | ⏳ **Needs testing on the office PC** |

---

## 5. What should be fixed or done next (in order)

1. **Test the MakeMyTrip night-2 fix** on the office PC:
   ```
   git pull
   python -m rate_parity run --config config.example.yaml --dry-run
   ```
   If it fails, send the `page showed: ...` line, or the `screenshots\makemytrip_load_failed_*.txt` file.
2. **Set up Cleartrip, Goibibo and Booking.com.** For each one:
   ```
   python -m rate_parity check --config config.example.yaml --site cleartrip
   ```
   Then send the output and the screenshot.
3. **WATI:** put the link and token in `.env` (never paste them in chat), and share the template name and phone numbers.
4. **Start the daily runs:** run `scripts\install_schedule.bat` once. Keep the PC on and logged in.
5. **Optional:** the Agoda checkout check, so that `VERIFY` becomes a confirmed result.
6. **7-day trial:** compare the agent's prices with hand checks. The target is 95% or more matching.

---

## 6. Useful commands (office PC, with the virtual environment active)

| Command | What it does |
|---|---|
| `git pull` | Get the latest code |
| `python -m pytest -q` | Run all tests (all must pass) |
| `python -m rate_parity run --config config.example.yaml --dry-run` | Full check with no WhatsApp |
| `python -m rate_parity check --config config.example.yaml --site agoda --days 1` | Check one site for tomorrow |
| `scripts\install_schedule.bat` | Start the 10 AM / 6 PM / 10 PM runs |
| `scripts\uninstall_schedule.bat` | Stop the scheduled runs |

## 7. Files to know

| File | What it holds |
|---|---|
| `config.example.yaml` | All sites, rooms and selectors (what the agent looks for) |
| `rate_parity/safety.py` | The no-booking guard |
| `rate_parity/collectors/` | How each site is read (`ezee.py` for the website, `generic.py` for the OTAs) |
| `rate_parity/runner.py` | Runs the checks and applies the rule |
| `rate_parity/report.py` | Builds the price table |
| `rate_parity/checker.py` | The site checker tool |
| `docs/site-findings.md` | Evidence found on each site |
| `docs/HANDOFF.md` | Short handoff note |

**Never commit** `.env`, `chrome-profile/`, `output/` or `screenshots/`. They are already in `.gitignore`.
