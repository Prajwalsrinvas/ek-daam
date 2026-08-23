# Runbook

Operational notes for going live and for the two things that are easy to get
wrong when extending the app. No real ids or keys belong in this file — every
value below is a placeholder.

All three universes — Zepto, Blinkit and Instamart — are live against published
collectors. The checklist below is written per universe: run it once for each,
against a saved payload, before that universe joins a demo.

---

## Flipping to live mode

Order matters. Zepto blocks are a per-attempt lottery, not a time-based quota:
failed attempts do not bill, so retry a failure immediately — but a run of 3+
consecutive blocks means the target has degraded; pause it rather than hammering.
Two run-log tells worth knowing before blaming your own code: a `peer_ip` that is
a raw dotted IP (not the hashed `r<hex>` form) means the site served a silently
empty page — a bad draw, retry, don't debug selectors; and a billed load is
`Total page loads: N` in the run log. Everything that can be done against a
saved payload should be.

### Before touching the environment

**1. Capture one dataset by hand and save it.**

Trigger the collector once via curl against the REST API (the CLI cannot pass
keyword/pincode inputs — it only takes a positional URL) and save the raw
`GET /dca/dataset` response to a file. Every step below is cheaper against that
file than against a live run.

**2. Eyeball the payload for five things, in this order.**

- **Key casing.** snake_case as contracted (`product_name`, `selling_price`, …)?
  If the real output is camelCase, `map_zepto` does not recognise the rows, falls
  through to the raw-search-response branch, finds no `layout`, and the run
  reports `zero_rows{broken}` — which reads as "the store had nothing", not as a
  shape mismatch. **This is the most likely first-run confusion. Check it first.**
- **`serp_screenshot` serialization.** A bare URL string, or an object? If an
  object, which key holds the URL? `mappers.extract_screenshot_url` already tries
  `url` / `href` / `src` / `link` / `file` / `path` / `download_url` /
  `public_url` plus any nested URL-shaped string, so it will probably just work —
  confirm rather than assume.
- **`resolved_area`.** Present, and does it contain the pincode you asked for?
  **This is the location proof** — a universe whose rows carry no resolved area,
  or one naming somewhere else, is refused outright. If the field is missing or
  nested, fix the collector's output schema before wiring the universe.
- **`store_id`.** Present and **top level** on every row? Note the value for
  `SVERSE_<UNIVERSE>_STORE_MAP`. It is advisory only — it is recorded on the
  `validated` event and sets `known_store`; it refuses nothing.
- **`captured_at` and `eta_minutes`.** On every row, or only some? Both fall back
  across rows, so partial stamping is survivable — but worth knowing.
- **Prices.** Really rupees (`309.0`), not paise (`30900`)? A regression here is
  a 100x error on the receipt.

**3. Run the saved payload through the mapper offline**, before spending another
credit:

```bash
uv run python -c "import json,sys; from server.mappers import get_mapper; \
  rows = get_mapper('zepto')(json.load(open(sys.argv[1]))); \
  print(len(rows), 'rows'); \
  [print(r.name, r.price, r.qty, r.unit, r.unit_price, r.eta_min, r.captured_at, r.resolved_area) for r in rows]" \
  /path/to/saved-dataset.json
```

Zero rows means the shape does not match. Fix the mapper, not the collector.

**4. Correct the fixture to match reality.** Update
`tests/fixtures/<universe>_collector_rows.json` so it pins what the collector
actually emits, and note what each row proves in `tests/fixtures/README.md`. All
three universes have been through this against real datasets; anything still
unconfirmed for a NEW universe should be marked so it stays greppable:

```bash
rg "PENDING CONFIRMATION"
```

### Flipping

**5. Set the environment** in `.env` (gitignored — nothing here goes in the repo):

```bash
BD_MODE=live
BD_API_KEY=<real key>
SVERSE_COLLECTOR_ZEPTO=c_<real collector id>     # published 2026-08-22
SVERSE_COLLECTOR_BLINKIT=c_<real collector id>   # published 2026-08-22
SVERSE_COLLECTOR_INSTAMART=c_<real collector id> # published 2026-08-23
SVERSE_COLLECTOR_VERSION=prod           # the validated templates are in production (use dev only for drafts)
# SVERSE_COLLECTOR_VERSION_<UNIVERSE>=dev   # per-universe override for smoke-testing a draft (none needed now)
SVERSE_ZEPTO_STORE_MAP=560001:<real dark-store id>       # advisory, optional
SVERSE_BLINKIT_STORE_MAP=560001:<merchant id>|<merchant id>
SVERSE_INSTAMART_STORE_MAP=560001:<pod id>
SVERSE_QUERY_ALLOWLIST=amul butter      # optional; empty = any query
SVERSE_RUN_COOLDOWN_S=60                # one run per client IP per minute
```

