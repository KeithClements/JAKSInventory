"""
app/routers/line_items.py
==========================
Unified product-search endpoint for the line-item builder shared by Quote, SO,
Invoice, and PO.

ONE JSON contract, backed by SearchService — SKU / OEM / cross-ref / vendor-SKU,
separator- and case-insensitive ("OK-1" == "ok1") — then description.  Every
workspace's line-adder calls this single endpoint instead of four divergent
per-document searches.

See LINE_ITEM_BUILDER_CONTRACT.md for the full JSON shape, the add-line POST
contract, and the UI migration checklist.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.product import Product
from app.services.search_service import ProductSearchResult, SearchService

router = APIRouter(prefix="/line-items", tags=["line-items"])

# Min query length — shorter strings return [] (matches the per-doc behaviour).
MIN_QUERY_LEN = 2
DEFAULT_LIMIT = 8

# match_type → result-chip label shown in every line adder.  R3: 'competitor'
# (R2 SearchService match on CompetitorPrice.competitor_part_number) gets its
# own COMP badge instead of falling back to the default PART chip.
MATCH_TYPE_LABELS = {
    "part_number": "PART",
    "barcode": "SCAN",         # §21 — matched the product's barcode/UPC
    "cross_ref": "OEM",
    "vendor_sku": "VEND",
    "competitor": "COMP",
    "engine_app": "ENGINE",    # §21 — matched a ProductApplication (engine fit)
    "description": "DESC",
}


def serialize_product_result(r: ProductSearchResult, product: Product | None = None) -> dict:
    """
    One JSON shape for a product-search hit, consumed by every workspace's
    line-adder.

    Includes the canonical keys (``sku`` / ``title`` / ``unit_cost``) AND the
    legacy aliases (``part_number`` / ``description`` / ``current_cost``) so the
    existing quote front-end keeps working unchanged, plus the core fields POs
    and core-bearing customer docs need.

    ``product`` is the ORM row (when available) — used only for fields the
    search dataclass doesn't carry (qty_available, core charges).
    """
    return {
        "product_id": r.product_id,
        "sku": r.part_number,
        "part_number": r.part_number,          # legacy alias (quote front-end)
        "title": r.description,
        "description": r.description,          # legacy alias
        "unit_cost": r.current_cost,
        "current_cost": r.current_cost,        # legacy alias
        "suggested_sell": r.suggested_sell,
        "qty_on_hand": r.qty_on_hand,
        "qty_available": product.qty_available if product is not None else r.qty_on_hand,
        "vendor_name": r.vendor_name,
        "match_type": r.match_type,            # part_number | cross_ref | vendor_sku | competitor | description
        "match_label": MATCH_TYPE_LABELS.get(r.match_type, "PART"),
        "cross_ref_number": r.cross_ref_number,
        "last_sold_price": r.last_sold_price,
        "last_sold_date": r.last_sold_date,
        "has_core": bool(product.has_core) if product is not None else False,
        "vendor_core_charge": product.vendor_core_charge if product is not None else 0.0,
        "customer_core_charge": product.customer_core_charge if product is not None else 0.0,
    }


def search_products_json(
    q: str, db: Session, limit: int = DEFAULT_LIMIT
) -> tuple[list[dict], int]:
    """
    Shared search → serialize. One source for the /line-items/product-search
    JSON contract.

    Returns (results, total_matches). ``total_matches`` is the DISTINCT product
    count across every search strategy — the M in the line-adder's
    "showing N of M" hint. The full count union is only computed when the
    slice actually filled up (M can exceed N only then); a short result list
    IS the total. Returns ([], 0) for queries shorter than MIN_QUERY_LEN.
    """
    q = (q or "").strip()
    if len(q) < MIN_QUERY_LEN:
        return [], 0
    svc = SearchService(db)
    results = svc.search_products(q, limit=limit)
    total = svc.count_product_matches(q) if len(results) >= limit else len(results)
    # The count is a distinct-product UNION mirroring the search filters; never
    # report fewer total matches than rows actually shown.
    total = max(total, len(results))
    pids = [r.product_id for r in results]
    products = (
        {p.id: p for p in db.query(Product).filter(Product.id.in_(pids)).all()}
        if pids
        else {}
    )
    return (
        [serialize_product_result(r, products.get(r.product_id)) for r in results],
        total,
    )


@router.get("/product-search")
def line_item_product_search(q: str = "", db: Session = Depends(get_db)):
    """
    Canonical JSON product search for the unified line-item builder.

    Query: ?q=<text>  (min 2 chars; returns [] otherwise).
    Matches SKU / OEM / cross-ref / vendor SKU (separator + case insensitive),
    then description.  Same shape for every document type — the front-end picks
    which fields to show (sell price for Quote/SO/Invoice, cost for PO).

    The body stays a bare JSON ARRAY — existing consumers (the shared
    line-adder AND the product-detail special-order box) parse it as a list —
    so the total match count rides in the X-Total-Matches response header
    instead of a wrapper object.
    """
    results, total = search_products_json(q, db)
    return JSONResponse(results, headers={"X-Total-Matches": str(total)})
