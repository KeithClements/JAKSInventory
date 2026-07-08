"""
app/routers/notifications.py
=============================
In-app notification bell — HTMX-driven.
  GET  /notifications/count   → count badge HTML (swaps into #notif-count span)
  GET  /notifications/panel   → panel HTML partial
  POST /notifications/{id}/acknowledge → mark read, return updated panel
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.deps import get_current_user_id, get_db
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)

_SEVERITY_CLASS = {
    "critical": "bg-red-500",
    "error":    "bg-red-500",
    "warning":  "bg-amber-400",
    "info":     "bg-blue-400",
}


@router.get("/count", response_class=HTMLResponse)
async def notification_count(
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Badge HTML — loaded into #notif-count on page load."""
    count = len(NotificationService(db, user_id).get_unacknowledged(user_id=user_id))
    if count == 0:
        return HTMLResponse('<span id="notif-count"></span>')
    label = str(count) if count < 100 else "99+"
    return HTMLResponse(
        f'<span id="notif-count" class="absolute -top-1 -right-1 min-w-[1.1rem] h-[1.1rem] '
        f'rounded-full bg-red-500 text-white text-[10px] font-bold flex items-center '
        f'justify-center px-0.5 pointer-events-none">{label}</span>'
    )


@router.get("/panel", response_class=HTMLResponse)
async def notification_panel(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Panel partial — loaded into #notif-panel-body when bell opens."""
    svc = NotificationService(db, user_id)
    notifications = svc.get_unacknowledged(user_id=user_id, limit=30)
    return templates.TemplateResponse(
        request,
        "notifications/_panel.html",
        {"notifications": notifications},
    )


@router.post("/{notification_id}/acknowledge", response_class=HTMLResponse)
async def acknowledge_notification(
    notification_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """Mark a notification read, return the refreshed panel body."""
    svc = NotificationService(db, user_id)
    svc.mark_acknowledged(notification_id, user_id)
    db.commit()
    notifications = svc.get_unacknowledged(user_id=user_id, limit=30)
    return templates.TemplateResponse(
        request,
        "notifications/_panel.html",
        {"notifications": notifications},
    )
