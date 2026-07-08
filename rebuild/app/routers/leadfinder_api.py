"""
app/routers/leadfinder_api.py
=============================
JAK's Lead Finder → ERP integration API (token-gated, JSON only).

This router is the HTTP surface for the cross-system Lead Finder integration.
The Lead Finder app (a separate FastAPI app on :8200) POSTs a lead PACKET to one
of these endpoints over localhost; the ERP dedups the lead and links-or-creates a
customer, then returns its id. The Lead Finder owns the write-back to its own
lead row — the ERP only returns the id.

These routes are EXEMPT from the ERP's login-redirect middleware (see
app/main.py — the guard allows ``path.startswith("/api/leadfinder")``) because a
machine-to-machine integration has no session cookie. Instead they are gated by a
shared secret: the caller sends ``X-LeadFinder-Token`` and the ERP compares it to
the ``LEADFINDER_API_TOKEN`` environment variable.

AUTH behaviour (contract):
  • LEADFINDER_API_TOKEN unset/empty on the ERP → 503 integration_disabled.
  • X-LeadFinder-Token missing or mismatched      → 401 unauthorized.

ROUTES:
  POST /api/leadfinder/match   → LeadConversionService.find_matches; NO writes.
  POST /api/leadfinder/convert → LeadConversionService.convert; link-or-create.

All business logic lives in app/services/lead_conversion_service.py — this router
is a thin token-gated JSON adapter only.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.deps import get_db
from app.services.lead_conversion_service import LeadConversionService

router = APIRouter(prefix="/api/leadfinder", tags=["leadfinder"])

_TOKEN_HEADER = "X-LeadFinder-Token"


def require_leadfinder_token(request: Request) -> None:
    """Gate every route on the shared LEADFINDER_API_TOKEN secret.

    Raises a token-gating ``_ApiError`` (mapped to a JSONResponse by the route)
    so the body shape matches the contract exactly:

      • env unset/empty → 503 {"error":"integration_disabled", "detail": ...}
      • header missing / mismatched → 401 {"error":"unauthorized"}
    """
    configured = (os.environ.get("LEADFINDER_API_TOKEN") or "").strip()
    if not configured:
        raise _ApiError(
            503,
            {
                "error": "integration_disabled",
                "detail": "Set LEADFINDER_API_TOKEN on the ERP",
            },
        )
    provided = request.headers.get(_TOKEN_HEADER)
    if not provided or provided != configured:
        raise _ApiError(401, {"error": "unauthorized"})


class _ApiError(Exception):
    """Internal signal carrying an HTTP status + JSON body for an early return."""

    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(str(body))


def _guard(request: Request) -> JSONResponse | None:
    """Run the token gate, returning the error JSONResponse to short-circuit on,
    or None when the request is authorized."""
    try:
        require_leadfinder_token(request)
    except _ApiError as exc:
        return JSONResponse(exc.body, status_code=exc.status_code)
    return None


async def _parse_body(request: Request) -> dict:
    """Best-effort JSON body parse → dict (empty dict on no/invalid body)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed JSON → empty packet → 422 downstream
        return {}
    return body if isinstance(body, dict) else {}


@router.post("/match")
async def match(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Dedup PREVIEW — NO writes. Returns candidate customers for the packet's
    ``lead`` dict in confidence order.

    Body: ``{"lead": {...}}``.
    Returns 200 ``{"candidates": [...], "exact_usdot": cand|null}``.
    """
    blocked = _guard(request)
    if blocked is not None:
        return blocked

    body = await _parse_body(request)
    try:
        result = LeadConversionService(db, current_user_id=None).find_matches(
            body.get("lead") or {}
        )
    except (ValueError, KeyError) as exc:
        return JSONResponse(
            {"error": "invalid_packet", "detail": str(exc)}, status_code=422
        )
    return JSONResponse(result, status_code=200)


@router.post("/convert")
async def convert(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Link-or-create per the contract mode logic + idempotency.

    Body: ``{"lead": {...}, "mode": "auto"|"create"|"link",
    "link_customer_id": int|null}``.

    Returns 200 with a created | linked | needs_review action dict, or 422
    ``{"error":"invalid_packet","detail":...}`` for a packet problem.
    """
    blocked = _guard(request)
    if blocked is not None:
        return blocked

    body = await _parse_body(request)
    try:
        result = LeadConversionService(db, current_user_id=None).convert(body)
    except (ValueError, KeyError) as exc:
        return JSONResponse(
            {"error": "invalid_packet", "detail": str(exc)}, status_code=422
        )
    return JSONResponse(result, status_code=200)