There is no `SVERSE_PINCODE_ALLOWLIST` and no `SVERSE_PINCODE_MAP` any more —
delete both from an older `.env`. Any 6-digit Indian pincode is accepted and the
app needs no coordinates for it.

Leave `SVERSE_FIXTURES_DIR` unset. `SVERSE_COLLECTOR_CHAOS` stays empty: that
universe was never built, so it reports "not wired" and is filtered out of
`/api/universes` (see "The chaos universe" below).

**The collector version can be set per universe.** `SVERSE_COLLECTOR_VERSION` is
the default for everything; `SVERSE_COLLECTOR_VERSION_<UNIVERSE>` overrides it
for one. The purpose is narrow and worth stating: **run a dev template for one
universe before promoting it**, while every other universe keeps using its
published, validated template. Without it, testing a freshly edited collector
means flipping the whole demo to `dev` and re-running templates that were
already signed off.

Only `dev` and `prod` are accepted — a typo falls back to the global setting
instead of travelling to `/dca/trigger` and failing the universe there. `prod`
is Bright Data's default and is sent by OMITTING the `version` parameter; `dev`
is sent explicitly.

The effective version is reported in three places and they cannot disagree,
because all three read the same registry field: the universe's row in
`GET /api/universes` (the top-level `collector_version` there is the DEFAULT),
and the `universe_dispatched` and `triggered` events for that universe. Check
the `triggered` event to confirm which template actually ran.

**Store maps are ADVISORY and per universe** (`SVERSE_<UNIVERSE>_STORE_MAP`).
A store id means nothing outside the site that issued it — Zepto reports a
dark-store UUID, Blinkit a numeric `merchant_id`, Instamart a numeric `podId` —
so each universe has its own. Each entry is `pincode:id[|id...];pincode:id`, and
the ids are a SET (Blinkit answers one search from an express dark store AND a
longtail warehouse).

Nothing is refused on this map. It sets `known_store` on the `validated` event:
`true` = every id the collector reported is one you listed, `false` = one is new
to you, `null` = no map for that universe/pincode or no id in the payload. An
unfamiliar id is worth a look — it is how you learn a fourth dark store exists —
but it is not grounds to throw away rows the site said it served at your pincode.
**Location itself is proved by `resolved_area`**, below.

**6. Restart the process.** `get_settings()` is cached and the app object is built
at import; there is no hot reload of the environment.

**7. Confirm the registry** with `GET /api/universes`: `mode: "live"`, and zepto,
blinkit and instamart each `wired: true` / `dispatchable: true` /
`status: "wired"`. A universe you have not wired an id for reports
`wired: false` / `status: "not wired"` and takes no part in a run. The chaos row
is not listed at all — it has no mapper, so it could never be dispatched and a
dead chip in the UI would be worse than an absent one.

