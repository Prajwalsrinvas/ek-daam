"""Bright Data Scraper Studio client — DESIGN.md §4.

Endpoint shapes below are taken from the Bright Data docs, not guessed:

  POST /dca/trigger?collector=<c_id>[&version=dev][&queue_next=1]
       body: JSON ARRAY of input objects matching the collector input schema
       200 -> {"collection_id": "j_...", "start_eta": "<iso8601>"}

  GET  /dca/log/{job_id}
       200 -> {"id","status","collector","template","inputs","lines","fails",
               "pages","pages_left","success","created","started","finished",
               "success_rate","job_time","queue_time"}
       status in: building | running | done | failed | cancelled

  GET  /dca/dataset?id=<collection_id>
       202 -> {"status":"building","message":"Dataset is not ready yet, ..."}
       200 -> JSON array of result records (our collector's output schema)

`LiveClient` and `MockClient` implement the same three calls plus the screenshot
fetch, so `runs.py` is byte-for-byte identical in both modes. Flipping BD_MODE
requires zero code changes, which is the whole point of the seam.
"""

from __future__ import annotations

import asyncio
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import Settings

BUILDING_STATUSES = frozenset({"building", "running", "collecting", "queued", "pending"})
FAILED_STATUSES = frozenset({"failed", "cancelled", "canceled", "error"})

# Routing hints that belong to us, not to the collector. The collector input
# schema is strictly keyword/pincode with strict_input_normalize, so an extra
# key makes /dca/trigger reject the whole batch with 422. MockClient still reads
# `universe` — it is how a fixture gets selected — so the strip happens here on
# the live path only, not at the call site in runs.py.
LOCAL_INPUT_FIELDS = frozenset({"universe"})


class BDError(RuntimeError):
    """Anything the Bright Data API told us it could not do."""


@dataclass(frozen=True)
class JobLog:
    """The subset of GET /dca/log/{job_id} the app actually observes."""

    job_id: str
    status: str
    pages: int | None = None
    pages_left: int | None = None
    lines: int | None = None
    fails: int | None = None
    success: int | None = None
    template: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def finished(self) -> bool:
        return self.status not in BUILDING_STATUSES

    @property
    def failed(self) -> bool:
        return self.status in FAILED_STATUSES

    @classmethod
    def from_payload(cls, job_id: str, payload: dict[str, Any]) -> "JobLog":
        def _int(key: str) -> int | None:
            value = payload.get(key)
            return int(value) if isinstance(value, (int, float)) else None

        return cls(
            job_id=str(payload.get("id") or job_id),
            status=str(payload.get("status") or "running").lower(),
            pages=_int("pages"),
            pages_left=_int("pages_left"),
            lines=_int("lines"),
            fails=_int("fails"),
            success=_int("success"),
            template=str(payload["template"]) if payload.get("template") else None,
            raw=payload,
        )


class BDClient(Protocol):
    mode: str

    async def trigger(self, collector_id: str, inputs: list[dict[str, Any]], version: str) -> str: ...

    async def job_log(self, job_id: str) -> JobLog: ...

    async def fetch_results(self, job_id: str) -> list[dict[str, Any]] | None: ...

    async def fetch_screenshot(self, record: dict[str, Any], universe_id: str) -> bytes | None: ...

    async def aclose(self) -> None: ...


