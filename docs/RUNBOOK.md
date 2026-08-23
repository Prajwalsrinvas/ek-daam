# Runbook

Operational notes for going live and for the two things that are easy to get
wrong when extending the app. No real ids or keys belong in this file - every
value below is a placeholder.

All four universes (Zepto, Blinkit, Instamart and the chaos demo store served by
this app) are live against published collectors. The checklist below is written per universe: run it once for each,
against a saved payload, before that universe joins a demo.

---

## Flipping to live mode

Order matters. Zepto blocks are a per-attempt lottery, not a time-based quota:
failed attempts do not bill, so retry a failure immediately - but a run of 3+
consecutive blocks means the target has degraded; pause it rather than hammering.
Two run-log tells worth knowing before blaming your own code: a `peer_ip` that is
a raw dotted IP (not the hashed `r<hex>` form) means the site served a silently
empty page - a bad draw, retry, don't debug selectors; and a billed load is
`Total page loads: N` in the run log. Everything that can be done against a
saved payload should be.

### Before touching the environment

**1. Capture one dataset by hand and save it.**

Trigger the collector once via curl against the REST API (the CLI cannot pass
keyword/pincode inputs - it only takes a positional URL) and save the raw
`GET /dca/dataset` response to a file. Every step below is cheaper against that
file than against a live run.

**2. Eyeball the payload for five things, in this order.**

- **Key casing.** snake_case as contracted (`product_name`, `selling_price`, …)?
  If the real output is camelCase, `map_zepto` does not recognise the rows, falls
  through to the raw-search-response branch, finds no `layout`, and the run
  reports `zero_rows{broken}` - which reads as "the store had nothing", not as a
  shape mismatch. **This is the most likely first-run confusion. Check it first.**
- **`serp_screenshot` serialization.** A bare URL string, or an object? If an
  object, which key holds the URL? `mappers.extract_screenshot_url` already tries
  `url` / `href` / `src` / `link` / `file` / `path` / `download_url` /
  `public_url` plus any nested URL-shaped string, so it will probably just work -
  confirm rather than assume.
- **`resolved_area`.** Present, and does it contain the pincode you asked for?
  **This is the location proof** - a universe whose rows carry no resolved area,
  or one naming somewhere else, is refused outright. If the field is missing or
  nested, fix the collector's output schema before wiring the universe.
- **`store_id`.** Present and **top level** on every row? Note the value for
  `SVERSE_<UNIVERSE>_STORE_MAP`. It is advisory only - it is recorded on the
  `validated` event and sets `known_store`; it refuses nothing.
- **`captured_at` and `eta_minutes`.** On every row, or only some? Both fall back
  across rows, so partial stamping is survivable - but worth knowing.
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
actually emits, and note what each row proves in `tests/fixtures/README.md`. The
three live universes have been through this against real datasets. The chaos
universe has no fixture and needs none: its store is served by this app, so a
current payload is one request away rather than a saved artefact. Anything still
unconfirmed for a NEW universe should be marked so it stays greppable:

```bash
rg "PENDING CONFIRMATION"
```

### Flipping

**5. Set the environment** in `.env` (gitignored - nothing here goes in the repo):

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

There is no `SVERSE_PINCODE_ALLOWLIST` and no `SVERSE_PINCODE_MAP` any more -
delete both from an older `.env`. Any 6-digit Indian pincode is accepted and the
app needs no coordinates for it.

Leave `SVERSE_FIXTURES_DIR` unset. `SVERSE_COLLECTOR_CHAOS` is the chaos
universe's collector; leave it empty and that universe reports "not wired" and
takes no part in a run (see "The chaos universe" below).

**The collector version can be set per universe.** `SVERSE_COLLECTOR_VERSION` is
the default for everything; `SVERSE_COLLECTOR_VERSION_<UNIVERSE>` overrides it
for one. The purpose is narrow and worth stating: **run a dev template for one
universe before promoting it**, while every other universe keeps using its
published, validated template. Without it, testing a freshly edited collector
means flipping the whole demo to `dev` and re-running templates that were
already signed off.

