from __future__ import annotations

import html
import json
import re
import uuid
from pathlib import Path

import csv
import io

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.constants import CrossRefType, SuggestedSellType
from app.deps import get_db, get_current_user_id, require_admin
from app.models.product import (
    CrossReference, Product, ProductImage, ProductVendorSource, SuggestedSell,
)
from app.models.vendor import Vendor
from app.services.product_service import ProductService
from app.services.suggested_sell_service import SuggestedSellService
from app.settings_utils import get_setting_value_db

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

router = APIRouter(prefix="/products", tags=["products"])
templates = Jinja2Templates(directory="app/templates")

MANUFACTURERS = [
    "Cummins", "Caterpillar", "Detroit Diesel", "Mack", "Volvo", "International",
]


# ── helpers ──────────────────────────────────────────────────────────────────

def _svc(db: Session, user_id: int = 1) -> ProductService:
    return ProductService(db, current_user_id=user_id)


def _ss_svc(db: Session, user_id: int = 1) -> SuggestedSellService:
    return SuggestedSellService(db, current_user_id=user_id)


def _vendors(db: Session) -> list[Vendor]:
    return db.query(Vendor).filter(Vendor.is_active == True).order_by(Vendor.name).all()  # noqa: E712


def _categories(db: Session):
    from app.models.product import ProductCategory
    cats = (
        db.query(ProductCategory)
        .filter(ProductCategory.is_active == True)  # noqa: E712
        .options(joinedload(ProductCategory.parent))
        .all()
    )
    # Sort by full path so each Major Group sits directly above its sub-categories
    # ("Engine", "Engine → Bearings", …) rather than scattered by bare name.
    return sorted(cats, key=lambda c: c.full_path.lower())


def _descendant_category_ids(db: Session, root_id: int) -> list[int]:
    """§18 — root category id + every descendant id, so filtering a top-level
    Category also shows products tagged to its Subcategories / Product Families."""
    from app.models.product import ProductCategory
    pairs = db.query(ProductCategory.id, ProductCategory.parent_id).all()
    children: dict[int, list[int]] = {}
    for cid, pid in pairs:
        children.setdefault(pid, []).append(cid)
    out: list[int] = []
    stack = [root_id]
    while stack:
        cid = stack.pop()
        out.append(cid)
        stack.extend(children.get(cid, []))
    return out


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def product_list(
    request: Request,
    q: str = "",
    tab: str = "all",
    sort: str = "sku",
    page: int = 1,
    category_id: int = 0,       # §18 — filter by category (incl. descendants)
    manufacturer: str = "",     # §18 — filter by Manufacturer / Engine Make
    brand: str = "",            # §18 — filter by Brand
    db: Session = Depends(get_db),
):
    base = db.query(Product).filter(Product.is_active == True)  # noqa: E712

    # Tab filter
    if tab == "low_stock":
        query = base.filter(
            Product.reorder_point > 0,
            Product.qty_on_hand > 0,
            Product.qty_on_hand <= Product.reorder_point,
        )
    elif tab == "in_stock":
        query = base.filter(Product.qty_on_hand > 0)
    elif tab == "out_of_stock":
        # Real stockouts only — special-order parts are qty 0 by design, not "out".
        query = base.filter(Product.qty_on_hand == 0, Product.special_order_only == False)  # noqa: E712
    elif tab == "special_order":
        query = base.filter(Product.special_order_only == True)  # noqa: E712
    elif tab == "needs_review":
        query = base.filter(Product.needs_review == True)  # noqa: E712
    elif tab == "uncategorized":
        query = base.filter(Product.category_id.is_(None))
    else:
        query = base

    # Search
    if q:
        like = f"%{q}%"
        _filter = (
            Product.sku.ilike(like)
            | Product.title.ilike(like)
            | Product.manufacturer.ilike(like)
            | Product.brand.ilike(like)
        )
        # De-dashed SKU branch so "141234" finds "14-1234" and "ok1" finds "OK-1".
        _q_clean = re.sub(r"[^a-zA-Z0-9]", "", q)
        if _q_clean:
            _dedashed_sku = func.replace(func.replace(Product.sku, "-", ""), " ", "")
            _filter = _filter | _dedashed_sku.ilike(f"%{_q_clean}%")
        query = query.filter(_filter)

    # §18 — structured filters (category incl. descendants · manufacturer · brand)
    if category_id:
        query = query.filter(Product.category_id.in_(_descendant_category_ids(db, category_id)))
    if manufacturer:
        query = query.filter(Product.engine_manufacturer == manufacturer)
    if brand:
        query = query.filter(Product.brand == brand)

    from app.models.product import ProductCategory

    # ── Pagination — the catalog can be 10k+ rows; never render the whole set ──
    PAGE_SIZE = 100
    total = query.count()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * PAGE_SIZE

    q_eager = query.options(
        # Eager-load so the category sub-line and vendor sort don't N+1.
        joinedload(Product.category).joinedload(ProductCategory.parent),
        joinedload(Product.vendor_sources).joinedload(ProductVendorSource.vendor),
    )

    # Sort: SKU (default) · Vendor · Category. SKU pages in SQL (fast common path).
    # Vendor/Category use the self-referential full_path so they sort in Python over
    # the matched set, then slice the page.
    if sort == "vendor":
        products = q_eager.order_by(Product.sku).all()
        def _vendor_key(p):
            pvs = p.preferred_vendor_source
            name = pvs.vendor.name if pvs and pvs.vendor else ""
            return (name == "", name.lower(), p.sku.lower())
        products.sort(key=_vendor_key)
        products = products[offset:offset + PAGE_SIZE]
    elif sort == "category":
        products = q_eager.order_by(Product.sku).all()
        def _category_key(p):
            path = p.category.full_path if p.category else ""
            return (path == "", path.lower(), p.sku.lower())
        products.sort(key=_category_key)
        products = products[offset:offset + PAGE_SIZE]
    else:
        sort = "sku"  # normalize unknown values back to the default
        products = q_eager.order_by(Product.sku).limit(PAGE_SIZE).offset(offset).all()

    page_start = (offset + 1) if total else 0
    page_end = min(offset + PAGE_SIZE, total)

    # Tab counts (always based on full active set, ignoring current tab/search)
    counts = {
        "all": base.count(),
        "in_stock": base.filter(Product.qty_on_hand > 0).count(),
        "low_stock": base.filter(
            Product.reorder_point > 0,
            Product.qty_on_hand > 0,
            Product.qty_on_hand <= Product.reorder_point,
        ).count(),
        "out_of_stock": base.filter(
            Product.qty_on_hand == 0, Product.special_order_only == False  # noqa: E712
        ).count(),
        "special_order": base.filter(
            Product.special_order_only == True  # noqa: E712
        ).count(),
        "needs_review": base.filter(Product.needs_review == True).count(),  # noqa: E712
        "uncategorized": base.filter(Product.category_id.is_(None)).count(),
    }

    # §18 — filter dropdown options (active category tree + maintained lists)
    from app.models.product import Brand as _Brand, Manufacturer as _Manufacturer
    filter_categories = sorted(
        db.query(ProductCategory).filter(ProductCategory.is_active == True).all(),  # noqa: E712
        key=lambda c: c.full_path.lower(),
    )
    filter_brands = (
        db.query(_Brand).filter(_Brand.is_active == True)  # noqa: E712
        .order_by(_Brand.sort_order, _Brand.name).all()
    )
    filter_manufacturers = (
        db.query(_Manufacturer).filter(_Manufacturer.is_active == True)  # noqa: E712
        .order_by(_Manufacturer.sort_order, _Manufacturer.name).all()
    )

    return templates.TemplateResponse(request, "products/list.html", {
        "products": products,
        "q": q,
        "tab": tab,
        "sort": sort,
        "counts": counts,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "page_start": page_start,
        "page_end": page_end,
        "page_size": PAGE_SIZE,
        "filter_categories": filter_categories,
        "filter_brands": filter_brands,
        "filter_manufacturers": filter_manufacturers,
        "category_id": category_id,
        "manufacturer": manufacturer,
        "brand": brand,
    })


