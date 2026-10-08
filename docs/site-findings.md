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

| Website room (max guests) | MMT size / bed | Proposed Booking.com room | Why | Confidence |
|---|---|---|---|---|
| Standard garden view room (3) | 17 m², double | **Deluxe Room** (₹2,915) | The only non-dorm room with **garden view**; cheapest on every site | High |
| Premium cottage Mountain View Room (4) | 21 m², king | **Superior Chalet** (₹5,009) | "Entire chalet" = cottage; mountain view | High |
| Family Suite with Mountain View Room (7) | 2 king beds | **Superior Family Room** (₹5,647) | The only room with 2 beds; 220 m² vs 200 for the others | High |
| Deluxe valley facing room (3) | 17 m², double | **Deluxe Double Room** (₹3,370) | Price order matches (website, MMT and Booking.com all rank it below Deluxe mountain) | **Low** |
| Deluxe mountain view room (4) | 21 m², king | **Double Room with Mountain View** (₹4,099) | Name says mountain view; price order matches | **Low** |
| (none on website) | Quadruple Room | Quadruple Room | Bunk beds; OTA-only, never compared | n/a |

The two "Low" rows are a guess from price order: both Booking.com rooms say "mountain view". Confirm all five in the Booking.com extranet (Property > Rooms) before go-live.

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