# --------------------------------------------------------------------------
# live
# --------------------------------------------------------------------------
class LiveClient:
    mode = "live"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not settings.bd_api_key:
            raise BDError("BD_MODE=live but BD_API_KEY is empty")
        self._settings = settings
        timeout = httpx.Timeout(30.0, connect=10.0)
        self._client = httpx.AsyncClient(
            base_url=settings.bd_base_url,
            headers={
                "Authorization": f"Bearer {settings.bd_api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )

    async def trigger(self, collector_id: str, inputs: list[dict[str, Any]], version: str) -> str:
        if not collector_id:
            raise BDError("no collector id configured for this universe")
        params: dict[str, Any] = {"collector": collector_id, "queue_next": 1}
        if version == "dev":
            params["version"] = "dev"

        body = [
            {key: value for key, value in item.items() if key not in LOCAL_INPUT_FIELDS}
            for item in inputs
        ]
        response = await self._client.post("/dca/trigger", params=params, json=body)
        if response.status_code >= 400:
            raise BDError(f"trigger failed: HTTP {response.status_code} {_terse(response.text)}")
        payload = response.json()
        job_id = payload.get("collection_id") if isinstance(payload, dict) else None
        if not job_id:
            raise BDError(f"trigger returned no collection_id: {_terse(response.text)}")
        return str(job_id)

    async def job_log(self, job_id: str) -> JobLog:
        response = await self._client.get(f"/dca/log/{job_id}")
        if response.status_code >= 400:
            raise BDError(f"job log failed: HTTP {response.status_code} {_terse(response.text)}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise BDError("job log returned an unexpected body")
        return JobLog.from_payload(job_id, payload)

    async def fetch_results(self, job_id: str) -> list[dict[str, Any]] | None:
        """None means "dataset still building" (HTTP 202), not "no rows"."""
        response = await self._client.get("/dca/dataset", params={"id": job_id})
        if response.status_code == 202:
            return None
        if response.status_code >= 400:
            raise BDError(f"dataset fetch failed: HTTP {response.status_code} {_terse(response.text)}")
        try:
            payload = response.json()
        except ValueError:
            # Real deliveries arrive as JSONL (one record per line), not a JSON
            # array — confirmed against the first production dataset.
            records: list[dict[str, Any]] = []
            for line in response.text.splitlines():
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)  # a malformed line is a real error — raise
                if not isinstance(record, dict):
                    raise BDError("dataset JSONL line is not an object")
                records.append(record)
            return records
        if isinstance(payload, dict):
            # A status object can also come back with 200 on some edge cases.
            if str(payload.get("status", "")).lower() in BUILDING_STATUSES:
                return None
            return [payload]
        if isinstance(payload, list):
            return payload
        raise BDError("dataset fetch returned an unexpected body")

    async def fetch_screenshot(self, record: dict[str, Any], universe_id: str) -> bytes | None:
        """Always None. Bright Data does not deliver collector media over the API.

        Every collector captures a SERP screenshot, and every one of them arrives
        as a file REFERENCE — `<job>.<hash>.file_<id>.serp_screenshot.png` for
        Zepto and Blinkit, a file OBJECT for Instamart whose `url` is the address
        of the page that was PHOTOGRAPHED rather than a download link. Neither is
        fetchable.

        There used to be a download path here for any http(s) URL found in the
        record. Nothing has ever produced one, so it fetched nothing — while
        being an unauthenticated GET, from inside our own server, to a URL taken
        out of scraped third-party data. It is deleted rather than guarded:
        removing the request is the only fix that cannot be got wrong, and the
        capability bought us nothing to weigh against it.

        `runs.py` turns this into one `artifact_failed` per universe, which is
        non-terminal — the rows still stand.
        """
        return None

    async def aclose(self) -> None:
        await self._client.aclose()


def _terse(text: str, limit: int = 80) -> str:
    """A Bright Data error body, made fit to travel in an event.

    Newlines collapsed — an event's `error` is one line in the feed, and an HTML
    error page pasted into it wrapped the whole run log. Truncated hard: the HTTP
    status is the fact worth keeping, and 80 characters of body is enough to tell
    an input-schema 422 from a quota 402 without pasting a stack trace into the
    UI. `runs/<id>/raw/` has the full story either way.
    """
    return " ".join(text.split())[:limit]


# --------------------------------------------------------------------------
# mock
# --------------------------------------------------------------------------
_PLACEHOLDER_COLORS = {
    "zepto": (107, 33, 168),
    "blinkit": (240, 177, 0),
    "instamart": (235, 91, 0),
    "chaos": (21, 93, 252),
}


def placeholder_png(rgb: tuple[int, int, int], width: int = 320, height: int = 200) -> bytes:
    """A synthetic, obviously-not-a-real-screenshot PNG for mock runs.

    Diagonal stripes so nobody can mistake it for a captured page. Written by
    hand to avoid pulling an imaging dependency in for one placeholder.
    """
    r, g, b = rgb
    dark = (max(r - 40, 0), max(g - 40, 0), max(b - 40, 0))
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter type 0 for this scanline
        for x in range(width):
            rows.extend(dark if ((x + y) // 12) % 2 else (r, g, b))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )


class MockClient:
    """Deterministic, no network. Drives the same lifecycle off fixture JSON.

    Poll 1 -> pages_left 2 (running)
    Poll 2 -> pages_left 1 (running)
    Poll 3 -> pages_left 0 (done), dataset available

    Mock is a dev tool. `runs.py` tags every mock run so the UI can never let it
    pass as a live capture.
    """

    mode = "mock"
    TOTAL_PAGES = 3

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._polls: dict[str, int] = {}
        self._universe_for_job: dict[str, str] = {}
        self._counter = 0

    def _fixture_path(self, universe_id: str) -> Path:
        return self._settings.fixtures_dir / f"{universe_id}_collector_result.json"

    async def _tick(self) -> None:
        delay = self._settings.mock_step_delay_s
        if delay > 0:
            await asyncio.sleep(delay)

    async def trigger(self, collector_id: str, inputs: list[dict[str, Any]], version: str) -> str:
        await self._tick()
        universe_id = str((inputs[0] if inputs else {}).get("universe") or "zepto")
        self._counter += 1
        job_id = f"j_mock_{universe_id}_{self._counter:04d}"
        self._polls[job_id] = 0
        self._universe_for_job[job_id] = universe_id
        return job_id

    async def job_log(self, job_id: str) -> JobLog:
        await self._tick()
        self._polls[job_id] = self._polls.get(job_id, 0) + 1
        polls = self._polls[job_id]
        pages_left = max(self.TOTAL_PAGES - polls, 0)
        done = pages_left == 0
        return JobLog(
            job_id=job_id,
            status="done" if done else "running",
            pages=self.TOTAL_PAGES,
            pages_left=pages_left,
            lines=30 if done else None,
            fails=0,
            success=1 if done else 0,
            template="t_mock.1",
            raw={"mock": True},
        )

    async def fetch_results(self, job_id: str) -> list[dict[str, Any]] | None:
        if self._polls.get(job_id, 0) < self.TOTAL_PAGES:
            return None
        universe_id = self._universe_for_job.get(job_id, "zepto")
        path = self._fixture_path(universe_id)
        if not path.is_file():
            raise BDError(f"no mock fixture for universe {universe_id!r} at {path.name}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else [payload]

    async def fetch_screenshot(self, record: dict[str, Any], universe_id: str) -> bytes | None:
        await self._tick()
        return placeholder_png(_PLACEHOLDER_COLORS.get(universe_id, (100, 100, 100)))

    async def aclose(self) -> None:
        return None


def build_client(settings: Settings) -> BDClient:
    return LiveClient(settings) if settings.is_live else MockClient(settings)
