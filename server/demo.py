"""The curated demo list: which captures anyone may open without running one.

A demo run used to be a single id in the environment (`SVERSE_DEMO_RUN_ID`).
That is one story, and the app now has several worth telling - a four-item cart,
a universe broken and healed, a run where the watchdog rescued a stalled job -
so the list moved into a JSON file an operator edits on the deployment.

The rule that matters: EVERY run id named in this file is public. It is readable
and replayable by anyone, whoever captured it. So the file is treated as
operator input rather than as data to be trusted blindly - an entry naming a run
that is not on disk, or shaped wrongly, is logged and skipped. A demo list is
the one thing that must not be able to 500 the endpoint every visitor hits
first.

The legacy single id still works and is appended if it is not already named.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

log = logging.getLogger("scrapeverse.demo")

DemoKind = Literal["run", "cart", "story"]

# What the legacy `SVERSE_DEMO_RUN_ID` becomes when it is not already listed.
LEGACY_ID = "demo"
LEGACY_NOTE = "captured on the server"


class DemoEntry(BaseModel):
    """One thing a visitor can open from the demo menu.

    `run_ids` is the whole payload: a cart is its items' runs in item order, a
    story is its chapters' runs in order. Nothing here holds rows or events; the
    ordinary run endpoints serve those, which is what keeps a demo entry from
    becoming a second way to read a run.
    """

    id: str
    title: str
    note: str = ""
    kind: DemoKind
    run_ids: list[str] = Field(default_factory=list)
    # Cart only: what was searched for, in the same order as `run_ids`.
    items: list[str] | None = None
    # Story only: one label per chapter, in the same order as `run_ids`.
    chapters: list[str] | None = None


def _shape_error(entry: DemoEntry) -> str | None:
    """Why this entry cannot be shown, or None if it can.

    Every kind is checked against its own arity, because the frontend zips the
    parallel lists: a cart with three runs and two item names would draw a card
    with a missing label rather than refusing to draw.
    """
    if not entry.id.strip() or not entry.title.strip():
        return "id and title are both required"
    if not entry.run_ids:
        return "no run_ids"
    if entry.kind == "run" and len(entry.run_ids) != 1:
        return f"kind 'run' takes exactly one run id, got {len(entry.run_ids)}"
    if entry.kind == "cart" and len(entry.items or []) != len(entry.run_ids):
        return "kind 'cart' needs one item name per run id"
    if entry.kind == "story" and len(entry.chapters or []) != len(entry.run_ids):
        return "kind 'story' needs one chapter label per run id"
    return None


def parse_entries(
    raw: Any,
    query_of: Callable[[str], str | None],
) -> list[DemoEntry]:
    """Validate a decoded demo file. Bad entries are logged and dropped.

    `query_of` returns a stored run's query, or None when there is no such run.
    It is how an entry is checked against reality: a list naming a capture that
    was never copied to the deployment would otherwise offer the judges a button
    that 404s.
    """
    if not isinstance(raw, list):
        log.warning("demo list: expected a JSON array, got %s", type(raw).__name__)
        return []

    out: list[DemoEntry] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        try:
            entry = DemoEntry.model_validate(item)
        except Exception as exc:
            log.warning("demo list: entry %d is not a demo entry: %s", index, exc)
            continue
        problem = _shape_error(entry)
        if problem is None:
            missing = [run_id for run_id in entry.run_ids if query_of(run_id) is None]
            if missing:
                problem = f"no stored run {missing}"
        if problem is None and entry.id in seen:
            problem = "duplicate id"
        if problem is not None:
            log.warning("demo list: entry %r skipped: %s", entry.id, problem)
            continue
        seen.add(entry.id)
        out.append(entry)
    return out


def load_demo_entries(
    path: Path,
    query_of: Callable[[str], str | None],
    legacy_run_id: str = "",
) -> list[DemoEntry]:
    """The demo list as the API reports it. A missing file is an empty list.

    Read fresh on every call on purpose: an operator adding a capture to the
    deployment edits this file, and having to restart the server to publish it
    is exactly the sort of step that gets skipped ten minutes before a demo.
    """
    raw: Any = []
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("demo list: %s could not be read: %s", path, exc)
            return _with_legacy([], query_of, legacy_run_id)
    return _with_legacy(parse_entries(raw, query_of), query_of, legacy_run_id)


def _with_legacy(
    entries: list[DemoEntry],
    query_of: Callable[[str], str | None],
    legacy_run_id: str,
) -> list[DemoEntry]:
    """Append the single-id demo run, unless the file already names it.

    Kept working because a deployment configured before the list existed still
    has it set, and a demo that quietly stops appearing is worse than one extra
    branch here.
    """
    if not legacy_run_id:
        return entries
    if any(legacy_run_id in entry.run_ids for entry in entries):
        return entries
    title = query_of(legacy_run_id)
    if title is None:
        log.warning("demo list: SVERSE_DEMO_RUN_ID names %r, which is not stored", legacy_run_id)
        return entries
    return [
        *entries,
        DemoEntry(
            id=LEGACY_ID,
            title=title,
            note=LEGACY_NOTE,
            kind="run",
            run_ids=[legacy_run_id],
        ),
    ]


def public_run_ids(entries: list[DemoEntry], legacy_run_id: str = "") -> frozenset[str]:
    """Every run id the demo list makes public.

    The legacy id is included whether or not its run is on disk: it was public
    before this file existed, and a run that is missing is a 404 anyway.
    """
    ids = {run_id for entry in entries for run_id in entry.run_ids}
    if legacy_run_id:
        ids.add(legacy_run_id)
    return frozenset(ids)
