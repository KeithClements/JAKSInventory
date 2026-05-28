"""
app/services/vendor_return_service.py
======================================
R11 — Vendor returns for non-core merchandise.

Distinct from VendorCoreReturn (which handles core returns to vendor for credit).
This handles general merchandise: wrong part ordered, defective, etc.

Number format: VR-YYYY-NNNN

Lifecycle:
  DRAFT     — created, items selected, not yet shipped
  SHIPPED   — packed + shipped to vendor (tracking + RMA captured)
  ACCEPTED  — vendor approved credit (full)
  PARTIAL   — vendor approved partial credit
  REJECTED  — vendor refused credit
  CLOSED    — finalized (after vendor decision)

When ACCEPTED or PARTIAL, VendorReturnService auto-creates a VendorCreditMemo
with trigger=VENDOR_RETURN_ACCEPTED linked to the VR.

Permission: ISSUE_CREDIT_MEMO (BOOKKEEPING + ADMIN).
"""
from __future__ import annotations

from datetime import datetime

from app.constants import (
    AuditAction, EntityType,
    InventoryTxnType, Permission,
    VendorCreditMemoTrigger,
    VendorReturnLineOutcome, VendorReturnStatus,
)
from app.models.inventory import InventoryTransaction
from app.models.product import Product
from app.models.vendor import Vendor
from app.models.vendor_return import VendorReturn, VendorReturnLine
from app.settings_utils import bump_counter
from app.services.base import BaseService


