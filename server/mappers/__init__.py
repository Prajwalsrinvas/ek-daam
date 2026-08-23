"""Mapper registry — DESIGN.md §6.

A mapper turns one collector *result record* into `NormalizedRow`s. It is a pure
function: same payload in, same rows out, no network, no clock, no LLM.

Collector result contract
-------------------------
`GET /dca/dataset?id=<collection_id>` returns a JSON array of records whose
fields are whatever output schema we defined for the collector in Scraper
Studio. Our collectors emit one record per input, shaped:

    {
      "universe": "zepto",
      "query": "amul butter",
      "pincode": "560001",
      "resolved_area": "Bengaluru - 560001, Karnataka",
      "eta_minutes": 12,
      "screenshot_url": "https://<bright-data-hosted>/....png",
      "captured_at": "2026-08-22T09:12:33.412Z",
      "search_response": { ...the site's own search API response, verbatim... }
    }

Mappers accept the whole record, a bare `search_response`, or the array — see
`unwrap_record`. That tolerance is deliberate: the wrapper is ours to change,
the site payload is not.

The published collectors emit a different shape: ONE RECORD PER PRODUCT, already
flattened and already in rupees. That 18-field contract is shared by every
universe and lives in `server/mappers/collector_rows.py`; each mapper detects
which shape it was handed and takes both.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from ..resolve import NormalizedRow


class Mapper(Protocol):
    def __call__(self, payload: Any) -> list[NormalizedRow]: ...


class MapperNotImplemented(NotImplementedError):
    """Raised by a stub mapper. Surfaces as `failed {error}` for one universe."""


def as_records(payload: Any) -> list[dict[str, Any]]:
    """Whatever `GET /dca/dataset` handed us -> a list of record dicts.

    A dataset is a JSON array; a single record and junk are both tolerated so
    callers never have to type-check the payload themselves.
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def unwrap_record(payload: Any) -> dict[str, Any]:
    """Collector array / wrapper record / bare site payload -> the record dict."""
    records = as_records(payload)
    return records[0] if records else {}


# Fields a collector record may carry a page capture under. `screenshot_url` is
# our own wrapper convention; `serp_screenshot` is the published collector's.
SCREENSHOT_FIELDS = ("screenshot_url", "screenshot", "serp_screenshot")

# `serp_screenshot` may be a plain URL string or a Bright-Data file object.
# These are the keys a file object plausibly carries the URL under; the
# extractor tries them in order and falls back to any URL-shaped string value.
_URL_KEYS = ("url", "href", "src", "link", "file", "path", "download_url", "public_url")


def _is_bd_file_object(value: dict[str, Any]) -> bool:
    """A Bright Data delivered-media reference, e.g. `{"__type__": "file", ...}`."""
    return str(value.get("__type__", "")).lower() == "file"


def extract_screenshot_url(value: Any, _depth: int = 0) -> str | None:
    """Pull an http(s) URL out of a string, a file object, or a list of either.

    Deliberately defensive: guessing wrong should mean "no screenshot", never a
    crashed universe — and never a DIFFERENT page served as if it were the
    capture.

    CONFIRMED against the first Instamart dataset: inside a Bright Data file
    object, `url` is the address of the page that was PHOTOGRAPHED, not a
    download link for the PNG (the object also carries `type: "screenshot"` and
    a `filename`, and the media itself is delivered out of band). Following it
    would have fetched the live search page's HTML and stored those bytes as
    `serp.png` — a fabricated artifact, and an unlogged request to the scraped
    site from our own server. So a recognised BD file object yields nothing.
    Zepto and Blinkit land the same way because their file reference is a bare,
    non-URL string.

    ONE TRUE SENTENCE for all three: Bright Data captures a SERP screenshot per
    run; it is not deliverable via API download, so the app shows none. The live
    client no longer attempts any download at all — this extractor stays as the
    shape check that decides whether a capture EXISTS, which is what
    `artifact_failed` reports.
    """
    if isinstance(value, str):
        text = value.strip()
        return text if text.startswith(("http://", "https://")) else None
    if isinstance(value, list):
        for item in value:
            found = extract_screenshot_url(item, _depth + 1)
            if found:
                return found
        return None
    if isinstance(value, dict) and _is_bd_file_object(value):
        return None
    if isinstance(value, dict) and _depth < 4:
        for key in _URL_KEYS:
            found = extract_screenshot_url(value.get(key), _depth + 1)
            if found:
                return found
        # Nothing under a known key: take any URL nested anywhere inside. The
        # search is confined to the value of a screenshot field, so an unrelated
        # URL cannot wander in from elsewhere in the record.
        for nested in value.values():
            found = extract_screenshot_url(nested, _depth + 1)
            if found:
                return found
    return None


