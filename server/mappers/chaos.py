"""Chaos mapper: the flattened collector-row contract, nothing else.

The chaos universe is the reliability universe. It points at the store this app
serves itself (`server/chaos_store.py`), whose markup can be changed on command
so a collector can be broken and repaired while somebody watches. Its collector
emits the same 18-field row as the three live ones, so the mapping is the shared
one in `mappers/collector_rows.py`.

There is no second input shape here. Zepto, Blinkit and Instamart each keep a
raw-search-response branch because a saved capture of the site's own API exists
for them; the chaos store has no JSON API to capture. A payload that is not
collector rows yields no rows, which `runs.py` reports as `zero_rows{broken}`:
the collector came back in a shape this app does not recognise, which is exactly
what a break looks like.
"""

from __future__ import annotations

from typing import Any

from ..resolve import NormalizedRow
from . import as_records
from .collector_rows import is_collector_row, map_collector_rows


def map_chaos(payload: Any) -> list[NormalizedRow]:
    rows = [record for record in as_records(payload) if is_collector_row(record)]
    return map_collector_rows(rows, "chaos") if rows else []
