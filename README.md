# EkDaam

![EkDaam comparing one product across four universes at one pincode](docs/hero.png)

EkDaam compares grocery shelf prices across Zepto, Blinkit, and Instamart for one
product and pincode. A fourth universe is a demo store this app serves itself,
included so a collector can be broken and self-healed while somebody watches. Each
search runs through Bright Data Scraper Studio, streams progress to the browser,
and builds a comparison from the returned listings.

Built for Into the Scrape-Verse, the hackathon run by WeMakeDevs with Bright Data.

## What it does

- Runs the wired Scraper Studio collectors in parallel.
- Streams collector lifecycle events to the browser.
- Normalizes prices, pack sizes, stock state, delivery estimates, and sponsorship.
- Groups listings only when brand, pack size, variant, and product names agree.
- Shows unmatched products separately instead of forcing a comparison.
- Adds a second, labelled layer of matches suggested by a language model, drawn
  beside the rule-based receipt and never mixed into it.
- Compares a whole cart: up to six products at one pincode run together, with
  the basket total per app and how many of the items each app could price.
- Stores each run locally so a completed run can be replayed from the UI, and
  serves a curated list of real captures as the demo anyone can open.

Matches are heuristic. The original listing name remains visible so the result can
be checked before comparing prices. Rule-based groups are labelled `close`, never
`exact`. Model-suggested groups are drawn as dashed lines and labelled
`model-suggested` with the model's own `high` or `low` confidence; the model only
ever returns product ids, so every name and price on the page was captured by a
collector, and every suggested group is re-checked by the same deterministic
guards (same brand and pack size, different shops, no id used twice) before it is
shown. Set `OPENROUTER_API_KEY` to turn the layer on; without it the receipt is
rules only.

## Run locally

Requirements:

