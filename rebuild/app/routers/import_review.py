"""Smart Import / Review Queue routes.

The scraper (or any CSV/Excel feed) is staged into ImportCandidate rows; a human
reviews them here and approves/rejects. Nothing is published from here — applying
approved candidates to the catalog is Phase C; Shopify/eBay publishing is Phase D.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import ImportDisposition, ScrapedItemReviewStatus
from app.deps import get_db, get_current_user_id
from app.models.import_review import ImportBatch, ImportCandidate
from app.models.product import Product
from app.services.base import PermissionDeniedError
from app.services.import_review_service import (
    ImportReviewService, run_background_staging, IMPORT_ERROR_PREFIX,
)

router = APIRouter(prefix="/import-review", tags=["import-review"])
templates = Jinja2Templates(directory="app/templates")

_RS = ScrapedItemReviewStatus
_DISP = ImportDisposition


# ── Landing: recent batches + upload ──────────────────────────────────────────
@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    batches = db.query(ImportBatch).order_by(ImportBatch.id.desc()).limit(50).all()
    return templates.TemplateResponse(request, "import_review/index.html", {"batches": batches})


@router.post("/upload")
def upload(request: Request, background: BackgroundTasks, file: UploadFile = File(...),
           source_app: str = Form(""), db: Session = Depends(get_db),
           user_id: int = Depends(get_current_user_id)):
    text = file.file.read().decode("utf-8", "replace")
    try:
        batch_id, rows = ImportReviewService(db, user_id).create_pending_batch(
            text, source_app=(source_app or "upload"), filename=(file.filename or ""))
    except ValueError as e:
        db.rollback()
        return HTMLResponse(
            '<div style="max-width:40rem;margin:3rem auto;font-family:system-ui">'
            '<h2 style="color:#b91c1c">Import rejected</h2>'
            f'<p>{e}</p>'
            '<p><a href="/import-review/">&larr; Back to Smart Import</a></p></div>',
            status_code=400)
    # Analyze the rows in the background so the upload returns immediately; the
    # queue page polls (candidate count vs total) until staging completes.
    background.add_task(run_background_staging, batch_id, rows, user_id)
    return RedirectResponse(f"/import-review/{batch_id}", status_code=303)


# ── Candidate preview dock partial — registered BEFORE /{batch_id} ────────────
@router.get("/preview/{candidate_id}", response_class=HTMLResponse)
def candidate_preview(candidate_id: int, request: Request, db: Session = Depends(get_db)):
    c = db.get(ImportCandidate, candidate_id)
    if not c:
        return HTMLResponse('<p class="px-6 py-4 text-sm text-gray-400">Candidate not found.</p>')
    matched = db.get(Product, c.matched_product_id) if c.matched_product_id else None
    return templates.TemplateResponse(request, "import_review/_preview_panel.html",
                                      {"c": c, "matched": matched})


_PAGE_SIZE = 100


@router.get("/{batch_id}/ai-status")
def ai_status(batch_id: int, db: Session = Depends(get_db)):
    """Lightweight AI-categorize progress poll — returns counts so the UI can
    show a live progress bar without reloading the whole candidate table."""
    from app.services.ai_categorization_service import AICategorizationService
    svc = AICategorizationService(db)
    total_flagged = db.query(func.count(ImportCandidate.id)).filter(
        ImportCandidate.batch_id == batch_id,
        ImportCandidate.needs_review == True,   # noqa: E712
        ImportCandidate.review_status == _RS.PENDING,
    ).scalar() or 0
    pending = svc.flagged_pending_count(batch_id) if svc.is_enabled() else 0
    processed = total_flagged - pending
    return JSONResponse({
        "total": total_flagged,
        "processed": processed,
        "pending": pending,
        "done": pending == 0,
    })


@router.get("/{batch_id}/progress")
def progress(batch_id: int, db: Session = Depends(get_db)):
    """Lightweight staging-progress poll (JSON only — no candidate rows, no tab
    COUNTs). The queue page polls THIS while a large feed analyzes, instead of
    reloading the entire candidate table every couple seconds."""
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        return JSONResponse({"error": "not found"}, status_code=404)
    staged = db.query(func.count(ImportCandidate.id)).filter(
        ImportCandidate.batch_id == batch_id).scalar() or 0
    failed = bool(batch.notes) and batch.notes.startswith(IMPORT_ERROR_PREFIX)
    total = batch.total or 0
    return JSONResponse({"staged": staged, "total": total,
                         "done": bool(failed or staged >= total), "failed": failed})


# ── Review Queue for one batch ────────────────────────────────────────────────
@router.get("/{batch_id}", response_class=HTMLResponse)
def queue(batch_id: int, request: Request, q: str = "", tab: str = "all",
          page: int = 1, db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        return RedirectResponse("/import-review/", status_code=303)
    base = db.query(ImportCandidate).filter(ImportCandidate.batch_id == batch_id)

    # Background-staging progress: a batch is still analyzing while fewer candidates
    # exist than the feed's row count. A notes sentinel marks a failed run.
    staged = base.count()
    failed = bool(batch.notes) and batch.notes.startswith(IMPORT_ERROR_PREFIX)
    processing = staged < (batch.total or 0) and not failed

    filtered = {
        "pending":      base.filter(ImportCandidate.review_status == _RS.PENDING),
        "needs_review": base.filter(ImportCandidate.needs_review == True),   # noqa: E712
        "new":          base.filter(ImportCandidate.disposition == _DISP.NEW),
        "update":       base.filter(ImportCandidate.disposition == _DISP.UPDATE),
        "cross_ref":    base.filter(ImportCandidate.disposition == _DISP.CROSS_REF),
        "accepted":     base.filter(ImportCandidate.review_status == _RS.ACCEPTED),
        "rejected":     base.filter(ImportCandidate.review_status == _RS.REJECTED),
    }.get(tab, base)

    if q:
        like = f"%{q}%"
        filtered = filtered.filter(ImportCandidate.sku.ilike(like) | ImportCandidate.title.ilike(like))

    # Pagination — NEVER send the whole feed to the template. Rendering 13k rows
    # (each with Alpine bindings) is what froze the browser; cap it to one page.
    total_matching = filtered.count()
    total_pages = max(1, (total_matching + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(1, min(page, total_pages))
    candidates = (filtered.order_by(ImportCandidate.id)
                  .limit(_PAGE_SIZE).offset((page - 1) * _PAGE_SIZE).all())

    # Tab counts — always from the full batch set (governance: unfiltered)
    counts = {
        "all":          base.count(),
        "pending":      base.filter(ImportCandidate.review_status == _RS.PENDING).count(),
        "needs_review": base.filter(ImportCandidate.needs_review == True).count(),  # noqa: E712
        "new":          base.filter(ImportCandidate.disposition == _DISP.NEW).count(),
        "update":       base.filter(ImportCandidate.disposition == _DISP.UPDATE).count(),
        "cross_ref":    base.filter(ImportCandidate.disposition == _DISP.CROSS_REF).count(),
        "accepted":     base.filter(ImportCandidate.review_status == _RS.ACCEPTED).count(),
        "rejected":     base.filter(ImportCandidate.review_status == _RS.REJECTED).count(),
        "confident_pending": base.filter(
            ImportCandidate.review_status == _RS.PENDING,
            ImportCandidate.needs_review == False).count(),  # noqa: E712
    }
    # AI categorize assist — only offered when an Anthropic key is configured.
    from app.services.ai_categorization_service import AICategorizationService
    ai_svc = AICategorizationService(db)
    ai_enabled = ai_svc.is_enabled()
    counts["ai_pending"] = ai_svc.flagged_pending_count(batch_id) if ai_enabled else 0
    return templates.TemplateResponse(request, "import_review/list.html", {
        "batch": batch, "candidates": candidates, "counts": counts, "tab": tab, "q": q,
        "staged": staged, "processing": processing, "failed": failed, "ai_enabled": ai_enabled,
        "fail_msg": (batch.notes[len(IMPORT_ERROR_PREFIX):].strip() if failed else ""),
        "page": page, "total_pages": total_pages, "total_matching": total_matching,
        "showing_from": ((page - 1) * _PAGE_SIZE + 1) if total_matching else 0,
        "showing_to": min(page * _PAGE_SIZE, total_matching),
    })


# ── Apply approved candidates to the catalog (Phase C) ───────────────────────
@router.post("/{batch_id}/apply")
def apply_batch(batch_id: int, request: Request, db: Session = Depends(get_db),
                user_id: int = Depends(get_current_user_id)):
    """Run apply_approved for a batch. Partial-success safe — errors are logged
    on the summary; the redirect always lands back on the queue. Applying to the
    catalog is gated (admin-only) — a denial returns 403, never silently applies."""
    try:
        summary = ImportReviewService(db, user_id).apply_approved(batch_id)
        created  = summary.get("created", 0)
        updated  = summary.get("updated", 0)
        err_count = len(summary.get("errors", []))
        return RedirectResponse(
            f"/import-review/{batch_id}?tab=accepted"
            f"&apply_created={created}&apply_updated={updated}&apply_errors={err_count}",
            status_code=303,
        )
    except PermissionDeniedError:
        return HTMLResponse(
            '<div style="max-width:40rem;margin:3rem auto;font-family:system-ui">'
            '<h2 style="color:#b91c1c">Not allowed</h2>'
            '<p>Applying an import batch to the catalog requires admin access.</p>'
            f'<p><a href="/import-review/{batch_id}?tab=accepted">&larr; Back to the queue</a></p></div>',
            status_code=403)
    except ValueError:
        pass  # batch not found → fall through to redirect
    return RedirectResponse(f"/import-review/{batch_id}?tab=accepted", status_code=303)


# ── Bulk review action (approve / reject / ignore selected) ───────────────────
@router.post("/{batch_id}/review")
def review(batch_id: int, request: Request, action: str = Form(...),
           candidate_ids: list[int] = Form([]), tab: str = Form("all"),
           db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    target = {"approve": _RS.ACCEPTED, "reject": _RS.REJECTED,
              "ignore": _RS.IGNORED}.get(action)
    if target and candidate_ids:
        svc = ImportReviewService(db, user_id)
        for cid in candidate_ids:
            try:
                svc.set_review_status(cid, target)
            except ValueError:
                continue
    return RedirectResponse(f"/import-review/{batch_id}?tab={tab}", status_code=303)


# ── Bulk approve/reject for a WHOLE batch (no per-row selection) ──────────────
@router.post("/{batch_id}/approve-all")
def approve_all(batch_id: int, request: Request,
                scope: str = Form("confident"), action: str = Form("approve"),
                db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """One-click bulk review for an ENTIRE batch — no checkbox selection.
      approve + confident -> accept every PENDING, not-flagged candidate
      approve + all       -> accept every PENDING candidate (incl. flagged)
      reject  + flagged   -> reject every PENDING flagged candidate
    Only PENDING rows are touched, so manual decisions are never overridden."""
    target = _RS.REJECTED if action == "reject" else _RS.ACCEPTED
    ImportReviewService(db, user_id).bulk_set_status(batch_id, target, scope=scope)
    back = "rejected" if action == "reject" else "accepted"
    return RedirectResponse(f"/import-review/{batch_id}?tab={back}", status_code=303)


# ── AI-categorize the flagged (needs-review) candidates via Claude ────────────
@router.post("/{batch_id}/ai-categorize")
def ai_categorize(batch_id: int, request: Request, background: BackgroundTasks,
                  limit: int = Form(0), db: Session = Depends(get_db),
                  user_id: int = Depends(get_current_user_id)):
    """Kick off a background pass that asks Claude to SUGGEST a category for each
    flagged, still-pending candidate. Writes only a suggestion (resolved category +
    a 🤖 AI flag); never changes review status and never writes the catalog. Gated
    on a configured Anthropic API key — fails soft to a flash otherwise."""
    from app.services.ai_categorization_service import (
        AICategorizationService, run_background_ai_categorize,
    )
    if not AICategorizationService(db).is_enabled():
        return RedirectResponse(
            f"/import-review/{batch_id}?tab=needs_review&ai_error=nokey", status_code=303)
    background.add_task(run_background_ai_categorize, batch_id, user_id, (limit or None))
    return RedirectResponse(
        f"/import-review/{batch_id}?tab=needs_review&ai_started=1", status_code=303)
