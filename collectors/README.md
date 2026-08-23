# The collectors

The Bright Data Scraper Studio code behind the universes. Each collector is two
files: an **interaction** script that drives the fetch (a real browser for the
three live sites) and a **parser** that reads cheap facts off the resulting DOM.

The zepto, blinkit and instamart files are **copied verbatim from the production
templates on 2026-08-23**. The only changes are cosmetic comment fixes - every one
of them is listed at the bottom of this file, and the code outside comments is
byte-identical to what ran, with a single exception noted there. The chaos pair is
the hand-written source that was pasted into Scraper Studio to create that
collector, before the store was flipped and the template was healed; see
"The chaos collector" below.

They are here to be read, not to be run from this repo: they execute inside
Scraper Studio, against Studio's workers and its API.

```
collectors/
  zepto/interaction.js      zepto/parser.js
  blinkit/interaction.js    blinkit/parser.js
  instamart/interaction.js  instamart/parser.js
  chaos/interaction.js      chaos/parser.js
```

## Input contract

Every collector takes exactly two inputs:

```json
{"keyword": "amul butter", "pincode": "560001"}
```

That is the whole input. **No coordinates are sent**, because none are read: each
collector types the pincode into the site's own location picker and lets the site
decide what that resolves to. A collector whose input schema still *requires*
`lat`/`long` will reject the trigger with a 422 until those fields are dropped.

Bright Data appends the job's `input` object to every delivered row, so the
keyword and pincode ride along on the output for free.

## Output contract: 18 fields per product

One record per product (per *variation* on Instamart - a multipack is a separate
SKU and gets its own row). Prices are **rupees**, already converted. This mirrors
`server/mappers/collector_rows.py`, which is what consumes it.

| field | type | notes |
| --- | --- | --- |
| `product_name` | string | a row with no name is dropped by the app |
| `brand` | string \| null | |
| `package_size` | string \| null | display text: `"100 g"`, `"1 pack (10 x 10 g)"`, `"100 g X 2"` |
| `product_id` | string | the site's own id; the app dedupes on it |
| `mrp` | Money \| null | Studio `Money`, serialised as `{"value": 65, "currency": "INR", "symbol": "₹"}` |
| `selling_price` | Money \| null | |
| `discounted_selling_price` | Money \| null | the app takes the lower of this and `selling_price` |
| `out_of_stock` | bool | |
| `available_quantity` | int \| null | null is not zero - Instamart publishes no stock count |
| `is_sponsored` | bool | paid slots are kept and flagged, never dropped |
| `rating` | number \| null | collected, and deliberately dropped by the app |
| `image_url` | string \| null | |
| `serp_screenshot` | file ref | see "Known limits" |
| `store_id` | string \| null | dark store / merchant / pod id - provenance only |
| `requested_pincode` | string | echoed back so a row states what it was asked for |
| `resolved_area` | string \| null | **the site's own delivery-address line.** This is the app's location proof |
| `eta_minutes` | int \| null | the listed delivery time |
| `captured_at` | ISO8601 UTC | stamped once per run, carried on every row |

There is no member-price field anywhere in this contract, on purpose: a Zepto Pass
or equivalent tier price has nowhere to travel, so it cannot leak into a receipt.

## Per universe

| universe | worker |
| --- | --- |
| zepto | Browser |
| blinkit | Browser |
| instamart | Browser |
| chaos | Code |

Collector and template IDs are deployment configuration. They are not committed
to this repository.

The three live universes follow the same shape:

1. `country('in')`, then navigate to the search results page.
2. Bind the location: open the site's location control, type the pincode, click
   the suggestion the site offers back. That suggestion IS the site's resolution
   of the pincode, and it is what becomes `resolved_area`.
3. Wait for the search payload the site fetches after the bind, tagged by URL.
4. Capture the SERP screenshot **before** building rows, so every row carries the
   same capture reference.
5. Build the 18-field rows in the interaction script and `collect()` them once.

The chaos collector shares only the last of those steps. It runs on a Code
worker: one plain HTTP fetch of a server-rendered page, no location choreography
(its pincode is a query parameter), no tagged JSON response and no screenshot.
It builds the same 18 fields and calls `collect()` once.

Rows are built in the *interaction*, not the parser, for a Studio reason worth
knowing: tag values are auto-injected into `parse()`'s **return value** as
`<field>` and `<field>_url`, and `parser.<field>` never populates during a live
run. The parsers therefore stay thin - they hand back DOM facts (`resolved_area`,
`page_eta` on Blinkit and Instamart, `eta_minutes` on Zepto, hydration marker
counts) and nothing else on the live path. Zepto's parser also keeps a full
`products` fallback for the offline harness, which the live run never uses.

## Studio functions used

**Interaction:** `tag_response`, `tag_screenshot`, `navigate`, `el_exists`,
`click`, `type`, `wait_page_idle`, `wait_network_idle`, `wait_hidden`,
`wait_visible`, `wait_for_parser_value`, `html`, `parse`, `collect`, `blocked`,
`bad_input`, `country`, `Money`.

**Parser:** `$` (cheerio over the rendered DOM), `Money`, `Image`.

