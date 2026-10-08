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
