"""Event envelope, append-only JSONL store, and the per-run SSE broker.

DESIGN.md §3. The rules that matter:
  * `i` is monotonic per run and is also the SSE event id, so a client can
    resume with Last-Event-ID.
  * Events are only ever *observed* states. Nothing here invents telemetry.
  * `replay` is False at capture time. A replay re-streams events that really
    happened, rewriting `replay: True` on the way out. Never fabricated.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, Field


class EventType(StrEnum):
    # Run lifecycle
    RUN_REQUESTED = "run_requested"
    UNIVERSE_DISPATCHED = "universe_dispatched"
    TRIGGERED = "triggered"
    PROGRESS = "progress"
    ROWS = "rows"
    SCREENSHOT = "screenshot"
    # Non-terminal: the rows stand, only the page capture is missing.
    ARTIFACT_FAILED = "artifact_failed"
    VALIDATED = "validated"
    ZERO_ROWS = "zero_rows"
    # Non-terminal: the collector job never started, so it was canceled and a
    # fresh one was triggered for the same universe. The universe is still
    # collecting.
    RETRIGGERED = "retriggered"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    DONE = "done"

    # Reserved for the self-healing pass — defined now, not implemented.
    INCIDENT = "incident"
    HEAL_STARTED = "heal_started"
    HEAL_PREVIEWED = "heal_previewed"
    HEAL_APPROVED = "heal_approved"
    HEAL_PROMOTED = "heal_promoted"


IMPLEMENTED_EVENT_TYPES = frozenset(
    {
        EventType.RUN_REQUESTED,
        EventType.UNIVERSE_DISPATCHED,
        EventType.TRIGGERED,
        EventType.PROGRESS,
        EventType.ROWS,
        EventType.SCREENSHOT,
        EventType.ARTIFACT_FAILED,
        EventType.VALIDATED,
        EventType.ZERO_ROWS,
        EventType.RETRIGGERED,
        EventType.FAILED,
        EventType.TIMED_OUT,
        EventType.DONE,
    }
)

TERMINAL_UNIVERSE_TYPES = frozenset(
    {EventType.VALIDATED, EventType.ZERO_ROWS, EventType.FAILED, EventType.TIMED_OUT}
)

ZERO_ROW_REASONS = ("oos", "unserviceable", "blocked", "broken")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Event(BaseModel):
    i: int
    ts: str
    run_id: str
    universe: str | None = None
    type: EventType
    replay: bool = False
    data: dict[str, Any] = Field(default_factory=dict)

    def to_jsonl(self) -> str:
        return self.model_dump_json() + "\n"

    def sse(self) -> str:
        """One SSE frame. The event id is `i`, so Last-Event-ID resumes exactly."""
        payload = self.model_dump_json()
        return f"id: {self.i}\nevent: {self.type.value}\ndata: {payload}\n\n"

    def as_replay(self) -> "Event":
        return self.model_copy(update={"replay": True})


class EventStore:
    """Append-only JSONL for one run, plus in-memory fanout to SSE subscribers.

    Writes are serialised through a lock so `i` and the file order can never
    disagree. Every line is flushed immediately: a run directory is meant to be
    a complete record even if the process dies mid-run.
    """

    def __init__(self, run_id: str, run_dir: Path, replay: bool = False) -> None:
        self.run_id = run_id
        self.run_dir = run_dir
        self.replay = replay
        self.path = run_dir / "events.jsonl"
        self._events: list[Event] = []
        self._subscribers: set[asyncio.Queue[Event | None]] = set()
        self._lock = asyncio.Lock()
        self._closed = False
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "raw").mkdir(exist_ok=True)
        (self.run_dir / "artifacts").mkdir(exist_ok=True)

    # -- writing ---------------------------------------------------------
    async def append(
        self,
        type: EventType,
        data: dict[str, Any] | None = None,
        universe: str | None = None,
    ) -> Event:
        async with self._lock:
            event = Event(
                i=len(self._events) + 1,
                ts=utc_now_iso(),
                run_id=self.run_id,
                universe=universe,
                type=type,
                replay=self.replay,
                data=data or {},
            )
            self._events.append(event)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(event.to_jsonl())
                fh.flush()
            # Fanout stays INSIDE the lock so subscribers see events in `i`
            # order no matter what. `put_nowait` on an unbounded queue never
            # blocks and never awaits, so holding the lock across it is free.
            # Moving it out would make ordering depend on no `await` ever
            # appearing between the release and the fanout — and because
            # subscribers drop `i <= seen`, out-of-order delivery LOSES events
            # rather than merely reordering them.
            self._fanout(event)
        return event

    async def append_replayed(self, source: Event, data: dict[str, Any] | None = None) -> Event:
        """Re-emit an event that really happened, into this (replay) run.

        The original `ts` is kept — that is when the capture actually occurred —
        while `i` is renumbered for this stream so Last-Event-ID resume still
        works. `replay` is forced True. Nothing else is invented.
        """
        async with self._lock:
            event = Event(
                i=len(self._events) + 1,
                ts=source.ts,
                run_id=self.run_id,
                universe=source.universe,
                type=source.type,
                replay=True,
                data=dict(source.data if data is None else data),
            )
            self._events.append(event)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(event.to_jsonl())
                fh.flush()
            self._fanout(event)  # inside the lock — see `append`
        return event

    def _fanout(self, event: Event) -> None:
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    async def close(self) -> None:
        """No more events. Subscribers get a sentinel so their stream ends."""
        self._closed = True
        for queue in list(self._subscribers):
            queue.put_nowait(None)

    @property
    def closed(self) -> bool:
        return self._closed

    # -- reading ---------------------------------------------------------
    @property
    def events(self) -> list[Event]:
        return list(self._events)

    def since(self, last_event_id: int | None) -> list[Event]:
        if last_event_id is None:
            return list(self._events)
        return [e for e in self._events if e.i > last_event_id]

    def latest_by_universe(self) -> dict[str, Event]:
        out: dict[str, Event] = {}
        for e in self._events:
            if e.universe:
                out[e.universe] = e
        return out

    # -- subscription ----------------------------------------------------
    def subscribe(self) -> asyncio.Queue[Event | None]:
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event | None]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


def read_events_file(path: Path) -> list[Event]:
    """Rehydrate a finished run from disk. Malformed trailing lines are skipped —
    a crashed run should still replay everything that was written cleanly."""
    if not path.is_file():
        return []
    out: list[Event] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Event.model_validate(json.loads(line)))
        except Exception:
            continue
    return out


def parse_last_event_id(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(raw.strip())
    except (ValueError, AttributeError):
        return None
    return value if value >= 0 else None


def heartbeat_frame() -> str:
    return ": heartbeat\n\n"


def frames_for(events: Iterable[Event], replay: bool = False) -> list[str]:
    return [(e.as_replay() if replay else e).sse() for e in events]