class VendorReturnService(BaseService):

    # ── Create ────────────────────────────────────────────────────────────────

    def create_vendor_return(
        self,
        vendor_id: int,
        lines: list[dict],
        reason: str,
        original_po_id: int | None = None,
        original_vendor_bill_id: int | None = None,
        notes: str = "",
    ) -> VendorReturn:
        """
        Create a vendor return in DRAFT status.

        Args:
            vendor_id:               vendor receiving the return
            lines:                   list of {product_id?, description, qty,
                                              expected_unit_credit, restocking_fee?}
            reason:                  reason for return (required)
            original_po_id:          back-ref to original PO if applicable
            original_vendor_bill_id: back-ref to vendor bill if applicable
            notes:                   free-text

        Returns the created VR (committed).
        """
        if self.current_user_id is not None:
            self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        if not lines:
            raise ValueError("Vendor return must have at least one line")
        if not reason or not reason.strip():
            raise ValueError("Return reason is required")

        vendor = self.db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if vendor is None:
            raise ValueError(f"Vendor {vendor_id} not found")

        # Generate document number
        year = datetime.utcnow().year
        vr_number = bump_counter(self.db, "next_vr_number", "VR", year)

        # Calculate expected_credit total + restocking_fee total
        expected_credit_total = 0.0
        restocking_total = 0.0
        for ln in lines:
            qty = int(ln.get("qty", 1))
            unit_credit = float(ln.get("expected_unit_credit", 0.0))
            fee = float(ln.get("restocking_fee", 0.0))
            expected_credit_total += qty * unit_credit
            restocking_total += fee

        vr = VendorReturn(
            vr_number=vr_number,
            vendor_id=vendor_id,
            original_po_id=original_po_id,
            original_vendor_bill_id=original_vendor_bill_id,
            reason=reason.strip(),
            status=VendorReturnStatus.DRAFT,
            expected_credit=round(expected_credit_total, 2),
            actual_credit=0.0,
            credit_difference=0.0,
            restocking_fee=round(restocking_total, 2),
            tracking_number="",
            rma_number="",
            notes=notes or "",
            created_by_user_id=self.current_user_id,
        )
        self.db.add(vr)
        self.db.flush()

        for ln in lines:
            self.db.add(VendorReturnLine(
                vendor_return_id=vr.id,
                product_id=ln.get("product_id"),
                description=str(ln.get("description", "")),
                qty=int(ln.get("qty", 1)),
                expected_unit_credit=float(ln.get("expected_unit_credit", 0.0)),
                actual_unit_credit=0.0,
                vendor_outcome=VendorReturnLineOutcome.PENDING,
                notes=str(ln.get("notes", "")),
            ))

        self.audit(
            entity_type=EntityType.VENDOR_RETURN,
            entity_id=vr.id,
            action=AuditAction.CREATED,
            new_value={
                "vr_number": vr_number,
                "vendor_id": vendor_id,
                "expected_credit": vr.expected_credit,
                "line_count": len(lines),
                "original_po_id": original_po_id,
                "original_vendor_bill_id": original_vendor_bill_id,
            },
            notes=reason,
        )
        self.db.commit()
        return vr

    # ── Ship to vendor ────────────────────────────────────────────────────────

    def ship_return(
        self,
        vr_id: int,
        tracking_number: str,
        rma_number: str = "",
        decrement_inventory: bool = True,
    ) -> VendorReturn:
        """
        Mark return as SHIPPED to vendor: DRAFT → SHIPPED.

        Args:
            vr_id:                target VR
            tracking_number:      carrier tracking#
            rma_number:           vendor's RMA#
            decrement_inventory:  if True (default), decrement product.qty_on_hand
                                  for each line (the goods physically left)

        Permission: ISSUE_CREDIT_MEMO.
        """
        if self.current_user_id is not None:
            self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        vr = self._get_or_404(vr_id)
        if vr.status != VendorReturnStatus.DRAFT:
            raise ValueError(
                f"VR {vr.vr_number} is '{vr.status}', not DRAFT. "
                f"Only DRAFT returns can be shipped."
            )
        if not tracking_number or not tracking_number.strip():
            raise ValueError("Tracking number is required to mark return as shipped")

        old_status = vr.status
        vr.status = VendorReturnStatus.SHIPPED
        vr.tracking_number = tracking_number.strip()
        vr.rma_number = rma_number.strip()
        vr.shipped_at = datetime.utcnow()

        # Decrement inventory for each line
        if decrement_inventory:
            for line in vr.lines:
                if not line.product_id or line.qty <= 0:
                    continue
                product = self.db.query(Product).filter(Product.id == line.product_id).first()
                if product is None:
                    continue
                product.qty_on_hand = max(0, product.qty_on_hand - line.qty)
                self.db.add(InventoryTransaction(
                    product_id=product.id,
                    transaction_type=InventoryTxnType.MANUAL_ADJUSTMENT,
                    qty_change=-line.qty,
                    qty_after=product.qty_on_hand,
                    reference_type=EntityType.VENDOR_RETURN,
                    reference_id=vr.id,
                    reason="vendor_return_shipped",
                    performed_by_id=self.current_user_id,
                    notes=f"Shipped to vendor on VR {vr.vr_number}",
                ))

        self.db.flush()
        self.audit(
            entity_type=EntityType.VENDOR_RETURN,
            entity_id=vr_id,
            action=AuditAction.STATUS_CHANGED,
            old_value={"status": old_status},
            new_value={
                "status": VendorReturnStatus.SHIPPED,
                "tracking_number": tracking_number,
                "rma_number": rma_number,
                "inventory_decremented": decrement_inventory,
            },
        )
        self.db.commit()
        return vr

    # ── Vendor Decision ───────────────────────────────────────────────────────

    def record_vendor_decision(
        self,
        vr_id: int,
        line_outcomes: list[dict],
        notes: str = "",
        auto_create_vcm: bool = True,
    ) -> VendorReturn:
        """
        Record vendor's decision on the return: SHIPPED → ACCEPTED/PARTIAL/REJECTED.

        Args:
            vr_id:           target VR
            line_outcomes:   [{line_id, outcome, actual_unit_credit}]
                             outcome: accepted | partial | rejected
            notes:           free-text vendor decision notes
            auto_create_vcm: if any line was accepted/partial AND actual_credit > 0,
                             auto-create a VendorCreditMemo with trigger=
                             VENDOR_RETURN_ACCEPTED linked to this VR

        Aggregates per-line outcomes into a single VR status. If ALL lines accepted,
        status=ACCEPTED. If some accepted/partial, status=PARTIAL. If ALL rejected,
        status=REJECTED.
        """
        if self.current_user_id is not None:
            self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        vr = self._get_or_404(vr_id)
        if vr.status != VendorReturnStatus.SHIPPED:
            raise ValueError(
                f"VR {vr.vr_number} is '{vr.status}', not SHIPPED. "
                "Vendor decision can only be recorded on SHIPPED returns."
            )

        line_map = {ln.id: ln for ln in vr.lines}
        actual_credit_total = 0.0
        outcomes_seen: set[str] = set()

        for upd in line_outcomes:
            line_id = int(upd["line_id"])
            line = line_map.get(line_id)
            if line is None:
                raise ValueError(f"Line {line_id} not found on VR {vr_id}")

            outcome = str(upd.get("outcome", VendorReturnLineOutcome.PENDING))
            actual_unit = float(upd.get("actual_unit_credit", 0.0))

            valid_outcomes = {o.value for o in VendorReturnLineOutcome}
            if outcome not in valid_outcomes:
                raise ValueError(
                    f"Invalid outcome '{outcome}'. Must be one of {sorted(valid_outcomes)}"
                )

            line.vendor_outcome = outcome
            line.actual_unit_credit = round(actual_unit, 2)
            actual_credit_total += line.qty * actual_unit
            outcomes_seen.add(outcome)

        actual_credit_total = round(actual_credit_total, 2)

        # Determine aggregate status
        old_status = vr.status
        if outcomes_seen == {VendorReturnLineOutcome.REJECTED}:
            vr.status = VendorReturnStatus.REJECTED
        elif outcomes_seen == {VendorReturnLineOutcome.ACCEPTED}:
            vr.status = VendorReturnStatus.ACCEPTED
        else:
            vr.status = VendorReturnStatus.PARTIAL

        vr.actual_credit = actual_credit_total
        vr.credit_difference = round(vr.expected_credit - actual_credit_total, 2)
        vr.vendor_decision_at = datetime.utcnow()
        if notes:
            vr.notes = f"{vr.notes}\n{notes}".strip() if vr.notes else notes

        self.db.flush()
        self.audit(
            entity_type=EntityType.VENDOR_RETURN,
            entity_id=vr_id,
            action=AuditAction.STATUS_CHANGED,
            old_value={"status": old_status},
            new_value={
                "status": vr.status,
                "actual_credit": actual_credit_total,
                "credit_difference": vr.credit_difference,
            },
            notes=notes,
        )
        self.db.commit()

        # R11 — auto-create VCM if vendor accepted any credit
        if auto_create_vcm and actual_credit_total > 0.001:
            from app.services.vendor_credit_service import VendorCreditService
            VendorCreditService(self.db, self.current_user_id).create_vendor_credit_memo(
                vendor_id=vr.vendor_id,
                trigger_type=VendorCreditMemoTrigger.VENDOR_RETURN_ACCEPTED,
                amount=actual_credit_total,
                original_vendor_bill_id=vr.original_vendor_bill_id,
                vendor_return_id=vr.id,
                reason=f"Vendor return {vr.vr_number} — vendor accepted credit",
            )

        return vr

    # ── Close ─────────────────────────────────────────────────────────────────

    def close_vendor_return(self, vr_id: int) -> VendorReturn:
        """
        Close the VR after vendor decision is recorded.
        Status: ACCEPTED/PARTIAL/REJECTED → CLOSED.
        """
        if self.current_user_id is not None:
            self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        vr = self._get_or_404(vr_id)
        closable = (
            VendorReturnStatus.ACCEPTED,
            VendorReturnStatus.PARTIAL,
            VendorReturnStatus.REJECTED,
        )
        if vr.status not in closable:
            raise ValueError(
                f"VR {vr.vr_number} is '{vr.status}'. "
                f"Can only close from: {', '.join(closable)}"
            )

        old_status = vr.status
        vr.status = VendorReturnStatus.CLOSED

        self.audit(
            entity_type=EntityType.VENDOR_RETURN,
            entity_id=vr_id,
            action=AuditAction.STATUS_CHANGED,
            old_value={"status": old_status},
            new_value={"status": VendorReturnStatus.CLOSED},
        )
        self.db.commit()
        return vr

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_or_404(self, vr_id: int) -> VendorReturn:
        vr = self.db.query(VendorReturn).filter(VendorReturn.id == vr_id).first()
        if vr is None:
            raise ValueError(f"Vendor return #{vr_id} not found")
        return vr
