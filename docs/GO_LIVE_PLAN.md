# Go-live plan: OTA Rate Parity Agent (Zen Manali)

**Goal:** every day at 10:00, 18:00 and 22:00, report whether any OTA sells a room
equal to or cheaper than the Zen website, **without false alarms and without
silent failures**.

**Principle:** a scraper rarely fails loudly. It fails by quietly reporting
"sold out" or "ok". So we launch only when (1) every failure is visible, (2) no
misread can become a "LOWER!", and (3) 7 days of results match hand checks.

---

## 1. Who does what

| Role | Who | Owns |
|---|---|---|
| Reliability | cloud agent (done) | lazy browser profile, `SITE BROKEN?`, exit codes, `last_run.json`, crash alert |
| Ops | cloud agent (done) | `doctor` preflight, `alert-test`, log rotation, Task Scheduler exit codes |
| Accuracy | cloud agent (done) | misread guard, eZee add-up check, tax basis, Goibibo/Booking candidates |
| Monitor | cloud agent (done) | reviewed each builder against its criteria, final go/no-go review |
| **Field engineer** | **Shadow, on the office PC** | everything that needs the real Indian office IP and real Chrome (section 3) |
| Decision owner | Keekoo manager / revenue | the business questions in section 5 |

Cloud agents cannot open the OTAs. Live checks were done through Composio and
Firecrawl snapshots (see `docs/site-findings.md`), which is evidence, not a
replacement for the office PC.

## 2. What is now in the code

| Risk | Before | Now |
|---|---|---|
| OTA changes its layout | every room shows "sold out", run says success | `SITE BROKEN?` on the rows, in the report and first in WhatsApp; exit code 4 |
| Whole property really sold out | (n/a) | page says sold out for every room: treated as a full house, not broken |
| Chrome profile locked or low memory | whole run crashed, nothing saved | profile opened only when a checkout check needs it; if it can't open, only those rows say "verify manually" |
| Run crashes | Task Scheduler showed success | exit code 1, `last_run.json` = failed, WhatsApp "agent FAILED" |
| No Zen price | rows "could not check", exit 0 | exit code 3, status degraded |
| Misread price (e.g. tax read as price) | could send a false **LOWER!** | price outside 40–300% of Zen's, or tax outside 0–30%: "verify manually: implausible price", never LOWER! |
| eZee numbers inconsistent | trusted | price + tax must equal total (to Rs 1), else refused |
| Tax-inclusive OTA price | would hide violations | `price_includes_tax` per site (Agoda proven before-tax) |
| Booking.com | selectors could never match | price + tax per room row; 5/5 on a 9 Oct snapshot |
| Nobody knows the PC is ready | trial and error | `doctor` checks Python, packages, config, disk, memory, profile lock, Chrome, website, WATI, clock, schedule, last run |

Tests: 229 → 389, all passing, including a real-Chromium end-to-end run.

## 3. Field tasks on the office PC (in this order)

Each step has a pass condition. Don't move on until it passes.

1. **Update.** `git pull`, then `python -m pytest -q`. Pass: all tests pass.
2. **Preflight.** Double-click `scripts\doctor.bat`. Pass: no `FAIL` line.
   Read every `WARN`; each one prints its fix.
3. **Booking.com (about 10 min).**
   `python -m rate_parity check --config config.example.yaml --site booking_com --days 1`
   Pass: all 5 rooms OK, and the prices look like the ones on booking.com
   in your own browser. Then delete the two `TODO-verify: ` prefixes in the
   `booking_com` block (`wait_for` and `container`) and send the output.
4. **MakeMyTrip night 2.** `python -m rate_parity run --config config.example.yaml --dry-run`.
   If night 2 is still empty, send `screenshots\makemytrip_load_failed_*.txt`.
   The 9 Oct snapshot shows the page for tomorrow works, so if it still fails
   the next fix is a longer wait between the two MMT visits.
5. **Goibibo.** `check --site goibibo --days 1`. The candidate is a copy of
   MakeMyTrip (same company). Pass: 5 rooms OK; then remove the prefixes.
6. **Cleartrip.** `check --site cleartrip --days 1` and send the output and
   page text. It blocks every scraper we tried, so it is the last one.
7. **WhatsApp.** Put WATI values in `.env` (never in chat), then
   `python -m rate_parity alert-test --config config.example.yaml --to <your number>`.
   Pass: the message arrives.
8. **Schedule.** `scripts\install_schedule.bat`. After the next run, `doctor`
   again. Pass: `last run` and `schedule` PASS, and "Last Run Result" is `0x0`.

## 4. Launch gate: 7-day shadow trial

- For 7 days, WhatsApp goes **only to Shadow's number** (`WATI_RECIPIENTS`).
- Each day, check **2 rooms × 2 OTAs by hand** against the report and note
  match / no match. That gives 28 or more spot checks.
- **GO to the revenue team** when all of these hold:
  - 95% or more of the spot checks match;
  - no false `LOWER!` at all;
  - no run missed (`last_run.json` updated 3 times a day);
  - no `SITE BROKEN?` left unexplained.
- **NO-GO** on any false `LOWER!`: find the cause, add a test, restart the 7 days.

After launch, budget 1–2 hours a week for layout changes. `SITE BROKEN?` tells
you which site to fix and which command to run.

## 5. Decisions needed from Keekoo

1. **Quadruple dorm room** (eZee type `...0004`): track it as a 6th room? All
   three OTAs sell it as "Quadruple Room".
2. **Agoda stock:** on 10 Oct Agoda showed Deluxe Mountain and Premium Cottage
   sold out while the website had 6 and 3 free. Is the channel manager pushing
   all inventory?
3. **Non-refundable OTA offers:** many OTA offers close to the date are
   non-refundable only, while Zen's is refundable. The report labels them;
   decide whether they count (option A, current) or not (option B).
4. **Recipients** after the trial, and whether degraded runs (exit 3/4) should
   alert the revenue team or only Shadow.

## 6. Next, after launch (not needed for go-live)

- Agoda checkout check, so `VERIFY` becomes a confirmed result.
- Google Sheets and the Looker Studio dashboard.
- More dates (7/14/30 days ahead) once 2 nights are stable.