# ── List row preview panel (HTMX partial) ────────────────────────────────────

@router.get("/preview/{product_id}", response_class=HTMLResponse)
def product_preview_panel(
    product_id: int, request: Request, db: Session = Depends(get_db)
):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        return HTMLResponse(
            '<p class="px-6 py-4 text-sm text-gray-400">Product not found.</p>'
        )
    return templates.TemplateResponse(request, "products/_preview_panel.html", {
        "p": p,
    })


# ── New ──────────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def product_new(request: Request, db: Session = Depends(get_db)):
    default_markup = get_setting_value_db(db, "default_markup_pct", "30.0")
    return templates.TemplateResponse(request, "products/new.html", {
        "vendors": _vendors(db),
        "categories": _categories(db),
        "category_tree": ProductService(db).category_tree(),  # #7 nested picker
        "manufacturers": MANUFACTURERS,
        "default_markup": default_markup,
    })


@router.post("/new", response_class=HTMLResponse)
async def product_create(request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    try:
        data = _parse_product_form(form)
        product = _svc(db, user_id).create_product(data)
        return RedirectResponse(f"/products/{product.id}", status_code=303)
    except ValueError as exc:
        default_markup = get_setting_value_db(db, "default_markup_pct", "30.0")
        return templates.TemplateResponse(request, "products/new.html", {
            "vendors": _vendors(db),
            "categories": _categories(db),
            "manufacturers": MANUFACTURERS,
            "default_markup": default_markup,
            "error": str(exc),
            "form_data": dict(form),
        }, status_code=422)


# ── Detail ───────────────────────────────────────────────────────────────────

# ── Quick Create (slide-over — called from quote "add non-stocked item") ──────

@router.get("/quick-create-form", response_class=HTMLResponse)
def product_quick_create_form(request: Request):
    return templates.TemplateResponse(request, "products/_quick_create.html")


@router.post("/quick-create", response_class=HTMLResponse)
async def product_quick_create(request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    sku_suffix = str(form.get("sku_suffix", "")).strip().upper()
    title = str(form.get("title", "")).strip()
    if not sku_suffix or not title:
        # D-6 — re-render the panel at 200 so HTMX swaps it. A bare <p> at 422 is
        # not swapped by HTMX → the user only sees the generic "Could not load the
        # form (422)" fallback and loses their input. `error` + `form_data` feed
        # UI-Builder's {% if error %} banner + value repopulation in the template.
        return templates.TemplateResponse(request, "products/_quick_create.html", {
            "error": "SKU and title are required.",
            "form_data": dict(form),
        })
    sku = f"JAKS-{sku_suffix}"
    svc = _svc(db, user_id)
    # O5 — when the quick-create form leaves markup blank, inherit the configured
    # default_markup_pct rather than a hardcoded 30.
    from app.services.pricing_service import PricingService
    _pricing = PricingService(db)
    markup_raw = str(form.get("markup_pct", "")).strip()
    markup_pct = float(markup_raw) if markup_raw else _pricing.default_markup_pct()
    try:
        # Bug 3 fix: include description so the quick-create slide-over passes
        # it through to the product record (the main /new form already does via
        # _parse_product_form; the inline dict here was the gap).
        product = svc.create_product({
            "sku": sku,
            "title": title,
            "description": str(form.get("description", "")).strip(),
            "cost": float(form.get("cost") or 0),
            "markup_pct": markup_pct,
            "has_core": bool(form.get("has_core")),
            "vendor_core_charge": float(form.get("vendor_core_charge") or 0),
            "customer_core_charge": float(form.get("customer_core_charge") or 0),
        })
    except ValueError as exc:
        # D-6 — same: re-render at 200 with the real validation message so HTMX
        # swaps the panel in place and keeps the user's entered values, instead of
        # the generic 422 fallback. UI-Builder renders error/form_data.
        return templates.TemplateResponse(request, "products/_quick_create.html", {
            "error": str(exc),
            "form_data": dict(form),
        })
    db.commit()
    sell = _pricing.sell_price_for(product)
    _detail = html.escape(json.dumps({
        "type": "product",
        "id": product.id,
        "label": f"{product.sku} — {product.title}",
        "part_number": product.sku,
        "description": product.title,
        "current_cost": product.cost,
        "suggested_sell": sell,
    }))
    _sku = html.escape(product.sku)
    return HTMLResponse(
        f"""<span></span>
<div id="toast-container" hx-swap-oob="beforeend">
  <div x-data x-init="
      setTimeout(() => $el.remove(), 4000);
      window.dispatchEvent(new CustomEvent('record-created', {{ detail: {_detail} }}));
    "
    class="toast toast-success">
    <svg class="w-4 h-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">
      <path fill-rule="evenodd"
            d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
            clip-rule="evenodd"/>
    </svg>
    Product created: {_sku}
  </div>
</div>"""
    )


# ── Bulk Set-Status — MUST be before /{product_id} ───────────────────────────

@router.post("/bulk-status", response_class=RedirectResponse)
async def product_bulk_status(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Set is_active status on a batch of products.
    Form fields:
      product_ids  — repeated int field (one per selected product)
      action       — "activate" | "deactivate"
    """
    form = await request.form()
    raw_ids = form.getlist("product_ids")
    action = str(form.get("action", "")).strip()

    product_ids = []
    for raw in raw_ids:
        try:
            product_ids.append(int(raw))
        except (ValueError, TypeError):
            pass

    if product_ids and action in ("activate", "deactivate"):
        new_state = action == "activate"
        db.query(Product).filter(Product.id.in_(product_ids)).update(
            {"is_active": new_state}, synchronize_session=False
        )
        db.commit()

    return RedirectResponse("/products/", status_code=303)


# ── Bulk Assign Category / Manufacturer — MUST be before /{product_id} ────────
# §18 — the Products-List triage action. Assigns a category and/or a
# Manufacturer / Engine Make to the selected products. Assigning clears
# needs_review (the row has now been triaged → leaves the Import Review queue).

@router.post("/bulk-assign", response_class=RedirectResponse)
async def product_bulk_assign(
    request: Request,
    db: Session = Depends(get_db),
):
    form = await request.form()
    raw_ids = form.getlist("product_ids")
    product_ids: list[int] = []
    for raw in raw_ids:
        try:
            product_ids.append(int(raw))
        except (ValueError, TypeError):
            pass

    category_id_raw = str(form.get("category_id", "")).strip()
    manufacturer = str(form.get("manufacturer", "")).strip()

    updates: dict = {}
    if category_id_raw:
        try:
            updates["category_id"] = int(category_id_raw)
        except (ValueError, TypeError):
            pass
    if manufacturer:
        updates["engine_manufacturer"] = manufacturer

    if product_ids and updates:
        # Assigning is the triage action → the row leaves "Needs Review".
        updates["needs_review"] = False
        db.query(Product).filter(Product.id.in_(product_ids)).update(
            updates, synchronize_session=False
        )
        db.commit()

    return_to = str(form.get("return_to", "/products/")).strip()
    if not return_to.startswith("/products"):
        return_to = "/products/"          # guard against open redirect
    return RedirectResponse(return_to, status_code=303)


# ── Import Review queue (§18.7) — MUST be before /{product_id} ────────────────
# Products the importer flagged needs_review (couldn't confidently place into a
# Subcategory / Product Family). Per-row inline assign posts to /bulk-assign and
# returns here; assigning clears the flag so the row leaves the queue.

@router.get("/review", response_class=HTMLResponse)
def product_review_queue(request: Request, db: Session = Depends(get_db)):
    from app.models.product import Manufacturer as _Manufacturer, ProductCategory as _PC
    base = db.query(Product).filter(
        Product.is_active == True, Product.needs_review == True  # noqa: E712
    )
    total = base.count()
    products = (
        base.options(joinedload(Product.category).joinedload(_PC.parent))
        .order_by(Product.sku).limit(300).all()
    )
    filter_categories = sorted(
        db.query(_PC).filter(_PC.is_active == True).all(),  # noqa: E712
        key=lambda c: c.full_path.lower(),
    )
    filter_manufacturers = (
        db.query(_Manufacturer).filter(_Manufacturer.is_active == True)  # noqa: E712
        .order_by(_Manufacturer.sort_order, _Manufacturer.name).all()
    )
    return templates.TemplateResponse(request, "products/review.html", {
        "products": products, "total": total, "shown": len(products),
        "filter_categories": filter_categories,
        "filter_manufacturers": filter_manufacturers,
    })


# ── Export CSV — MUST be before /{product_id} ─────────────────────────────────

@router.get("/export.csv")
def product_export_csv(
    request: Request,
    tab: str = "",
    q: str = "",
    db: Session = Depends(get_db),
):
    """
    Stream the current filtered product list as a CSV download.
    Respects the same tab/q filters as the list view so "export what I see" works.
    """
    # Mirror the list view's tab slugs so "export what I see" matches exactly.
    base = db.query(Product).filter(Product.is_active == True)  # noqa: E712
    if tab == "low_stock":
        base = base.filter(
            Product.reorder_point > 0,
            Product.qty_on_hand > 0,
            Product.qty_on_hand <= Product.reorder_point,
        )
    elif tab == "in_stock":
        base = base.filter(Product.qty_on_hand > 0)
    elif tab == "out_of_stock":
        base = base.filter(Product.qty_on_hand == 0, Product.special_order_only == False)  # noqa: E712
    elif tab == "special_order":
        base = base.filter(Product.special_order_only == True)  # noqa: E712

    if q:
        from sqlalchemy import or_
        base = base.filter(
            or_(
                Product.sku.ilike(f"%{q}%"),
                Product.title.ilike(f"%{q}%"),
                Product.description.ilike(f"%{q}%"),
            )
        )

    products = base.order_by(Product.sku).all()

    # O5 — settings-backed sell price (default markup from default_markup_pct).
    from app.services.pricing_service import PricingService
    _pricing = PricingService(db)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "sku", "title", "description", "status",
        "cost", "markup_pct", "price_override", "selling_price",
        "qty_on_hand", "is_active", "category",
    ])
    for p in products:
        try:
            sell = _pricing.sell_price_for(p)
        except Exception:
            sell = round(p.cost, 2)
        writer.writerow([
            p.sku,
            p.title,
            p.description,
            p.status,
            f"{p.cost:.4f}",
            # The product's OWN markup (blank when unset — the selling_price column
            # already reflects the settings-backed default for unset products).
            f"{p.markup_pct:.2f}" if p.markup_pct is not None else "",
            f"{p.price_override:.2f}" if p.price_override else "",
            f"{sell:.2f}",
            p.qty_on_hand,
            "yes" if p.is_active else "no",
            p.category.name if p.category else "",
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=products.csv"},
    )


# ── §7.2 Enrichment sync (external catalog CSV → enrich existing products) ─────
# Registered BEFORE /{product_id} so the literal path isn't captured by the
# dynamic int route. Enrich-only: matches jaks_sku→sku, never creates products,
# never touches cost/sell. Returns a JSON summary.

@router.post("/enrich-sync")
async def product_enrich_sync(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    from app.services.enrichment_service import ProductEnrichmentService
    raw = await file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    return ProductEnrichmentService(db).enrich_from_csv_text(text)


# ── Product Importer (Full Import + Pricing Update) — admin-gated ──────────────
# Two modes, both dry-run-first. Full creates products from the PAI scraper's
# Shopify export; Pricing Update only updates PAI vendor_cost (+ cost history) or
# competitor_prices (+ history). Registered BEFORE /{product_id}.

@router.get("/import", response_class=HTMLResponse)
def product_import_page(request: Request, _admin=Depends(require_admin)):
    return templates.TemplateResponse(request, "products/import.html", {})


@router.post("/import-run")
async def product_import_run(
    file: UploadFile = File(...),
    mode: str = Form("full"),                # full | pricing
    source_type: str = Form("pai_cost"),     # pricing: pai_cost | competitor
    dry_run: bool = Form(True),
    import_images: bool = Form(True),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
    _admin=Depends(require_admin),
):
    from app.services.product_import_service import ProductImportService
    raw = await file.read()
    text = raw.decode("utf-8-sig", errors="replace")
    svc = ProductImportService(db, user_id)
    if mode == "full":
        return svc.full_import(text, dry_run=dry_run, import_images=import_images)
    if mode == "pricing" and source_type == "competitor":
        return svc.pricing_update_competitor(text, dry_run=dry_run)
    if mode == "pricing":
        return svc.pricing_update_pai_cost(text, dry_run=dry_run)
    return {"error": f"unknown mode {mode!r}"}


# ── Detail / Update / Deactivate / Reactivate ─────────────────────────────────

@router.get("/{product_id}", response_class=HTMLResponse)
def product_detail(
    product_id: int,
    request: Request,
    saved: str = "",
    ok: str = "",
    error: str = "",
    db: Session = Depends(get_db),
):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        return RedirectResponse("/products/", status_code=303)
    return templates.TemplateResponse(request, "products/detail.html", {
        "product": p,
        "vendors": _vendors(db),
        "categories": _categories(db),
        "category_tree": ProductService(db).category_tree(),  # #7 nested picker
        "manufacturers": MANUFACTURERS,
        "cross_ref_types": list(CrossRefType),
        "suggested_sell_types": list(SuggestedSellType),
        "default_markup": float(get_setting_value_db(db, "default_markup_pct", "30.0")),
        "ok": ok or (saved and "Saved.") or "",
        "error": error,
    })


@router.post("/{product_id}", response_class=HTMLResponse)
async def product_update(product_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        return RedirectResponse("/products/", status_code=303)
    form = await request.form()
    try:
        data = _parse_product_form(form)
        _svc(db, user_id).update_product(product_id, data)
        return RedirectResponse(f"/products/{product_id}?saved=1", status_code=303)
    except ValueError as exc:
        # Re-fetch product fresh (may be partially updated in service before error)
        p = db.query(Product).filter(Product.id == product_id).first()
        return templates.TemplateResponse(request, "products/detail.html", {
            "product": p,
            "vendors": _vendors(db),
            "categories": _categories(db),
            "manufacturers": MANUFACTURERS,
            "cross_ref_types": list(CrossRefType),
            "suggested_sell_types": list(SuggestedSellType),
            "default_markup": float(get_setting_value_db(db, "default_markup_pct", "30.0")),
            "error": str(exc),
        }, status_code=422)


# ── Autosave (Save Standard v2) ───────────────────────────────────────────────
# Real auto-save for the Info-tab edit form. The detail page POSTs the whole form here
# (debounced) via fetch and drives the honest save-state pill off the HTTP status —
# green "All changes saved" appears ONLY after a confirmed commit. Replaces the old
# cosmetic _dirty flag, which defaulted to "All changes saved" on load even though
# nothing had been persisted, so edits were silently lost on navigation (owner-reported
# 2026-06-06). Same field set / validation as product_update; never redirects.
@router.post("/{product_id}/autosave", response_class=HTMLResponse)
async def product_autosave(
    product_id: int, request: Request, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        return HTMLResponse(
            '<span class="text-xs text-red-600">Product not found.</span>',
            status_code=404,
        )
    form = await request.form()
    try:
        data = _parse_product_form(form)
        _svc(db, user_id).update_product(product_id, data)
        return HTMLResponse('<span class="text-xs text-green-600 font-medium">&#10003; Saved</span>')
    except ValueError as exc:
        db.rollback()
        return HTMLResponse(
            f'<span class="text-xs text-red-600">{html.escape(str(exc))}</span>',
            status_code=422,
        )
    except Exception:
        db.rollback()
        import logging
        logging.getLogger(__name__).exception("Product autosave failed for %s", product_id)
        return HTMLResponse(
            '<span class="text-xs text-red-600">Save failed.</span>',
            status_code=500,
        )


# ── Deactivate ───────────────────────────────────────────────────────────────

@router.post("/{product_id}/deactivate", response_class=HTMLResponse)
async def product_deactivate(product_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    reason = str(form.get("reason", "archived via UI")).strip() or "archived via UI"
    try:
        _svc(db, user_id).deactivate_product(product_id, reason)
    except ValueError:
        pass
    return RedirectResponse("/products/", status_code=303)


@router.post("/{product_id}/reactivate", response_class=RedirectResponse)
async def product_reactivate(product_id: int, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if p:
        p.is_active = True
        db.commit()
    return RedirectResponse(f"/products/{product_id}", status_code=303)


# ── Vendor Sources ────────────────────────────────────────────────────────────

@router.post("/{product_id}/vendor-sources", response_class=HTMLResponse)
async def vendor_source_add(product_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    vendor_id_raw = str(form.get("vendor_id", "")).strip()
    if not vendor_id_raw:
        return HTMLResponse('<tr><td colspan="6" class="px-4 py-2 text-red-600 text-sm">Vendor is required.</td></tr>', status_code=422)
    try:
        vendor_id = int(vendor_id_raw)
        data = {
            "vendor_part_number": str(form.get("vendor_part_number", "")).strip(),
            "vendor_cost": float(form.get("vendor_cost", 0) or 0),
            "lead_time_days": int(form.get("lead_time_days") or 0) or None,
            "is_preferred": bool(form.get("is_preferred")),
            "notes": str(form.get("notes", "")).strip(),
        }
        source = _svc(db, user_id).add_vendor_source(product_id, vendor_id, data)
        db.refresh(source)
        resp = templates.TemplateResponse(request, "products/_vendor_source_row.html", {
            "source": source,
            "product_id": product_id,
        })
        # When this source became the preferred one, product.cost was mirrored
        # from it. Tell the Info tab to live-update the "Our Cost" field (and the
        # derived sell price) without a full page reload.
        if source.is_preferred:
            resp.headers["HX-Trigger"] = json.dumps(
                {"product-cost-synced": {"cost": round(source.vendor_cost, 2)}}
            )
        return resp
    except ValueError as exc:
        return HTMLResponse(
            f'<tr><td colspan="6" class="px-4 py-2 text-red-600 text-sm">{exc}</td></tr>',
            status_code=422,
        )


@router.post("/{product_id}/vendor-sources/{source_id}/prefer", response_class=HTMLResponse)
def vendor_source_prefer(product_id: int, source_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    try:
        _svc(db, user_id).set_preferred_vendor(product_id, source_id)
        # Return the full updated sources list partial so preferred badges refresh
        p = db.query(Product).filter(Product.id == product_id).first()
        resp = templates.TemplateResponse(request, "products/_vendor_sources_table.html", {
            "product": p,
            "product_id": product_id,
        })
        # Switching the preferred vendor changes product.cost — live-update the
        # Info tab's Pricing card to match.
        if p is not None:
            resp.headers["HX-Trigger"] = json.dumps(
                {"product-cost-synced": {"cost": round(p.cost, 2)}}
            )
        return resp
    except ValueError as exc:
        return HTMLResponse(f'<span class="text-red-600 text-sm">{exc}</span>', status_code=422)


@router.delete("/{product_id}/vendor-sources/{source_id}", response_class=HTMLResponse)
def vendor_source_remove(product_id: int, source_id: int, db: Session = Depends(get_db)):
    source = (
        db.query(ProductVendorSource)
        .filter(
            ProductVendorSource.id == source_id,
            ProductVendorSource.product_id == product_id,
        )
        .first()
    )
    if source:
        source.is_active = False
        db.commit()
    response = HTMLResponse("", status_code=200)
    response.headers["HX-Trigger"] = "sourceRemoved"
    return response


# ── Cross References ──────────────────────────────────────────────────────────

@router.post("/{product_id}/cross-refs", response_class=HTMLResponse)
async def cross_ref_add(product_id: int, request: Request, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    form = await request.form()
    ref_number = str(form.get("ref_number", "")).strip()
    if not ref_number:
        return HTMLResponse('<tr><td colspan="5" class="px-4 py-2 text-red-600 text-sm">Part number is required.</td></tr>', status_code=422)
    ref_type = str(form.get("ref_type", CrossRefType.OEM)).strip()
    brand = str(form.get("brand", "")).strip()
    status = str(form.get("status", "proven")).strip() or "proven"
    try:
        _svc(db, user_id).add_cross_reference(product_id, ref_type, ref_number, brand or None, status=status)
        # Fetch the newly inserted xref (last one for this product+type+number)
        xref = (
            db.query(CrossReference)
            .filter(
                CrossReference.product_id == product_id,
                CrossReference.ref_number == ref_number.upper(),
                CrossReference.ref_type == ref_type,
            )
            .order_by(CrossReference.id.desc())
            .first()
        )
        return templates.TemplateResponse(request, "products/_cross_ref_row.html", {
            "xref": xref,
        })
    except ValueError as exc:
        return HTMLResponse(
            f'<tr><td colspan="5" class="px-4 py-2 text-red-600 text-sm">{exc}</td></tr>',
            status_code=422,
        )


@router.delete("/{product_id}/cross-refs/{xref_id}", response_class=HTMLResponse)
def cross_ref_remove(product_id: int, xref_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    try:
        _svc(db, user_id).remove_cross_reference(xref_id)
    except ValueError:
        pass
    return HTMLResponse("", status_code=200)


# ── Cross Reference Status ────────────────────────────────────────────────────

@router.patch("/{product_id}/cross-refs/{xref_id}/status", response_class=HTMLResponse)
async def cross_ref_update_status(
    product_id: int, xref_id: int, request: Request, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()
    status = str(form.get("status", "proven")).strip() or "proven"
    try:
        xref = _svc(db, user_id).update_cross_reference_status(xref_id, status)
        return templates.TemplateResponse(request, "products/_cross_ref_row.html", {
            "xref": xref,
        })
    except ValueError as exc:
        return HTMLResponse(
            f'<tr><td colspan="5" class="px-4 py-2 text-red-600 text-sm">{exc}</td></tr>',
            status_code=422,
        )


# ── Enrichment Panel removed ──────────────────────────────────────────────────
# The per-product vendor-source scrape panel is gone — all scraping/cataloging is
# external now (PHASE_2_PLAN.md §6 P2-D9). Product enrichment lands via the §7.2
# CSV enrich-sync (cross_references + product_applications), not a live panel.
# UI follow-up (UI lane, plan §8P): delete products/_enrich_panel.html + the
# detail.html enrich trigger.


# ── Images ────────────────────────────────────────────────────────────────────

@router.post("/{product_id}/images", response_class=HTMLResponse)
async def product_image_upload(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        return HTMLResponse("Product not found", status_code=404)

    # Validate image MIME type
    if not file.content_type or not file.content_type.startswith("image/"):
        return HTMLResponse(
            '<p class="text-sm text-red-600 p-4">File must be an image (jpg, png, webp, etc).</p>',
            status_code=422,
        )

    # Build safe file path
    suffix = Path(file.filename).suffix.lower() if file.filename else ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        suffix = ".jpg"
    filename = f"{uuid.uuid4().hex}{suffix}"
    upload_dir = STATIC_DIR / "uploads" / "products" / str(product_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    rel_path = f"uploads/products/{product_id}/{filename}"

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:  # 10 MB server-side limit
        return HTMLResponse(
            '<p class="text-sm text-red-600 p-4">Image must be under 10 MB.</p>',
            status_code=413,
        )
    (STATIC_DIR / rel_path).write_bytes(content)

    _svc(db, user_id).add_product_image(product_id, rel_path)
    db.refresh(p)

    return templates.TemplateResponse(request, "products/_images_grid.html", {
        "images": p.images,
        "product_id": product_id,
    })


@router.delete("/{product_id}/images/{image_id}", response_class=HTMLResponse)
def product_image_remove(product_id: int, image_id: int, db: Session = Depends(get_db), user_id: int = Depends(get_current_user_id)):
    try:
        _svc(db, user_id).remove_product_image(product_id, image_id)
    except ValueError:
        pass
    return HTMLResponse("", status_code=200)


@router.post("/{product_id}/images/{image_id}/set-primary", response_class=HTMLResponse)
def product_image_set_primary(
    product_id: int, image_id: int, request: Request, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        _svc(db, user_id).set_primary_image(product_id, image_id)
    except ValueError as exc:
        return HTMLResponse(f'<p class="text-sm text-red-600 p-4">{exc}</p>', status_code=422)
    p = db.query(Product).filter(Product.id == product_id).first()
    return templates.TemplateResponse(request, "products/_images_grid.html", {
        "images": p.images if p else [],
        "product_id": product_id,
    })


# ── Suggested Sells ──────────────────────────────────────────────────────────

@router.post("/{product_id}/suggested-sells", response_class=HTMLResponse)
async def suggested_sell_add(
    product_id: int, request: Request, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()
    sku = str(form.get("sku", "")).strip().upper()
    if not sku:
        return HTMLResponse(
            '<tr><td colspan="5" class="px-4 py-2 text-red-600 text-sm">SKU is required.</td></tr>',
            status_code=422,
        )
    # Look up the product by SKU
    suggested = db.query(Product).filter(Product.sku == sku).first()
    if not suggested:
        return HTMLResponse(
            f'<tr><td colspan="5" class="px-4 py-2 text-red-600 text-sm">SKU "{sku}" not found.</td></tr>',
            status_code=422,
        )
    rel_type = str(form.get("relationship_type", "recommended")).strip()
    notes = str(form.get("notes", "")).strip()
    try:
        suggestion = _ss_svc(db, user_id).add_suggestion(
            product_id=product_id,
            suggested_product_id=suggested.id,
            relationship_type=rel_type,
            notes=notes,
        )
        return templates.TemplateResponse(request, "products/_suggested_sell_row.html", {
            "suggestion": suggestion,
            "product_id": product_id,
        })
    except ValueError as exc:
        return HTMLResponse(
            f'<tr><td colspan="5" class="px-4 py-2 text-red-600 text-sm">{exc}</td></tr>',
            status_code=422,
        )


@router.patch("/{product_id}/suggested-sells/{suggestion_id}", response_class=HTMLResponse)
async def suggested_sell_update(
    product_id: int, suggestion_id: int, request: Request, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    form = await request.form()
    data = {}
    if "relationship_type" in form:
        data["relationship_type"] = str(form["relationship_type"]).strip()
    if "notes" in form:
        data["notes"] = str(form["notes"]).strip()
    try:
        suggestion = _ss_svc(db, user_id).update_suggestion(suggestion_id, data)
        return templates.TemplateResponse(request, "products/_suggested_sell_row.html", {
            "suggestion": suggestion,
            "product_id": product_id,
        })
    except ValueError as exc:
        return HTMLResponse(f'<span class="text-red-600 text-sm">{exc}</span>', status_code=422)


@router.delete("/{product_id}/suggested-sells/{suggestion_id}", response_class=HTMLResponse)
def suggested_sell_remove(
    product_id: int, suggestion_id: int, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    try:
        _ss_svc(db, user_id).remove_suggestion(suggestion_id)
    except ValueError:
        pass
    return HTMLResponse("", status_code=200)


# ── Inventory Adjustment ─────────────────────────────────────────────────────

@router.post("/{product_id}/adjust-inventory", response_class=HTMLResponse)
async def adjust_inventory_handler(
    product_id: int, request: Request, db: Session = Depends(get_db),
    user_id: int = Depends(get_current_user_id),
):
    from urllib.parse import quote as url_quote
    import logging
    from app.services.inventory_service import InventoryService

    form = await request.form()
    qty_delta_raw = str(form.get("qty_delta", "0")).strip()
    try:
        qty_delta = int(qty_delta_raw)
    except (ValueError, TypeError):
        qty_delta = 0

    if qty_delta == 0:
        return RedirectResponse(
            f"/products/{product_id}?error={url_quote('Qty change must be non-zero.')}",
            status_code=303,
        )

    reason = str(form.get("reason", "")).strip()
    note = str(form.get("note", "")).strip()
    unit_cost_raw = str(form.get("unit_cost", "")).strip()
    unit_cost: float | None = None
    if unit_cost_raw:
        try:
            parsed = float(unit_cost_raw)
            if parsed > 0:
                unit_cost = parsed
        except (ValueError, TypeError):
            pass

    try:
        svc = InventoryService(db, current_user_id=user_id)
        svc.adjust_inventory(
            product_id=product_id,
            qty_delta=qty_delta,
            reason=reason,
            note=note,
            unit_cost=unit_cost,
        )
        return RedirectResponse(
            f"/products/{product_id}?ok={url_quote('Inventory adjusted.')}",
            status_code=303,
        )
    except (ValueError, PermissionError) as exc:
        return RedirectResponse(
            f"/products/{product_id}?error={url_quote(str(exc))}",
            status_code=303,
        )
    except Exception as exc:
        logging.getLogger(__name__).exception("Unexpected error in adjust_inventory_handler")
        return RedirectResponse(
            f"/products/{product_id}?error={url_quote('An unexpected error occurred. Please try again.')}",
            status_code=303,
        )


# ── Form parsing helper ───────────────────────────────────────────────────────

def _parse_product_form(form) -> dict:
    """Convert raw multidict form data into a typed dict for the service layer."""

    # Strip commas and percent signs so "1,250.00" and "35%" parse cleanly.
    def _clean_num(raw: str) -> str:
        return raw.strip().replace(",", "").replace("%", "")

    def _float(key: str, default: float = 0.0) -> float:
        raw = _clean_num(str(form.get(key, "")))
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            label = key.replace("_", " ").title()
            raise ValueError(f"{label}: '{raw}' is not a valid number.")

    def _int(key: str, default: int = 0) -> int:
        raw = _clean_num(str(form.get(key, "")))
        if not raw:
            return default
        try:
            return int(float(raw))  # int(float()) handles "2.0" from browser autofill
        except ValueError:
            label = key.replace("_", " ").title()
            raise ValueError(f"{label}: '{raw}' is not a valid whole number.")

    def _opt_float(key: str):
        raw = _clean_num(str(form.get(key, "")))
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            label = key.replace("_", " ").title()
            raise ValueError(f"{label}: '{raw}' is not a valid number.")

    def _opt_int(key: str):
        raw = _clean_num(str(form.get(key, "")))
        if not raw:
            return None
        try:
            return int(float(raw))
        except ValueError:
            label = key.replace("_", " ").title()
            raise ValueError(f"{label}: '{raw}' is not a valid whole number.")

    category_id = _opt_int("category_id")

    return {
        "sku": str(form.get("sku", "")).strip().upper(),
        "title": str(form.get("title", "")).strip(),
        "description": str(form.get("description", "")).strip(),
        "brand": str(form.get("brand", "")).strip(),
        "manufacturer": str(form.get("manufacturer", "")).strip(),
        "cost": _float("cost"),
        "markup_pct": _opt_float("markup_pct"),
        "price_override": _opt_float("price_override"),
        "category_id": category_id,
        "has_core": bool(form.get("has_core")),
        "vendor_core_charge": _float("vendor_core_charge"),
        "customer_core_charge": _float("customer_core_charge"),
        "reorder_point": _int("reorder_point"),
        "notes": str(form.get("notes", "")).strip(),
        "internal_notes": str(form.get("internal_notes", "")).strip(),
        # Warranty fields
        "is_warrantable": bool(form.get("is_warrantable")),
        "manufacturer_warranty_months": _int("manufacturer_warranty_months"),
        "supplier_warranty_months": _int("supplier_warranty_months"),
        "supplier_warranty_type": str(form.get("supplier_warranty_type", "parts_only")).strip() or "parts_only",
        "jaks_warranty_months": _int("jaks_warranty_months"),
        "warranty_percentage": _float("warranty_percentage", 10.0),
    }
