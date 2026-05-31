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
        "match_type": r.match_type,            # part_number | cross_ref | vendor_sku | description
        "cross_ref_number": r.cross_ref_number,
        "last_sold_price": r.last_sold_price,
        "last_sold_date": r.last_sold_date,
        "has_core": bool(product.has_core) if product is not None else False,
        "vendor_core_charge": product.vendor_core_charge if product is not None else 0.0,
        "customer_core_charge": product.customer_core_charge if product is not None else 0.0,
    }


def search_products_json(q: str, db: Session, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """
    Shared search → serialize. Used by both /line-items/product-search and the
    legacy /quotes/product-search alias so the JSON contract has one source.

    Returns [] for queries shorter than MIN_QUERY_LEN.
    """
    q = (q or "").strip()
    if len(q) < MIN_QUERY_LEN:
        return []
    results = SearchService(db).search_products(q, limit=limit)
    pids = [r.product_id for r in results]
    products = (
        {p.id: p for p in db.query(Product).filter(Product.id.in_(pids)).all()}
        if pids
        else {}
    )
    return [serialize_product_result(r, products.get(r.product_id)) for r in results]


@router.get("/product-search")
def line_item_product_search(q: str = "", db: Session = Depends(get_db)):
    """
    Canonical JSON product search for the unified line-item builder.

    Query: ?q=<text>  (min 2 chars; returns [] otherwise).
    Matches SKU / OEM / cross-ref / vendor SKU (separator + case insensitive),
    then description.  Same shape for every document type — the front-end picks
    which fields to show (sell price for Quote/SO/Invoice, cost for PO).
    """
    return JSONResponse(search_products_json(q, db))
