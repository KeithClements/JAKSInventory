"""
app/services/document_links.py
==============================
Single source of truth for the cross-document relationship graph.

Every workspace (Quote, Sales Order, Invoice, Purchase Order) renders a compact
"Linked" chip strip under its action bar. Rather than scatter the traversal
logic across the templates, ``related_documents(db, entity)`` resolves the whole
neighbourhood of a document into a normalised list of :class:`DocLink`s that the
``linked_strip`` macro renders identically everywhere.

The graph is read entirely off relationships/columns that already exist — there
is **no new schema**. The one link that was never stored structurally (a PO ->
the SO it was auto-created to source) is recovered by walking
``SOLine.linked_po_line_id`` backwards (:func:`_sales_orders_sourced_by`).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.constants import QuoteOutcome
from app.models.invoice import Invoice
from app.models.purchase_order import PurchaseOrder, VendorBill
from app.models.quote import Quote, SalesOrder, SOLine

# Coarse semantic tone — the linked_strip macro maps these five buckets onto the
# §4 permitted chip colour families. Keeping the resolver tone-only (not class
# strings) preserves the plan's "colour lives in the template" separation.
NEUTRAL, INFO, WARNING, SUCCESS, DANGER = (
    "neutral", "info", "warning", "success", "danger",
)

# Display order: sources (upstream) read first, then siblings, then children.
_GROUP_ORDER = {"upstream": 0, "related": 1, "downstream": 2}


@dataclass(frozen=True)
class DocLink:
    """One edge in the document graph, normalised for rendering."""
    relation: str       # short relationship label, e.g. "Source Quote", "Sourcing"
    kind: str           # document type, e.g. "Quote", "SO", "Invoice", "PO"
    number: str         # human id, e.g. "SO-2026-0002"
    url: str            # workspace url
    tone: str           # one of NEUTRAL/INFO/WARNING/SUCCESS/DANGER
    status_label: str   # current status of the linked doc (shown in the tooltip)
    group: str          # upstream | related | downstream


# ── per-document status -> (tone, label) ────────────────────────────────────────
_SO_TONE = {
    "open": (INFO, "Open"), "partial": (WARNING, "Partial"), "hold": (WARNING, "On Hold"),
    "fulfilled": (SUCCESS, "Fulfilled"), "invoiced": (SUCCESS, "Invoiced"),
    "cancelled": (DANGER, "Cancelled"),
}
_INV_TONE = {
    "draft": (NEUTRAL, "Draft"), "open": (INFO, "Open"), "partial": (WARNING, "Partial"),
    "paid": (SUCCESS, "Paid"), "void": (DANGER, "Void"),
}
_PO_TONE = {
    "draft": (NEUTRAL, "Draft"), "verbal_order": (INFO, "Verbal Order"), "sent": (INFO, "Sent"),
    "partial": (WARNING, "Partial"), "received": (SUCCESS, "Received"),
    "billed": (SUCCESS, "Billed"), "cancelled": (DANGER, "Cancelled"),
}
_BILL_TONE = {
    "pending": (INFO, "Pending"), "approved": (SUCCESS, "Approved"),
    "discrepancy": (DANGER, "Discrepancy"), "paid": (SUCCESS, "Paid"),
}


def _tone(mapping: dict, status) -> tuple[str, str]:
    key = str(status or "").lower()
    return mapping.get(key, (NEUTRAL, key.replace("_", " ").title() or "—"))


def _quote_link(q: Quote, relation: str, group: str) -> DocLink:
    # Quote has no single status column; derive a coarse state from conversion,
    # expiry and outcome so the chip tone is still meaningful.
    if q.converted_to_so_id or q.converted_to_invoice_id:
        tone, label = SUCCESS, "Converted"
    elif q.is_expired:
        tone, label = NEUTRAL, "Expired"
    elif q.outcome == QuoteOutcome.WON:
        tone, label = SUCCESS, "Won"
    elif q.outcome == QuoteOutcome.LOST:
        tone, label = DANGER, "Lost"
    else:
        tone, label = INFO, "Open"
    return DocLink(relation, "Quote", q.quote_number, f"/quotes/{q.id}", tone, label, group)


def _so_link(so: SalesOrder, relation: str, group: str) -> DocLink:
    tone, label = _tone(_SO_TONE, so.status)
    return DocLink(relation, "SO", so.so_number, f"/sales-orders/{so.id}", tone, label, group)


def _invoice_link(inv: Invoice, relation: str, group: str) -> DocLink:
    tone, label = _tone(_INV_TONE, inv.status)
    return DocLink(relation, "Invoice", inv.invoice_number, f"/invoices/{inv.id}", tone, label, group)


def _po_link(po: PurchaseOrder, relation: str, group: str) -> DocLink:
    tone, label = _tone(_PO_TONE, po.status)
    return DocLink(relation, "PO", po.po_number, f"/purchase-orders/{po.id}", tone, label, group)


def _bill_link(bill: VendorBill, relation: str, group: str) -> DocLink:
    # Vendor bills have no standalone workspace — they live on their PO's "Vendor
    # Bills" card, so the chip links to that card's anchor on the PO page. The
    # chip's value is surfacing the bill's status (e.g. Discrepancy) at a glance.
    tone, label = _tone(_BILL_TONE, bill.status)
    number = bill.bill_number or f"Bill #{bill.id}"
    url = f"/purchase-orders/{bill.po_id}#vendor-bills" if bill.po_id else "#"
    return DocLink(relation, "Bill", number, url, tone, label, group)


# ── reverse traversal: which SOs does this PO source? ──────────────────────────
def _sales_orders_sourced_by(db: Session, po: PurchaseOrder) -> list[SalesOrder]:
    """PO -> SO(s), recovered by walking ``SOLine.linked_po_line_id`` backwards.

    ``create_po_for_line`` stores the link on the SO side only (the SO line points
    at the PO line). To answer "what SO is this PO sourcing?" we find every SO
    line whose ``linked_po_line_id`` is one of this PO's lines, then load the
    distinct parent SOs. No PO->SO column required.
    """
    line_ids = [ln.id for ln in po.lines]
    if not line_ids:
        return []
    so_ids = [
        r[0]
        for r in db.query(SOLine.so_id)
        .filter(SOLine.linked_po_line_id.in_(line_ids))
        .distinct()
        .all()
    ]
    if not so_ids:
        return []
    return db.query(SalesOrder).filter(SalesOrder.id.in_(so_ids)).all()


def _purchase_orders_sourcing(so: SalesOrder) -> list[PurchaseOrder]:
    """SO -> the distinct POs created to source its backordered lines."""
    seen: set[int] = set()
    pos: list[PurchaseOrder] = []
    for ln in so.lines:
        lp = ln.linked_po_line
        if lp is not None and lp.po is not None and lp.po.id not in seen:
            seen.add(lp.po.id)
            pos.append(lp.po)
    return pos


def related_documents(db: Session, entity) -> list[DocLink]:
    """Resolve the linked-document neighbourhood of any workspace document.

    Returns upstream sources, downstream children and siblings as a flat,
    display-ordered list of :class:`DocLink`. Safe to call on any of the four
    document types; returns ``[]`` for anything else.
    """
    links: list[DocLink] = []
    name = type(entity).__name__

    if name == "Quote":
        if entity.converted_to_so_id:
            so = db.get(SalesOrder, entity.converted_to_so_id)
            if so:
                links.append(_so_link(so, "Sales Order", "downstream"))
        if entity.converted_to_invoice_id:
            inv = db.get(Invoice, entity.converted_to_invoice_id)
            if inv:
                links.append(_invoice_link(inv, "Invoice", "downstream"))

    elif name == "SalesOrder":
        if entity.quote_id:
            q = db.get(Quote, entity.quote_id)
            if q:
                links.append(_quote_link(q, "Source Quote", "upstream"))
        for inv in entity.invoices:
            links.append(_invoice_link(inv, "Invoice", "downstream"))
        for po in _purchase_orders_sourcing(entity):
            links.append(_po_link(po, "Purchase Order", "downstream"))

    elif name == "Invoice":
        if entity.sales_order_id:
            so = db.get(SalesOrder, entity.sales_order_id)
            if so:
                links.append(_so_link(so, "Sales Order", "upstream"))
        if entity.quote_id:
            q = db.get(Quote, entity.quote_id)
            if q:
                links.append(_quote_link(q, "Source Quote", "upstream"))

    elif name == "PurchaseOrder":
        for so in _sales_orders_sourced_by(db, entity):
            links.append(_so_link(so, "Sourcing", "upstream"))
        for bill in entity.bills:
            links.append(_bill_link(bill, "Vendor Bill", "downstream"))

    links.sort(key=lambda d: _GROUP_ORDER.get(d.group, 9))
    return links
