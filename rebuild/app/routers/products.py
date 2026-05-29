from __future__ import annotations

import html
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.constants import CrossRefType, SuggestedSellType
from app.deps import get_db
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

CURRENT_USER_ID = 1

MANUFACTURERS = [
    "Cummins", "Caterpillar", "Detroit Diesel", "Mack", "Volvo", "International",
]


# ── helpers ──────────────────────────────────────────────────────────────────

def _svc(db: Session) -> ProductService:
    return ProductService(db, current_user_id=CURRENT_USER_ID)


def _ss_svc(db: Session) -> SuggestedSellService:
    return SuggestedSellService(db, current_user_id=CURRENT_USER_ID)


def _vendors(db: Session) -> list[Vendor]:
    return db.query(Vendor).filter(Vendor.is_active == True).order_by(Vendor.name).all()  # noqa: E712


def _categories(db: Session):
    from app.models.product import ProductCategory
    return (
        db.query(ProductCategory)
        .filter(ProductCategory.is_active == True)  # noqa: E712
        .order_by(ProductCategory.name)
        .all()
    )


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def product_list(
    request: Request,
    q: str = "",
    tab: str = "all",
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
    elif tab == "out_of_stock":
        query = base.filter(Product.qty_on_hand == 0)
    elif tab == "special_order":
        query = base.filter(Product.special_order_only == True)  # noqa: E712
    else:
        query = base

    # Search
    if q:
        like = f"%{q}%"
        query = query.filter(
            Product.sku.ilike(like)
            | Product.title.ilike(like)
            | Product.manufacturer.ilike(like)
            | Product.brand.ilike(like)
        )

    products = query.order_by(Product.sku).all()

    # Tab counts (always based on full active set, ignoring current tab/search)
    counts = {
        "all": base.count(),
        "low_stock": base.filter(
            Product.reorder_point > 0,
            Product.qty_on_hand > 0,
            Product.qty_on_hand <= Product.reorder_point,
        ).count(),
        "out_of_stock": db.query(Product).filter(
            Product.is_active == True, Product.qty_on_hand == 0  # noqa: E712
        ).count(),
        "special_order": db.query(Product).filter(
            Product.is_active == True, Product.special_order_only == True  # noqa: E712
        ).count(),
    }

    return templates.TemplateResponse("products/list.html", {
        "request": request,
        "products": products,
        "q": q,
        "tab": tab,
        "counts": counts,
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
    return templates.TemplateResponse("products/_preview_panel.html", {
        "request": request,
        "p": p,
    })


# ── New ──────────────────────────────────────────────────────────────────────

@router.get("/new", response_class=HTMLResponse)
def product_new(request: Request, db: Session = Depends(get_db)):
    default_markup = get_setting_value_db(db, "default_markup_pct", "30.0")
    return templates.TemplateResponse("products/new.html", {
        "request": request,
        "vendors": _vendors(db),
        "categories": _categories(db),
        "manufacturers": MANUFACTURERS,
        "default_markup": default_markup,
    })


@router.post("/new", response_class=HTMLResponse)
async def product_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        data = _parse_product_form(form)
        product = _svc(db).create_product(data)
        return RedirectResponse(f"/products/{product.id}", status_code=303)
    except ValueError as exc:
        default_markup = get_setting_value_db(db, "default_markup_pct", "30.0")
        return templates.TemplateResponse("products/new.html", {
            "request": request,
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
    return templates.TemplateResponse("products/_quick_create.html", {"request": request})


@router.post("/quick-create", response_class=HTMLResponse)
async def product_quick_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    sku_suffix = str(form.get("sku_suffix", "")).strip().upper()
    title = str(form.get("title", "")).strip()
    if not sku_suffix or not title:
        return HTMLResponse(
            '<p class="text-sm text-red-600 font-medium px-5 py-3">SKU and title are required.</p>',
            status_code=422,
        )
    sku = f"JAKS-{sku_suffix}"
    svc = _svc(db)
    try:
        product = svc.create_product({
            "sku": sku,
            "title": title,
            "cost": float(form.get("cost") or 0),
            "markup_pct": float(form.get("markup_pct") or 30),
            "has_core": bool(form.get("has_core")),
            "vendor_core_charge": float(form.get("vendor_core_charge") or 0),
            "customer_core_charge": float(form.get("customer_core_charge") or 0),
        })
    except ValueError as exc:
        return HTMLResponse(
            f'<p class="text-sm text-red-600 font-medium px-5 py-3">{exc}</p>',
            status_code=422,
        )
    db.commit()
    from app.utils import calc_sell_price
    sell = (
        product.price_override
        if (product.price_override and product.price_override > 0)
        else calc_sell_price(product.cost, product.markup_pct or 30.0)
    )
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
    return templates.TemplateResponse("products/detail.html", {
        "request": request,
        "product": p,
        "vendors": _vendors(db),
        "categories": _categories(db),
        "manufacturers": MANUFACTURERS,
        "cross_ref_types": list(CrossRefType),
        "suggested_sell_types": list(SuggestedSellType),
        "ok": ok or (saved and "Saved.") or "",
        "error": error,
    })


@router.post("/{product_id}", response_class=HTMLResponse)
async def product_update(product_id: int, request: Request, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == product_id).first()
    if not p:
        return RedirectResponse("/products/", status_code=303)
    form = await request.form()
    try:
        data = _parse_product_form(form)
        _svc(db).update_product(product_id, data)
        return RedirectResponse(f"/products/{product_id}?saved=1", status_code=303)
    except ValueError as exc:
        # Re-fetch product fresh (may be partially updated in service before error)
        p = db.query(Product).filter(Product.id == product_id).first()
        return templates.TemplateResponse("products/detail.html", {
            "request": request,
            "product": p,
            "vendors": _vendors(db),
            "categories": _categories(db),
            "manufacturers": MANUFACTURERS,
            "cross_ref_types": list(CrossRefType),
            "error": str(exc),
        }, status_code=422)


# ── Deactivate ───────────────────────────────────────────────────────────────

@router.post("/{product_id}/deactivate", response_class=HTMLResponse)
async def product_deactivate(product_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    reason = str(form.get("reason", "archived via UI")).strip() or "archived via UI"
    try:
        _svc(db).deactivate_product(product_id, reason)
    except ValueError:
        pass
    return RedirectResponse("/products/", status_code=303)


# ── Vendor Sources ────────────────────────────────────────────────────────────

@router.post("/{product_id}/vendor-sources", response_class=HTMLResponse)
async def vendor_source_add(product_id: int, request: Request, db: Session = Depends(get_db)):
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
        source = _svc(db).add_vendor_source(product_id, vendor_id, data)
        db.refresh(source)
        return templates.TemplateResponse("products/_vendor_source_row.html", {
            "request": request,
            "source": source,
            "product_id": product_id,
        })
    except ValueError as exc:
        return HTMLResponse(
            f'<tr><td colspan="6" class="px-4 py-2 text-red-600 text-sm">{exc}</td></tr>',
            status_code=422,
        )


@router.post("/{product_id}/vendor-sources/{source_id}/prefer", response_class=HTMLResponse)
def vendor_source_prefer(product_id: int, source_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        _svc(db).set_preferred_vendor(product_id, source_id)
        # Return the full updated sources list partial so preferred badges refresh
        p = db.query(Product).filter(Product.id == product_id).first()
        return templates.TemplateResponse("products/_vendor_sources_table.html", {
            "request": request,
            "product": p,
            "product_id": product_id,
        })
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
async def cross_ref_add(product_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    ref_number = str(form.get("ref_number", "")).strip()
    if not ref_number:
        return HTMLResponse('<tr><td colspan="5" class="px-4 py-2 text-red-600 text-sm">Part number is required.</td></tr>', status_code=422)
    ref_type = str(form.get("ref_type", CrossRefType.OEM)).strip()
    brand = str(form.get("brand", "")).strip()
    status = str(form.get("status", "proven")).strip() or "proven"
    try:
        _svc(db).add_cross_reference(product_id, ref_type, ref_number, brand or None, status=status)
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
        return templates.TemplateResponse("products/_cross_ref_row.html", {
            "request": request,
            "xref": xref,
        })
    except ValueError as exc:
        return HTMLResponse(
            f'<tr><td colspan="5" class="px-4 py-2 text-red-600 text-sm">{exc}</td></tr>',
            status_code=422,
        )


@router.delete("/{product_id}/cross-refs/{xref_id}", response_class=HTMLResponse)
def cross_ref_remove(product_id: int, xref_id: int, db: Session = Depends(get_db)):
    try:
        _svc(db).remove_cross_reference(xref_id)
    except ValueError:
        pass
    return HTMLResponse("", status_code=200)


# ── Cross Reference Status ────────────────────────────────────────────────────

@router.patch("/{product_id}/cross-refs/{xref_id}/status", response_class=HTMLResponse)
async def cross_ref_update_status(
    product_id: int, xref_id: int, request: Request, db: Session = Depends(get_db)
):
    form = await request.form()
    status = str(form.get("status", "proven")).strip() or "proven"
    try:
        xref = _svc(db).update_cross_reference_status(xref_id, status)
        return templates.TemplateResponse("products/_cross_ref_row.html", {
            "request": request,
            "xref": xref,
        })
    except ValueError as exc:
        return HTMLResponse(
            f'<tr><td colspan="5" class="px-4 py-2 text-red-600 text-sm">{exc}</td></tr>',
            status_code=422,
        )


# ── Enrichment Panel ──────────────────────────────────────────────────────────

@router.get("/{product_id}/enrich-panel", response_class=HTMLResponse)
def product_enrich_panel(
    product_id: int, request: Request, source: str = "", db: Session = Depends(get_db)
):
    """Load the enrichment slide-over content for a given vendor source (pai/hhp/atl)."""
    from app.models.scraper import ScraperSource
    scraper_source = None
    if source:
        scraper_source = (
            db.query(ScraperSource)
            .filter(ScraperSource.name.ilike(source))
            .first()
        )
    return templates.TemplateResponse("products/_enrich_panel.html", {
        "request": request,
        "product_id": product_id,
        "source": scraper_source,
    })


# ── Images ────────────────────────────────────────────────────────────────────

@router.post("/{product_id}/images", response_class=HTMLResponse)
async def product_image_upload(
    product_id: int,
    request: Request,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
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

    _svc(db).add_product_image(product_id, rel_path)
    db.refresh(p)

    return templates.TemplateResponse("products/_images_grid.html", {
        "request": request,
        "images": p.images,
        "product_id": product_id,
    })


@router.delete("/{product_id}/images/{image_id}", response_class=HTMLResponse)
def product_image_remove(product_id: int, image_id: int, db: Session = Depends(get_db)):
    try:
        _svc(db).remove_product_image(product_id, image_id)
    except ValueError:
        pass
    return HTMLResponse("", status_code=200)


@router.post("/{product_id}/images/{image_id}/set-primary", response_class=HTMLResponse)
def product_image_set_primary(
    product_id: int, image_id: int, request: Request, db: Session = Depends(get_db)
):
    try:
        _svc(db).set_primary_image(product_id, image_id)
    except ValueError as exc:
        return HTMLResponse(f'<p class="text-sm text-red-600 p-4">{exc}</p>', status_code=422)
    p = db.query(Product).filter(Product.id == product_id).first()
    return templates.TemplateResponse("products/_images_grid.html", {
        "request": request,
        "images": p.images if p else [],
        "product_id": product_id,
    })


# ── Suggested Sells ──────────────────────────────────────────────────────────

@router.post("/{product_id}/suggested-sells", response_class=HTMLResponse)
async def suggested_sell_add(
    product_id: int, request: Request, db: Session = Depends(get_db)
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
        suggestion = _ss_svc(db).add_suggestion(
            product_id=product_id,
            suggested_product_id=suggested.id,
            relationship_type=rel_type,
            notes=notes,
        )
        return templates.TemplateResponse("products/_suggested_sell_row.html", {
            "request": request,
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
    product_id: int, suggestion_id: int, request: Request, db: Session = Depends(get_db)
):
    form = await request.form()
    data = {}
    if "relationship_type" in form:
        data["relationship_type"] = str(form["relationship_type"]).strip()
    if "notes" in form:
        data["notes"] = str(form["notes"]).strip()
    try:
        suggestion = _ss_svc(db).update_suggestion(suggestion_id, data)
        return templates.TemplateResponse("products/_suggested_sell_row.html", {
            "request": request,
            "suggestion": suggestion,
            "product_id": product_id,
        })
    except ValueError as exc:
        return HTMLResponse(f'<span class="text-red-600 text-sm">{exc}</span>', status_code=422)


@router.delete("/{product_id}/suggested-sells/{suggestion_id}", response_class=HTMLResponse)
def suggested_sell_remove(
    product_id: int, suggestion_id: int, db: Session = Depends(get_db)
):
    try:
        _ss_svc(db).remove_suggestion(suggestion_id)
    except ValueError:
        pass
    return HTMLResponse("", status_code=200)


# ── Inventory Adjustment ─────────────────────────────────────────────────────

@router.post("/{product_id}/adjust-inventory", response_class=HTMLResponse)
async def adjust_inventory_handler(
    product_id: int, request: Request, db: Session = Depends(get_db)
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
        svc = InventoryService(db, current_user_id=CURRENT_USER_ID)
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
    def _float(key: str, default: float = 0.0) -> float:
        raw = str(form.get(key, "")).strip()
        return float(raw) if raw else default

    def _int(key: str, default: int = 0) -> int:
        raw = str(form.get(key, "")).strip()
        return int(raw) if raw else default

    def _opt_float(key: str):
        raw = str(form.get(key, "")).strip()
        return float(raw) if raw else None

    def _opt_int(key: str):
        raw = str(form.get(key, "")).strip()
        return int(raw) if raw else None

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
