"""Environment loading and the guards from DESIGN.md §9.

No secret ever lives in this file. Collector ids and the API key come from the
environment only; `.env.example` carries dummies.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("scrapeverse.config")

REPO_ROOT = Path(__file__).resolve().parent.parent

UNIVERSE_IDS = ("zepto", "blinkit", "instamart", "chaos")

# The model the assist layer asks by default (server/llm_match.py). Measured in
# spike/llm-match: free, and the fastest of the models tried at this job.
DEFAULT_LLM_MODEL = "stealth/ox-alpha"

# No pincode table lives here any more. The app takes any valid Indian pincode
# (validated in `runs.py`) and sends exactly that to the collector, which types
# it into the site. Coordinates were never read by any collector, so carrying
# them meant shipping one household's location in the repo for nothing.


@dataclass(frozen=True)
class Settings:
    bd_api_key: str
    bd_mode: str
    bd_base_url: str
    collector_ids: dict[str, str]
    # The default collector version for every universe (SVERSE_COLLECTOR_VERSION).
    collector_version: str
    # Empty/unset = any query. A demo can still lock it to a short list.
    query_allowlist: tuple[str, ...]
    max_concurrent_runs: int
    rate_limit_per_min: int
    # Seconds one client IP must wait between runs. 0 = off.
    run_cooldown_s: float
    poll_interval_s: float
    universe_timeout_s: float
    mock_step_delay_s: float
    replay_max_gap_s: float
    runs_dir: Path
    # ADVISORY ONLY. universe -> pincode -> the store ids we have SEEN serve that
    # pincode. Per universe because a store id means nothing outside the site
    # that issued it: Zepto reports a dark-store UUID, Blinkit a numeric merchant
    # id, Instamart a numeric pod id. Nothing is ever refused on this map — it
    # only decides the `known_store` flag on the `validated` event. Location
    # itself is proved by what the SITE resolved (see `runs.py`).
    store_maps: dict[str, dict[str, frozenset[str]]] = field(default_factory=dict)
    # Which rendering the chaos store serves at startup (server/chaos_store.py).
    # Runtime changes go through the token-protected flip endpoint, never here.
    chaos_version: str = ""
    # Shared secret for the chaos admin endpoints (flip and heal). EMPTY MEANS
    # DISABLED: with no token configured there is no value a caller could send
    # that would be accepted, so a deployment that forgets to set one cannot
    # have its store flipped or its collector healed by a stranger.
    chaos_admin_token: str = ""
    # Self-heal polling. A Bright Data heal has run anywhere from ~30s to ~4.5
    # minutes in this project's own measurements, so the timeout is generous and
    # the interval is slower than the scrape poll.
    heal_poll_interval_s: float = 5.0
    heal_timeout_s: float = 600.0
    # Seconds a collector job may report zero navigations and zero lines before
    # `runs.py` treats it as a worker that was never allocated, cancels it and
    # triggers one replacement. A healthy job in this project has delivered rows
    # in around 36s, so 75s is well clear of a slow one.
    stall_retrigger_s: float = 75.0
    # Live runs allowed per UTC day, across all clients. A backstop against
    # draining collector credits, not accounting: see `RunManager`.
    daily_run_budget: int = 300
    # The ONE run id that is public regardless of who captured it: the judges'
    # one-click demo. Empty means there is no public run and every run is scoped
    # to the visitor who made it. See server/owner.py.
    demo_run_id: str = ""
    # Whether the owner cookie is marked Secure. None = decide per request from
    # the scheme and X-Forwarded-Proto, which is what a Caddy deployment needs.
    cookie_secure: bool | None = None
    # universe -> collector version, from SVERSE_COLLECTOR_VERSION_<UNIVERSE>.
    # Only universes that set an override appear here; everything else uses
    # `collector_version`. This is what lets ONE universe run a dev template
    # while the rest of the demo stays on the published prod ones.
    collector_versions: dict[str, str] = field(default_factory=dict)
    fixtures_dir: Path = field(default=REPO_ROOT / "tests" / "fixtures")
    # Items one cart may hold. A cart starts one run per item at once, so this is
    # also how many collector jobs one click can spend.
    cart_max_items: int = 6
    # Where the curated demo list is read from (SVERSE_DEMO_FILE). None means
    # `demo.json` inside the runs directory, so a test with a runs directory of
    # its own can never pick up a deployment's list. See server/demo.py.
    demo_file: Path | None = None
    # The model layer (server/llm_match.py). Empty key = the layer is off, which
    # is the state of every deployment that has not opted into it. The key is
    # read here and passed to the client; it is never logged and never stored.
    openrouter_api_key: str = ""
    llm_model: str = DEFAULT_LLM_MODEL
    # Wall budget for the whole phase, not per call. Measured: 15 parallel block
    # calls finished in 8s, so 30s is well clear of a slow one.
    llm_timeout_s: float = 30.0
    # The off switch that does not need the key removed.
    llm_enabled: bool = True

    @property
    def is_live(self) -> bool:
        return self.bd_mode == "live"

    @property
    def demo_list_path(self) -> Path:
        """The demo list file. Inside the runs directory unless one is named, so
        it moves with `SVERSE_RUNS_DIR` rather than being pinned to the repo."""
        return self.demo_file if self.demo_file is not None else self.runs_dir / "demo.json"

    def store_map_for(self, universe_id: str) -> dict[str, frozenset[str]]:
        """pincode -> known store ids for one universe. Empty = nothing to
        compare against, so `known_store` is reported as unknown rather than
        false."""
        return self.store_maps.get(universe_id, {})

    def collector_version_for(self, universe_id: str) -> str:
        """The version actually sent to /dca/trigger for one universe.

        The per-universe override wins; otherwise the global default. Everything
        that reports a version — `/api/universes`, `universe_dispatched`,
        `triggered` — goes through here, so what the UI shows is by construction
        the version the trigger used.
        """
        return self.collector_versions.get(universe_id) or self.collector_version

    def query_allowed(self, query: str) -> bool:
        if not self.query_allowlist:
            return True
        return query.strip().lower() in self.query_allowlist


def load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines, # comments, no interpolation.

    Existing environment variables win, so `BD_MODE=live uv run ...` still works
    with a .env on disk.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _bool(name: str) -> bool | None:
    """A three-state flag: on, off, or unset meaning "work it out per request"."""
    raw = (os.getenv(name) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _parse_store_map(raw: str) -> dict[str, frozenset[str]]:
    """`560001:<id>;560001:<id>|<id>` -> {pincode: {allowed store ids}}.

    One pincode may legitimately be served by more than one store — Blinkit
    fulfils some items from a longtail warehouse rather than the express dark
    store — so ids are a `|`-separated SET. A single id stays a set of one, which
    is why the existing `SVERSE_ZEPTO_STORE_MAP=560001:<uuid>` needs no change.

    Provenance, not a secret and not a gate: a store id is visible to any
    logged-out browser, and an id outside this map never refuses anything. It
    still lives in the environment rather than the repo, because DESIGN.md §10
    keeps store ids out of committed files.
    """
    table: dict[str, frozenset[str]] = {}
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        pincode, _, store_ids = entry.partition(":")
        pincode = pincode.strip()
        allowed = frozenset(part.strip() for part in store_ids.split("|") if part.strip())
        if pincode and allowed:
            table[pincode] = allowed
    return table


COLLECTOR_VERSIONS = ("dev", "prod")

# `prod` is the default because `dev` is not a safe one. A collector's DELIVERED
# fields are synced from the template's output schema only when a template is
# saved to production, so a dev-version run can deliver a truncated projection of
# rows the template really did collect — which reaches the app as a shape
# mismatch and reports `zero_rows{broken}`, i.e. "the store had nothing".
# Choosing `dev` has to be deliberate.
DEFAULT_COLLECTOR_VERSION = "prod"


def _clamped(name: str, value: float, minimum: float, fallback: float) -> float:
    """A config value that would break the app is corrected, loudly.

    Every one of these has a silent failure mode: `SVERSE_POLL_INTERVAL_S=0` is a
    busy loop hammering `/dca/log`, `SVERSE_UNIVERSE_TIMEOUT_S=0` times every
    universe out before it starts, and `SVERSE_MAX_CONCURRENT_RUNS=0` refuses
    every run with "a run is already in flight" when none is. Refusing to boot
    over a typo is worse; booting silently wrong is worse still.
    """
    if value >= minimum:
        return value
    log.warning(
        "%s=%s is below the minimum %s — using %s instead", name, value, minimum, fallback
    )
    return fallback


def _chaos_version(raw: str | None) -> str:
    """Which chaos-store rendering to serve at startup.

    An unrecognised value falls back to the first version rather than being
    passed through, so a typo cannot leave the store with no renderer.
    """
    from .chaos_store import DEFAULT_VERSION, VERSIONS

    value = (raw or "").strip().lower()
    return value if value in VERSIONS else DEFAULT_VERSION


def _version(raw: str | None, default: str | None = None) -> str | None:
    """`dev`/`prod`, case-insensitive. Anything else is not a version we can send.

    An unrecognised value falls back rather than being passed through: the
    trigger only understands these two, and inventing a third would fail the
    whole run at Bright Data instead of here.
    """
    value = (raw or "").strip().lower()
    return value if value in COLLECTOR_VERSIONS else default


def _load_collector_versions(default: str) -> dict[str, str]:
    """`SVERSE_COLLECTOR_VERSION_<UNIVERSE>` for every universe that sets one.

    A universe whose override equals the default is dropped, so the map only
    ever holds real, deliberate divergences from the global setting.
    """
    out: dict[str, str] = {}
    for uid in UNIVERSE_IDS:
        version = _version(os.getenv(f"SVERSE_COLLECTOR_VERSION_{uid.upper()}"))
        if version and version != default:
            out[uid] = version
    return out


def _load_store_maps() -> dict[str, dict[str, frozenset[str]]]:
    """`SVERSE_<UNIVERSE>_STORE_MAP` for every universe that configures one.

    Optional everywhere: an unset map means `known_store` is reported as unknown
    for that universe, never as a failure.
    """
    maps = {
        uid: _parse_store_map(os.getenv(f"SVERSE_{uid.upper()}_STORE_MAP", ""))
        for uid in UNIVERSE_IDS
    }
    return {uid: table for uid, table in maps.items() if table}


def build_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")

    mode = (os.getenv("BD_MODE") or "mock").strip().lower()
    if mode not in ("mock", "live"):
        mode = "mock"

    version = (
        _version(os.getenv("SVERSE_COLLECTOR_VERSION"), DEFAULT_COLLECTOR_VERSION)
        or DEFAULT_COLLECTOR_VERSION
    )

    runs_dir = Path(os.getenv("SVERSE_RUNS_DIR") or "runs")
    if not runs_dir.is_absolute():
        runs_dir = REPO_ROOT / runs_dir

    # Dev-only: point the mock driver at a different set of fixtures, e.g. to
    # preview the live collector-row shape in the UI without spending credits.
    fixtures_dir = Path(os.getenv("SVERSE_FIXTURES_DIR") or (REPO_ROOT / "tests" / "fixtures"))
    if not fixtures_dir.is_absolute():
        fixtures_dir = REPO_ROOT / fixtures_dir

    demo_file: Path | None = None
    raw_demo_file = (os.getenv("SVERSE_DEMO_FILE") or "").strip()
    if raw_demo_file:
        demo_file = Path(raw_demo_file)
        if not demo_file.is_absolute():
            demo_file = REPO_ROOT / demo_file

    # Unset means on: the layer already refuses to run without a key, so the flag
    # only has to answer "a key is configured but leave it alone tonight".
    llm_enabled = _bool("SVERSE_LLM_ENABLED")

    return Settings(
        bd_api_key=os.getenv("BD_API_KEY", "").strip(),
        bd_mode=mode,
        bd_base_url=(os.getenv("BD_BASE_URL") or "https://api.brightdata.com").rstrip("/"),
        collector_ids={
            uid: os.getenv(f"SVERSE_COLLECTOR_{uid.upper()}", "").strip()
            for uid in UNIVERSE_IDS
        },
        collector_version=version,
        collector_versions=_load_collector_versions(version),
        query_allowlist=tuple(q.lower() for q in _csv("SVERSE_QUERY_ALLOWLIST")),
        max_concurrent_runs=int(
            _clamped("SVERSE_MAX_CONCURRENT_RUNS", _int("SVERSE_MAX_CONCURRENT_RUNS", 1), 1, 1)
        ),
        rate_limit_per_min=int(
            _clamped("SVERSE_RATE_LIMIT_PER_MIN", _int("SVERSE_RATE_LIMIT_PER_MIN", 5), 1, 5)
        ),
        run_cooldown_s=_float("SVERSE_RUN_COOLDOWN_S", 60.0),
        poll_interval_s=_clamped(
            "SVERSE_POLL_INTERVAL_S", _float("SVERSE_POLL_INTERVAL_S", 2.5), 0.5, 2.5
        ),
        universe_timeout_s=_clamped(
            "SVERSE_UNIVERSE_TIMEOUT_S", _float("SVERSE_UNIVERSE_TIMEOUT_S", 180.0), 1.0, 180.0
        ),
        mock_step_delay_s=_float("SVERSE_MOCK_STEP_DELAY_S", 0.4),
        replay_max_gap_s=_float("SVERSE_REPLAY_MAX_GAP_S", 2.0),
        runs_dir=runs_dir,
        store_maps=_load_store_maps(),
        fixtures_dir=fixtures_dir,
        chaos_version=_chaos_version(os.getenv("SVERSE_CHAOS_VERSION")),
        chaos_admin_token=os.getenv("SVERSE_CHAOS_ADMIN_TOKEN", "").strip(),
        heal_poll_interval_s=_clamped(
            "SVERSE_HEAL_POLL_INTERVAL_S", _float("SVERSE_HEAL_POLL_INTERVAL_S", 5.0), 1.0, 5.0
        ),
        heal_timeout_s=_clamped(
            "SVERSE_HEAL_TIMEOUT_S", _float("SVERSE_HEAL_TIMEOUT_S", 600.0), 10.0, 600.0
        ),
        stall_retrigger_s=_clamped(
            "SVERSE_STALL_RETRIGGER_S", _float("SVERSE_STALL_RETRIGGER_S", 75.0), 15.0, 75.0
        ),
        daily_run_budget=int(
            _clamped("SVERSE_DAILY_RUN_BUDGET", _int("SVERSE_DAILY_RUN_BUDGET", 300), 1, 300)
        ),
        demo_run_id=os.getenv("SVERSE_DEMO_RUN_ID", "").strip(),
        demo_file=demo_file,
        cookie_secure=_bool("SVERSE_COOKIE_SECURE"),
        cart_max_items=int(
            _clamped("SVERSE_CART_MAX_ITEMS", _int("SVERSE_CART_MAX_ITEMS", 6), 1, 6)
        ),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        llm_model=(os.getenv("SVERSE_LLM_MODEL") or "").strip() or DEFAULT_LLM_MODEL,
        llm_timeout_s=_clamped(
            "SVERSE_LLM_TIMEOUT_S", _float("SVERSE_LLM_TIMEOUT_S", 30.0), 1.0, 30.0
        ),
        llm_enabled=True if llm_enabled is None else llm_enabled,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return build_settings()
