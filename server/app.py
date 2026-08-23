"""FastAPI factory — DESIGN.md §1 and §7.

One process: REST + SSE + the built Vite frontend, no DB, no queue.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import logging
import re
import secrets as _secrets

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .bd_client import initials_png
from .chaos_store import (
    VERSIONS,
    ChaosStore,
    render_product_page,
    tile_for_image,
)
from .config import REPO_ROOT, Settings, get_settings
from .events import heartbeat_frame, parse_last_event_id
from .owner import OwnerCookieMiddleware, owner_of, owns
from .registry import listed
from .runs import RunManager, RunMeta, RunRejected, RunThrottled

log = logging.getLogger("scrapeverse.app")

HEARTBEAT_SECONDS = 15.0
WEB_DIST = REPO_ROOT / "web" / "dist"

# The chaos store is a live page, and a flipped version has to be visible on the
# next request or the demonstration proves nothing.
NO_STORE = {"Cache-Control": "no-store"}

CHAOS_TOKEN_HEADER = "X-Chaos-Token"

_CHAOS_IMAGE_RE = re.compile(r"^[A-Za-z0-9._-]{1,60}\.png$")


class RunRequest(BaseModel):
    """Outer bounds only, and no lower bound on purpose: the real rules —
    6-digit pincode, 2-60 printable characters of query — live in
    `RunManager._validate_request`, so even an empty field comes back as a 400
    with a sentence a person can act on rather than as a pydantic 422."""

    query: str = Field(max_length=200)
    pincode: str = Field(max_length=20)


class ChaosFlipRequest(BaseModel):
    """Which rendering to serve. Omitted means the next one in the list."""

    version: str | None = Field(default=None, max_length=20)


# What one custom-input row for the chaos collector may say. The collector reads
# exactly these two and nothing else, so anything more is either a typo or an
# attempt to push arbitrary payload through the heal endpoint.
HEAL_INPUT_KEYS = frozenset({"keyword", "pincode"})
HEAL_INPUT_MAX_ITEMS = 5
HEAL_INPUT_MAX_VALUE = 200


class ChaosHealRequest(BaseModel):
    """A heal is a token-gated admin call, and it is still the one place a caller
    hands Bright Data a payload of their own. The prompt is length-capped, and
    the custom input is capped in count and pinned to the two keys the collector
    actually reads, so nothing outside that shape can be forwarded."""

    prompt: str | None = Field(default=None, max_length=4000)
    custom_input: list[dict[str, Any]] | None = Field(default=None, max_length=HEAL_INPUT_MAX_ITEMS)

    @field_validator("custom_input")
    @classmethod
    def _known_keys_only(
        cls, value: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]] | None:
        if value is None:
            return value
        for item in value:
            unknown = set(item) - HEAL_INPUT_KEYS
            if unknown:
                raise ValueError(
                    f"custom_input accepts only {sorted(HEAL_INPUT_KEYS)}, "
                    f"got {sorted(unknown)}"
                )
            for key, entry in item.items():
                if not isinstance(entry, str):
                    raise ValueError(f"custom_input {key!r} must be a string")
                if len(entry) > HEAL_INPUT_MAX_VALUE:
                    raise ValueError(
                        f"custom_input {key!r} is longer than {HEAL_INPUT_MAX_VALUE} characters"
                    )
        return value


def _client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Built here rather than in the lifespan so the store answers even when an
    # app is constructed without one, which is how several tests build it.
    chaos = ChaosStore(settings.chaos_version)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.runs = RunManager(settings)
        app.state.chaos = chaos
        try:
            yield
        finally:
            await app.state.runs.shutdown()

    app = FastAPI(title="EkDaam", version="0.1.0", lifespan=lifespan)
    app.state.chaos = chaos
    # Every request, not only the API ones: the landing page is what a visitor
    # loads first, so issuing the cookie there means their first run already has
    # an owner. NOT authentication - see server/owner.py.
    app.add_middleware(OwnerCookieMiddleware, cookie_secure=settings.cookie_secure)

    def manager(request: Request) -> RunManager:
        return request.app.state.runs

    def public_meta(meta: RunMeta) -> dict[str, Any]:
        """A run's meta as the API reports it, with the owner hash removed.

        Nobody outside the process has any use for it: the server already decides
        what a requester may see, and the hash of the demo run's owner would
        otherwise be served to every visitor for nothing. It is not a credential
        - the cookie is what is compared, and the hash is not reversible - but a
        value with no reader is a value not worth publishing.
        """
        payload = meta.model_dump()
        payload.pop("owner_hash", None)
        return payload

    def mine_or_404(request: Request, run_id: str) -> RunMeta:
        """The one gate in front of every read of a stored run.

        404 rather than 403 on purpose: a 403 confirms that the run id exists,
        which is exactly the fact a stranger would be probing for. A run that is
        not yours is indistinguishable from a run that is not there.
        """
        meta = manager(request).load_meta(run_id)
        if meta is None or not owns(meta, owner_of(request), settings.demo_run_id):
            raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
        return meta

    def require_chaos_token(supplied: str | None) -> None:
        """The chaos admin gate: flipping the store and healing its collector.

        An unset token disables both, because there is no value a caller could
        send that would be accepted. Compared with `compare_digest` so a wrong
        token cannot be found one character at a time.
        """
        configured = settings.chaos_admin_token
        if not configured:
            raise HTTPException(
                status_code=503,
                detail="chaos admin is disabled: SVERSE_CHAOS_ADMIN_TOKEN is not set",
            )
        if not supplied or not _secrets.compare_digest(supplied, configured):
            raise HTTPException(
                status_code=401, detail=f"{CHAOS_TOKEN_HEADER} missing or incorrect"
            )

    def require_admin(request: Request) -> None:
        """The pacing in front of the chaos admin gate.

        Applied BEFORE the token is compared, so a wrong token spends an attempt
        too. `compare_digest` stops the token being read one character at a time;
        this is what stops it being guessed whole at network speed.
        """
        try:
            manager(request).check_admin_rate(_client_ip(request))
        except RunThrottled as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    # -- registry --------------------------------------------------------
    @app.get("/api/universes")
    async def get_universes() -> dict[str, Any]:
        # `listed`, not `universes`: a registry row with no mapper can never be
        # dispatched, and a permanently dead chip in the UI reads as a broken
        # feature rather than an unbuilt one.
        rows = listed(settings)
        return {
            "mode": settings.bd_mode,
            # The DEFAULT. A universe that sets SVERSE_COLLECTOR_VERSION_<ID>
            # reports its own effective version on its own row below, which is
            # the same value its trigger and its events carry.
            "collector_version": settings.collector_version,
            "universes": [u.model_dump() for u in rows],
            # No area list: any valid Indian pincode is accepted, so there is
            # nothing for the server to enumerate.
            "query_allowlist": list(settings.query_allowlist),
            # The one capture anyone may replay without having run anything.
            # Null when none is configured, and the UI hides the button then.
            "demo_run_id": settings.demo_run_id or None,
        }

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "mode": settings.bd_mode}

    # -- runs ------------------------------------------------------------
    @app.post("/api/runs", status_code=202)
    async def post_run(body: RunRequest, request: Request) -> dict[str, Any]:
        try:
            meta = manager(request).create_run(
                body.query, body.pincode, _client_ip(request), owner_of(request)
            )
        except RunThrottled as exc:
            # Nothing wrong with the request, just too soon. Must be caught
            # before RunRejected — it is a subclass.
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except RunRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"run_id": meta.run_id, "meta": public_meta(meta)}

    @app.get("/api/runs")
    async def list_runs(request: Request, limit: int = 25) -> dict[str, Any]:
        """THIS VISITOR'S runs only. A public demo whose listing showed every
        visitor's searches to the next one is a privacy leak, not a feature."""
        limit = max(1, min(limit, 100))
        runs = manager(request).list_runs(limit, owner_of(request))
        return {"runs": [public_meta(m) for m in runs]}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, request: Request) -> dict[str, Any]:
        runs = manager(request)
        meta = mine_or_404(request, run_id)
        events = runs.events_for(run_id)
        return {
            "meta": public_meta(meta),
            "events": [e.model_dump() for e in events],
            "comparison": runs.comparison_for(run_id).model_dump(),
        }

    @app.get("/api/runs/{run_id}/events")
    async def stream_events(
        run_id: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        runs = manager(request)
        mine_or_404(request, run_id)

        resume_from = parse_last_event_id(last_event_id)
        store = runs.get_store(run_id)

        async def generator() -> AsyncIterator[str]:
            if store is None:
                # Finished run, already off the in-memory broker: replay the file.
                for event in runs.events_for(run_id):
                    if resume_from is None or event.i > resume_from:
                        yield event.sse()
                return

            queue = store.subscribe()
            try:
                seen = 0
                for event in store.since(resume_from):
                    seen = max(seen, event.i)
                    yield event.sse()
                if store.closed:
                    return

                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        yield heartbeat_frame()
                        continue
                    if event is None:
                        # The store closed. The sentinel goes on the queue after
                        # the events that preceded it, but a slow consumer can
                        # still be several frames behind — returning here dropped
                        # them, and `done` is the last one, so a client could see
                        # the whole run except the fact that it ended.
                        for tail in store.since(seen):
                            seen = tail.i
                            yield tail.sse()
                        return
                    if event.i <= seen:
                        continue
                    seen = event.i
                    yield event.sse()
            finally:
                store.unsubscribe(queue)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/runs/{run_id}/artifacts/{name}")
    async def get_artifact(run_id: str, name: str, request: Request) -> FileResponse:
        # An artifact is part of the run's record, so it is scoped with it: a
        # screenshot names the store and the moment somebody searched.
        mine_or_404(request, run_id)
        path = manager(request).artifact_path(run_id, name)
        if path is None:
            raise HTTPException(status_code=404, detail="no such artifact")
        return FileResponse(path)

    @app.post("/api/replays/{run_id}", status_code=202)
    async def post_replay(run_id: str, request: Request) -> dict[str, Any]:
        # You can replay your own captures, and the demo run. Nothing else is
        # even admitted to exist.
        mine_or_404(request, run_id)
        try:
            meta = await manager(request).start_replay(
                run_id, _client_ip(request), owner_of(request)
            )
        except RunThrottled as exc:
            # Must be caught before RunRejected — it is a subclass.
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except RunRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"run_id": meta.run_id, "meta": public_meta(meta)}

    # -- the chaos store -------------------------------------------------
    # A grocery store this app serves itself, so the reliability story can be
    # demonstrated on demand: the same catalogue is rendered by two structurally
    # different markups, and which one is served is server-side state that only
    # the token holder can change. Registered before the frontend mount, which
    # claims "/".
    @app.get("/chaos", response_class=HTMLResponse)
    @app.get("/chaos/search", response_class=HTMLResponse)
    async def chaos_page(request: Request, q: str = "", pincode: str = "") -> HTMLResponse:
        store: ChaosStore = request.app.state.chaos
        return HTMLResponse(store.render(q, pincode), headers=NO_STORE)

    @app.get("/chaos/product/{product_id}", response_class=HTMLResponse)
    async def chaos_product(product_id: str, pincode: str = "") -> HTMLResponse:
        """One product's own page. Shared by both store versions on purpose: the
        redesign the demo turns on is the search page's, because the search page
        is what the collector reads."""
        page = render_product_page(product_id, pincode)
        if page is None:
            raise HTTPException(status_code=404, detail="no such product")
        return HTMLResponse(page, headers=NO_STORE)

    @app.get("/chaos/static/{name}")
    async def chaos_image(name: str) -> Response:
        """One flat tile per product: its own colour, its own initials. The store
        is fictional and has no product photography, so the tiles exist to give
        the page the shape of a real listing and to tell two listings apart -
        never to look like a photograph."""
        if not _CHAOS_IMAGE_RE.match(name):
            raise HTTPException(status_code=404, detail="no such image")
        rgb, initials = tile_for_image(name)
        return Response(content=initials_png(rgb, 96, 96, initials), media_type="image/png")

    @app.get("/api/chaos")
    async def chaos_state(request: Request) -> dict[str, Any]:
        store: ChaosStore = request.app.state.chaos
        return {**store.state(), "admin_enabled": bool(settings.chaos_admin_token)}

    @app.post("/api/chaos/flip")
    async def chaos_flip(
        body: ChaosFlipRequest,
        request: Request,
        token: str | None = Header(default=None, alias=CHAOS_TOKEN_HEADER),
    ) -> dict[str, Any]:
        require_admin(request)
        require_chaos_token(token)
        store: ChaosStore = request.app.state.chaos
        previous = store.version
        try:
            current = store.flip(body.version)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Visible on the server as well as in the response: a version change is
        # the event the whole demonstration turns on.
        log.info("chaos store version %s -> %s", previous, current)
        return {"previous": previous, "version": current, "versions": list(VERSIONS)}

    @app.post("/api/chaos/heal", status_code=202)
    async def chaos_heal(
        body: ChaosHealRequest,
        request: Request,
        token: str | None = Header(default=None, alias=CHAOS_TOKEN_HEADER),
    ) -> dict[str, Any]:
        """Run Bright Data self-healing against the chaos collector, as a run."""
        require_admin(request)
        require_chaos_token(token)
        try:
            meta = manager(request).start_heal(
                prompt=body.prompt,
                custom_input=body.custom_input,
                # The operator watches their own heal; it is not listed for
                # anyone else, the same as every other run.
                owner_hash=owner_of(request),
            )
        except RunThrottled as exc:
            # Must be caught before RunRejected - it is a subclass.
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except RunRejected as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"run_id": meta.run_id, "meta": public_meta(meta)}

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built Vite bundle at `/`. Absent build = an honest hint, not a
    500, so the API is still usable during development."""
    if WEB_DIST.is_dir() and (WEB_DIST / "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=WEB_DIST, html=True), name="web")
        return

    @app.get("/")
    async def no_frontend() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "frontend not built: run `npm --prefix web install && "
                "npm --prefix web run build`",
                "api": ["/api/universes", "/api/runs", "/api/health"],
            },
        )


class SPAStaticFiles(StaticFiles):
    """Static files with an index.html fallback for unknown paths."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except Exception:
            if path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


app = create_app()