Not every file uses every one: Instamart uses `wait_visible` and no
`wait_network_idle` (Swiggy never goes idle, so each such call burnt Bright
Data's 30-second cap - five of them cost 150 s a run); Zepto and Blinkit use
`wait_network_idle` with an ignore list and no `wait_visible`.

## How the app drives them

`server/bd_client.py`, three REST calls, identical in shape for every universe:

```
POST /dca/trigger?collector=<c_id>[&version=dev]&queue_next=1
     body: [{"keyword": "...", "pincode": "560001"}]
     -> {"collection_id": "j_..."}

GET  /dca/log/{collection_id}
     -> {"status", "pages", "pages_left", "lines", "fails", ...}
        status: building | running | done | failed | cancelled
        `pages_left` is what becomes the app's `progress` events

GET  /dca/dataset?id=<collection_id>
     202 -> still building
     200 -> the delivered rows, as JSONL (one record per line)
```

The trigger must be the REST call. The Studio CLI only takes a positional URL and
cannot pass `keyword`/`pincode` inputs at all.

`version=dev` is sent only when asked for; `prod` is Bright Data's default and is
sent by omitting the parameter. Prefer `prod`: a collector's *delivered* fields
are synced from the template's output schema only when a template is saved to
production, so a dev-version run can deliver a truncated projection of rows the
template really did collect.

## Known limits

**The screenshot is captured and cannot be delivered.** Each of the three live
collectors takes a SERP screenshot, and Bright Data holds it - but collector
media is not downloadable through the API. Zepto and Blinkit deliver a bare
file reference string (`<job>.<hash>.file_<id>.serp_screenshot.png`); Instamart
delivers a file object whose `url` is the address of the page that was
*photographed*, not a download link. The app reports one `artifact_failed` per
universe saying exactly that, and shows no capture for a live run. Following
that `url` would have fetched the live search page's HTML and stored it as
`serp.png` - a fabricated artifact.

**Blinkit needs a page-2 merge.** Blinkit prefetches the second results page, and
tagging is last-match-wins, so the `offset=12` response overwrote page 1. Page 1
is tagged with a lookahead that excludes a non-zero offset, page 2 is tagged
separately, and the interaction merges them by `product_id`.

**Instamart's map-confirm is part of the location bind.** After the address
suggestion, Instamart may show a map confirmation screen. It is probed for and
clicked when present, and skipped when absent - whether Studio's worker is shown
it varies, so treating it as mandatory would fail runs that never see it.

**Peers vary, and the retry belongs to Bright Data.** A bad proxy draw serves a
silently empty page rather than an error. Failed attempts do not bill, so an
immediate retry is the right response to one; three or more consecutive blocks
means the target has degraded and it should be paused rather than hammered. The
app does not retry at the collector level - Bright Data retries at the job level,
and a universe that comes back empty reports `zero_rows`, which is the honest
answer to "we could not see the shelf".

**Selectors marked `UNVERIFIED` are the authors' own markers**, left exactly as
they were written. Each one dumps the surrounding DOM on a miss, so a single
preview run replaces a guess with a fact. They have been left alone rather than
cleared wholesale, because clearing a marker is a claim, and only a run can make
it.

## The chaos collector

`chaos/` is different from the other three in three ways, and all of them are
deliberate.

**It runs on a Code worker.** There is nothing to click, so there is no browser:
one plain HTTP fetch of a server-rendered page per run, parsed with Cheerio.

**What is committed here is the pre-heal source.** These two files are the
hand-written originals. They were pasted into Scraper Studio, saved to
production, and run live against the store; flipping the store then broke them,
and Bright Data's self-heal rewrote the template to read the other rendering in
4 minutes 40 seconds. The healed code lives in the collector, not in this
repository, so what is here still reads the rendering it was written for. That is
the point of keeping it: this is what the break looked like.

**Its target is this app.** The chaos universe reads the store the app serves at
`/chaos`, which exists so a collector can be broken and repaired on demand:
`server/chaos_store.py` renders one catalogue of 22 products as two structurally
different pages, and a token-protected endpoint decides which one is served. Set
`STORE_BASE` at the top of `chaos/interaction.js` to the deployed store host
before pasting the file into Studio. That host must be a real hostname, because
Bright Data refuses a raw IP address and some free dynamic-DNS domains, and it
must be reachable from the internet: a store served on localhost is a store
Bright Data cannot fetch.

There is no JSON API behind that store and no machine-readable copy of the
catalogue in the page, so every field is read out of the DOM. The selectors here
target store version `v1` (`.product-card` tiles, `#delivery-area`). They stop
matching when the store is flipped to `v2`, which is the point: the break is what
Bright Data's self-healing is then asked to repair. See `docs/RUNBOOK.md`,
"The chaos universe", for the flip and heal commands.

## The comment fixes applied to these copies

Everything below is a comment, with one stated exception. Verified by diffing
each file against its original with comment lines removed.

- **Bright Data preview ids stripped.** Two Blinkit comments cited internal
  preview run ids. The findings they record are kept; the ids are gone.
- **`keyword/lat/long ride along` corrected to `{keyword, pincode}`** in the
  Blinkit and Instamart 18-field-contract comments. The claim was simply false -
  no collector has ever been sent coordinates.
- **Cross-references to research documents removed.** Comments cited a notes file
  and a choreography document that live outside this repo, by names that would
  resolve to unrelated files inside it. The *findings* those citations carried are
  stated inline instead; nothing technical was dropped. Two file references were
  repointed to the copies here (`../zepto/interaction.js`,
  `../blinkit/interaction.js`).
- **One string literal, not a comment:** Zepto's routing log said
  `B8 ROUTING=...`. This file is B12; the log now says so. It is a console line
  with no behaviour attached to it.

No selector, timeout, regex, tag pattern or row-building expression was changed.
