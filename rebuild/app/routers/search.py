"""
app/routers/search.py
======================
Global header search — HTMX-driven live results dropdown.
Returns a partial HTML snippet for the header dropdown.
Searches products (SKU, cross-refs, vendor SKU, description) and customers.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.deps import get_current_user_id, get_db
from app.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["search"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


@router.get("", response_class=HTMLResponse)
async def global_search(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    """
    HTMX endpoint — powers the global header search bar.
    Returns an HTML partial rendered into the results dropdown div.
    Requires q >= 2 chars; returns empty string otherwise (hides dropdown).
    """
    q = q.strip()
    if len(q) < 2:
        return HTMLResponse("")

    svc = SearchService(db, user_id)
    products = svc.search_products(q, limit=6)
    customers = svc.search_customers(q, limit=4)

    return templates.TemplateResponse(
        "search/_results_dropdown.html",
        {
            "request": request,
            "q": q,
            "products": products,
            "customers": customers,
        },
    )