Only `dev` and `prod` are accepted - a typo falls back to the global setting
instead of travelling to `/dca/trigger` and failing the universe there. `prod`
is Bright Data's default and is sent by OMITTING the `version` parameter; `dev`
is sent explicitly.

The effective version is reported in three places and they cannot disagree,
because all three read the same registry field: the universe's row in
`GET /api/universes` (the top-level `collector_version` there is the DEFAULT),
and the `universe_dispatched` and `triggered` events for that universe. Check
the `triggered` event to confirm which template actually ran.

**Store maps are ADVISORY and per universe** (`SVERSE_<UNIVERSE>_STORE_MAP`).
A store id means nothing outside the site that issued it - Zepto reports a
dark-store UUID, Blinkit a numeric `merchant_id`, Instamart a numeric `podId` -
so each universe has its own. Each entry is `pincode:id[|id...];pincode:id`, and
the ids are a SET (Blinkit answers one search from an express dark store AND a
longtail warehouse).

Nothing is refused on this map. It sets `known_store` on the `validated` event:
`true` = every id the collector reported is one you listed, `false` = one is new
to you, `null` = no map for that universe/pincode or no id in the payload. An
unfamiliar id is worth a look - it is how you learn a fourth dark store exists -
but it is not grounds to throw away rows the site said it served at your pincode.
**Location itself is proved by `resolved_area`**, below.

**6. Restart the process.** `get_settings()` is cached and the app object is built
at import; there is no hot reload of the environment.

**7. Confirm the registry** with `GET /api/universes`: `mode: "live"`, and zepto,
blinkit and instamart each `wired: true` / `dispatchable: true` /
`status: "wired"`. A universe you have not wired an id for reports
`wired: false` / `status: "not wired"` and takes no part in a run. The chaos row
is listed too, and reports the same way.

