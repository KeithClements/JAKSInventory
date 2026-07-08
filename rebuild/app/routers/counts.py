"""
app/routers/counts.py
=====================
§24 — Inventory Count Sessions UI (Audit · Cycle · Full · Opening).

Routes follow the house PRG convention: services commit internally, the router
rolls back on exception and redirects with ?ok= / ?error= (303). Permission
gating lives in CountService.assert_can — PermissionError is caught here and
turned into an ?error= redirect. The count-entry endpoint additionally supports
HTMX (returns the updated line card + an out-of-band progress swap).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import CountLineStatus, CountStatus, CountType, Permission
from app.deps import get_current_user_id, get_db
from app.models.count_session import CountLine, CountSession
from app.models.product import ProductCategory
from app.models.vendor import Vendor
from app.services.document_render import get_company_dict, render_pdf_or_fallback

router = APIRouter(prefix="/counts", tags=["counts"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)
log = logging.getLogger(__name__)

_ENUMS = {
    "CountStatus": CountStatus,
    "CountType": CountType,
    "CountLineStatus": CountLineStatus,
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _nonneg_int(value) -> int | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    return n if n >= 0 else None


def _nonneg_float(value) -> float | None:
    s = str(value or "").strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f >= 0 else None


def _build_scope(form, count_type: str) -> dict:
    """Turn the new-count wizard form into a scope filter dict (§24.5)."""
    mode = str(form.get("scope_mode", "")).strip()
    if not mode:
        mode = "all" if count_type in (CountType.FULL, CountType.OPENING) else "filtered"

    scope: dict = {"mode": mode}
    if mode == "all":
        # A whole-location count still honors include_inactive / only_with_stock.
        if form.get("include_inactive") is not None:
            scope["include_inactive"] = True
        return scope

    cat_ids = [int(c) for c in form.getlist("category_ids") if str(c).isdigit()]
    if cat_ids:
        scope["category_ids"] = cat_ids
    vendor_ids = [int(v) for v in form.getlist("vendor_ids") if str(v).isdigit()]
    if vendor_ids:
        scope["vendor_ids"] = vendor_ids
    bin_prefix = str(form.get("bin_prefix", "")).strip()
    if bin_prefix:
        scope["bin_prefix"] = bin_prefix
    raw_skus = str(form.get("sku_list", "")).replace(",", "\n")
    skus = [s.strip() for s in raw_skus.splitlines() if s.strip()]
    if skus:
        scope["sku_list"] = skus
    if form.get("include_inactive") is not None:
        scope["include_inactive"] = True
    if form.get("only_with_stock") is not None:
        scope["only_with_stock"] = True
    return scope


def _scope_summary(db: Session, scope: dict) -> list[str]:
    """Human-readable scope description for the workspace header."""
    if scope.get("mode") == "all":
        parts = ["All active products"]
        if scope.get("include_inactive"):
            parts = ["All products (incl. inactive)"]
        return parts
    parts: list[str] = []
    cat_ids = scope.get("category_ids") or []
    if cat_ids:
        names = [
            c.name
            for c in db.query(ProductCategory).filter(ProductCategory.id.in_(cat_ids)).all()
        ]
        parts.append("Categories: " + ", ".join(names) if names else "Categories: —")
    vendor_ids = scope.get("vendor_ids") or []
    if vendor_ids:
        names = [
            v.name for v in db.query(Vendor).filter(Vendor.id.in_(vendor_ids)).all()
        ]
        parts.append("Vendors: " + ", ".join(names) if names else "Vendors: —")
    if scope.get("bin_prefix"):
        parts.append(f"Bin starts with '{scope['bin_prefix']}'")
    if scope.get("sku_list"):
        parts.append(f"{len(scope['sku_list'])} specific SKU(s)")
    if scope.get("only_with_stock"):
        parts.append("only items with stock")
    return parts or ["No filters set — will match all active products"]


def _load_scope(session: CountSession) -> dict:
    try:
        return json.loads(session.scope_json) if session.scope_json else {}
    except (ValueError, TypeError):
        return {}


def _workspace_context(request: Request, db: Session, session: CountSession, q: str) -> dict:
    scope = _load_scope(session)
    lines = list(session.lines)

    # Count-entry view: optional search filter (SKU / bin / description).
    shown = lines
    if q:
        needle = q.strip().lower()
        shown = [
            l for l in lines
            if needle in (l.sku or "").lower()
            or needle in (l.bin_location or "").lower()
            or needle in (l.description or "").lower()
        ]
    shown = sorted(shown, key=lambda l: (l.bin_location or "~", l.sku or ""))
    LINE_CAP = 500
    truncated = len(shown) > LINE_CAP
    shown_lines = shown[:LINE_CAP]

    # Review / variance view: only counted lines that actually moved, worst first.
    variance_lines = sorted(
        [l for l in lines if l.is_counted and (l.variance or 0) != 0],
        key=lambda l: abs(l.variance_value),
        reverse=True,
    )
    recount_lines = [l for l in lines if l.needs_recount]

    # Inventory record accuracy = zero-variance counted lines / counted lines.
    counted = [l for l in lines if l.is_counted]
    exact = sum(1 for l in counted if (l.variance or 0) == 0)
    accuracy = round(100 * exact / len(counted)) if counted else None

    return {
        "session": session,
        "scope": scope,
        "scope_summary": _scope_summary(db, scope),
        "lines": shown_lines,
        "line_total": len(lines),
        "shown_count": len(shown_lines),
        "truncated": truncated,
        "line_cap": LINE_CAP,
        "q": q,
        "variance_lines": variance_lines,
        "recount_lines": recount_lines,
        "accuracy": accuracy,
        "exact_lines": exact,
        "counted_lines": len(counted),
        **_ENUMS,
    }


def _progress_fragment(request: Request, session: CountSession) -> str:
    """Render the out-of-band progress bar for HTMX line saves."""
    return templates.get_template("counts/_progress.html").render(
        {"request": request, "session": session, "oob": True}
    )


# ── List + new ───────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def counts_list(
    request: Request,
    status: str = "",
    type: str = "",
    db: Session = Depends(get_db),
):
    q = db.query(CountSession)
    if status:
        q = q.filter(CountSession.status == status)
    if type:
        q = q.filter(CountSession.count_type == type)
    sessions = q.order_by(CountSession.created_at.desc()).limit(200).all()

    by_status = dict(
        db.query(CountSession.status, func.count(CountSession.id))
        .group_by(CountSession.status)
        .all()
    )
    return templates.TemplateResponse(
        request,
        "counts/list.html",
        {
            "sessions": sessions,
            "status": status,
            "type": type,
            "by_status": by_status,
            "total": sum(by_status.values()),
            **_ENUMS,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def counts_new(
    request: Request,
    type: str = CountType.CYCLE,
    db: Session = Depends(get_db),
):
    from app.services.category_service import CategoryService

    if type not in {t.value for t in CountType}:
        type = CountType.CYCLE
    categories = CategoryService(db).categories_flat(include_inactive=False)
    vendors = (
        db.query(Vendor).filter(Vendor.is_active == True).order_by(Vendor.name).all()  # noqa: E712
    )
    return templates.TemplateResponse(
        request,
        "counts/new.html",
        {"categories": categories, "vendors": vendors, "type": type, **_ENUMS},
    )


@router.post("/", response_class=RedirectResponse)
async def counts_create(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.count_service import CountService

    form = await request.form()
    count_type = str(form.get("count_type", "")).strip()
    name = str(form.get("name", "")).strip()
    blind = form.get("blind") is not None
    scope = _build_scope(form, count_type)
    try:
        session = CountService(db, user_id).create_session(
            count_type,
            name=name,
            scope=scope,
            blind=blind,
            recount_threshold_qty=_nonneg_int(form.get("recount_threshold_qty")),
            recount_threshold_value=_nonneg_float(form.get("recount_threshold_value")),
        )
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return RedirectResponse(
            f"/counts/new?type={url_quote(count_type)}&error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception:  # pragma: no cover
        db.rollback()
        log.exception("Unexpected error creating count session")
        return RedirectResponse(
            f"/counts/new?error={url_quote('Unexpected error creating the count.')}",
            status_code=303,
        )
    return RedirectResponse(
        f"/counts/{session.id}?ok="
        + url_quote(
            f"Count {session.session_number} created. Review the scope, then Open to freeze the snapshot."
        ),
        status_code=303,
    )


# ── Workspace ────────────────────────────────────────────────────────────────

@router.get("/{session_id}", response_class=HTMLResponse)
def counts_workspace(
    session_id: int,
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
):
    from app.services.count_service import CountService

    try:
        session = CountService(db).get_session(session_id)
    except ValueError:
        return RedirectResponse(
            f"/counts/?error={url_quote('Count not found.')}", status_code=303
        )
    ctx = _workspace_context(request, db, session, q)
    # Draft sessions can re-scope — supply the category/vendor pickers.
    if session.status == CountStatus.DRAFT:
        from app.services.category_service import CategoryService

        ctx["categories"] = CategoryService(db).categories_flat(include_inactive=False)
        ctx["vendors"] = (
            db.query(Vendor).filter(Vendor.is_active == True)  # noqa: E712
            .order_by(Vendor.name).all()
        )
    return templates.TemplateResponse(request, "counts/workspace.html", ctx)


def _redirect_ws(session_id: int, *, ok: str = "", error: str = "") -> RedirectResponse:
    qs = f"?ok={url_quote(ok)}" if ok else f"?error={url_quote(error)}" if error else ""
    return RedirectResponse(f"/counts/{session_id}{qs}", status_code=303)


@router.post("/{session_id}/scope", response_class=RedirectResponse)
async def counts_update_scope(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.count_service import CountService

    form = await request.form()
    svc = CountService(db, user_id)
    try:
        session = svc.get_session(session_id)
        # Only rebuild the product scope when the scope editor was actually
        # submitted (scope_mode present) — the lightweight options form omits it
        # and must PRESERVE the existing scope, not reset it to defaults.
        scope = _build_scope(form, session.count_type) if form.get("scope_mode") else None
        svc.update_scope(
            session_id,
            scope=scope,
            name=str(form.get("name", "")).strip(),
            blind=form.get("blind") is not None,
            recount_threshold_qty=_nonneg_int(form.get("recount_threshold_qty")),
            recount_threshold_value=_nonneg_float(form.get("recount_threshold_value")),
        )
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return _redirect_ws(session_id, error=str(exc))
    return _redirect_ws(session_id, ok="Scope updated.")


@router.post("/{session_id}/open", response_class=RedirectResponse)
async def counts_open(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.count_service import CountService

    try:
        session = CountService(db, user_id).open_session(session_id)
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return _redirect_ws(session_id, error=str(exc))
    return _redirect_ws(
        session_id,
        ok=f"Snapshot frozen — {session.line_count} item(s) ready to count.",
    )


@router.post("/{session_id}/count")
async def counts_record(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.count_service import CountService

    form = await request.form()
    is_htmx = request.headers.get("HX-Request") == "true"
    product_id = _nonneg_int(form.get("product_id"))
    sku = str(form.get("sku", "")).strip() or None
    counted_raw = form.get("counted_qty")
    svc = CountService(db, user_id)
    try:
        counted = int(str(counted_raw).strip())
        line = svc.record_count(session_id, counted, product_id=product_id, sku=sku)
    except (ValueError, PermissionError) as exc:
        db.rollback()
        if is_htmx:
            return HTMLResponse(
                f'<div class="text-sm text-red-600 px-3 py-2">{str(exc)}</div>',
                status_code=200,
            )
        return _redirect_ws(session_id, error=str(exc))

    if is_htmx:
        session = svc.get_session(session_id)
        body = templates.get_template("counts/_line_card.html").render(
            {"request": request, "line": line, "session": session, **_ENUMS}
        )
        body += _progress_fragment(request, session)
        return HTMLResponse(body)
    return _redirect_ws(session_id, ok=f"Counted {line.sku}: {line.counted_qty}.")


@router.post("/{session_id}/found", response_class=RedirectResponse)
async def counts_found(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.count_service import CountService

    form = await request.form()
    sku = str(form.get("sku", "")).strip()
    counted = _nonneg_int(form.get("counted_qty"))
    try:
        line = CountService(db, user_id).add_found_line(
            session_id, sku, counted_qty=counted
        )
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return _redirect_ws(session_id, error=str(exc))
    return _redirect_ws(session_id, ok=f"Added found item {line.sku}.")


@router.post("/{session_id}/accept-recount", response_class=RedirectResponse)
async def counts_accept_recount(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.count_service import CountService

    form = await request.form()
    line_id = _nonneg_int(form.get("line_id"))
    try:
        CountService(db, user_id).accept_recount(session_id, line_id)
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return _redirect_ws(session_id, error=str(exc))
    return _redirect_ws(session_id, ok="Variance accepted.")


@router.post("/{session_id}/review", response_class=RedirectResponse)
async def counts_review(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.count_service import CountService

    try:
        CountService(db, user_id).to_review(session_id)
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return _redirect_ws(session_id, error=str(exc))
    return _redirect_ws(session_id, ok="Moved to review.")


@router.post("/{session_id}/reopen", response_class=RedirectResponse)
async def counts_reopen(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.count_service import CountService

    try:
        CountService(db, user_id).reopen(session_id)
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return _redirect_ws(session_id, error=str(exc))
    return _redirect_ws(session_id, ok="Reopened for counting.")


@router.post("/{session_id}/post", response_class=RedirectResponse)
async def counts_post(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.count_service import CountService

    form = await request.form()
    zero_uncounted = form.get("zero_uncounted") is not None
    try:
        summary = CountService(db, user_id).post_session(
            session_id, zero_uncounted=zero_uncounted
        )
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return _redirect_ws(session_id, error=str(exc))
    except Exception:  # pragma: no cover
        db.rollback()
        log.exception("Unexpected error posting count %s", session_id)
        return _redirect_ws(session_id, error="Unexpected error posting the count.")
    msg = (
        f"Count posted — {summary['posted']} adjustment(s), "
        f"{summary['unchanged']} unchanged"
    )
    if summary["zeroed"]:
        msg += f", {summary['zeroed']} zeroed"
    if summary["skipped"]:
        msg += f", {summary['skipped']} skipped"
    return _redirect_ws(session_id, ok=msg + ".")


@router.post("/{session_id}/cancel", response_class=RedirectResponse)
async def counts_cancel(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from app.services.count_service import CountService

    form = await request.form()
    try:
        CountService(db, user_id).cancel_session(
            session_id, note=str(form.get("note", "")).strip()
        )
    except (ValueError, PermissionError) as exc:
        db.rollback()
        return _redirect_ws(session_id, error=str(exc))
    return _redirect_ws(session_id, ok="Count cancelled.")


# ── Printable count sheet ────────────────────────────────────────────────────

def _sheet_context(request: Request, db: Session, session: CountSession) -> dict:
    lines = sorted(
        session.lines, key=lambda l: (l.bin_location or "~", l.sku or "")
    )
    return {
        "request": request,
        "session": session,
        "lines": lines,
        "company": get_company_dict(db),
        **_ENUMS,
    }


@router.get("/{session_id}/sheet", response_class=HTMLResponse)
def counts_sheet(session_id: int, request: Request, db: Session = Depends(get_db)):
    from app.services.count_service import CountService

    try:
        session = CountService(db).get_session(session_id)
    except ValueError:
        return RedirectResponse(
            f"/counts/?error={url_quote('Count not found.')}", status_code=303
        )
    return templates.TemplateResponse(
        request, "counts/sheet.html", _sheet_context(request, db, session)
    )


@router.get("/{session_id}/pdf")
def counts_pdf(session_id: int, request: Request, db: Session = Depends(get_db)):
    from app.services.count_service import CountService

    try:
        session = CountService(db).get_session(session_id)
    except ValueError:
        return RedirectResponse(
            f"/counts/?error={url_quote('Count not found.')}", status_code=303
        )
    return render_pdf_or_fallback(
        request=request,
        templates=templates,
        template_name="counts/sheet.html",
        context=_sheet_context(request, db, session),
        fallback_print_url=f"/counts/{session_id}/sheet",
        download_filename=f"{session.session_number}-count-sheet.pdf",
    )
