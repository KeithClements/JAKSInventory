"""
app/routers/catalog_hygiene.py
==============================
Catalog hygiene tools. First tool: the cross-reference garbage purge —
a read-only dry-run preview (counts + top offending ref_numbers) and an
explicitly-triggered apply that deletes ONLY rows failing
crossref_hygiene.is_garbage_ref (the same rule the importer now enforces at
ingest). The purge never runs automatically — the owner pulls the trigger
from this screen.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import AuditAction, Permission
from app.deps import get_current_user_id, get_db
from app.services.base import AuditService, PermissionDeniedError
from app.services.crossref_hygiene import purge_garbage_crossrefs

router = APIRouter(prefix="/catalog-hygiene", tags=["catalog-hygiene"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/crossrefs", response_class=HTMLResponse)
def crossref_hygiene_preview(
    request: Request, ok: str = "", db: Session = Depends(get_db)
):
    """Dry-run preview: how many cross-reference rows are garbage and the top
    offending ref_numbers. Read-only — deletes nothing."""
    report = purge_garbage_crossrefs(db, dry_run=True)
    return templates.TemplateResponse(request, "catalog_hygiene/crossrefs.html", {
        "report": report,
        "ok": ok,
    })


@router.post("/crossrefs/apply")
def crossref_hygiene_apply(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Delete every garbage cross-reference row. Gated by MERGE_CATALOG (an
    irreversible bulk catalog mutation) and audited with the deleted count."""
    svc = AuditService(db, current_user_id=user_id)
    try:
        svc.assert_can(Permission.MERGE_CATALOG)
    except PermissionDeniedError:
        return JSONResponse(
            {"ok": False, "error": "Purging cross-references requires admin access."},
            status_code=403,
        )
    report = purge_garbage_crossrefs(db, dry_run=False)
    # entity_id=0: the purge is table-wide, not tied to one row; the counts and
    # worst offenders live in new_value so the audit trail shows WHAT went.
    svc.audit(
        entity_type="cross_reference",
        entity_id=0,
        action=AuditAction.DELETED,
        new_value={
            "deleted": report["deleted"],
            "total_rows_before": report["total_rows"],
            "top_offenders": report["by_ref_number"][:10],
        },
        notes=f"Cross-reference hygiene purge: deleted {report['deleted']} garbage rows",
    )
    db.commit()
    msg = f"Deleted {report['deleted']} garbage cross-reference rows."
    return RedirectResponse(
        f"/catalog-hygiene/crossrefs?ok={quote(msg)}", status_code=303
    )