**8. First live run.** The trigger is the risky step. A `422` means the collector's
input schema still disagrees - the `universe` routing hint is already stripped on
the live path, so look at the names and types of `keyword` / `pincode`. Those two
are the whole input: the app sends no coordinates, because no collector reads
them (each types the pincode into the site's own location picker). A collector
whose input schema still *requires* `lat`/`long` will 422 until those fields are
dropped from it.

**9. Watch for a silent gap between `triggered` and `rows`.** `progress` events are
driven off `pages_left` from `GET /dca/log/{job_id}`, and a single-input search
collector may report `pages_left: 0` throughout - in which case the live feed has
no progress beat at all. That is a collector-side fix (emit per-navigation
progress). The app will not fake one.

**10. Curate the first good capture immediately.** On the deployment, copy the
whole run directory into `runs/replays/<run_id>/` (`events.jsonl`, `meta.json`,
`raw/`, `artifacts/`) and name it in `SVERSE_DEMO_RUN_ID`. That capture is the
demo asset, and it is the only thing that makes a demo independent of Zepto's
block lottery on the day. It is NOT in the repository: `runs/` is gitignored in
full, `runs/replays/` included, so a fresh clone has no demo capture and the
capture travels to the deployment out of band. Read the events file before you
copy it and check there is nothing in it you would not want served to a
stranger.

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

`runs/` is gitignored in full, `runs/replays/` included, so a curated capture
lives on the deployment and not in the repository. A curated run dropped in there
**must keep the directory name it was generated with**:

```
runs/replays/r_20260822_091230_ab12/     ✅ found
runs/replays/zepto-demo-final/           ❌ invisible to the API
```

Every disk read goes through `RunManager.read_dir()`, which validates the id
against `^r(?:p)?_\d{8}_\d{6}_[0-9a-f]{4}$`. That regex is also the
path-traversal defence, so it is not going to be loosened. A wrongly named
directory does not error - `GET /api/runs/<name>` simply 404s as if it were not
there.

---

## The chaos universe

`chaos` is the reliability universe. It does not point at a shop on the internet:
it points at a small grocery store this app serves itself at `/chaos`, so a
collector can be broken and repaired while somebody is watching.

**The store.** `server/chaos_store.py` holds one catalogue and two renderings of
it. `v1` is a grid of `.product-card` tiles; `v2` is a `.listing-row` table with
different tags, class names, attribute names and nesting. Both serve the same
prices, the same stock states and the same product ids, and neither embeds a
machine-readable copy of the catalogue: a parser that could read a JSON blob out
of the page would survive any redesign, and the break would prove nothing.

The URL contract does not change between versions:

```
GET /chaos/search?q=amul%20butter&pincode=560001
```

With no valid pincode the store shows a location prompt and no prices, the way a
real quick-commerce site does. With one, it renders a delivering-to line that
echoes that pincode, which is what the app's location proof reads.

**Flipping it.** Which version is served is server-side state. It starts at
`SVERSE_CHAOS_VERSION` and changes only through a token-protected endpoint:

```bash
curl -sS -X POST http://localhost:8000/api/chaos/flip \
  -H "X-Chaos-Token: $SVERSE_CHAOS_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"version": "v2"}'
```

Omit `version` to advance to the next one and wrap. `GET /api/chaos` reports the
active version without a token. With `SVERSE_CHAOS_ADMIN_TOKEN` unset, flipping
answers 503: an empty token is not a blank password.

**Healing it.** `POST /api/chaos/heal` (same token) drives Bright Data's AI flow
against the chaos collector and records it as a run of its own:

```bash
curl -sS -X POST http://localhost:8000/api/chaos/heal \
  -H "X-Chaos-Token: $SVERSE_CHAOS_ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "the store was redesigned; update the selectors"}'
```

It returns a `run_id` whose event stream carries the cycle: `heal_started`, a
`progress` line per stage Bright Data reports, `heal_previewed` with the job's
own `preview_result`, `heal_approved` (`resume_automation_job` with
`auto_save: true`, which is what publishes the template), and `heal_promoted`
only when `save_new_template` appears in the job's own `completed_steps`. A job
that finishes without that step is reported as a failure to publish, because an
approval on its own leaves an unpublished draft while production keeps running
the broken template.

**A heal that times out here has not necessarily failed there.** `SVERSE_HEAL_TIMEOUT_S`
is how long the app watches, not how long Bright Data works: a heal that times
out in the app can still finish minutes later and auto-save a template. The run
records `timed_out` and nothing else, because the app never claims a promotion it
did not observe. Check the collector in the Bright Data console before assuming
the heal failed.

**Both admin endpoints are paced per client IP**, at 10 attempts a minute,
counted whether or not the token was right. Beyond that they answer 429 before
the token is even compared, so the token cannot be guessed at network speed. The
brake is separate from the run rate limit: neither can spend the other's window.

---

## Anonymous run ownership

**This is not authentication.** There is no account, no password and no login.
Every visitor is handed one opaque random cookie the first time they touch the
app, and a run remembers only the SHA-256 of whatever cookie created it. That is
enough to stop a public demo from showing one visitor's searches to the next one,
and it is not enough for anything else.

What it does:

- `GET /api/runs` lists only the requesting browser's runs. It used to list every
  visitor's, which on a public demo is a privacy leak.
- `GET /api/runs/{id}`, its SSE stream, its artifacts, and `POST /api/replays/{id}`
  answer **404** for a run that is not yours - not 403, because a 403 confirms the
  id exists, which is the one fact a stranger probing for run ids is after. A run
  that is not yours is indistinguishable from a run that is not there.
- A replay belongs to whoever asked for it, not to whoever captured the original.
- A self-heal run belongs to the operator who started it, so they can watch it.
  It is token-gated as before and appears in nobody else's listing.

What it does not do, said plainly:

- **Clearing cookies makes a new identity.** The old runs are not deleted; they
  are simply owned by a cookie nobody holds any more, so they become unreachable
  from that browser and invisible in every listing.
- Anyone who copies the cookie value out of a browser has that identity.
- Runs captured before this existed carry no owner. They still load, and they
  belong to nobody: invisible in every listing and readable by no visitor, unless
  one of them is named as the public demo run.
- It is per browser, not per person: a second browser or a private window is a
  second identity.

The raw cookie value is never written to disk and never logged. Only its hash
reaches `meta.json`, so a leaked runs directory hands out no identities.

**The one public run.** `SVERSE_DEMO_RUN_ID` names a single run id that is
readable and replayable by anyone regardless of who captured it. That is the
judges' one-click demo and it is public by design; the frontend shows a "Replay
the demo capture" button on the landing state whenever it is set. Point it at a
curated capture under `runs/replays/` on the deployment. That directory is
gitignored along with the rest of `runs/`, so the capture is not tracked and a
fresh checkout has none: copy it across when you deploy. Leave it empty and there
is no public run at all.

Cookie attributes: `ekdaam_owner`, HttpOnly, `SameSite=Lax`, `Path=/`, one year.
`Secure` is decided per request from the scheme and `X-Forwarded-Proto`, because
Caddy terminates TLS and the app's own scheme is http even in production - a
cookie marked Secure over plain http is never sent back, and every request would
look like a new visitor. `SVERSE_COOKIE_SECURE=1`/`0` overrides that for a proxy
that sets neither. Implementation: `server/owner.py`.

---

## Product links: one file, one edit per site

No collector reports a product URL, so every link the UI shows is built from the
site's own product id. Both maps live in `server/product_links.py` and nothing
else in the app writes a URL down: `PRODUCT_URL_TEMPLATES` builds the per-row
link, `SEARCH_URL_TEMPLATES` builds the one search link shown in a universe's
column header.

To retune a pattern, edit its template. To switch a site off, set its template to
`None`, and that universe's rows then render as plain text instead of as links,
which is the right answer for a pattern nobody has verified against the live
site. A link that 404s is worse than no link, because it reads as the app having
matched the wrong product.

**Where each site stands**, checked against the live sites through a browser:

| universe | product link | why |
| --- | --- | --- |
| `blinkit` | on | `/prn/<slug>/prid/<id>` lands on the exact product. |
| `chaos` | on | The page is served by this app, so it is certain rather than inferred. |
| `zepto` | off | The `/pvid/` route wants Zepto's VARIANT id. The collector does not export one, so the id we hold would build a link to some other listing. |
| `instamart` | off | The product page is store-gated and does not resolve for a visitor whose session has not picked the same store. |

The pattern that failed is kept in a comment beside each `None`, so turning one
back on is a copy rather than a rediscovery. Search links stay on for all three
live universes either way: a search is not a listing, and every site answers one
for any visitor.

`{product_id}` is the id the collector read. `{slug}` is the product name,
slugified, and is cosmetic on the two sites that take one: both route on the id.
The links are COMPUTED at serialisation time from `NormalizedRow`, never stored,
so a template fixed today also fixes the links on runs captured yesterday.
`tests/test_product_links.py` pins the shape each template produces, one test per
site, so a retuned pattern fails there rather than in front of a judge.

---

## Adding a server event type

This takes edits in **five** places. Miss one of the frontend two and the event
is dropped silently, with no error anywhere - the server names every SSE frame
(`event: rows`), and a named frame never reaches `EventSource.onmessage`.

**Server:**

1. `server/events.py` - add the member to `EventType`.
2. `server/events.py` - add it to `IMPLEMENTED_EVENT_TYPES`. Add it to
   `TERMINAL_UNIVERSE_TYPES` **only** if it genuinely ends a universe's work.
3. `server/runs.py` - emit it.

**Frontend:**

4. `web/src/types.ts` - add it to the `EventType` union.
5. `web/src/runState.ts` - add it to `IMPLEMENTED_EVENT_TYPES` (this array *is*
   the SSE subscription list, imported by `App.tsx`), give it a `case` in
   `foldEvent`, and a line in `describe`.

Then add a case to `web/src/runState.test.ts` - the "renders a line for every
implemented event type" test will fail until `describe` handles it, which is the
cheapest possible reminder.

The four `heal_*` events are emitted by a heal run and are wired through all five
places. `incident` is still declared and not emitted, and is exactly
the case this checklist exists for.

---

## Secret scan: what it does and does not flag

`bash tests/secret_scan.sh` and `tests/test_secret_scan.py` are GREEN. The scan
looks for API keys, bearer tokens, cookies, AWS keys and assigned session or
signature values.

It no longer flags **Bright Data ids** - collector `c_…`, job `j_…`, template
`t_…`, and the `<job id>.<hash>.<file id>.<name>.png` file references built out
of them. They name a job inside one account and do nothing without `BD_API_KEY`,
which the scan does still catch. They also appear legitimately in any curated
replay capture, whose `triggered` events record which job ran, so flagging them
left the scan permanently red - and a check that is always red stops being
read. The `long hex token` rule survives, narrowed to skip the hash inside a
file reference; a bare 40+ hex string anywhere else is still caught.

**Store ids are not flagged either.** `validated` events now record them as
provenance, and any logged-out browser sees the same value.

### Replays and coordinates (resolved Sun 2026-08-23)

No replay capture is tracked in the repository; `runs/` is gitignored. The public demo
replay is a real run stored on the server and named by `SVERSE_DEMO_RUN_ID`, which is the
only run readable and replayable without the owner cookie. Captures are never hand-edited:
a capture that needs changing is recaptured. Every row's input echo carries only
`{keyword, pincode}`, and `test_no_coordinates_or_pincode_table_in_the_code_or_docs` keeps
coordinates out of the code and docs.

---

## Refusals and what they mean

| what you see | what happened |
| --- | --- |
| `failed` + `location proof failed: site did not resolve <pincode>` | No row from that universe carried a `resolved_area` containing the requested pincode - the site never confirmed it was serving that location. That universe contributes nothing: no capture, no comparison row. The event records the `store_ids` the collector reported, and `runs/<id>/raw/<universe>.json` has the full payload. |
| `validated` with `unresolved_location` in `reasons` | Some rows on the page named the requested pincode and some did not. The ones that did not are dropped; the rest are served. |
| `validated` with `known_store: false` | Every kept row IS location-proved; one of the store ids is just not in `SVERSE_<UNIVERSE>_STORE_MAP`. Advisory - usually means a new dark store. Add the id to the map once you have seen it serve the pincode. |
| `zero_rows {oos}` | Rows came back but nothing kept by the gate is in stock. |
| `zero_rows {broken}` | The honest default. Empty or unparseable collector output. Also what a payload shape mismatch looks like. |
| `artifact_failed` | A page capture was attempted and the fetch itself failed. **Non-terminal**: the rows still stand and the universe still validates. Not emitted for the ordinary live case, where Bright Data holds a SERP capture it will not serve over the API: that is true of every live universe on every run, so the app shows no capture and reports no failure for one. |
| `failed` on a universe | That universe only. A run never dies because one universe did. |
| `retriggered` | Bright Data accepted the job but never started navigating with it (`reason: job never started navigating`). The stalled job was canceled and ONE replacement was triggered for the same universe, inside the same universe timeout. **Non-terminal**: the universe is still collecting, and the `triggered` event right after it carries the new job id. Only one retrigger per universe per run: if the replacement stalls too, the universe times out. |
| `429` + `daily live-run budget reached, try tomorrow or watch the demo replay` | `SVERSE_DAILY_RUN_BUDGET` LIVE runs have already been started today, across every client. Nothing is wrong with the request; the day is spent. Replays and self-heals call no collector and are not counted, so both still work. The count is held in memory and keyed on the UTC date, so it resets at midnight UTC or on a restart. |

A universe that did not reach `validated` never contributes rows, even after a
restart: `rows_for()` re-derives from a saved raw payload only for universes with
a stored `validated` event. If you add a terminal state that *should* still
contribute rows, that filter in `server/runs.py` is what you change.
