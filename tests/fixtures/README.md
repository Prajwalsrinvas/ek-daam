# Fixtures

Two shapes, because the collector has two.

| file | shape | used by |
| --- | --- | --- |
| `<universe>_collector_result.json` | wrapper record carrying the site's raw `search_response` | `BD_MODE=mock`, which selects by filename |
| `zepto_collector_rows.json` | the live collector's flattened one-record-per-product output | tests only |
| `blinkit_search_response.json` | wrapper record carrying Blinkit's raw `POST /v1/layout/search` body — 12 product cards | tests only |
| `blinkit_search_response_oos.json` | the same shape, trimmed to the out-of-stock cards and the second merchant | tests only |
| `blinkit_collector_rows.json` | the Blinkit collector's flattened output — same 18 fields as Zepto's | tests only |
| `instamart_search_response.json` | wrapper record carrying Instamart's raw `POST /api/instamart/search/v2` body — 32 items / 72 variations | tests only |
| `instamart_collector_rows.json` | the Instamart collector's flattened output — same 18 fields — all 75 rows of one real preview run | tests only |

The Blinkit and Instamart files are deliberately **not** named
`<universe>_collector_result.json`:
that filename is what `BD_MODE=mock` dispatches on, and turning the local mock
demo into a two-universe run is a product decision, not a side effect of adding a
fixture. Rename them the day that decision is made (and expect the mock-run row
counts asserted across the suite to change).

Both model what `GET /dca/dataset?id=<collection_id>` returns. NOTE: the real live
endpoint returns **JSONL** (one record per line), not a JSON array — `fetch_results`
parses both; the fixtures stay arrays for readability. `BD_MODE=mock` serves `<universe>_collector_result.json` instead of calling
Bright Data, so the whole app runs end to end with zero credits. It does **not**
load `zepto_collector_rows.json` — the mock path is untouched by the live contract.

## `zepto_collector_rows.json`

Checked against a saved `GET /dca/dataset` response from the production collector.
The public fixture replaces its location and store identifiers with neutral values:

1. **Key casing: snake_case confirmed** exactly as contracted. Delivery also adds an
   `input` echo object per row (ignored by the mapper).
2. **Prices arrive as Money objects** — `{"value": 65, "currency": "INR", "symbol": "₹"}`,
   value in RUPEES. `_rupees` unwraps dicts via `value`/`amount`; fixture now mirrors
   this shape.
3. **`serp_screenshot` ARRIVES per row, as a Bright Data FILE REFERENCE — not a URL.**
   It is a string of the form `<job>.<hash>.file_<id>.serp_screenshot.png`, which is
   how BD names a delivered media file. **Bright Data captures a SERP screenshot per
   run; it is not deliverable via API download, so the app shows none.** The universe
   reports one `artifact_failed` carrying that reason — non-terminal by design, the
   rows still stand — and the live client makes no download request at all. What to
   do about it (deliver to S3? re-shoot app-side?) is deferred.
   `zepto_collector_rows.json` keeps the file-OBJECT shape and
   `blinkit_collector_rows.json` the file-reference string, because both are what
   really arrive and both are what `has_screenshot_reference` has to recognise.
4. **`captured_at`** ISO8601 (`2026-08-22T14:20:54.…Z`), identical on all rows —
   as modelled. `eta_minutes` and `resolved_area` populated on every row.

Rows pin the things the live mapper has to get right:

| row | what it proves |
| --- | --- |
| Amul Unsalted Cooking Butter | prices are ALREADY rupees — nothing is divided by 100 |
| Amul Salted Butter | `variant: "salted"`, unit price per 100 g |
| Milky Mist Butter Chiplet | multipack text `"10 x 10 g"` resolves to 100 g |
| Amul Butter Chiplet | multipack `"50 x 20 g"` resolves to 1000 g, not 20 g |
| Nandini Salted Butter | `out_of_stock: true` + `available_quantity: 0` |
| Nutralite | `is_sponsored: true` |
| Heritage Table Butter | every nullable field null; `discounted_selling_price` null falls back to `selling_price`; null `eta_minutes` inherits from the dataset |