**8. First live run.** The trigger is the risky step. A `422` means the collector's
input schema still disagrees — the `universe` routing hint is already stripped on
the live path, so look at the names and types of `keyword` / `pincode`. Those two
are the whole input: the app sends no coordinates, because no collector reads
them (each types the pincode into the site's own location picker). A collector
whose input schema still *requires* `lat`/`long` will 422 until those fields are
dropped from it.

**9. Watch for a silent gap between `triggered` and `rows`.** `progress` events are
driven off `pages_left` from `GET /dca/log/{job_id}`, and a single-input search
collector may report `pages_left: 0` throughout — in which case the live feed has
no progress beat at all. That is a collector-side fix (emit per-navigation
progress). The app will not fake one.

**10. Curate the first good capture immediately.** Copy the whole run directory
into `runs/replays/<run_id>/` (`events.jsonl`, `meta.json`, `raw/`, `artifacts/`)
and commit it. Per the demo-data rule that is the demo asset, and it is the only
thing that makes a demo independent of Zepto's block lottery on the day. Read the
events file before `git add` — check there is nothing in it you would not want
committed.

---

### Dev-version runs deliver a truncated projection (learned 2026-08-22)

`GET /dca/dataset` projects every row through the COLLECTOR-level output fields,
and those are synced from the template's output schema only when a template is
saved to PRODUCTION. A collector whose production template is still the AI
scaffold therefore delivers dev-version rows with the scaffold's fields only
(Instamart: `product_name`, `is_sponsored`, `input`), even though the dev
template collects all 18. Use `SVERSE_COLLECTOR_VERSION_<UNIVERSE>=dev` for
smoke runs only; promote before wiring a universe into a demo.

## Curated replays: the directory name is load-bearing

`runs/` is gitignored except `runs/replays/`. A curated run dropped in there
**must keep the directory name it was generated with**:

```
runs/replays/r_20260822_091230_ab12/     ✅ found
runs/replays/zepto-demo-final/           ❌ invisible to the API
```

Every disk read goes through `RunManager.read_dir()`, which validates the id
against `^r(?:p)?_\d{8}_\d{6}_[0-9a-f]{4}$`. That regex is also the
path-traversal defence, so it is not going to be loosened. A wrongly named
directory does not error — `GET /api/runs/<name>` simply 404s as if it were not
there.

---

## The chaos universe

`chaos` is a registry row and a stub mapper. **It was never built.** There is no
chaos site, no collector and no payload contract behind it — the row exists as
the record of the shape a fourth universe would take, and the stub is there so a
half-built universe can never quietly report "no results".

`GET /api/universes` filters it out (`registry.listed()` drops any universe whose
mapper is a stub), so the UI shows three chips, not a fourth dead one. Building it
means: write the mapper, drop the stub from `MAPPERS`, and the row starts being
listed on its own — no other change.

---

## Adding a server event type

This takes edits in **five** places. Miss one of the frontend two and the event
is dropped silently, with no error anywhere — the server names every SSE frame
(`event: rows`), and a named frame never reaches `EventSource.onmessage`.

**Server:**

1. `server/events.py` — add the member to `EventType`.
2. `server/events.py` — add it to `IMPLEMENTED_EVENT_TYPES`. Add it to
   `TERMINAL_UNIVERSE_TYPES` **only** if it genuinely ends a universe's work.
3. `server/runs.py` — emit it.

**Frontend:**

4. `web/src/types.ts` — add it to the `EventType` union.
5. `web/src/runState.ts` — add it to `IMPLEMENTED_EVENT_TYPES` (this array *is*
   the SSE subscription list, imported by `App.tsx`), give it a `case` in
   `foldEvent`, and a line in `describe`.

Then add a case to `web/src/runState.test.ts` — the "renders a line for every
implemented event type" test will fail until `describe` handles it, which is the
cheapest possible reminder.

The reserved heal events (`incident`, `heal_started`, `heal_previewed`,
`heal_approved`, `heal_promoted`) are declared but not emitted, and are exactly
the case this checklist exists for.

---

## Secret scan: what it does and does not flag

`bash tests/secret_scan.sh` and `tests/test_secret_scan.py` are GREEN. The scan
looks for API keys, bearer tokens, cookies, AWS keys and assigned session or
signature values.

It no longer flags **Bright Data ids** — collector `c_…`, job `j_…`, template
`t_…`, and the `<job id>.<hash>.<file id>.<name>.png` file references built out
of them. They name a job inside one account and do nothing without `BD_API_KEY`,
which the scan does still catch. They also appear legitimately in every committed
replay, whose `triggered` events record which job ran, so flagging them left the
scan permanently red — and a check that is always red stops being read. The
`long hex token` rule survives, narrowed to skip the hash inside a file
reference; a bare 40+ hex string anywhere else is still caught.

**Store ids are not flagged either.** `validated` events now record them as
provenance, and any logged-out browser sees the same value.

### Replays and coordinates (resolved Sun 2026-08-23)

The single curated replay `runs/replays/r_20260823_074638_a586` was captured after
the app switched to the `{keyword, pincode}` trigger, so the input echo on its
rows carries no coordinates. The three earlier replays (captured while the app
still sent `lat`/`long`) were deleted rather than hand-edited — editing a capture
would turn evidence into a fabrication. `test_no_coordinates_or_pincode_table_in_the_code_or_docs`
now scans `runs/replays/` as well; untracked `runs/r_*` dirs are gitignored.

---

## Refusals and what they mean

| what you see | what happened |
| --- | --- |
| `failed` + `location proof failed: site did not resolve <pincode>` | No row from that universe carried a `resolved_area` containing the requested pincode — the site never confirmed it was serving that location. That universe contributes nothing: no capture, no comparison row. The event records the `store_ids` the collector reported, and `runs/<id>/raw/<universe>.json` has the full payload. |
| `validated` with `unresolved_location` in `reasons` | Some rows on the page named the requested pincode and some did not. The ones that did not are dropped; the rest are served. |
| `validated` with `known_store: false` | Every kept row IS location-proved; one of the store ids is just not in `SVERSE_<UNIVERSE>_STORE_MAP`. Advisory — usually means a new dark store. Add the id to the map once you have seen it serve the pincode. |
| `zero_rows {oos}` | Rows came back but nothing kept by the gate is in stock. |
| `zero_rows {broken}` | The honest default. Empty or unparseable collector output. Also what a payload shape mismatch looks like. |
| `artifact_failed` | The page capture could not be fetched. **Non-terminal** — the rows still stand and the universe still validates. |
| `failed` on a universe | That universe only. A run never dies because one universe did. |

A universe that did not reach `validated` never contributes rows, even after a
restart: `rows_for()` re-derives from a saved raw payload only for universes with
a stored `validated` event. If you add a terminal state that *should* still
contribute rows, that filter in `server/runs.py` is what you change.
