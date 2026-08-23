"""Run orchestration — DESIGN.md §5.

One run fans out into one asyncio task per dispatchable universe. Every task
drives the same lifecycle and every observed state becomes an event. A universe
that fails, times out or returns nothing does exactly that and nothing more: it
never takes the run down with it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
import shutil
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, Field

from .bd_client import BDError, JobLog, build_client
from .config import Settings
from .events import Event, EventStore, EventType, read_events_file, utc_now_iso
from .heal import (
    DEFAULT_CUSTOM_INPUT,
    HealClient,
    HealError,
    run_heal_cycle,
    validate_prompt,
)
from .mappers import (
    as_records,
    get_mapper,
    has_screenshot_reference,
    screenshot_record,
    unwrap_record,
)
from .registry import Universe, collector_id_for, dispatchable
from .resolve import Comparison, NormalizedRow, match

RUN_ID_RE = re.compile(r"^r(?:p)?_\d{8}_\d{6}_[0-9a-f]{4}$")
ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")

# Any Indian pincode: six digits, and no postal circle starts at 0. Open on
# purpose — a live user types their own, and the collector types it into the
# site, so there is nothing for the app to look up.
PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")

_WHITESPACE_RUN = re.compile(r"\s+")
QUERY_MIN_LEN = 2
QUERY_MAX_LEN = 60

# Validation gate v0 (§5): shelf prices outside this band are not believable.
MIN_PRICE = 1.0
MAX_PRICE = 10_000.0

# The one reason a row can be dropped for where it came from.
UNRESOLVED_LOCATION = "unresolved_location"

# The whole truth about page captures on the live path, in one sentence, in one
# place. Bright Data takes a SERP screenshot on every run; its collector media is
# not downloadable through the API, so the app has a capture it cannot show.
ARTIFACT_NOT_DELIVERABLE = "capture exists in Bright Data but is not deliverable via API"

# What the retrigger watchdog says it saw. One sentence, because it is shown.
STALL_REASON = "job never started navigating"

# Ceiling on a best-effort cancel. Every cancel happens on a path that has
# already gone wrong, so it must not add its own wait to one.
JOB_CANCEL_TIMEOUT_S = 5.0


class _JobHandle:
    """The Bright Data job a universe currently has in flight.

    Mutable and shared on purpose: the poll loop can replace the job under the
    stall watchdog, and the timeout handler in `_run_universe` needs to cancel
    whichever job is live at that moment, not the one that was triggered first.
    """

    __slots__ = ("job_id",)

    def __init__(self, job_id: str | None = None) -> None:
        self.job_id = job_id


class RunRejected(Exception):
    """A guard said no. The message is shown to the user verbatim, so it has to
    be an honest reason, not a generic error."""


class RunThrottled(RunRejected):
    """Right request, wrong moment. Served as 429 rather than 400 so a client
    can tell "try later" apart from "that input is not acceptable"."""


class RunMeta(BaseModel):
    run_id: str
    query: str
    pincode: str
    # The coarse label shown next to the pincode. There is no lookup table any
    # more, so a new run's label IS its pincode; what the site resolved that
    # pincode to travels on the rows as `resolved_area`. Kept as a field because
    # stored runs captured under the old table still carry a real label.
    area_label: str
    mode: str
    created_at: str
    status: str = "running"
    replay: bool = False
    source_run_id: str | None = None
    universes: list[str] = Field(default_factory=list)
    finished_at: str | None = None


def _new_run_id(prefix: str = "r") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{secrets.token_hex(2)}"


def _utc_date() -> str:
    """The day the daily run budget is keyed on. UTC, so it does not roll over
    twice or not at all depending on where the process happens to run."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _terse_error(exc: BaseException, limit: int = 240) -> str:
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text[:limit]


def normalize_query(raw: str) -> str:
    """Trim, then collapse internal whitespace runs to single spaces.

    Done before every length and allowlist check so `"  amul   butter "` and
    `"amul butter"` are the same query everywhere — including in the run's
    stored meta, which is what a receipt shows.
    """
    return _WHITESPACE_RUN.sub(" ", (raw or "").strip())


def validate_rows(rows: Iterable[NormalizedRow]) -> tuple[list[NormalizedRow], int, dict[str, int]]:
    """Deterministic, cheap gate. Returns (kept, dropped_count, reasons)."""
    kept: list[NormalizedRow] = []
    reasons: dict[str, int] = defaultdict(int)
    dropped = 0

    for row in rows:
        if not row.name or not row.name.strip():
            reasons["no_name"] += 1
            dropped += 1
            continue
        if row.price is None or row.price <= 0:
            reasons["no_price"] += 1
            dropped += 1
            continue
        if not (MIN_PRICE <= row.price <= MAX_PRICE):
            reasons["price_out_of_band"] += 1
            dropped += 1
            continue
        kept.append(row)

    return kept, dropped, dict(reasons)


def location_proof_error(pincode: str) -> str:
    """The exact wording of a location refusal. One place, so the event feed,
    the docs and the tests cannot drift apart."""
    return f"location proof failed: site did not resolve {pincode}"