Two page-level facts ride on per-product rows, and the fixture puts each on a row
that is not the first so the any-row handling is actually exercised: the SERP
capture sits on row 3 only, and `eta_minutes` is null on the last row. Both fall
back to the first row that carries a value. `captured_at` deliberately does **not**
fall back — a capture time that is not this row's own would be a fabricated
provenance claim.

Store ids in the FIXTURES are synthetic (`store-fixture-560001`). They are not
secrets — any logged-out browser sees one — but a fixture that pinned a real shelf
would rot the moment that dark store changed, so the map a run is checked against
lives in `SVERSE_ZEPTO_STORE_MAP`. Captured replays under `runs/replays/` are the
opposite case: they are evidence, so they record the sites' real store ids as
provenance and are never edited.

`resolved_area` carries `560001` on every row that has one. That is what the
location proof reads, so a fixture whose resolved area does not name the pincode
under test makes the whole universe refuse — which is the intended behaviour, and
worth knowing before editing one of these files.

## `<universe>_collector_result.json`

These are **derived**, not copied. They keep the response-body *structure* of a real
capture and a handful of realistic rows. Everything session-shaped is gone: no
cookies, no headers, no request signatures, no store ids, no session ids, no query
ids. Product and variant ids are synthetic (`p-fixture-00N` / `v-fixture-00N`).

`zepto_collector_result.json` deliberately encodes the cases the mapper has to get
right:

| row | what it proves |
| --- | --- |
| Amul Unsalted Cooking Butter, 100 g | paise -> rupees, `variant: "unsalted"` |
| Amul Salted Butter, 500 g | `variant: "salted"`, unit price per 100 g |
| Milky Mist Butter Chiplet, 100 g | `formattedPacksize` unlike `packsize` ("10 x 10 g") |
| Amul Butter Chiplet, 1 KILO | `unitOfMeasure: KILO` normalises to grams |
| Nandini Salted Butter | `outOfStock: true` + `availableQuantity: 0` |
| Nutralite (ads widget) | sponsored slot, product node nested one level deeper |
| Heritage Table Butter | `sellingPrice: 0` — dropped by the validation gate |

Every row carries a `zeptoPassPrice` and a `superSaverSellingPrice` that are
**lower** than `sellingPrice`. Nothing in the app may ever report them; the mapper
test asserts that no emitted price matches one of those values. The real capture
happened to have `zeptoPassPrice: 0` throughout, which would have made that test
vacuous, so the fixture uses realistic member prices instead.

## The Blinkit fixtures

Derived from two saved `POST /v1/layout/search` responses. The public fixtures use
neutral location and identity values while retaining the response structure and
the product cases needed by the mapper tests.

Sanitised: `merchant_id` -> `900001` (express) / `900002` (longtail warehouse),
product ids -> `9100NN`, image and ETA-icon hosts -> `*.invalid`, the ads tracking
blob -> a single `ads_campaign_id: "ads-fixture-001"` marker. Numeric-shaped ids
are kept numeric-shaped (string in `identity.id`, int in `cart_item.product_id`)
because the mapper has to coerce both. No cookies, headers, signatures, session
ids or postback params — response-body shape only. The merchant ids a run is
checked against are configured in `SVERSE_BLINKIT_STORE_MAP`; the ones a run
actually saw are recorded in its captured replay as provenance.

`blinkit_search_response.json` (12 rows) pins:

| row | what it proves |
| --- | --- |
| `image_text_vr_type_header` heading | a non-product snippet in the same list is skipped by widget TYPE, not by position |
| `grid_container_vr` "Similar brands" carousel | containers are walked, and brand tiles inside one still yield no rows |
| Nutralite (rank 1) | the paid substitute at rank 1 is KEPT and marked `sponsored` |
| Amul Salted Butter, 100 g | `"₹63"` is 63 rupees — no paise division on Blinkit; no card-level `mrp`, so the site's own `cart_item.mrp` is reported |
| Milky Mist Chiplet Salted Butter | a real discount: `mrp` 90 above `normal_price` 88 |
| Amul Masti Spiced Salted Buttermilk, "1 ltr" | litres normalise into the same bucket as millilitres |

`blinkit_search_response_oos.json` (7 rows) pins `product_state: "out_of_stock"`
with `inventory: 0` and `is_sold_out: false` — that last flag is false on every
out-of-stock card in both captures, so it is never trusted alone — plus the two
`Jus'Amazin` rows served by the SECOND merchant, which is why the advisory store
map takes a SET of ids rather than one.

