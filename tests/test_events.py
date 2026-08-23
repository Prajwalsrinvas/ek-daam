"""Event store ordering, durability and Last-Event-ID resume — DESIGN.md §3."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from server.events import (
    Event,
    EventStore,
    EventType,
    parse_last_event_id,
    read_events_file,
)


@pytest.fixture
def store(tmp_path: Path) -> EventStore:
    return EventStore("r_20260822_091230_ab12", tmp_path / "r_20260822_091230_ab12")


async def test_indices_are_monotonic_from_one(store: EventStore) -> None:
    for _ in range(5):
        await store.append(EventType.PROGRESS, {"pages_left": 1}, universe="zepto")

    assert [e.i for e in store.events] == [1, 2, 3, 4, 5]


async def test_concurrent_appends_never_collide(store: EventStore) -> None:
    """Twenty tasks racing must still produce 1..20 exactly once each, and the
    file order must match the in-memory order."""
    await asyncio.gather(
        *(store.append(EventType.ROWS, {"n": n}, universe="zepto") for n in range(20))
    )

    indices = [e.i for e in store.events]
    assert indices == list(range(1, 21))
    assert [e.i for e in read_events_file(store.path)] == indices


async def test_every_append_is_on_disk_immediately(store: EventStore) -> None:
    await store.append(EventType.RUN_REQUESTED, {"query": "amul butter"})
    lines = store.path.read_text(encoding="utf-8").strip().splitlines()

    assert len(lines) == 1
    assert json.loads(lines[0])["type"] == "run_requested"


async def test_since_resumes_after_last_event_id(store: EventStore) -> None:
    for n in range(6):
        await store.append(EventType.ROWS, {"n": n}, universe="zepto")

    assert [e.i for e in store.since(None)] == [1, 2, 3, 4, 5, 6]
    assert [e.i for e in store.since(4)] == [5, 6]
    assert store.since(6) == []


async def test_subscriber_gets_backlog_then_live(store: EventStore) -> None:
    await store.append(EventType.RUN_REQUESTED, {"query": "amul butter"})
    queue = store.subscribe()
    backlog = store.since(None)
    await store.append(EventType.ROWS, {"n": 7}, universe="zepto")

    live = await asyncio.wait_for(queue.get(), timeout=1.0)

    assert [e.i for e in backlog] == [1]
    assert live is not None and live.i == 2 and live.data == {"n": 7}


async def test_close_sends_a_sentinel(store: EventStore) -> None:
    queue = store.subscribe()
    await store.close()

    assert await asyncio.wait_for(queue.get(), timeout=1.0) is None
    assert store.closed


async def test_sse_frame_carries_index_as_event_id(store: EventStore) -> None:
    event = await store.append(EventType.ROWS, {"n": 3}, universe="zepto")
    frame = event.sse()

    assert frame.startswith("id: 1\nevent: rows\ndata: {")
    assert frame.endswith("\n\n")
    assert json.loads(frame.split("data: ", 1)[1])["data"] == {"n": 3}


async def test_replayed_events_keep_capture_time_and_are_labelled(tmp_path: Path) -> None:
    captured = EventStore("r_20260822_091230_ab12", tmp_path / "src")
    original = await captured.append(EventType.ROWS, {"n": 3}, universe="zepto")

    replayed_store = EventStore("rp_20260822_101010_cd34", tmp_path / "rp", replay=True)
    replayed = await replayed_store.append_replayed(original)

    assert original.replay is False
    assert replayed.replay is True
    assert replayed.ts == original.ts  # a replay never invents a fresh timestamp
    assert replayed.data == original.data
    assert replayed.i == 1


def test_parse_last_event_id_rejects_junk() -> None:
    assert parse_last_event_id("12") == 12
    assert parse_last_event_id(" 3 ") == 3
    assert parse_last_event_id(None) is None
    assert parse_last_event_id("abc") is None
    assert parse_last_event_id("-1") is None


def test_read_events_file_skips_a_torn_trailing_line(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    good = Event(
        i=1, ts="2026-08-22T09:12:33.412Z", run_id="r_20260822_091230_ab12",
        universe="zepto", type=EventType.ROWS, data={"n": 3},
    )
    path.write_text(good.to_jsonl() + '{"i": 2, "ts": "2026-08', encoding="utf-8")

    events = read_events_file(path)

    assert [e.i for e in events] == [1]


async def test_everything_appended_before_close_is_still_recoverable_after_it(
    tmp_path,
) -> None:
    """What the SSE tail drain in `app.py` depends on.

    A subscriber that reaches the close sentinel must be able to pick up
    everything it has not seen yet, because `done` is the LAST event a run emits:
    returning on the sentinel without draining lets a client see the whole run
    except the fact that it ended.
    """
    store = EventStore("r_20260823_000000_aaaa", tmp_path / "run")
    queue = store.subscribe()

    await store.append(EventType.RUN_REQUESTED, {})
    seen = (await queue.get()).i  # the subscriber has consumed exactly one event
    await store.append(EventType.ROWS, {"n": 3})
    await store.append(EventType.DONE, {"rows_total": 3})
    await store.close()

    # Whatever else is on the queue, `since(seen)` is the complete tail.
    tail = store.since(seen)

    assert [e.type for e in tail] == [EventType.ROWS, EventType.DONE]
    assert [e.i for e in tail] == [2, 3]
    assert store.closed


async def test_the_close_sentinel_is_the_last_thing_a_subscriber_receives(tmp_path) -> None:
    """The invariant that makes the drain a belt-and-braces measure rather than a
    load-bearing one: appends fan out under the store lock, so the sentinel can
    never overtake an event that was already appended."""
    store = EventStore("r_20260823_000000_bbbb", tmp_path / "run")
    queue = store.subscribe()

    for _ in range(3):
        await store.append(EventType.PROGRESS, {})
    await store.close()

    received = [queue.get_nowait() for _ in range(4)]

    assert [e.i for e in received[:3]] == [1, 2, 3]
    assert received[-1] is None
