"""Smart Import / Review Queue routes.

The scraper (or any CSV/Excel feed) is staged into ImportCandidate rows; a human
reviews them here and approves/rejects. Nothing is published from here — applying
approved candidates to the catalog is Phase C; Shopify/eBay publishing is Phase D.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
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


# ── Review Queue for one batch ────────────────────────────────────────────────
@router.get("/{batch_id}", response_class=HTMLResponse)
def queue(batch_id: int, request: Request, q: str = "", tab: str = "all",
          db: Session = Depends(get_db)):
    batch = db.get(ImportBatch, batch_id)
    if not batch:
        return RedirectResponse("/import-review/", status_code=303)
    base = db.query(ImportCandidate).filter(ImportCandidate.batch_id == batch_id)

    # Background-staging progress: a batch is still analyzing while fewer candidates
    # exist than the feed's row count. A notes sentinel marks a failed run so the
    # page stops polling.
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
    candidates = filtered.order_by(ImportCandidate.id).all()

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
    }
    return templates.TemplateResponse(request, "import_review/list.html", {
        "batch": batch, "candidates": candidates, "counts": counts, "tab": tab, "q": q,
        "staged": staged, "processing": processing, "failed": failed,
        "fail_msg": (batch.notes[len(IMPORT_ERROR_PREFIX):].strip() if failed else ""),
    })


# ── Apply approved candidates to the catalog (Phase C) ───────────────────────
@router.post("/{batch_id}/apply")
def apply_batch(batch_id: int, request: Request, db: Session = Depends(get_db),
                user_id: int = Depends(get_current_user_id)):
    """Run apply_approved for a batch. Partial-success safe — errors are logged
    on the summary; the redirect always lands back on the queue. Applying to the
    catalog is gated (admin-only) — a denial returns 403, never silently applies."""
    try:
        ImportReviewService(db, user_id).apply_approved(batch_id)
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