`blinkit_collector_rows.json` (6 rows) is the flattened live shape: Money objects
in rupees, a sponsored row, an out-of-stock row, a null
`discounted_selling_price` falling back to `selling_price`, a null `eta_minutes`
on the last row inheriting the dataset's, the SERP file reference on row 3 only,
and one row from store `900002` so `known_store` can be tested against a SET.

## The Instamart fixtures

Derived from a saved Instamart collector result and
`POST /api/instamart/search/v2` response. The public fixtures use neutral location
and identity values while retaining the response structure and product cases
needed by the mapper tests.

Sanitised: `podId` -> `930001` (and `930002` for the secondary pod in the page
config), sku/spin ids -> `IMFIX000NN` / `IMSPN000NN`, image ids and hosts ->
`*.invalid` or a short fixture path, the ads campaign id -> `ads-fixture-001`,
`resolved_area` -> a short label that still carries 560001. The raw fixture keeps
the real nesting (`cards[].card.card.gridElements.infoWithStyle.items[]
.variations[]`) and every item and variation, so the counts it pins are real;
each variation is trimmed to the fields the mapper reads plus the decoys it must
ignore, and the display blobs (medias, offerPanels, dimensions, analytics) are
dropped. The pod ids a run is checked against are configured in
`SVERSE_INSTAMART_STORE_MAP`; the ones a run actually saw are recorded in its
captured replay as provenance.

`instamart_collector_rows.json` (75 rows) pins:

| row | what it proves |
| --- | --- |
| Amul Pasteurised Butter, `100 g` | ₹63 is rupees; unit price per 100 g |
| Amul Pasteurised Butter, `100 g x 4` | a multipack is the TOTAL, 400 g at ₹252 — the same ₹63/100 g as the single, and four times too high if read as 100 g |
| Amul Pasteurised Butter+ English Oven Sandwich Bread, `1 Combo` | no comparable pack size: qty, unit and unit price are all None (5 such rows) |
| Amul Unsalted Butter, `100 g` | ₹65 — the row that matches Zepto and Blinkit |
| 13 rows with `out_of_stock: true` | out-of-stock rows are kept, priced, and pass the validation gate |
| every row | `available_quantity` null — Instamart publishes no stock count, and null is not zero |
| last row | `eta_minutes` null, inheriting the dataset's |
| row 3 only | the SERP capture, so the any-row handling is exercised |

That SERP capture is a Bright Data **file object**, and it is why
`extract_screenshot_url` refuses one. The object carries a `url` — but it is
the address of the page that was PHOTOGRAPHED
(`https://…/instamart/search?query=amul%20butter`), not a download link for the
PNG. Following it would have fetched the live search page's HTML and stored
those bytes as `serp.png`: a fabricated artifact, and an unlogged request to the
scraped site from our own server. Instamart lands where Zepto and Blinkit already
were — `artifact_failed`, non-terminal, rows intact. The live client no longer
attempts any screenshot download, so no payload can talk it into a request.

`instamart_search_response.json` (72 variations) pins:

| row | what it proves |
| --- | --- |
| `InlineViewFilterSortWidget` (card 0) | a non-product card in the same `cards[]` list carries no `gridElements` and yields nothing |
| one item, several `variations[]` | the ROW is the variation, not the item — each pack has its own skuId and price |
| `{"units": "63", "nanos": 0}` | Money is protobuf-style and `units` arrives as a STRING |
| `unitLevelPrice: "63/100 g"` | the site's own per-unit figure is preferred; present on 49 of 72 and agreeing with our arithmetic on all 49 |
| `weightInGrams` on a `1 Combo` row | present (520 g) and deliberately UNUSED — a bundle's total mass is not a comparable pack size |
| 4 Akshayakalpa items | `BADGE_TYPE_AD` and `adTrackingContext` agree; sponsorship is an ITEM fact, so all 12 of their variations carry it |
| the NOICE item | `BADGE_TYPE_INSTA_UPGRADE` also contains "AD" as a substring and must not count |
| `configs.IM_PAGE_CONFIGS…podDetailsList` | item-level `sla` is null throughout, so the ETA comes from the per-pod delivery SLA |
