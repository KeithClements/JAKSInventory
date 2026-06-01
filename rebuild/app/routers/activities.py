"""
app/routers/activities.py
==========================
Unified Activity Log routes (ACTIVITY_LOG_CONTRACT.md §3).

Backend owns these view functions. UI-Builder builds the modal/timeline/widget
templates against the contracts below.

Routes:
  POST /activities                     Log an activity (global, customer, or doc)
  POST /activities/{id}/follow-up-done Mark a follow-up as done
  GET  /customers/{id}/timeline        Customer activity timeline partial
  GET  /activities/follow-ups          Dashboard follow-ups-due widget partial
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import ActivityType
from app.deps import get_current_user_id, get_db
from app.services.crm_service import CRMService

log = logging.getLogger(__name__)

router = APIRouter(tags=["activities"])
templates = Jinja2Templates(directory="app/templates")


# ── Log activity (the ONE write endpoint) ────────────────────────────────────

@router.post("/activities", status_code=204)
async def log_activity(
    request: Request,
    customer_id: int = Form(...),
    activity_type: str = Form(ActivityType.CALL),
    outcome: str = Form(""),
    notes: str = Form(""),
    follow_up_date: str = Form(""),
    related_entity_type: str = Form(""),
    related_entity_id: str = Form(""),
    direction: str = Form(""),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    Log any customer interaction.

    Called from three contexts (all post to the same endpoint):
      - Global   — header "Log Activity" button (customer_id required, doc optional)
      - Customer — Activity tab "+ Log" (customer_id pre-filled, doc optional)
      - Document — Quote/Invoice/PO workspace (related_entity_* pre-filled)
    """
    follow_up_dt: date | None = None
    if follow_up_date.strip():
        try:
            follow_up_dt = date.fromisoformat(follow_up_date.strip())
        except ValueError:
            pass

    entity_type = related_entity_type.strip() or None
    entity_id: int | None = None
    if entity_type and related_entity_id.strip():
        try:
            entity_id = int(related_entity_id.strip())
        except (ValueError, TypeError):
            entity_type = None

    try:
        CRMService(db, user_id).log_activity(
            customer_id=customer_id,
            activity_type=activity_type,
            outcome=outcome.strip() or None,
            notes=notes.strip(),
            follow_up_date=follow_up_dt,
            related_entity_type=entity_type,
            related_entity_id=entity_id,
            direction=direction.strip() or None,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    # HTMX: fire a trigger so the timeline / follow-up widget refreshes.
    # Non-HTMX (plain form post, tests): redirect back to the customer page.
    if request.headers.get("HX-Request"):
        return JSONResponse(
            {},
            status_code=204,
            headers={"HX-Trigger": "activityLogged"},
        )
    return JSONResponse(
        {},
        status_code=303,
        headers={"Location": f"/customers/{customer_id}?tab=activity"},
    )


# ── Mark follow-up done ───────────────────────────────────────────────────────

@router.post("/activities/{activity_id}/follow-up-done", status_code=204)
def follow_up_done(
    activity_id: int,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Mark a follow-up activity as done. Drops it from the due-today widget."""
    try:
        CRMService(db, user_id).mark_follow_up_done(activity_id)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return JSONResponse(
        {},
        status_code=204,
        headers={"HX-Trigger": "followUpDone"},
    )


# ── Customer timeline (Activity tab body) ─────────────────────────────────────

@router.get("/customers/{customer_id}/timeline", response_class=HTMLResponse)
def customer_timeline(
    customer_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Activity tab partial — merged manual activities + system comms, newest first.

    Context published (§3 contract):
      items  — list[dict] each with keys: kind, when, activity_type, outcome,
               notes, follow_up_date, follow_up_done, related{type,id}, logged_by_id
    """
    try:
        items = CRMService(db).get_timeline(customer_id)
    except ValueError:
        return HTMLResponse(
            '<p class="px-6 py-4 text-sm text-gray-400">Customer not found.</p>',
            status_code=404,
        )
    return templates.TemplateResponse(
        request,
        "customers/_timeline.html",
        {"customer_id": customer_id, "items": items, "ActivityType": ActivityType},
    )


# ── Dashboard follow-ups widget ───────────────────────────────────────────────

@router.get("/activities/follow-ups", response_class=HTMLResponse)
def follow_ups_widget(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Dashboard widget: activities with follow_up_date <= today and follow_up_done_at IS NULL.

    Context published (§3 contract):
      due   — list[Activity] ordered by follow_up_date asc
      today — date (for display)
    """
    due = CRMService(db).follow_ups_due()
    return templates.TemplateResponse(
        request,
        "dashboard/_followups_widget.html",
        {"due": due, "today": date.today()},
    )
