# Site findings (read-only Firecrawl scrape, 2026-10-08)

Property: **Zen Manali by Keekoo Stays**. Each page was scraped once with Firecrawl
(location IN, no clicks, no form input). These are leads, not verified selectors:
Firecrawl's browser is not the agent's logged-in Chrome, so confirm every
selector on the office PC before filling `config.yaml`.

## What each site returned

| Site | Result | Notes |
|---|---|---|
| Website zenhotels.in | OK | WordPress site. "Book Now" goes to `https://book.zenhotels.in/booking/roomlist-zenmanalibykeekoostays-be` |
| Booking engine | OK | **eZee / Yanolja** (`book.zenhotels.in` is a CNAME of `live.ipms247.com`). Rates shown "Exclusive of Tax". **Ignores date params in the URL** (`?checkin=22-10-2026…` and `?checkin=2026-10-22&nonights=1` both opened on today). Room search is a `POST /booking/multibox.php` |
| Second engine link `secure-booking-engine.com/...` | HTTP 500 | Linked from the site but broken. Worth telling the web team |
| Booking.com | OK | Full room table in the HTML |
| MakeMyTrip | OK | Room list rendered |
| Agoda | OK | Room grid rendered |
| Goibibo | Blocked | Returned the Goibibo home page, not the hotel |
| Cleartrip | Blocked | `SCRAPE_ALL_ENGINES_FAILED` |

## Room names per site (for the room mapping in `config.yaml`)

Matching is by name only and **needs a human to confirm**. Booking.com uses different names.

| Website (eZee) | MakeMyTrip | Agoda | Booking.com |
|---|---|---|---|
| Standard garden view room | Standard Garden View Room | Standard Garden View Room | ? (Deluxe Room / Deluxe Double Room / …) |
| Deluxe valley facing room | Deluxe Valley Facing Room | Deluxe Valley View Room | ? |
| Deluxe mountain view room | Deluxe Mountain View Room | Deluxe Mountain View | ? (Double Room with Mountain View?) |
| Premium cottage Mountain View Room | Premium Cottage Room | Premium Cottage | ? (Superior Chalet?) |
| Family Suite with Mountain View Room | Family Suite with Mountain View | Family Suite | ? (Superior Family Room?) |
| — | Quadruple Room | Quadruple Room | Quadruple Room |

## Website (eZee) room-type IDs, 8 Oct 2026

These were confirmed against the room-list screenshot from Keekoo. The price element `#roomtype_<id>` shows the room's **lowest** rate, which can be a minimum-stay deal.

| Room | eZee id | Headline price (per night, excl. tax) |
|---|---|---|
| Standard garden view room | 4757200000000000001 | Rs 1,363.44 (3-night-min deal; the 1-night rate was Rs 1,468.32) |
| Deluxe valley facing room | 4757200000000000002 | Rs 1,576.38 |
| Deluxe mountain view room | 4757200000000000003 | Rs 1,917.24 |
| Family Suite with Mountain View Room | 4757200000000000005 | Rs 2,641.86 |
| Premium cottage Mountain View Room | 4757200000000000006 | Rs 2,343.51 |

The agent must read the **1-night** plan price, not the headline. Otherwise a website min-stay deal looks cheaper than every OTA, and an OTA that is genuinely cheaper gets missed.

## Prices seen for 22–23 Oct 2026, 2 adults (search page, before checkout)

These are search-page prices, not checkout finals, so they are **not** parity results.

| Room | Booking.com | MakeMyTrip | Agoda |
|---|---|---|---|
| Cheapest room | Deluxe Room ₹2,915 + ₹166 taxes | Standard Garden View ₹1,901 + ₹245 taxes & fees | Standard Garden View ₹2,035 (non-ref.) / ₹2,050 (free cancel), before taxes |

Agoda also shows separate non-refundable and free-cancellation rates. The comparator already refuses to mix them.

## Candidate selectors (verify in real Chrome)

**Agoda** (stable `data-testid` attributes):
- room block `[data-testid='room-item']`
- room name `[data-testid='room-name']`
- offer `[data-testid='room-offer']`
- final price `[data-testid='room-offer-final-price']`
- cancellation `[data-testid='free-cancellation-message']`
- book button `[data-testid='book-button']`
- sign in `[data-testid='sign-in-button']`

**Booking.com:**
- room name `.hprt-roomtype-link`
- price `.bui-price-display__value`
- taxes `[data-testid='prd-taxes-and-fees-under-price']`
- cancellation `[data-testid='cancellation-policy']`
- room count `select.hprt-nos-select` (`data-testid='select-room-trigger'`)
- reserve `.js-reservation-button` ("I'll reserve")
- sign in `[data-testid='header-sign-in-button']`

**MakeMyTrip:**
- room name `[data-testid='rmType__roomName']`
- select-room buttons `[data-testid$='-selectRoom']`
- price: no test id; `.latoBlack.font28` (class-based, fragile)
- tax text "+ ₹ N taxes & fees"

## Blockers found

