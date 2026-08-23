"""Anonymous run ownership.

THIS IS NOT AUTHENTICATION. There is no account, no password and no identity
behind it. Every visitor is handed one opaque random cookie the first time they
touch the app, and a run remembers only the SHA-256 of whatever cookie created
it. That is enough to stop a public demo from showing one visitor's searches to
the next one, and it is not enough for anything else:

  * clearing cookies makes a new identity, and the old runs become unreachable
    to that browser (they are not deleted, they are simply owned by a cookie
    nobody holds any more);
  * anyone who copies the cookie value out of a browser has that identity;
  * runs captured before this existed carry no owner and belong to nobody.

The raw cookie value is never written to disk and never logged. Only its hash
reaches `meta.json`, so a leaked runs directory does not hand out identities.
"""

from __future__ import annotations

import hashlib
import re
import secrets

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

COOKIE_NAME = "ekdaam_owner"

# One year. A demo visitor should still recognise their own runs the next day,
# and there is nothing behind the cookie worth expiring sooner.
COOKIE_MAX_AGE_S = 365 * 24 * 60 * 60

# 32 bytes of urlsafe base64 is 43 characters. Anything outside this shape was
# not issued by us, so it is replaced rather than trusted: it costs one cookie
# and it keeps junk out of the owner hash.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")

# Where the request's owner hash lives for the rest of the request. Read through
# `owner_of` rather than poked at directly.
SCOPE_KEY = "owner_hash"


def hash_token(raw: str) -> str:
    """The only form of the cookie that is ever stored or compared."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def owner_of(request) -> str:
    """The requester's owner hash. Always a string: the middleware runs on every
    request, so an empty value means the middleware was not installed, which is
    only possible in a test that builds the app by hand."""
    state = request.scope.get("state") or {}
    return state.get(SCOPE_KEY, "")


def owns(meta, owner_hash: str, demo_run_id: str = "") -> bool:
    """Whether this requester may see this run.

    The demo run is public BY DESIGN: it is the one capture the judges can open
    without having run anything themselves. Everything else needs a matching
    owner hash, and a run with no owner (captured before ownership existed)
    matches nobody.
    """
    if demo_run_id and meta.run_id == demo_run_id:
        return True
    stored = getattr(meta, "owner_hash", None)
    if not stored or not owner_hash:
        return False
    return secrets.compare_digest(stored, owner_hash)


def _secure_flag(scope: Scope, headers: Headers, override: bool | None) -> bool:
    """Whether to mark the cookie Secure.

    Auto by default, because the app runs behind Caddy in production and plain
    http in development: marking the cookie Secure on http means the browser
    never sends it back and every request looks like a new visitor. Caddy
    terminates TLS, so the app's own scheme is http even in production and
    `X-Forwarded-Proto` is the only honest signal there is. The env override
    exists for a proxy that sets neither.
    """
    if override is not None:
        return override
    forwarded = headers.get("x-forwarded-proto", "")
    if forwarded:
        # A proxy chain can send a list; the first hop is the client-facing one.
        return forwarded.split(",")[0].strip().lower() == "https"
    return scope.get("scheme") == "https"


def _cookie_header(value: str, secure: bool) -> str:
    """Serialised through Starlette's own `set_cookie` rather than by hand."""
    carrier = Response()
    carrier.set_cookie(
        COOKIE_NAME,
        value,
        max_age=COOKIE_MAX_AGE_S,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    return carrier.headers["set-cookie"]


class OwnerCookieMiddleware:
    """Issues the owner cookie and puts its hash on the request scope.

    Pure ASGI rather than `BaseHTTPMiddleware` on purpose: the events endpoint is
    a long-lived SSE stream that asks whether the client has disconnected, and
    `BaseHTTPMiddleware` sits between that stream and the receive channel.
    """

    def __init__(self, app: ASGIApp, cookie_secure: bool | None = None) -> None:
        self.app = app
        self.cookie_secure = cookie_secure

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        raw = _read_cookie(headers)
        issue = raw is None
        if raw is None:
            raw = new_token()

        scope.setdefault("state", {})[SCOPE_KEY] = hash_token(raw)

        if not issue:
            await self.app(scope, receive, send)
            return

        cookie = _cookie_header(raw, _secure_flag(scope, headers, self.cookie_secure))
        # Deliberately not logged, not even truncated: the raw value IS the
        # identity, and a log line is the easiest place to leak one from.
        del raw

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("set-cookie", cookie)
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def _read_cookie(headers: Headers) -> str | None:
    """The cookie header parsed just far enough to find one name.

    A value that does not have the shape we issue is treated as absent, so a
    stale or hand-edited cookie becomes a fresh identity rather than a strange
    one.
    """
    from http.cookies import SimpleCookie

    raw_header = headers.get("cookie")
    if not raw_header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(raw_header)
    except Exception:
        return None
    morsel = jar.get(COOKIE_NAME)
    if morsel is None:
        return None
    value = morsel.value
    return value if _TOKEN_RE.match(value) else None
