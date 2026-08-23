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
    # universe -> collector version, from SVERSE_COLLECTOR_VERSION_<UNIVERSE>.
    # Only universes that set an override appear here; everything else uses
    # `collector_version`. This is what lets ONE universe run a dev template
    # while the rest of the demo stays on the published prod ones.
    collector_versions: dict[str, str] = field(default_factory=dict)
    fixtures_dir: Path = field(default=REPO_ROOT / "tests" / "fixtures")

    @property
    def is_live(self) -> bool:
        return self.bd_mode == "live"

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
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return build_settings()