1. **Website dates.** eZee does not take dates from the URL, so the agent cannot read a future-date website price by opening a link. Options, best first:
   - **eZee rate API.** eZee offers a property-side API (HotelCode + API key from Keekoo's eZee admin). Read-only and exact, with no clicking. *Endpoint details are unverified until we have the key and docs.*
   - **Deep-link parameter.** Ask eZee support for a deep-link parameter the room list accepts.
   - **Date-picker clicks.** Click the date picker with templated selectors. This is more fragile.
2. **Booking.com deep check.** Room count is a `<select>` (the agent never sets form values), and "I'll reserve" is a form submit to `/book.html`, which the network guard blocks. The Booking.com deep check stays `COULD_NOT_CHECK` until this is designed.
3. **Goibibo and Cleartrip** block automated reads from Firecrawl. They may still work from the office PC's Chrome.

## Booking.com room matching: evidence (scraped 2026-10-08, stay 22–23 Oct)

Screenshots, from a read-only Firecrawl capture:
- Booking.com room table: https://backend.composio.dev/api/v3/sl/GtmNo4P1zZ
- Website (eZee) room list: https://backend.composio.dev/api/v3/sl/f0dL4Zb7Nw

| Website room (max guests) | MMT size / bed | Booking.com room | Why | Confidence |
|---|---|---|---|---|
| Standard garden view room (3) | 17 m², double | **Deluxe Room** (₹2,915) | The only non-dorm room with **garden view**; cheapest on every site | High |
| Premium cottage Mountain View Room (4) | 21 m², king | **Superior Chalet** (₹5,009) | "Entire chalet" = cottage; mountain view | High |
| Family Suite with Mountain View Room (7) | 2 king beds | **Superior Family Room** (₹5,647) | The only room with 2 beds; 220 m² vs 200 for the others | High |
| Deluxe valley facing room (3) | 17 m², double | **Deluxe Double Room** (₹3,370) | Price order matches (website, MMT and Booking.com all rank it below Deluxe mountain) | **Low** |
| Deluxe mountain view room (4) | 21 m², king | **Double Room with Mountain View** (₹4,099) | Name says mountain view; price order matches | **Low** |
| (none on website) | Quadruple Room | Quadruple Room | Bunk beds; OTA-only, never compared | n/a |

**All five matches were confirmed by Keekoo on 2026-10-08**, including the two that started as price-order guesses.

### Listing problems seen (worth fixing with each OTA)

- **Room sizes look wrong.** Booking.com lists every room as **200 m²** (Family 220 m²) and Agoda says **500–550 square meters**, but MakeMyTrip says **17–21 m² (180–225 sq ft)**. Square feet were probably typed into a square-metre field.
- **Room names don't match the website** on Booking.com ("Superior Chalet" vs "Premium cottage Mountain View Room"), which confuses guests comparing prices.
- **Valley room shows mountain view.** Booking.com's likely match for the *valley-facing* room says "Mountain view".
- **Broken booking link.** The second booking link on zenhotels.in (`secure-booking-engine.com/...`) returns HTTP 500.

## Booking.com deep check: solved without clicks

The room table already shows "₹N + ₹M taxes and charges" (and Genius prices when logged in). The agent reads both there: `steps: []`, `final: null`, final = shown price + shown taxes. There is no dropdown and no Reserve click.

## Goibibo and Cleartrip: options

| Option | Goibibo | Cleartrip |
|---|---|---|
| 1. Office PC real Chrome (recommended first) | Likely works: the page is a JavaScript app that Firecrawl never rendered (we got the generic shell twice), not a hard block | Possible: strong bot protection; try once by hand with the agent profile |
| 2. Use MakeMyTrip as a proxy | Same company and same listing (`mmtId=202004271355036572` in the link), so prices are usually identical. Unverified: report as "via MMT", never as a Goibibo check | n/a |
| 3. Manual weekly spot-check | Fallback | Fallback if the office PC is blocked too |

## Goibibo: candidate setup (copied from MakeMyTrip, NOT verified)

Goibibo is in the MakeMyTrip group and its hotel link carries MMT's id
(`mmtId=202004271355036572`), so `config.example.yaml` now holds a Goibibo
block that mirrors the working MakeMyTrip one: scroll, wait for room names,
read price and taxes from each room card's text, MMT's room names. Every
site-specific value starts with `TODO-verify: `, so a normal run still says
Goibibo is "not set up" and never opens it.

To confirm on the office PC (agent's Chrome, venv active):

    python -m rate_parity check --config config.example.yaml --site goibibo --days 1

The checker tries every candidate without its `TODO-verify: ` prefix. Confirm:

1. The page is the Zen Manali hotel page for the right dates and 2 adults
   (title, URL and screenshot). If it is the Goibibo home page, the link
   (`search_url`, `date_format: "%Y%m%d"`) needs fixing first.
2. `wait_for` works: the room list loads (no "room list did not load" NOTE).
3. Each of the 5 rooms shows `OK  price N + taxes M`, and N and M match what
   the screenshot shows for that room's free-cancellation, room-only offer
   (not the struck-out old price, not an "Upgrade" box).
4. Room names: if a room is MISSING, look at "room names seen" / the saved
   page text and copy Goibibo's exact name into that room's `labels.room`.
5. The tax line: if Goibibo words it differently from "+ ₹ N Taxes & Fees",
   copy the line from the saved `.txt` page text and adjust `pattern`.

Then delete every `TODO-verify: ` in the Goibibo block, run the check again,
and finally `python -m rate_parity run --config config.example.yaml --only goibibo --dry-run`.
Until then, never report Goibibo prices "via MMT" as a Goibibo check.

## Accuracy guards (added 2026-10-09)

- **Misread guard.** An OTA price below 40% or above 300% of Zen's price (same
  tax basis), or OTA taxes above 30% of the OTA price, is treated as a
  probable misread: the row shows `VERIFY` with "implausible price (...) -
  possible misread", never `LOWER!`. Limits: `sanity:` in config.
- **Website numbers must add up.** eZee's before-tax price + tax must equal
  its total within Rs 1, or the website price for that night is refused
  (every row says "website numbers do not add up").
- **Website meal plan.** The eZee reply has no meal-plan field that we have
  seen (only `Room_Name`, the plan's name). Rows with a verdict now end with
  `website plan: <name>` so a human can check the plan is room-only. If a
  plan with breakfast is ever chosen, tell the developer: the plan rule
  (refundable, 1-night, cheapest) would need a meal-plan filter.
- **Tax basis.** `price_includes_tax: true` on an OTA makes the agent compare
  its search price with Zen's final incl. tax. The checker prints a
  "tax basis hint" line (phrases such as 'incl. taxes', 'excl. taxes',
  '+ taxes', 'taxes & fees', 'price per night' found on the page). For
  Agoda, confirm the basis with it: the 8 Oct scrape said "before taxes", but
  the agent's link asks for `finalPriceView=1`.

## Live evidence, 9 Oct 2026 (stay 10–11 Oct, 2 adults)

Gathered through Composio: a direct HTTP call from its sandbox for eZee, and
Firecrawl snapshots (location India) for the OTAs. These were not taken on the
office PC, so every OTA selector below still needs one `check --site` there.

| Room | Zen before tax (+tax) | Agoda before tax | MakeMyTrip before tax + taxes & fees | Booking.com price + taxes |
|---|---|---|---|---|
| Standard Garden | 1,622.88 (+81.14) | 2,233 (cancellation policy) | 1,622 + 230 | 3,400.32 + 193 (non-ref) |
| Deluxe Valley | 1,876.56 (+93.83) | 2,583 (non-ref) | 1,876 + 265 | 3,931.84 + 223 (non-ref) |
| Deluxe Mountain | 2,282.28 (+114.11) | sold out | 2,281 + 323 | 4,781.92 + 272 (non-ref) |
| Family Suite | 3,144.54 (+157.23) | 4,334 (cancellation policy) | 3,142 + 445 | 6,588.56 + 374 (non-ref) |
| Premium Cottage | 2,789.22 (+139.46) | sold out | 2,787 + 395 | 5,844.08 + 332 (non-ref) |

What this proves:
- **eZee:** for all 36 records, the price incl. tax equals the price before tax
  plus the tax (5% GST), so the add-up guard is safe. There is no meal-plan
  field: `Room_Name` is the rate-plan name, and `Room_Description` ends in
  `EP` (European Plan = room only) for all 5 tracked rooms. Each room has 2
  plans, both refundable; one is "3 Min Nights", which the agent skips.
- **eZee has a 6th room type, `4757200000000000004` "Quadruple dom room EP",**
  which is not tracked. Agoda, MakeMyTrip and Booking.com all sell it as
  "Quadruple Room". Keekoo decides whether to track it.
- **Agoda's price is before tax:** each card says "Per night before taxes &
  fees", even with `finalPriceView=1`, so `price_includes_tax: false` is right.
  Agoda showed Deluxe Mountain and Premium Cottage sold out while eZee had 6
  and 3 rooms free: check that the channel manager pushes stock to Agoda.
- **MakeMyTrip:** the current `price_text` read all 5 rooms, and the page for
  tomorrow (the "night 2" that fails on the office PC) loaded normally. So the
  office-PC failure is more likely MMT reacting to a second quick visit than
  rooms being sold out. Before tax, MMT is Rs 1–2 **below** Zen (whole rupees
  vs paise); with taxes and fees, Zen is about 9% cheaper. The agent compares
  finals for MMT, so this is correctly "ok".
- **Booking.com:** the old candidates could not work. The tax is the class
  `.prd-taxes-and-fees-under-price`, not a `data-testid`, and `.hprt-conditions`
  does not exist. The new `price_text` (room row → "price +₹ tax taxes and fees")
  read 5/5 rooms on the snapshot. Each room had one row (non-refundable only).
  When a room has several rate plans, only its first row carries the room
  name, so the first (usually cheapest) plan is the one compared.
- **Goibibo:** Firecrawl was redirected to the Goibibo home page. **Cleartrip:**
  every Firecrawl engine was blocked. Both can only be set up from the office PC.