def screenshot_record(payload: Any) -> dict[str, Any]:
    """A record shaped the way `BDClient.fetch_screenshot` expects.

    Per-product dataset rows carry the SERP capture on the rows themselves, and
    not necessarily on the first one, so the URL is taken from the first record
    that has one. The single-wrapper-record convention keeps working unchanged.
    """
    records = as_records(payload)
    base = records[0] if records else {}
    for record in records:
        for field in SCREENSHOT_FIELDS:
            url = extract_screenshot_url(record.get(field))
            if url:
                return {**base, "screenshot_url": url}
    return base


def has_screenshot_reference(payload: Any) -> bool:
    """Did the collector report a page capture at all?

    True means Bright Data holds a SERP screenshot for this run — as a file
    reference string or a file object — and we cannot download it. That is a
    different fact from "no capture was taken", and `runs.py` reports it as
    `artifact_failed` so the gap is visible rather than silent.
    """
    for record in as_records(payload):
        for field in SCREENSHOT_FIELDS:
            value = record.get(field)
            if isinstance(value, (str, bytes)):
                if value.strip() if isinstance(value, str) else value:
                    return True
            elif isinstance(value, (dict, list)) and value:
                return True
    return False


def site_payload(record: dict[str, Any], marker: str) -> dict[str, Any]:
    """Pull the site's own response out of the record.

    `marker` is a key that must exist on the site payload (e.g. "layout" for
    Zepto), which lets us tell a wrapper apart from a bare site response without
    guessing.
    """
    if marker in record:
        return record
    nested = record.get("search_response")
    if isinstance(nested, dict):
        return nested
    # Last resort: a single dict-valued key that carries the marker.
    for value in record.values():
        if isinstance(value, dict) and marker in value:
            return value
    return {}


def _stub(universe: str) -> Callable[[Any], list[NormalizedRow]]:
    def _raise(payload: Any) -> list[NormalizedRow]:
        raise MapperNotImplemented(
            f"{universe} mapper is not implemented yet — no rows can be reported for it"
        )

    _raise.__name__ = f"{universe}_stub"
    _raise.is_stub = True  # type: ignore[attr-defined]
    return _raise


from .zepto import map_zepto  # noqa: E402  (registry needs the concrete mappers)
from .blinkit import map_blinkit  # noqa: E402
from .instamart import map_instamart  # noqa: E402

MAPPERS: dict[str, Mapper] = {
    "zepto": map_zepto,
    "blinkit": map_blinkit,
    "instamart": map_instamart,
    "chaos": _stub("chaos"),
}


def has_mapper(name: str) -> bool:
    return name in MAPPERS


def mapper_is_stub(name: str) -> bool:
    return bool(getattr(MAPPERS.get(name), "is_stub", False))


def get_mapper(name: str) -> Mapper:
    if name not in MAPPERS:
        raise KeyError(f"no mapper registered for {name!r}")
    return MAPPERS[name]


__all__ = [
    "MAPPERS",
    "Mapper",
    "MapperNotImplemented",
    "as_records",
    "extract_screenshot_url",
    "get_mapper",
    "has_mapper",
    "has_screenshot_reference",
    "mapper_is_stub",
    "screenshot_record",
    "site_payload",
    "unwrap_record",
]
