"""Shopify publication routes — preview + push ERP products to the JAK's Diesel
store. Admin-gated (PUBLISH_SHOPIFY) and fail-soft: with no store token configured,
publish returns a structured "not configured" result and changes nothing.
"""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.constants import Permission
from app.deps import get_db, get_current_user_id
from app.services.base import PermissionDeniedError
from app.services.shopify_service import ShopifyService, run_background_shopify_sync

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


# ── Phase A — Connect & Link ─────────────────────────────────────────────────

@router.get("/status")
def status(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """Settings-card status: configured + linked/unlinked counts. Read-only, no network."""
    return JSONResponse(ShopifyService(db, user_id).link_status())


@router.post("/link-products")
def link_products(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """Match every unlinked ERP product to its existing Shopify listing by SKU /
    handle and store the real GIDs. READ-ONLY against Shopify; idempotent; safe to
    re-run any time. Admin only."""
    try:
        summary = ShopifyService(db, user_id).match_and_link()
    except PermissionDeniedError:
        return JSONResponse({"ok": False, "error": "Linking requires admin access."},
                            status_code=403)
    return JSONResponse(summary, status_code=200 if summary.get("ok") else 400)


@router.post("/update-batch")
def update_batch(product_ids: list[int] = Form([]),
                 db: Session = Depends(get_db),
                 user_id: int = Depends(get_current_user_id)):
    """Re-sync price + SEO + tags to already-linked listings (the safe partial
    update — never touches title/description/images/publish-status). Admin only."""
    svc = ShopifyService(db, user_id)
    if not svc.is_configured():
        return JSONResponse({"ok": False, "error": "Shopify not configured."}, status_code=400)
    try:
        return JSONResponse(svc.update_batch(product_ids))
    except PermissionDeniedError:
        return JSONResponse({"ok": False, "error": "Syncing to Shopify requires admin access."},
                            status_code=403)


@router.post("/sync-inventory")
def sync_inventory(product_ids: list[int] = Form([]),
                   db: Session = Depends(get_db),
                   user_id: int = Depends(get_current_user_id)):
    """Overwrite Shopify stock with the ERP's sellable qty_available for the given
    products (or all linked when none are passed). Admin only; fail-soft."""
    svc = ShopifyService(db, user_id)
    if not svc.is_configured():
        return JSONResponse({"ok": False, "error": "Shopify not configured."}, status_code=400)
    try:
        summary = svc.sync_inventory(product_ids) if product_ids else svc.sync_inventory_all_linked()
    except PermissionDeniedError:
        return JSONResponse({"ok": False, "error": "Syncing to Shopify requires admin access."},
                            status_code=403)
    return JSONResponse(summary, status_code=200 if summary.get("ok") else 400)


@router.post("/sync-now")
def sync_now(background: BackgroundTasks,
             db: Session = Depends(get_db),
             user_id: int = Depends(get_current_user_id)):
    """Kick off a background refresh of price + stock for ALL linked listings, and
    return immediately. The result lands in the shopify_last_sync* settings. The
    same worker is what the nightly schedule calls. Admin only."""
    svc = ShopifyService(db, user_id)
    if not svc.is_configured():
        return JSONResponse({"ok": False, "error": "Shopify not configured."}, status_code=400)
    try:
        svc.assert_can(Permission.PUBLISH_SHOPIFY)
    except PermissionDeniedError:
        return JSONResponse({"ok": False, "error": "Syncing to Shopify requires admin access."},
                            status_code=403)
    background.add_task(run_background_shopify_sync, user_id)
    return JSONResponse({"ok": True,
                         "message": "Shopify sync started — refreshing price & stock "
                                    "for linked listings in the background."})


@router.get("/sync-status")
def sync_status(db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    """Last-sync outcome + nightly auto-sync config, for the Settings card to show."""
    from app.settings_utils import get_setting_value_db
    return JSONResponse({
        "last_sync": get_setting_value_db(db, "shopify_last_sync", ""),
        "last_error": get_setting_value_db(db, "shopify_last_sync_error", ""),
        "last_summary": get_setting_value_db(db, "shopify_last_sync_summary", ""),
        "auto_enabled": get_setting_value_db(db, "shopify_auto_sync_enabled", "0") == "1",
        "auto_hour": get_setting_value_db(db, "shopify_auto_sync_hour", "2"),
    })


_TRUE = {"1", "true", "on", "yes"}


@router.post("/auto-sync")
def auto_sync_config(enabled: str = Form("0"), hour: str = Form("2"),
                     db: Session = Depends(get_db),
                     user_id: int = Depends(get_current_user_id)):
    """Enable/disable the nightly price+stock sync and set its hour (0-23). Admin."""
    from app.settings_utils import set_setting_value_db
    svc = ShopifyService(db, user_id)
    try:
        svc.assert_can(Permission.PUBLISH_SHOPIFY)
    except PermissionDeniedError:
        return JSONResponse({"ok": False, "error": "Admin access required."}, status_code=403)
    try:
        h = max(0, min(23, int(hour)))
    except (TypeError, ValueError):
        h = 2
    on = enabled.strip().lower() in _TRUE
    set_setting_value_db(db, "shopify_auto_sync_enabled", "1" if on else "0")
    set_setting_value_db(db, "shopify_auto_sync_hour", str(h))
    db.commit()
    return JSONResponse({"ok": True, "enabled": on, "hour": h})
