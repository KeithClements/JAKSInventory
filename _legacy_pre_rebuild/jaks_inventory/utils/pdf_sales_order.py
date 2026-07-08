"""PDF sales-order generator.

Mirrors the layout/look of :func:`jaks_inventory.utils.quote_pdf.generate_quote_pdf`
but adapts a sales-order dict (from ``db.get_sales_order``) into the shape
the quote PDF builder expects, then re-titles the document.
"""
from __future__ import annotations

from pathlib import Path

from .quote_pdf import generate_quote_pdf, OUTPUT_DIR as _QUOTE_OUTPUT_DIR

OUTPUT_DIR = _QUOTE_OUTPUT_DIR.parent / "sales_orders"


def _so_to_quote_dict(so: dict) -> dict:
    """Adapt SO line/header field names to the quote PDF's expectations."""
    raw_lines = so.get("lines")
    if not raw_lines and so.get("id"):
        # ``get_sales_order`` returns header-only; fetch lines on demand.
        try:
            from db import get_so_lines
            raw_lines = get_so_lines(int(so["id"])) or []
        except Exception:
            raw_lines = []
    lines = []
    for ln in raw_lines or []:
        # Quote PDF reads ``qty``; SO lines store ``quantity_ordered``.
        qty = ln.get("qty")
        if qty in (None, ""):
            qty = ln.get("quantity_ordered") or ln.get("quantity") or 1
        lines.append({
            **ln,
            "qty": int(qty or 1),
            "is_selected": 1,  # every SO line prints
        })

    return {
        **so,
        # Re-key the SO number into the slot the quote header reads from.
        "quote_number": so.get("so_number") or so.get("quote_number") or "",
        "quote_date": so.get("created_at") or so.get("quote_date") or "",
        "lines": lines,
    }


def generate_sales_order_pdf(so: dict, output_path: str | None = None) -> str:
    """Generate a branded PDF for a sales order."""
    so_num = so.get("so_number") or "DRAFT"
    if not output_path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        safe = str(so_num).replace("/", "-").replace("\\", "-")
        output_path = str(OUTPUT_DIR / f"{safe}.pdf")

    extra: list[str] = []
    if so.get("tracking_number"):
        extra.extend(["Tracking #:", str(so["tracking_number"])])
    if so.get("ship_method"):
        extra.extend(["Ship Via:", str(so["ship_method"])])
    if so.get("promised_date"):
        extra.extend(["Promised:", str(so["promised_date"])[:10]])

    return generate_quote_pdf(
        _so_to_quote_dict(so),
        output_path=output_path,
        doc_title="SALES ORDER",
        cust_label="SHIP TO",
        show_expiration=False,
        extra_intro_lines=extra or None,
    )
