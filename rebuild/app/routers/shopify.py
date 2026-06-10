"""Shopify publication routes — preview + push ERP products to the JAK's Diesel
store. Admin-gated (PUBLISH_SHOPIFY) and fail-soft: with no store token configured,
publish returns a structured "not configured" result and changes nothing.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user_id
from app.services.base import PermissionDeniedError
from app.services.shopify_service import ShopifyService

router = APIRouter(prefix="/shopify", tags=["shopify"])


@router.get("/preview/{product_id}")
def preview(product_id: int, db: Session = Depends(get_db),
            user_id: int = Depends(get_current_user_id)):
    """Dry-run: the exact productSet input that WOULD be published. No network call."""
    payloads = ShopifyService(db, user_id).preview([product_id])
    if not payloads:
        return JSONResponse({"error": f"product {product_id} not found"}, status_code=404)
    return JSONResponse(payloads[0])


@router.post("/publish")
def publish(request: Request,
            product_ids: list[int] = Form([]),
            status: str = Form("DRAFT"),
            db: Session = Depends(get_db),
            user_id: int = Depends(get_current_user_id)):
    """Publish selected products (admin only). Draft-first; idempotent per product."""
    svc = ShopifyService(db, user_id)
    if not svc.is_configured():
        return JSONResponse(
            {"ok": False, "error": "Shopify not configured — set shopify_store_url and "
             "shopify_access_token in Settings."}, status_code=400)
    try:
        summary = svc.publish_batch(product_ids, status=(status or "DRAFT").upper())
    except PermissionDeniedError:
        return JSONResponse({"ok": False, "error": "Publishing to Shopify requires admin access."},
                            status_code=403)
    return JSONResponse(summary)