- Python 3.12
- Node.js 22
- [uv](https://docs.astral.sh/uv/)

Install dependencies and build the frontend:

```bash
uv sync --frozen
npm --prefix web ci
npm --prefix web run build
```

Copy the environment template and start the server:

```bash
cp .env.example .env
uv run uvicorn server.app:app --port 8000
```

Open <http://127.0.0.1:8000>.

The default `BD_MODE=mock` uses a local Zepto fixture and makes no network calls.
For live comparisons, configure live mode.

## Live mode

Set these values in `.env`:

```dotenv
BD_MODE=live
BD_API_KEY=
SVERSE_COLLECTOR_ZEPTO=
SVERSE_COLLECTOR_BLINKIT=
SVERSE_COLLECTOR_INSTAMART=
SVERSE_COLLECTOR_CHAOS=
SVERSE_COLLECTOR_VERSION=prod
SVERSE_CHAOS_VERSION=v1
SVERSE_CHAOS_ADMIN_TOKEN=
SVERSE_DEMO_RUN_ID=
SVERSE_DEMO_FILE=runs/demo.json
OPENROUTER_API_KEY=
SVERSE_MAX_CONCURRENT_RUNS=6
```

`SVERSE_COLLECTOR_CHAOS` is the demo store's collector; leave it empty and that
universe reports "not wired" and takes no part in a run.
`SVERSE_CHAOS_VERSION` is the rendering the demo store starts on, changed at
runtime only through `POST /api/chaos/flip`.
`SVERSE_CHAOS_ADMIN_TOKEN` is the shared secret for the two chaos admin
endpoints, sent as the `X-Chaos-Token` header; empty means disabled, and both
endpoints answer 503.
`SVERSE_DEMO_RUN_ID` names one run id that is readable and replayable by anyone.
`SVERSE_DEMO_FILE` points at a JSON list of demo entries (single runs, carts, or
a story of chapters); every run id it names is public, and the UI's **Watch a
demo** strip is built from it. Both empty means there is no public run and no
demo strip.
`OPENROUTER_API_KEY` turns on the model-suggested matching layer; empty leaves
the receipt rules only.
`SVERSE_MAX_CONCURRENT_RUNS` must be at least the cart size you want to allow: a
cart is admitted all or nothing, one run per item.

Collector IDs and API keys stay in the environment. The collector source used by
the app is included in [`collectors/`](collectors/README.md).

After a run completes, click **Replay this run** to stream its saved events again.
Replay runs are visibly labelled and do not call Bright Data. Past runs from the
same browser are listed under **My runs**, each with its own replay button. Real
captures can be published to every visitor through the demo list: the **Watch a
demo** strip replays them, clearly labelled as replays, with no run of your own
and no collector being paid for.

## The chaos store

The app serves its own demo store at `/chaos`, with two different renderings of
one catalogue. Flipping between them breaks the collector that reads the store,
which is what makes a self-healing run something you can watch on demand. Rows
from this store are shown under their own heading and are never matched against
the live universes, because its products and prices are invented.

`./chaos-monkey.sh` prints the version being served and flips it to the next one.
Add `--heal` to start a self-heal run instead and print its run id. It reads
`CHAOS_ADMIN_TOKEN` from the environment and takes `EKDAAM_URL` for a host other
than the default.

## How I used Scraper Studio

Scraper Studio is the whole data layer. The app has no scraping code of its own. Four collectors run per search: three in production against Zepto, Blinkit and Instamart, and one against a demo store served by this app that is built to break on purpose.

- Typed inputs, not URLs. Every collector takes `{keyword, pincode}` and nothing else. The shelf does not exist at a URL; it exists only after a location is set in the session.
- Location choreography in the Browser worker. Each production collector opens the app, types the pincode into the site's own location picker, picks a suggestion, and reads back the location text the site itself resolved before it searches. A row only counts if that resolved-location text echoes the requested pincode.
- The site's own JSON, not class names. After the bind, `tag_response` captures the search endpoint each app calls for itself, and the parser reads that payload. Hashed class names rotate; the API contract does not.
- One output contract. All four collectors emit the same 18 fields with the same types, so the app has one mapper and one comparison.
- The REST loop. The app triggers with `POST /dca/trigger`, follows `GET /dca/log/{job}` for progress, fetches `GET /dca/dataset`, and cancels and retriggers a job that never starts navigating.
- Self-healing, end to end. The demo store has two renderings of the same catalogue. A protected flip switches the markup, the chaos collector stops finding rows, and the app runs Studio's `refactor_template` and `resume_automation_job` with `auto_save`, streaming each stage into the run feed. The healed template is promoted only when Bright Data reports `save_new_template` as completed.
- Code worker where a browser is not needed. The chaos collector fetches server rendered HTML over plain HTTP and parses it with Cheerio, one page load per run.

Live at https://ekdaam.duckdns.org.

## How it works

```text
Browser
  -> FastAPI API
  -> Bright Data Scraper Studio collectors
  -> normalized rows and location checks
  -> deterministic product grouping
  -> SSE event feed and comparison table
```

The backend is a single FastAPI process. Runs are stored as JSON and JSONL under
`runs/`, which is ignored by Git. The React frontend is built with Vite and served
by the same process.

The collectors accept a product keyword and six-digit Indian pincode. Each site
resolves the pincode through its own location picker. A row is included only when
the returned location text contains the requested pincode.

DESIGN.md is the internal design document the module docstrings cite by section
number. It is kept out of the published repository.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/universes` | Available collectors and status |
| `POST` | `/api/runs` | Start a search |
| `GET` | `/api/runs` | List this browser's runs |
| `GET` | `/api/runs/{id}` | Read a run and its comparison |
| `GET` | `/api/runs/{id}/events` | Stream run events over SSE |
| `POST` | `/api/replays/{id}` | Replay a completed run |
| `POST` | `/api/carts` | Start one run per item of a cart, all at once |
| `GET` | `/api/carts` | List this browser's carts |
| `GET` | `/api/carts/{id}` | Read a cart and its run ids |
| `GET` | `/api/chaos` | Demo store rendering and admin state |
| `POST` | `/api/chaos/flip` | Switch the demo store rendering (token, header `X-Chaos-Token`) |
| `POST` | `/api/chaos/heal` | Start a self-heal run for the chaos collector (token, header `X-Chaos-Token`) |
| `GET` | `/chaos` | The demo store itself |
| `GET` | `/chaos/search` | Demo store search results |
| `GET` | `/chaos/product/{id}` | A demo store product page |

## Data and privacy

The app stores the submitted query and pincode, collector responses, site-resolved
location text, and run events. It does not ask for an account or exact coordinates.
Runtime data stays under the ignored `runs/` directory unless it is deliberately
exported.

Runs are scoped to the browser that made them. Each visitor is given one opaque
random cookie, and a run stores only its hash, so the run list and every run page
show that browser's runs and nothing else. **This is not authentication**: there
is no account, clearing cookies starts a new identity and makes the old runs
unreachable, and one run id can be published as a public demo replay. See
[`docs/RUNBOOK.md`](docs/RUNBOOK.md), "Anonymous run ownership".

A public deployment should set conservative rate limits and restrict trusted proxy
headers. See [`docs/RUNBOOK.md`](docs/RUNBOOK.md) for deployment notes.

## Tests

```bash
uv run pytest
npm --prefix web test
bash tests/secret_scan.sh
```

## AI disclosure

Claude and Codex were used during implementation, testing, and documentation.
Scraped results come from the collectors and are not generated by AI.

## License

MIT. See [LICENSE](LICENSE).
