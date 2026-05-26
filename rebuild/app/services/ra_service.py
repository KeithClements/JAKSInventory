"""
app/services/ra_service.py
===========================
Return Authorization lifecycle — customer returns wrong/unused/damaged parts.

Workflow:
  create_ra()   → DRAFT
  approve_ra()  → OPEN   (authorizes the customer to ship parts back)
  receive_goods() → RECEIVED  (goods arrive, disposition logged, inventory updated)
  close_ra()    → CLOSED  (account credit applied)

CRMService owns Customer.credit_balance — this service delegates to it
rather than writing the field directly.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.constants import (
    AuditAction,
    EntityType,
    InventoryTxnType,
    RAStatus,
    ReturnDisposition,
)
from app.models.returns import ReturnAuthorization, ReturnLine
from app.services.base import BaseService
from app.settings_utils import bump_counter

log = logging.getLogger(__name__)


class RAService(BaseService):
    """Manages the full Return Authorization lifecycle."""

    def __init__(self, db: Session, current_user_id: int | None = None) -> None:
        super().__init__(db, current_user_id)

    # ── Create ────────────────────────────────────────────────────────────────

    def create_ra(
        self,
        customer_id: int,
        reason: str,
        lines: list[dict],
        invoice_id: int | None = None,
        notes: str = "",
        internal_notes: str = "",
    ) -> ReturnAuthorization:
        """
        Open a new Return Authorization in DRAFT status.

        lines items must contain:
            product_id       int | None
            description      str
            qty              int   (≥ 1)
            unit_price       float
            restocking_fee   float (default 0.0)
            disposition      str   (ReturnDisposition, default QUARANTINE)
        """
        if not lines:
            raise ValueError("Return authorization must have at least one line item")
        if not reason or not reason.strip():
            raise ValueError("Return reason is required")

        year = datetime.utcnow().year
        ra_number = bump_counter(self.db, "next_ra_number", "RA", year)

        ra = ReturnAuthorization(
            ra_number=ra_number,
            customer_id=customer_id,
            invoice_id=invoice_id,
            status=RAStatus.DRAFT,
            reason=reason.strip(),
            notes=notes.strip(),
            internal_notes=internal_notes.strip(),
        )
        self.db.add(ra)
        self.db.flush()

        for item in lines:
            qty = int(item.get("qty", 1))
            if qty < 1:
                raise ValueError("Line quantity must be at least 1")
            line = ReturnLine(
                ra_id=ra.id,
                product_id=item.get("product_id") or None,
                description=str(item.get("description", "")).strip(),
                qty=qty,
                unit_price=float(item.get("unit_price", 0.0)),
                restocking_fee=float(item.get("restocking_fee", 0.0)),
                disposition=str(item.get("disposition", ReturnDisposition.QUARANTINE)),
            )
            self.db.add(line)

        self.db.flush()
        self.audit(
            entity_type=EntityType.RETURN_AUTHORIZATION,
            entity_id=ra.id,
            action=AuditAction.CREATED,
            new_value={
                "ra_number": ra_number,
                "customer_id": customer_id,
                "invoice_id": invoice_id,
                "line_count": len(lines),
            },
        )
        self.db.commit()
        return ra

    # ── Approve ───────────────────────────────────────────────────────────────

    def approve_ra(
        self,
        ra_id: int,
        override_reason: str | None = None,
    ) -> ReturnAuthorization:
        """
        Approve the RA — DRAFT → OPEN.
        Sets approved_by_id to current_user_id.
        """
        ra = self._get_or_404(ra_id)
        if ra.status != RAStatus.DRAFT:
            raise ValueError(
                f"RA {ra.ra_number} is '{ra.status}', not DRAFT. "
                "Only DRAFT returns can be approved."
            )

        old_status = ra.status
        ra.status = RAStatus.OPEN
        ra.approved_by_id = self.current_user_id
        if override_reason:
            ra.override_reason = override_reason.strip()

        self.db.flush()
        self.audit(
            entity_type=EntityType.RETURN_AUTHORIZATION,
            entity_id=ra_id,
            action=AuditAction.STATUS_CHANGED,
            old_value={"status": old_status},
            new_value={"status": RAStatus.OPEN},
            notes="Return authorization approved",
        )
        self.db.commit()
        return ra

    # ── Receive Goods ─────────────────────────────────────────────────────────

    def receive_goods(
        self,
        ra_id: int,
        line_updates: list[dict],
    ) -> ReturnAuthorization:
        """
        Record receipt of returned goods.

        line_updates is a list of dicts, one per line:
            line_id            int
            disposition        str   (ReturnDisposition)
            qty_returned_to_stock int
            condition_notes    str | None

        For lines with disposition == RETURN_TO_STOCK, increments
        product.qty_on_hand and writes an InventoryTxn row.
        Transitions RA from OPEN → RECEIVED.
        """
        ra = self._get_or_404(ra_id)
        if ra.status not in (RAStatus.OPEN, RAStatus.DRAFT):
            raise ValueError(
                f"RA {ra.ra_number} is '{ra.status}'. "
                "Can only receive goods on OPEN or DRAFT returns."
            )

        now = datetime.utcnow()
        line_map = {ln.id: ln for ln in ra.lines}

        for upd in line_updates:
            line_id = int(upd["line_id"])
            line = line_map.get(line_id)
            if not line:
                continue

            disposition = str(upd.get("disposition", ReturnDisposition.QUARANTINE))
            qty_to_stock = max(0, int(upd.get("qty_returned_to_stock", 0)))
            condition_notes = str(upd.get("condition_notes", "")).strip() or None

            line.disposition = disposition
            line.condition_notes = condition_notes
            line.inspected_by_id = self.current_user_id
            line.inspected_at = now

            # Validate stock qty doesn't exceed line qty
            if qty_to_stock > line.qty:
                qty_to_stock = line.qty
            line.qty_returned_to_stock = qty_to_stock

            # Update inventory for RETURN_TO_STOCK disposition
            if disposition == ReturnDisposition.RETURN_TO_STOCK and qty_to_stock > 0:
                self._return_to_stock(line, qty_to_stock, ra.ra_number)

        old_status = ra.status
        ra.status = RAStatus.RECEIVED
        self.db.flush()
        self.audit(
            entity_type=EntityType.RETURN_AUTHORIZATION,
            entity_id=ra_id,
            action=AuditAction.STATUS_CHANGED,
            old_value={"status": old_status},
            new_value={"status": RAStatus.RECEIVED},
            notes=f"Goods received — {len(line_updates)} line(s) processed",
        )
        self.db.commit()
        return ra

    # ── Close ─────────────────────────────────────────────────────────────────

    def close_ra(self, ra_id: int) -> ReturnAuthorization:
        """
        Close the RA and apply account credit — RECEIVED (or OPEN) → CLOSED.
        Credit is applied via CRMService.add_credit (sole owner of credit_balance).
        """
        ra = self._get_or_404(ra_id)
        if ra.status not in (RAStatus.RECEIVED, RAStatus.OPEN):
            raise ValueError(
                f"RA {ra.ra_number} is '{ra.status}'. "
                "Can only close RECEIVED or OPEN returns."
            )

        credit = ra.total_credit
        old_status = ra.status

        # Apply credit FIRST (CRMService is sole owner of credit_balance and commits internally).
        # Status update happens after that commit — same two-phase pattern as WarrantyService.
        if credit > 0:
            from app.services.crm_service import CRMService
            CRMService(self.db, self.current_user_id).add_credit(
                customer_id=ra.customer_id,
                amount=credit,
                reason=f"Return authorization {ra.ra_number}",
            )

        # CRMService committed (or no credit to apply) — now close the RA.
        ra.status = RAStatus.CLOSED
        self.audit(
            entity_type=EntityType.RETURN_AUTHORIZATION,
            entity_id=ra_id,
            action=AuditAction.STATUS_CHANGED,
            old_value={"status": old_status},
            new_value={
                "status": RAStatus.CLOSED,
                **({"credit_applied": credit} if credit > 0 else {}),
            },
            notes=(
                f"Return closed — ${credit:.2f} credit applied to customer account"
                if credit > 0
                else "Return closed — no credit applicable"
            ),
        )
        self.db.commit()

        return ra

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_or_404(self, ra_id: int) -> ReturnAuthorization:
        ra = self.db.query(ReturnAuthorization).filter(ReturnAuthorization.id == ra_id).first()
        if not ra:
            raise ValueError(f"Return authorization #{ra_id} not found")
        return ra

    def _return_to_stock(self, line: ReturnLine, qty: int, ra_number: str) -> None:
        """Bump product qty_on_hand and write an inventory transaction row."""
        if not line.product_id:
            return

        from app.models.product import Product
        from app.models.inventory import InventoryTransaction

        product = self.db.query(Product).filter(Product.id == line.product_id).first()
        if not product:
            return

        old_qty = product.qty_on_hand
        product.qty_on_hand = (product.qty_on_hand or 0) + qty

        txn = InventoryTransaction(
            product_id=line.product_id,
            transaction_type=InventoryTxnType.RETURN_TO_STOCK,
            qty_change=qty,
            qty_after=product.qty_on_hand,
            reference_type=EntityType.RETURN_AUTHORIZATION,
            reference_id=line.ra_id,
            notes=f"Customer return — {ra_number}",
            performed_by_id=self.current_user_id,
        )
        self.db.add(txn)
        self.db.flush()
