"""
app/security.py
===============
§21.3 — hardening for the INTERNET-EXPOSED deployment (owner decision 6.16 #5).

Two pieces:

1. ``CSRFMiddleware`` — double-submit-cookie CSRF protection. A non-HttpOnly
   ``jaks_csrf`` cookie is issued on first response; every state-changing request
   (POST/PUT/PATCH/DELETE) must echo that token back via the ``X-CSRF-Token``
   header (HTMX/fetch) OR a ``_csrf`` form field (native forms). This layers on
   top of the session cookie's existing SameSite=Lax (which already blocks the
   common cross-site-POST vector) for defense in depth.

   It is a PURE-ASGI middleware on purpose: it buffers and REPLAYS the request
   body so reading the ``_csrf`` form field never starves the downstream route
   (the well-known BaseHTTPMiddleware body-consumption trap).

2. ``security_headers_middleware`` — sets X-Frame-Options, X-Content-Type-Options,
   Referrer-Policy, and a Content-Security-Policy compatible with this app's
   self-hosted vendor JS + inline Alpine (needs 'unsafe-inline'/'unsafe-eval')
   and the Google-Fonts stylesheet/font origins.

Both honor the test bypass (``JAKS_SKIP_AUTH`` + in-memory engine) so the suite's
TestClient POSTs aren't forced to carry tokens.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from urllib.parse import parse_qs

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

CSRF_COOKIE = "jaks_csrf"
CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "_csrf"
CSRF_MAX_AGE = 60 * 60 * 12  # 12h, matches the session cookie

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
# These never require a token: login establishes the session; logout must never
# lock anyone out; static + the leadfinder API mirror the auth-middleware exempts.
_EXEMPT_EXACT = frozenset({"/login", "/logout"})
_EXEMPT_PREFIXES = ("/static/", "/api/leadfinder")
# Above this size we don't buffer the body to find a form field — require the
# header instead (guards against a memory-abuse POST of a huge body).
_MAX_BUFFER_BYTES = 12 * 1024 * 1024
_MULTIPART_CSRF_RE = re.compile(rb'name="' + CSRF_FIELD.encode() + rb'"\r?\n\r?\n([^\r\n]+)')


def _test_bypass() -> bool:
    """True only inside the test suite (JAKS_SKIP_AUTH + in-memory engine)."""
    if os.getenv("JAKS_SKIP_AUTH"):
        try:
            from app.deps import _is_test_env
            return _is_test_env()
        except Exception:  # noqa: BLE001
            return False
    return False


def secure_cookies(request: Request) -> bool:
    """Whether to set the Secure flag — honored for an HTTPS request (directly or
    behind a proxy that sets X-Forwarded-Proto) or when JAKS_SECURE_COOKIES=1.
    Defaults OFF so a plain-HTTP LAN run doesn't silently drop the login cookie."""
    if os.getenv("JAKS_SECURE_COOKIES") == "1":
        return True
    xf_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return xf_proto == "https" or request.url.scheme == "https"


def _is_exempt(path: str) -> bool:
    return path in _EXEMPT_EXACT or path.startswith(_EXEMPT_PREFIXES)


def _csrf_cookie_attrs(request: Request, token: str) -> str:
    attrs = (
        f"{CSRF_COOKIE}={token}; Path=/; Max-Age={CSRF_MAX_AGE}; SameSite=Lax"
    )
    if secure_cookies(request):
        attrs += "; Secure"
    return attrs


class CSRFMiddleware:
    """Pure-ASGI double-submit-cookie CSRF guard (see module docstring)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        cookie_token = request.cookies.get(CSRF_COOKIE)
        issue_token = None if cookie_token else secrets.token_urlsafe(32)

        async def send_wrapper(message: Message) -> None:
            # Issue the CSRF cookie on the first response if the client lacks one.
            if message["type"] == "http.response.start" and issue_token:
                headers = MutableHeaders(scope=message)
                headers.append("set-cookie", _csrf_cookie_attrs(request, issue_token))
            await send(message)

        method = scope["method"]
        path = request.url.path
        if method in _SAFE_METHODS or _is_exempt(path) or _test_bypass():
            await self.app(scope, receive, send_wrapper)
            return

        # CSRF only protects an AUTHENTICATED session — an unauthenticated request
        # carries no session cookie for an attacker to ride, and the auth layer
        # redirects it to /login anyway. Skip validation (the CSRF cookie is still
        # issued above) when there is no valid session, so unauthenticated POSTs
        # get the normal login redirect rather than a 403.
        try:
            from app.auth import SESSION_COOKIE, read_session_token
            if read_session_token(request.cookies.get(SESSION_COOKIE)) is None:
                await self.app(scope, receive, send_wrapper)
                return
        except Exception:  # noqa: BLE001 — never let the guard itself 500 a request
            pass

        # ── Unsafe method → validate the double-submit token ──────────────────
        submitted = request.headers.get(CSRF_HEADER)
        if submitted is None:
            submitted, receive = await self._token_from_body(request, receive)

        expected = cookie_token
        if not expected or not submitted or not secrets.compare_digest(
            str(submitted), str(expected)
        ):
            await PlainTextResponse(
                "CSRF token missing or invalid. Reload the page and try again.",
                status_code=403,
            )(scope, receive, send_wrapper)
            return

        await self.app(scope, receive, send_wrapper)

    async def _token_from_body(self, request: Request, receive: Receive):
        """Buffer the body, extract the _csrf field, and return a receive that
        REPLAYS the buffered body so the downstream route can still read it."""
        ctype = request.headers.get("content-type", "")
        is_form = (
            ctype.startswith("application/x-www-form-urlencoded")
            or ctype.startswith("multipart/form-data")
        )
        if not is_form:
            return None, receive

        try:
            length = int(request.headers.get("content-length", "0") or "0")
        except ValueError:
            length = 0
        if length > _MAX_BUFFER_BYTES:
            # Too big to buffer — header was already absent, so reject by token=None.
            return None, receive

        body = b""
        more = True
        while more:
            msg = await receive()
            body += msg.get("body", b"")
            more = msg.get("more_body", False)
            if len(body) > _MAX_BUFFER_BYTES:
                break

        token: str | None = None
        if ctype.startswith("application/x-www-form-urlencoded"):
            parsed = parse_qs(body.decode("latin-1"))
            vals = parsed.get(CSRF_FIELD)
            token = vals[0].strip() if vals else None
        else:  # multipart/form-data
            m = _MULTIPART_CSRF_RE.search(body)
            token = m.group(1).decode("latin-1").strip() if m else None

        async def replay() -> Message:
            return {"type": "http.request", "body": body, "more_body": False}

        return token, replay


# ── Security headers (response-only; safe as a BaseHTTPMiddleware) ─────────────
# CSP notes: vendor JS (alpine/htmx/chart) is self-hosted under /static, Tailwind
# is compiled to /static/css/app.css, and the only external origins are Google
# Fonts. Inline Alpine expressions need 'unsafe-eval'; inline <script>/@click and
# inline style= need 'unsafe-inline'. Product images are hotlinked from vendor
# CDNs → img-src allows https:. Set JAKS_DISABLE_CSP=1 to drop only the CSP
# header (keeps the others) if a future inline source needs triage.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    # §21 — fonts are self-hosted under /static/fonts, so no external font/style
    # origins are needed. 'unsafe-inline' stays for inline style= attributes.
    "style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)


async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if os.getenv("JAKS_DISABLE_CSP") != "1":
        response.headers.setdefault("Content-Security-Policy", _CSP)
    return response
