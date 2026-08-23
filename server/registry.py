"""Universe registry — DESIGN.md §2.

A new universe is a new row here plus a mapper. Collector ids are NEVER
hardcoded: they are read from the environment at request time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .config import Settings
from .mappers import has_mapper, mapper_is_stub

Badge = Literal["live", "chaos", "replay-only"]
TriggerMode = Literal["batch", "realtime"]


class Universe(BaseModel):
    id: str
    display: str
    color: str
    badge: Badge = "live"
    collector_id: str = ""
    # The EFFECTIVE version for this universe, filled in by `universes()`: the
    # per-universe override if one is set, otherwise the global default. Every
    # consumer — the API, the trigger, the events — reads it from here, so they
    # cannot drift apart.
    collector_version: str = "dev"
    trigger_mode: TriggerMode = "batch"
    mapper: str

    # Derived, filled in by `universes()`.
    wired: bool = Field(default=False, description="collector id present in env")
    dispatchable: bool = Field(default=False, description="may take part in a run now")
    status: str = Field(default="not wired", description="human reason for the UI")


_ROWS: tuple[Universe, ...] = (
    Universe(id="zepto", display="Zepto-verse", color="#6b21a8", badge="live", mapper="zepto"),
    Universe(id="blinkit", display="Blinkit-verse", color="#f0b100", badge="live", mapper="blinkit"),
    Universe(
        id="instamart", display="Instamart-verse", color="#eb5b00", badge="live", mapper="instamart"
    ),
    Universe(id="chaos", display="Chaos-verse", color="#155dfc", badge="chaos", mapper="chaos"),
)


def universes(settings: Settings) -> list[Universe]:
    """Registry rows with env-derived collector id, version and wiring status."""
    out: list[Universe] = []
    for row in _ROWS:
        collector_id = settings.collector_ids.get(row.id, "")
        wired = bool(collector_id)
        mapper_ready = has_mapper(row.mapper) and not mapper_is_stub(row.mapper)

        if settings.is_live:
            # Live mode is gated purely on the collector id, per §2.
            dispatchable = wired and mapper_ready
            if not wired:
                status = "not wired"
            elif not mapper_ready:
                status = "no mapper yet"
            else:
                status = "wired"
        else:
            # Mock mode has no collector to call; it is gated on having a fixture
            # and a real mapper instead. Wiring status is still reported honestly.
            has_fixture = (settings.fixtures_dir / f"{row.id}_collector_result.json").is_file()
            dispatchable = mapper_ready and has_fixture
            status = "mock" if dispatchable else ("no mapper yet" if not mapper_ready else "no fixture")

        out.append(
            row.model_copy(
                update={
                    "collector_id": "",  # never leaves the process
                    "collector_version": settings.collector_version_for(row.id),
                    "wired": wired,
                    "dispatchable": dispatchable,
                    "status": status,
                }
            )
        )
    return out


def dispatchable(settings: Settings) -> list[Universe]:
    return [u for u in universes(settings) if u.dispatchable]


def listed(settings: Settings) -> list[Universe]:
    """What `/api/universes` shows — every universe that could actually run.

    A universe whose mapper is a stub cannot be dispatched in ANY mode and never
    will be until someone writes the mapper. Showing it put a permanently dead
    "Chaos-verse — no mapper yet" chip in the UI, which reads as a broken feature
    rather than as an unbuilt one. The registry row stays: it is the record of
    the shape a fourth universe would take, and it is still what
    `test_unimplemented_mappers_refuse_rather_than_return_empty` holds to
    refusing rather than reporting empty rows.

    A universe with a real mapper is always listed, even unwired — "not wired"
    is honest status a reader can act on.
    """
    return [u for u in universes(settings) if not mapper_is_stub(u.mapper)]


def collector_id_for(settings: Settings, universe_id: str) -> str:
    """Kept out of the serialised model on purpose — server-side use only."""
    return settings.collector_ids.get(universe_id, "")