def resolves_pincode(row: NormalizedRow, pincode: str) -> bool:
    """Site-resolved location proof: did the SITE say it was serving us there?

    `resolved_area` is the site's own delivery-address line. The requested
    pincode must appear on its own digit boundary.

    Fail-CLOSED, which reverses the old store-id rule on purpose. Under that
    rule an absent proof was not a failed proof, because the store id was a
    corroborating detail. Here the resolved line IS the proof, so a row without
    one proves nothing about where its prices come from — and the failure it
    guards against (a collector that quietly ignores the pincode and serves some
    default store's shelf) looks exactly like a missing resolved area.

    The pincode has to sit on its own digit boundary. Plain containment let
    "1560001" and a phone number that happens to embed the six digits pass the
    proof, which is a location claim the site never made.
    """
    if not pincode:
        return False
    return re.search(rf"(?<!\d){re.escape(pincode)}(?!\d)", row.resolved_area or "") is not None


def split_by_location(
    rows: Iterable[NormalizedRow], pincode: str
) -> tuple[list[NormalizedRow], list[NormalizedRow]]:
    """(rows the site resolved to `pincode`, rows it did not)."""
    resolved: list[NormalizedRow] = []
    unresolved: list[NormalizedRow] = []
    for row in rows:
        (resolved if resolves_pincode(row, pincode) else unresolved).append(row)
    return resolved, unresolved


def store_ids_in(records: Iterable[Any]) -> list[str]:
    """Distinct, sorted store ids the collector reported. PROVENANCE ONLY.

    Recorded on the event so a run says which shelf it read, and so an id can be
    checked by hand afterwards. It never refuses anything.
    """
    found: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        got = record.get("store_id")
        if isinstance(got, bool):
            continue
        if isinstance(got, float) and got.is_integer():
            # JSON has one number type, so a numeric merchant id can arrive as
            # 34540.0. `str()` on that is "34540.0", which matches nothing in a
            # store map and made `known_store` read false for a store the
            # operator had listed correctly.
            got = str(int(got))
        elif isinstance(got, (int, float)):
            got = str(got)
        if isinstance(got, str) and got.strip():
            found.add(got.strip())
    return sorted(found)


def known_store(
    store_ids: list[str],
    pincode: str,
    store_map: dict[str, frozenset[str]] | dict[str, set[str]],
) -> bool | None:
    """Advisory flag: are these the stores we have seen serve this pincode?

    True = every reported id is in the configured map. False = at least one is
    not — worth a look, but the rows still stand, because the site already told
    us it resolved our pincode. None = nothing to compare (no map for this
    universe or pincode, or no store id in the payload); an unknown is reported
    as unknown rather than as a false.
    """
    allowed = (store_map or {}).get(pincode)
    if not allowed or not store_ids:
        return None
    return all(store_id in allowed for store_id in store_ids)


def zero_rows_reason(rows: list[NormalizedRow], record: dict[str, Any]) -> str:
    """Zero-rows taxonomy (§5). When it is ambiguous, say `broken` — claiming
    `oos` or `unserviceable` we cannot see is a worse failure than admitting the
    collector came back empty."""
    if rows and all(not row.in_stock for row in rows):
        return "oos"

    haystack = " ".join(
        str(record.get(key, "")) for key in ("status", "message", "error", "note", "reason")
    ).lower()
    if "unserviceable" in haystack or "not serviceable" in haystack or "no store" in haystack:
        return "unserviceable"
    if "blocked" in haystack or "captcha" in haystack or "403" in haystack or "429" in haystack:
        return "blocked"
    return "broken"


# How many finished runs stay in memory. Everything a finished run holds is
# already on disk — events.jsonl, raw/, meta.json — and every read path falls
# back to it, so keeping more than a working set is a leak that grows for as
# long as the process lives.
MEMORY_RUNS_KEPT = 20


class RunManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._stores: dict[str, EventStore] = {}
        self._metas: dict[str, RunMeta] = {}
        # Live runs only. Replays cost no collector credits and must not hold the
        # single live slot, so they are tracked separately and never counted by
        # `_active_runs`.
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._replay_tasks: dict[str, asyncio.Task[None]] = {}
        self._replay_owner: dict[str, str] = {}
        # Self-heal runs. Like replays they call no collector and spend no
        # scrape credits, so they never hold the single live slot. They ARE
        # capped at one at a time: Bright Data allows three concurrent AI jobs
        # per account and a second heal of the same collector has nothing to
        # heal that the first is not already changing.
        self._heal_tasks: dict[str, asyncio.Task[None]] = {}
        self._rows: dict[str, dict[str, list[NormalizedRow]]] = {}
        self._rate: dict[str, deque[float]] = defaultdict(deque)
        self._last_run_at: dict[str, float] = {}
        self._last_replay_at: dict[str, float] = {}
        # Global live-run budget for the current UTC day. In memory only, so a
        # restart resets it. That is accepted: this is a backstop against
        # draining collector credits over a day, not accounting, and a counter
        # that survives restarts would need a store, a schema and a migration to
        # protect a number nobody audits.
        self._budget_day: str = _utc_date()
        self._budget_used: int = 0
        self.settings.runs_dir.mkdir(parents=True, exist_ok=True)

    # -- guards ----------------------------------------------------------
    def _check_rate(self, client_ip: str) -> None:
        window = self._rate[client_ip]
        now = time.monotonic()
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= self.settings.rate_limit_per_min:
            # Throttled, not rejected: the request is fine, the moment is not.
            # A 400 told the client to change its input, which is exactly the
            # wrong advice — the fix is to wait.
            raise RunThrottled(
                f"rate limit: {self.settings.rate_limit_per_min} runs per minute per client"
            )
        window.append(now)

    def _check_cooldown(
        self, client_ip: str, stamps: dict[str, float] | None = None, what: str = "run"
    ) -> None:
        """One start per client per cooldown window.

        The app is open to any pincode and any query now, so this is what stops
        one visitor from spending the whole collector budget. Pure check: the
        clock is only stamped once something is actually being created, so a
        request refused by any other guard never costs the client its next
        window.

        Replays keep their OWN stamp table. They spend no collector credits, so
        pacing them out of the live budget would mean the demo replay is refused
        for a minute after every live run — a brake on the wrong thing. They are
        still paced, and still capped at one in flight per client.
        """
        cooldown = self.settings.run_cooldown_s
        if cooldown <= 0:
            return
        last = (self._last_run_at if stamps is None else stamps).get(client_ip)
        if last is None:
            return
        elapsed = time.monotonic() - last
        if elapsed < cooldown:
            raise RunThrottled(
                f"one {what} per {int(cooldown)}s per client — "
                f"try again in {int(cooldown - elapsed) + 1}s"
            )

    def _check_daily_budget(self) -> None:
        """One budget for the whole day, shared by every client.

        The per-IP cooldown paces one visitor; nothing paced the sum of them, so
        a day of demo traffic could spend the collector budget outright. Counted
        here rather than in the run task because a refusal has to reach the
        caller as a 429 instead of a run that exists and then dies.

        LIVE runs only. Replays re-stream a stored file and self-heals drive the
        AI flow; neither calls a collector, and both are created through paths of
        their own that never reach this check.
        """
        today = _utc_date()
        if today != self._budget_day:
            self._budget_day = today
            self._budget_used = 0
        if self._budget_used >= self.settings.daily_run_budget:
            # Throttled, not rejected: the request is fine, the day is spent.
            raise RunThrottled(
                "daily live-run budget reached, try tomorrow or watch the demo replay"
            )

    def _active_runs(self) -> int:
        """LIVE runs in flight. Replays are deliberately not counted: a re-stream
        of a stored file calls no collector, so making it occupy the one live
        slot would block a real run for nothing."""
        return sum(1 for task in self._tasks.values() if not task.done())

    def _active_replays(self, client_ip: str) -> int:
        return sum(
            1
            for run_id, task in self._replay_tasks.items()
            if not task.done() and self._replay_owner.get(run_id) == client_ip
        )

    def _prune_memory(self) -> None:
        """Drop finished runs beyond the working set.

        Nothing is lost: `events.jsonl`, `raw/` and `meta.json` are written as
        the run happens, and every read path already falls back to disk for a run
        this process does not hold. Without this, a long-lived process keeps
        every event and every parsed row of every run it has ever served.
        """
        excess = len(self._metas) - MEMORY_RUNS_KEPT
        if excess <= 0:
            return
        for run_id in list(self._metas)[:excess]:
            task = (
                self._tasks.get(run_id)
                or self._replay_tasks.get(run_id)
                or self._heal_tasks.get(run_id)
            )
            if task is not None and not task.done():
                continue
            self._metas.pop(run_id, None)
            self._stores.pop(run_id, None)
            self._rows.pop(run_id, None)
            self._tasks.pop(run_id, None)
            self._replay_tasks.pop(run_id, None)
            self._replay_owner.pop(run_id, None)
            self._heal_tasks.pop(run_id, None)

    def _validate_request(self, query: str, pincode: str) -> tuple[str, str]:
        """Open inputs: any Indian pincode, any sane query.

        The old build only accepted an allowlisted pincode it could look up
        coordinates for. Nothing needs those coordinates, so the only questions
        left are whether the pincode is well formed and whether the query is
        something a person could have typed.
        """
        query = normalize_query(query)
        pincode = (pincode or "").strip()

        if not query:
            raise RunRejected("query is empty")
        if len(query) < QUERY_MIN_LEN:
            raise RunRejected(f"query is shorter than {QUERY_MIN_LEN} characters")
        if len(query) > QUERY_MAX_LEN:
            raise RunRejected(f"query is longer than {QUERY_MAX_LEN} characters")
        if not query.isprintable():
            raise RunRejected("query contains non-printable characters")
        if not PINCODE_RE.match(pincode):
            raise RunRejected(
                f"pincode {pincode!r} is not a 6-digit Indian pincode (e.g. 560001)"
            )
        if not self.settings.query_allowed(query):
            raise RunRejected(
                f"query {query!r} is not on the demo allowlist — "
                f"allowed: {', '.join(self.settings.query_allowlist)}"
            )

        targets = dispatchable(self.settings)
        if not targets:
            raise RunRejected(
                "no universe is dispatchable right now — "
                + ("wire a collector id in the environment" if self.settings.is_live
                   else "no mock fixture is available")
            )
        return query, pincode

    # -- run creation ----------------------------------------------------
    def create_run(self, query: str, pincode: str, client_ip: str = "local") -> RunMeta:
        query, pincode = self._validate_request(query, pincode)

        if self._active_runs() >= self.settings.max_concurrent_runs:
            # Throttled, not rejected: nothing is wrong with the request.
            raise RunThrottled(
                f"a run is already in flight (max {self.settings.max_concurrent_runs}) — "
                "wait for it to finish"
            )
        self._check_cooldown(client_ip)
        self._check_rate(client_ip)
        if self.settings.is_live:
            self._check_daily_budget()

        # Build the client BEFORE the run exists. `LiveClient` refuses to start
        # without BD_API_KEY, and finding that out inside the run task created a
        # run that was stranded the moment it was created: a run id, a directory,
        # a `run_requested` event and then a `failed` the caller had already been
        # told 202 about. It is a configuration error, so it is a 400 with the
        # reason in it.
        try:
            client = build_client(self.settings)
        except BDError as exc:
            raise RunRejected(f"cannot start a run: {exc}") from exc

        targets = dispatchable(self.settings)
        run_id = _new_run_id("r")
        run_dir = self.settings.runs_dir / run_id
        store = EventStore(run_id, run_dir)

        meta = RunMeta(
            run_id=run_id,
            query=query,
            pincode=pincode,
            area_label=pincode,
            mode=self.settings.bd_mode,
            created_at=utc_now_iso(),
            universes=[u.id for u in targets],
        )
        self._stores[run_id] = store
        self._metas[run_id] = meta
        self._rows[run_id] = {}
        self._write_meta(meta)

        self._last_run_at[client_ip] = time.monotonic()
        if self.settings.is_live:
            # Stamped only once the run is really being created, so a request
            # refused by any later guard never costs the day a slot.
            self._budget_used += 1
        self._tasks[run_id] = asyncio.create_task(self._drive(store, meta, targets, client))
        self._prune_memory()
        return meta

    def _write_meta(self, meta: RunMeta) -> None:
        path = self.settings.runs_dir / meta.run_id / "meta.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")

    # -- the run itself --------------------------------------------------
    async def _drive(
        self, store: EventStore, meta: RunMeta, targets: list[Universe], client: Any
    ) -> None:
        try:
            await store.append(
                EventType.RUN_REQUESTED,
                {
                    "query": meta.query,
                    "pincode": meta.pincode,
                    "area_label": meta.area_label,
                    "mode": meta.mode,
                    "universes": meta.universes,
                },
            )
            await asyncio.gather(
                *(self._run_universe(store, meta, u, client) for u in targets),
                return_exceptions=True,
            )
            rows = self._rows.get(meta.run_id, {})
            comparison = match(rows)
            await store.append(
                EventType.DONE,
                {
                    "universes": meta.universes,
                    "rows_total": comparison.row_count,
                    "groups": len(comparison.groups),
                    "unmatched": len(comparison.unmatched),
                },
            )
            meta.status = "done"
        except asyncio.CancelledError:
            # A shutdown is not a failure. Recording it as one would put a red
            # line in a stored run for something the collector never did, and
            # leaving the status at "running" would strand the run forever.
            meta.status = "cancelled"
            raise
        except Exception as exc:  # pragma: no cover - the gather above absorbs the normal cases
            await store.append(EventType.FAILED, {"error": _terse_error(exc)})
            meta.status = "failed"
        finally:
            meta.finished_at = utc_now_iso()
            self._write_meta(meta)
            with contextlib.suppress(Exception):
                await client.aclose()
            await store.close()

    async def _run_universe(
        self,
        store: EventStore,
        meta: RunMeta,
        universe: Universe,
        client: Any,
    ) -> None:
        uid = universe.id
        # Shared with the poll loop so the timeout handler below cancels the job
        # that is actually in flight, which the stall watchdog may have replaced.
        handle = _JobHandle()
        await store.append(
            EventType.UNIVERSE_DISPATCHED,
            {
                "display": universe.display,
                "badge": universe.badge,
                "trigger_mode": universe.trigger_mode,
                "collector_version": universe.collector_version,
                "mode": meta.mode,
            },
            universe=uid,
        )

        try:
            async with asyncio.timeout(self.settings.universe_timeout_s):
                # Exactly what the collector reads. No coordinates: every
                # collector types the pincode into the site's own location
                # picker, so lat/long were carried, sent and ignored.
                inputs = [
                    {"universe": uid, "keyword": meta.query, "pincode": meta.pincode}
                ]
                collector_id = collector_id_for(self.settings, uid)
                job_id = await client.trigger(
                    collector_id, inputs, universe.collector_version
                )
                handle.job_id = job_id
                await store.append(
                    EventType.TRIGGERED,
                    {"job_id": job_id, "version": universe.collector_version},
                    universe=uid,
                )

                results = await self._poll_for_results(
                    store,
                    client,
                    uid,
                    handle,
                    collector_id,
                    inputs,
                    universe.collector_version,
                )
                # Raw first: it is the evidence for everything below, including a
                # location refusal.
                (store.run_dir / "raw" / f"{uid}.json").write_text(
                    json.dumps(results, ensure_ascii=False), encoding="utf-8"
                )

                rows = get_mapper(universe.mapper)(results)
                await store.append(EventType.ROWS, {"n": len(rows)}, universe=uid)

                # Location proof, before anything is stood behind. Store ids are
                # read for the record only; they never decide the outcome.
                ids = store_ids_in(as_records(results))

                # Nothing came back at all. There is no location claim to make
                # about a payload with no rows in it — saying "the site did not
                # resolve your pincode" would be inventing a reason for an
                # emptiness we cannot see the cause of. The zero-rows taxonomy
                # exists for precisely this, and its honest default is `broken`.
                if not rows:
                    await store.append(
                        EventType.ZERO_ROWS,
                        {
                            "reason": zero_rows_reason([], unwrap_record(results)),
                            "rows_seen": 0,
                            "rows_kept": 0,
                            "rows_dropped": 0,
                            "store_ids": ids,
                        },
                        universe=uid,
                    )
                    return

                located, unlocated = split_by_location(rows, meta.pincode)
                if not located:
                    await store.append(
                        EventType.FAILED,
                        {
                            "error": location_proof_error(meta.pincode),
                            "pincode": meta.pincode,
                            "store_ids": ids,
                        },
                        universe=uid,
                    )
                    return

                await self._capture_screenshot(store, meta, client, uid, results)

                kept, dropped, reasons = validate_rows(located)
                if unlocated:
                    # Counted with the gate's own drops so `rows_kept +
                    # rows_dropped` still accounts for every parsed row.
                    dropped += len(unlocated)
                    reasons[UNRESOLVED_LOCATION] = len(unlocated)
                # Nothing survived the gate, or nothing that did is buyable. Either
                # way this universe has no usable rows, and §5 wants the reason
                # named rather than an empty `validated`.
                if not any(row.in_stock for row in kept):
                    reason = zero_rows_reason(kept or located, unwrap_record(results))
                    await store.append(
                        EventType.ZERO_ROWS,
                        {
                            "reason": reason,
                            "rows_seen": len(rows),
                            "rows_kept": len(kept),
                            "rows_dropped": dropped,
                        },
                        universe=uid,
                    )
                    return

                await store.append(
                    EventType.VALIDATED,
                    {
                        "rows_kept": len(kept),
                        "rows_dropped": dropped,
                        "reasons": reasons,
                        "store_ids": ids,
                        "known_store": known_store(
                            ids, meta.pincode, self.settings.store_map_for(uid)
                        ),
                    },
                    universe=uid,
                )
                # Only AFTER the event. `append` awaits, so a timeout landing in
                # that window used to leave rows feeding the comparison while the
                # feed said `timed_out` — a universe contributing prices to a
                # receipt it had just been reported as not finishing.
                self._rows.setdefault(meta.run_id, {})[uid] = kept
        except TimeoutError:
            # CancelledError is deliberately not caught here: a shutdown is not a
            # collector timeout and must not be recorded as one.
            await store.append(
                EventType.TIMED_OUT,
                {"after_s": self.settings.universe_timeout_s},
                universe=uid,
            )
            # The event is written BEFORE the cancel so nothing about the cancel
            # can mask or replace it. Giving up on the job here does not stop it
            # at Bright Data: one job was observed running 319s past the app's
            # timeout, billing the whole time, until it was canceled by hand.
            # Awaited rather than detached because `_drive` closes the client as
            # soon as this task returns, which would kill a detached cancel.
            await self._cancel_job(client, handle.job_id)
        except Exception as exc:
            await store.append(EventType.FAILED, {"error": _terse_error(exc)}, universe=uid)

    async def _poll_for_results(
        self,
        store: EventStore,
        client: Any,
        uid: str,
        handle: _JobHandle,
        collector_id: str,
        inputs: list[dict[str, Any]],
        version: str,
    ) -> list[dict[str, Any]]:
        """Poll one universe's job to completion, retriggering a stalled one once.

        THE STALL. Bright Data accepts the trigger in under a second, reports the
        job as running, and then never allocates a worker: `navigations` and
        `lines` both sit at 0 for as long as anyone watches. Seen three times in
        one day on the Instamart collector, while the identical template
        delivered rows in 36s on the very next trigger. There is nothing to wait
        for in that state, so waiting out the universe timeout spends the whole
        budget on a job that was never going to start.

        THE BUDGET IS NOT RESET BY A RETRIGGER. The `asyncio.timeout` around this
        call lives in `_run_universe` and keeps running across the swap, so a
        universe still gets its one `universe_timeout_s` in total, not one per
        job. The replacement inherits whatever is left of it. That is the point:
        a retrigger is a second chance inside the same deadline, not a way to
        double a universe's share of the run.

        ONE retrigger per universe per run. If the replacement stalls too, the
        stall is not this job's bad luck and trying again would only spend the
        rest of the deadline finding that out, so it is left to time out.
        """
        interval = (
            self.settings.poll_interval_s
            if self.settings.is_live
            else max(self.settings.mock_step_delay_s, 0.0)
        )
        last_pages_left: int | None = None
        watching_since = time.monotonic()
        retriggered = False

        while True:
            job_id = handle.job_id
            if not job_id:  # unreachable: the caller triggers before it polls
                raise BDError("no collector job to poll")
            log = await client.job_log(job_id)
            if log.pages_left is not None and log.pages_left > 0 and log.pages_left != last_pages_left:
                last_pages_left = log.pages_left
                await store.append(
                    EventType.PROGRESS,
                    {"pages_left": log.pages_left, "pages": log.pages},
                    universe=uid,
                )
            if log.failed:
                raise BDError(f"collector job ended {log.status}")
            if log.finished:
                results = await client.fetch_results(job_id)
                if results is not None:
                    return results
            elif not retriggered and self._is_stalled(log, watching_since):
                observed = round(time.monotonic() - watching_since, 1)
                await self._cancel_job(client, job_id)
                await store.append(
                    EventType.RETRIGGERED,
                    {"job_id": job_id, "after_s": observed, "reason": STALL_REASON},
                    universe=uid,
                )
                handle.job_id = await client.trigger(collector_id, inputs, version)
                retriggered = True
                last_pages_left = None
                # A real second job with a real id, so it is reported the same
                # way the first one was. The UI reads the job it is watching from
                # here; `retriggered` names the job that was abandoned.
                await store.append(
                    EventType.TRIGGERED,
                    {"job_id": handle.job_id, "version": version},
                    universe=uid,
                )
                continue
            if interval > 0:
                await asyncio.sleep(interval)

    def _is_stalled(self, log: JobLog, watching_since: float) -> bool:
        """A job Bright Data has not started running, as opposed to a slow one.

        Every part of this has to hold. `navigations` absent counts as stalled
        because a worker that has opened no page reports nothing either way, but
        `lines` must be an explicit 0: those two are the only evidence there is,
        and firing on a payload that carries neither would be guessing.
        """
        if time.monotonic() - watching_since < self.settings.stall_retrigger_s:
            return False
        return log.navigations in (0, None) and log.lines == 0

    async def _cancel_job(self, client: Any, job_id: str | None) -> None:
        """Best effort, and deliberately silent about failing.

        Every caller is already handling something that went wrong; the job is
        lost to the run either way and the only thing a cancel saves is the
        collector time Bright Data would go on spending on it. A cancel that
        fails must not change one word of what the run reports.
        """
        if not job_id:
            return
        cancel = getattr(client, "cancel_job", None)
        if cancel is None:
            return
        with contextlib.suppress(Exception):
            async with asyncio.timeout(JOB_CANCEL_TIMEOUT_S):
                await cancel(job_id)

    async def _capture_screenshot(
        self, store: EventStore, meta: RunMeta, client: Any, uid: str, results: Any
    ) -> None:
        """A missing screenshot is not a run failure — the rows still stand.

        Hence `artifact_failed`, which is NON-terminal: the universe carries on to
        the validation gate and still reports `validated`. Using `failed` here put
        a red line in the feed for a universe that had actually succeeded. The
        capture is evidence, so its absence stays visible rather than swallowed.

        Live runs always land here. Bright Data captures a SERP screenshot per
        run and does not deliver collector media over the API, so the app has one
        honest thing to say about it — and says it once per universe rather than
        silently showing nothing.
        """
        try:
            blob = await client.fetch_screenshot(screenshot_record(results), uid)
        except Exception as exc:
            await store.append(
                EventType.ARTIFACT_FAILED, {"error": _terse_error(exc)}, universe=uid
            )
            return
        if not blob:
            if has_screenshot_reference(results):
                await store.append(
                    EventType.ARTIFACT_FAILED,
                    {"error": ARTIFACT_NOT_DELIVERABLE},
                    universe=uid,
                )
            return
        name = f"{uid}.png"
        (store.run_dir / "artifacts" / name).write_bytes(blob)
        await store.append(
            EventType.SCREENSHOT,
            {
                "artifact": name,
                "url": f"/api/runs/{meta.run_id}/artifacts/{name}",
                "bytes": len(blob),
                "placeholder": client.mode == "mock",
            },
            universe=uid,
        )

    # -- replay ----------------------------------------------------------
    async def start_replay(self, source_run_id: str, client_ip: str = "local") -> RunMeta:
        source = self.load_meta(source_run_id)
        if source is None:
            raise RunRejected(f"no stored run {source_run_id!r}")
        if source.replay:
            raise RunRejected("that run is itself a replay — replay the original capture instead")
        if source.status != "done":
            # A run still in flight has a file that is still being appended to,
            # and a failed or cancelled one is a partial record. Re-streaming
            # either presents an incomplete capture as a complete one.
            raise RunRejected(
                f"run {source_run_id!r} is {source.status!r}, not done — "
                "only a completed capture can be replayed"
            )

        # Replays were the one entrance with no guard on it at all: any client
        # could open unbounded concurrent re-streams, each holding a task, a
        # file handle and every event in memory.
        if self._active_replays(client_ip) >= 1:
            raise RunThrottled("a replay is already streaming for this client — wait for it to end")
        self._check_cooldown(client_ip, self._last_replay_at, what="replay")

        source_dir = self.read_dir(source_run_id)
        if source_dir is None:
            raise RunRejected(f"no stored run {source_run_id!r}")
        events = read_events_file(source_dir / "events.jsonl")
        if not events:
            raise RunRejected(f"run {source_run_id!r} has no stored events to replay")

        run_id = _new_run_id("rp")
        run_dir = self.settings.runs_dir / run_id
        store = EventStore(run_id, run_dir, replay=True)

        src_artifacts = source_dir / "artifacts"
        if src_artifacts.is_dir():
            shutil.copytree(src_artifacts, run_dir / "artifacts", dirs_exist_ok=True)

        meta = RunMeta(
            run_id=run_id,
            query=source.query,
            pincode=source.pincode,
            area_label=source.area_label,
            mode=source.mode,
            created_at=utc_now_iso(),
            replay=True,
            source_run_id=source_run_id,
            universes=source.universes,
        )
        self._stores[run_id] = store
        self._metas[run_id] = meta
        self._rows[run_id] = {}
        self._write_meta(meta)

        self._last_replay_at[client_ip] = time.monotonic()
        self._replay_owner[run_id] = client_ip
        self._replay_tasks[run_id] = asyncio.create_task(
            self._stream_replay(store, meta, events)
        )
        self._prune_memory()
        return meta

    async def _stream_replay(self, store: EventStore, meta: RunMeta, events: list[Event]) -> None:
        """Re-stream events that really happened. Nothing is fabricated: only
        `replay`, the event index and our own artifact URLs are rewritten."""
        cap = self.settings.replay_max_gap_s
        previous: datetime | None = None
        try:
            for event in events:
                current = _parse_ts(event.ts)
                if previous is not None and current is not None:
                    gap = max((current - previous).total_seconds(), 0.0)
                    if cap > 0:
                        await asyncio.sleep(min(gap, cap))
                previous = current

                data = dict(event.data)
                if event.type == EventType.SCREENSHOT and data.get("artifact"):
                    data["url"] = f"/api/runs/{meta.run_id}/artifacts/{data['artifact']}"
                await store.append_replayed(event, data=data)

            meta.status = "done"
        except asyncio.CancelledError:
            meta.status = "cancelled"
            raise
        except Exception as exc:  # pragma: no cover
            await store.append(EventType.FAILED, {"error": _terse_error(exc)})
            meta.status = "failed"
        finally:
            meta.finished_at = utc_now_iso()
            self._write_meta(meta)
            await store.close()

    # -- self-heal -------------------------------------------------------
    def _active_heals(self) -> int:
        return sum(1 for task in self._heal_tasks.values() if not task.done())

    def start_heal(
        self,
        prompt: str | None = None,
        custom_input: list[dict[str, Any]] | None = None,
        universe_id: str = "chaos",
        client: Any | None = None,
    ) -> RunMeta:
        """Repair one collector through Bright Data self-healing, as a run.

        A heal gets a run of its own so the cycle leaves the same kind of record
        a search does: an append-only event file, an SSE stream while it happens,
        and a directory that can be read afterwards. It spends no scrape credits
        and takes no part in the live-run slot.
        """
        collector_id = collector_id_for(self.settings, universe_id)
        if not collector_id:
            raise RunRejected(
                f"no collector id is configured for {universe_id!r}, so there is nothing to heal"
            )
        if self._active_heals() >= 1:
            raise RunThrottled("a self-heal is already running - wait for it to finish")

        try:
            checked_prompt = validate_prompt(prompt)
        except HealError as exc:
            raise RunRejected(str(exc)) from exc

        inputs = custom_input if custom_input is not None else DEFAULT_CUSTOM_INPUT
        if client is None:
            try:
                client = HealClient(self.settings)
            except HealError as exc:
                raise RunRejected(f"cannot start a self-heal: {exc}") from exc

        run_id = _new_run_id("r")
        run_dir = self.settings.runs_dir / run_id
        store = EventStore(run_id, run_dir)
        meta = RunMeta(
            run_id=run_id,
            query=f"self-heal: {universe_id} collector",
            pincode="",
            area_label="",
            mode=self.settings.bd_mode,
            created_at=utc_now_iso(),
            universes=[universe_id],
        )
        self._stores[run_id] = store
        self._metas[run_id] = meta
        self._rows[run_id] = {}
        self._write_meta(meta)

        self._heal_tasks[run_id] = asyncio.create_task(
            self._drive_heal(store, meta, client, collector_id, checked_prompt, inputs, universe_id)
        )
        self._prune_memory()
        return meta

    async def _drive_heal(
        self,
        store: EventStore,
        meta: RunMeta,
        client: Any,
        collector_id: str,
        prompt: str,
        custom_input: list[dict[str, Any]],
        universe_id: str,
    ) -> None:
        try:
            async with asyncio.timeout(self.settings.heal_timeout_s):
                await run_heal_cycle(
                    store,
                    self.settings,
                    client,
                    collector_id,
                    prompt,
                    custom_input,
                    universe=universe_id,
                )
            await store.append(
                EventType.DONE,
                {"universes": [universe_id], "rows_total": 0, "groups": 0, "unmatched": 0},
            )
            meta.status = "done"
        except asyncio.CancelledError:
            meta.status = "cancelled"
            raise
        except TimeoutError:
            await store.append(
                EventType.TIMED_OUT,
                {"after_s": self.settings.heal_timeout_s},
                universe=universe_id,
            )
            await store.append(EventType.TIMED_OUT, {"after_s": self.settings.heal_timeout_s})
            meta.status = "timed_out"
        except Exception as exc:
            # Twice on purpose, and they say different things: the universe-level
            # line is why THIS collector was not repaired, the run-level one ends
            # the run so the UI stops waiting. Neither is a guess: both carry the
            # same message Bright Data or this app produced.
            error = _terse_error(exc)
            await store.append(EventType.FAILED, {"error": error}, universe=universe_id)
            await store.append(EventType.FAILED, {"error": error})
            meta.status = "failed"
        finally:
            meta.finished_at = utc_now_iso()
            self._write_meta(meta)
            with contextlib.suppress(Exception):
                await client.aclose()
            await store.close()

    # -- reads -----------------------------------------------------------
    def read_dir(self, run_id: str) -> Path | None:
        """Where a stored run lives.

        Captured runs sit directly under `runs/`, which is gitignored. Curated
        demo runs live in `runs/replays/` and are committed, so they survive a
        fresh checkout and can be re-streamed when nothing live is available.
        """
        if not RUN_ID_RE.match(run_id):
            return None
        for candidate in (
            self.settings.runs_dir / run_id,
            self.settings.runs_dir / "replays" / run_id,
        ):
            if (candidate / "events.jsonl").is_file() or (candidate / "meta.json").is_file():
                return candidate
        return None

    def load_meta(self, run_id: str) -> RunMeta | None:
        if run_id in self._metas:
            return self._metas[run_id]
        run_dir = self.read_dir(run_id)
        if run_dir is None:
            return None
        path = run_dir / "meta.json"
        if not path.is_file():
            return None
        try:
            return RunMeta.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_store(self, run_id: str) -> EventStore | None:
        return self._stores.get(run_id)

    def events_for(self, run_id: str) -> list[Event]:
        store = self._stores.get(run_id)
        if store is not None:
            return store.events
        run_dir = self.read_dir(run_id)
        return read_events_file(run_dir / "events.jsonl") if run_dir else []

    def rows_for(self, run_id: str) -> dict[str, list[NormalizedRow]]:
        """In-memory while the run is in this process; re-derived from the saved
        raw payloads otherwise. Mappers are pure, so both paths agree."""
        meta = self.load_meta(run_id)
        if meta and meta.source_run_id:
            # A replay owns no rows; they belong to the run it re-streams.
            return self._rows_from_disk(meta.source_run_id)
        if run_id in self._rows:
            # Authoritative: empty means the run produced nothing, not "go and
            # look on disk".
            return self._rows[run_id]
        return self._rows_from_disk(run_id)

    def _rows_from_disk(self, run_id: str) -> dict[str, list[NormalizedRow]]:
        source_dir = self.read_dir(run_id)
        if source_dir is None:
            return {}
        raw_dir = source_dir / "raw"
        if not raw_dir.is_dir():
            return {}

        # Only universes that actually reached `validated` contribute. Raw is
        # written before the mapper runs, so a universe that failed, timed out,
        # was refused on location proof, or reported zero_rows has a payload on
        # disk too — re-deriving from it blindly would resurrect exactly the data
        # the run refused to stand behind.
        stood_behind = {
            event.universe
            for event in self.events_for(run_id)
            if event.type == EventType.VALIDATED and event.universe
        }

        # The same pincode the run proved against. Without it there is nothing to
        # prove location against, so nothing is re-derived — a stored run with no
        # meta.json reports no rows rather than unproven ones.
        meta = self.load_meta(run_id)
        pincode = meta.pincode if meta else ""

        out: dict[str, list[NormalizedRow]] = {}
        for path in sorted(raw_dir.glob("*.json")):
            uid = path.stem
            if uid not in stood_behind:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                rows = get_mapper(uid)(payload)
            except Exception:
                continue
            located, _ = split_by_location(rows, pincode)
            kept, _, _ = validate_rows(located)
            if kept:
                out[uid] = kept
        return out

    def comparison_for(self, run_id: str) -> Comparison:
        return match(self.rows_for(run_id))

    def list_runs(self, limit: int = 25) -> list[RunMeta]:
        metas: dict[str, RunMeta] = {}
        if self.settings.runs_dir.is_dir():
            for pattern in ("*/meta.json", "replays/*/meta.json"):
                for path in self.settings.runs_dir.glob(pattern):
                    try:
                        meta = RunMeta.model_validate_json(path.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    metas[meta.run_id] = meta
        metas.update(self._metas)
        return sorted(metas.values(), key=lambda m: m.created_at, reverse=True)[:limit]

    def artifact_path(self, run_id: str, name: str) -> Path | None:
        if not ARTIFACT_NAME_RE.match(name):
            return None
        run_dir = self.read_dir(run_id)
        if run_dir is None:
            return None
        base = (run_dir / "artifacts").resolve()
        candidate = (base / name).resolve()
        if not str(candidate).startswith(str(base) + "/") or not candidate.is_file():
            return None
        return candidate

    async def shutdown(self) -> None:
        tasks = (
            list(self._tasks.values())
            + list(self._replay_tasks.values())
            + list(self._heal_tasks.values())
        )
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


def _parse_ts(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
