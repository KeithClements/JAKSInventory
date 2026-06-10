"""
app/services/serial_service.py
==============================
Serial number lifecycle (R3) — the first write paths for ProductSerialNumber.

For serialized products (cylinder heads etc.) the serial number is
warranty/liability data, so it is captured at the three inventory touchpoints:

  - PO receiving      → record_received_serials()  (status IN_STOCK,
                        linked to the POReceiptLine that brought it in)
  - Invoice finalize  → assign_serials_for_invoice() / assign_to_invoice_line()
                        (FIFO assignment, status SOLD, invoice_line_id set)
  - Invoice void      → release_for_invoice() (mirror of the inventory
                        restore: status back to IN_STOCK, invoice link cleared)

Design rules:
  - Serial capture is OPTIONAL and tolerant. Receiving with no serials is
    allowed (units are simply unconsumed), assignment with fewer serials than
    qty assigns what's there, and duplicates are skipped — never fatal.
  - COMMIT SEMANTICS: no method here commits. Callers own the transaction:
    the receive route commits its own follow-up transaction after
    POService.create_receipt has committed; InvoiceService.finalise /
    void_invoice commit at the end of their existing transactions and wrap
    the serial work fail-safe (a serial problem must never block money paths).
"""
from __future__ import annotations

import logging
import re

from app.constants import LineType, SerialNumberStatus
from app.models.product import Product, ProductSerialNumber
from app.services.base import BaseService

log = logging.getLogger(__name__)

# Textarea input is "comma or newline separated"; tolerate semicolons too.
_SPLIT_RE = re.compile(r"[,;\r\n]+")


def parse_serials(raw: str | None) -> list[str]:
    """Split a comma/newline-separated string into clean serial numbers.

    Trims whitespace, uppercases, drops empties, and de-dupes within the
    batch while preserving entry order.
    """
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for token in _SPLIT_RE.split(str(raw)):
        s = token.strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


class SerialService(BaseService):
    """Write paths for ProductSerialNumber. See module docstring for rules."""

    # ── Receiving ─────────────────────────────────────────────────────────────

    def record_received_serials(
        self,
        *,
        product_id: int,
        serials: list[str],
        po_receipt_line_id: int | None = None,
    ) -> dict:
        """Create IN_STOCK ProductSerialNumber rows for received units.

        Each serial is trimmed + uppercased. A serial that already exists for
        the SAME product (any status — a serial is unique per product for its
        lifetime) is skipped, not fatal. Returns
        ``{"recorded": [...], "skipped": [...]}`` so the caller can flash an
        info note about skipped duplicates.

        Does NOT commit — caller owns the transaction.
        """
        recorded: list[str] = []
        skipped: list[str] = []
        if not serials:
            return {"recorded": recorded, "skipped": skipped}

        existing: set[str] = {
            row[0]
            for row in (
                self.db.query(ProductSerialNumber.serial_number)
                .filter(ProductSerialNumber.product_id == product_id)
                .all()
            )
        }

        for raw in serials:
            s = str(raw).strip().upper()
            if not s:
                continue
            if s in existing:
                skipped.append(s)
                continue
            existing.add(s)  # also de-dupes within this batch
            self.db.add(
                ProductSerialNumber(
                    product_id=product_id,
                    serial_number=s,
                    status=SerialNumberStatus.IN_STOCK,
                    po_receipt_line_id=po_receipt_line_id,
                )
            )
            recorded.append(s)

        self.db.flush()
        if skipped:
            log.info(
                "record_received_serials: product %s — skipped %d duplicate serial(s): %s",
                product_id, len(skipped), ", ".join(skipped[:10]),
            )
        return {"recorded": recorded, "skipped": skipped}

    # ── Invoice assignment (finalize) ─────────────────────────────────────────

    def assign_to_invoice_line(self, invoice_line, qty: int | None = None) -> int:
        """FIFO-assign available IN_STOCK serials to an invoice line.

        Oldest serials first (lowest id = received earliest). Sets status to
        SOLD and links invoice_line_id. Tolerant by design: when fewer serials
        exist than qty, assigns what's there and returns the count assigned.

        Does NOT commit — caller owns the transaction.
        """
        if invoice_line is None or not invoice_line.product_id:
            return 0
        need = int(qty if qty is not None else (invoice_line.qty or 0))
        if need <= 0:
            return 0

        available = (
            self.db.query(ProductSerialNumber)
            .filter(
                ProductSerialNumber.product_id == invoice_line.product_id,
                ProductSerialNumber.status == SerialNumberStatus.IN_STOCK,
                ProductSerialNumber.invoice_line_id.is_(None),
            )
            .order_by(ProductSerialNumber.id.asc())  # FIFO: lowest id = oldest
            .limit(need)
            .all()
        )
        for sn in available:
            sn.status = SerialNumberStatus.SOLD
            sn.invoice_line_id = invoice_line.id

        if len(available) < need:
            log.info(
                "assign_to_invoice_line: product %s line %s — only %d of %d "
                "serial(s) available; assigned what exists",
                invoice_line.product_id, invoice_line.id, len(available), need,
            )
        self.db.flush()
        return len(available)

    def assign_serials_for_invoice(self, invoice) -> int:
        """Auto-assign serials for every serialized PRODUCT line on an invoice.

        Skips non-product lines, lines without a product, and products with
        has_serial_number=False (those are never touched). Returns the total
        number of serials assigned. Does NOT commit.
        """
        assigned = 0
        for ln in invoice.lines:
            if ln.line_type != LineType.PRODUCT or not ln.product_id:
                continue
            product = (
                self.db.query(Product).filter(Product.id == ln.product_id).first()
            )
            if not product or not product.has_serial_number:
                continue
            assigned += self.assign_to_invoice_line(ln)
        return assigned

    # ── Invoice void (release) ────────────────────────────────────────────────

    def release_for_invoice(self, invoice) -> int:
        """Release every serial assigned to this invoice's lines.

        Mirror of the void inventory restore: status back to IN_STOCK and the
        invoice_line link cleared (the receipt-line link is kept — provenance
        survives the void). Returns the number released. Does NOT commit.
        """
        line_ids = [ln.id for ln in invoice.lines if ln.id is not None]
        if not line_ids:
            return 0
        rows = (
            self.db.query(ProductSerialNumber)
            .filter(ProductSerialNumber.invoice_line_id.in_(line_ids))
            .all()
        )
        for sn in rows:
            sn.status = SerialNumberStatus.IN_STOCK
            sn.invoice_line_id = None
        self.db.flush()
        return len(rows)
