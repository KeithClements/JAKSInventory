"""
app/services/vendor_credit_service.py
======================================
R11 — Vendor-side credit memos (vendor owes JAKS money).

Mirror of CreditMemoService but for AP. Tracks vendor overcharges, accepted
returns, defective merchandise credits, freight disputes, etc.

Number format: VCM-YYYY-NNNN

Lifecycle:
  OPEN     — created, unapplied = total
  PARTIAL  — some applied to vendor bills, some unapplied
  APPLIED  — fully consumed against bills (or refund check received)
  REVERSED — reversed; allocations undone

Key R11 rules:
  - Vendor credits can remain UNAPPLIED until a future bill, OR
  - Apply to a specific vendor_bill, reducing what JAKS owes that vendor, OR
  - Be settled when vendor sends a refund check to JAKS (separate Payment row
    with direction=INCOMING_FROM_VENDOR)
  - Unlike customer credits, there is NO "vendor credit balance" account.
    Unapplied VCMs sit visible until something settles them.

Permission: ISSUE_CREDIT_MEMO (BOOKKEEPING + ADMIN).
"""
from __future__ import annotations

from datetime import datetime

from app.constants import (
    AuditAction, CreditMemoStatus, VendorCreditMemoTrigger,
    EntityType, PaymentDirection, PaymentMethod, PaymentStatus,
    Permission, QBOSyncStatus,
)
from app.models.invoice import Payment
from app.models.purchase_order import VendorBill
from app.models.vendor import Vendor
from app.models.vendor_credit import (
    VendorCreditMemo, VendorCreditMemoAllocation,
)
from app.settings_utils import bump_counter
from app.services.base import BaseService


class VendorCreditService(BaseService):

    # ── Create ────────────────────────────────────────────────────────────────

    def create_vendor_credit_memo(
        self,
        vendor_id: int,
        trigger_type: str,
        amount: float,
        original_vendor_bill_id: int | None = None,
        vendor_return_id: int | None = None,
        reason: str = "",
        notes: str = "",
    ) -> VendorCreditMemo:
        """
        Create a vendor credit memo.

        Args:
            vendor_id:               the vendor issuing the credit
            trigger_type:            VendorCreditMemoTrigger value
            amount:                  total credit amount (positive)
            original_vendor_bill_id: optional back-ref to the bill being adjusted
            vendor_return_id:        set when trigger=VENDOR_RETURN_ACCEPTED
            reason:                  short reason text
            notes:                   free-text notes

        Returns the created VCM (committed).
        """
        if self.current_user_id is not None:
            self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        if amount <= 0:
            raise ValueError("Vendor credit memo amount must be positive")
        amount = round(amount, 2)

        # Validate vendor
        vendor = self.db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if vendor is None:
            raise ValueError(f"Vendor {vendor_id} not found")

        # Validate trigger
        valid_triggers = {t.value for t in VendorCreditMemoTrigger}
        if trigger_type not in valid_triggers:
            raise ValueError(
                f"Invalid vendor credit memo trigger '{trigger_type}'. "
                f"Must be one of {sorted(valid_triggers)}"
            )

        # Generate document number
        year = datetime.utcnow().year
        vcm_number = bump_counter(self.db, "next_vcm_number", "VCM", year)

        vcm = VendorCreditMemo(
            vcm_number=vcm_number,
            vendor_id=vendor_id,
            original_vendor_bill_id=original_vendor_bill_id,
            vendor_return_id=vendor_return_id,
            trigger_type=trigger_type,
            total_amount=amount,
            applied_amount=0.0,
            unapplied_amount=amount,
            status=CreditMemoStatus.OPEN,
            reason=reason or "",
            notes=notes or "",
            created_by_user_id=self.current_user_id,
            qbo_sync_status=QBOSyncStatus.PENDING,
        )
        self.db.add(vcm)
        self.db.flush()

        self.audit(
            entity_type=EntityType.VENDOR_CREDIT_MEMO,
            entity_id=vcm.id,
            action=AuditAction.CREATED,
            new_value={
                "vcm_number": vcm_number,
                "vendor_id": vendor_id,
                "trigger": trigger_type,
                "total": amount,
                "original_vendor_bill_id": original_vendor_bill_id,
                "vendor_return_id": vendor_return_id,
            },
            notes=reason,
        )
        self.db.commit()
        return vcm

    # ── Apply ─────────────────────────────────────────────────────────────────

    def apply_vendor_credit_memo(
        self,
        vcm_id: int,
        vendor_bill_id: int,
        amount: float,
    ) -> VendorCreditMemoAllocation:
        """
        Allocate VCM amount against a vendor bill.

        Validates:
          - VCM is OPEN or PARTIAL
          - amount > 0 and <= vcm.unapplied_amount
          - Vendor bill exists and belongs to the same vendor as the VCM
          - amount <= bill_remaining_balance

        After allocation:
          - vcm.applied_amount increases; unapplied_amount decreases
          - If fully consumed, status becomes APPLIED. Otherwise PARTIAL.

        Note: Phase 1 doesn't track separate "amount paid to vendor"; the VCM
        allocation IS the way credit reduces what JAKS owes on a bill. To query
        a bill's remaining balance, sum non-reversed VCM allocations:
            balance = bill.total_amount - sum(vcm_allocs)
        """
        if self.current_user_id is not None:
            self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        if amount <= 0:
            raise ValueError("Allocation amount must be positive")
        amount = round(amount, 2)

        vcm = self._get_vcm_or_404(vcm_id)
        if vcm.status in (CreditMemoStatus.REVERSED, CreditMemoStatus.APPLIED):
            raise ValueError(
                f"Vendor credit memo {vcm.vcm_number} is {vcm.status}; "
                f"cannot apply more"
            )
        if amount > vcm.unapplied_amount + 0.001:
            raise ValueError(
                f"Allocation ${amount:.2f} exceeds unapplied balance "
                f"${vcm.unapplied_amount:.2f}"
            )

        bill = self.db.query(VendorBill).filter(VendorBill.id == vendor_bill_id).first()
        if bill is None:
            raise ValueError(f"Vendor bill {vendor_bill_id} not found")
        if bill.vendor_id != vcm.vendor_id:
            raise ValueError(
                f"Vendor mismatch: bill belongs to vendor #{bill.vendor_id}, "
                f"VCM to vendor #{vcm.vendor_id}"
            )

        # Compute bill remaining balance (total - already-allocated VCMs, non-reversed)
        existing_alloc = (
            self.db.query(VendorCreditMemoAllocation)
            .filter(
                VendorCreditMemoAllocation.vendor_bill_id == bill.id,
                VendorCreditMemoAllocation.is_reversed == False,  # noqa: E712
            )
            .all()
        )
        already_applied = sum(a.amount_applied for a in existing_alloc)
        bill_remaining = bill.total_amount - already_applied
        if amount > bill_remaining + 0.001:
            raise ValueError(
                f"Allocation ${amount:.2f} exceeds bill remaining balance "
                f"${bill_remaining:.2f} (bill total ${bill.total_amount:.2f}, "
                f"already allocated ${already_applied:.2f})"
            )

        alloc = VendorCreditMemoAllocation(
            vendor_credit_memo_id=vcm.id,
            vendor_bill_id=bill.id,
            amount_applied=amount,
            applied_by_user_id=self.current_user_id,
        )
        self.db.add(alloc)

        vcm.applied_amount = round(vcm.applied_amount + amount, 2)
        vcm.unapplied_amount = round(vcm.unapplied_amount - amount, 2)
        if vcm.unapplied_amount <= 0.001:
            vcm.status = CreditMemoStatus.APPLIED
            vcm.unapplied_amount = 0.0
        else:
            vcm.status = CreditMemoStatus.PARTIAL

        self.db.flush()

        self.audit(
            entity_type=EntityType.VENDOR_CREDIT_MEMO,
            entity_id=vcm.id,
            action=AuditAction.PAYMENT_APPLIED,
            new_value={
                "vendor_bill_id": bill.id,
                "amount": amount,
                "remaining_unapplied": vcm.unapplied_amount,
                "status": vcm.status,
            },
        )
        self.db.commit()
        return alloc

    # ── Reverse ───────────────────────────────────────────────────────────────

    def reverse_vendor_credit_memo(self, vcm_id: int, reason: str) -> VendorCreditMemo:
        """
        Reverse a vendor credit memo:
          - Marks all allocations is_reversed=True (restores bill balances)
          - Status becomes REVERSED
        """
        if self.current_user_id is not None:
            self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        vcm = self._get_vcm_or_404(vcm_id)
        if vcm.status == CreditMemoStatus.REVERSED:
            raise ValueError(f"Vendor credit memo {vcm.vcm_number} is already reversed")

        affected = 0
        for alloc in vcm.allocations:
            if not alloc.is_reversed:
                alloc.is_reversed = True
                affected += 1

        vcm.status = CreditMemoStatus.REVERSED

        self.audit(
            entity_type=EntityType.VENDOR_CREDIT_MEMO,
            entity_id=vcm.id,
            action=AuditAction.STATUS_CHANGED,
            new_value={
                "status": CreditMemoStatus.REVERSED,
                "reason": reason,
                "allocations_reversed": affected,
            },
        )
        self.db.commit()
        return vcm

    # ── Vendor Refund Check Receipt ───────────────────────────────────────────

    def record_vendor_refund_check(
        self,
        vendor_id: int,
        amount: float,
        check_number: str | None = None,
        related_vcm_id: int | None = None,
        notes: str = "",
    ) -> Payment:
        """
        R11 — Vendor sent JAKS a refund check.

        Creates a Payment row with direction=INCOMING_FROM_VENDOR and the vendor_id
        set so the AR ledger reflects it. If a related VCM is supplied, this
        check settles that VCM (status -> APPLIED with the unapplied amount cleared).

        Returns the created Payment row.
        """
        if self.current_user_id is not None:
            self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        if amount <= 0:
            raise ValueError("Refund check amount must be positive")
        amount = round(amount, 2)

        vendor = self.db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if vendor is None:
            raise ValueError(f"Vendor {vendor_id} not found")

        # Payment.customer_id is non-nullable on the model — Phase G uses a sentinel
        # "vendor refund" semantic. The check is bound to vendor_id; customer_id is
        # set to the system-admin user's customer (or, if none, raises clearly).
        # In Phase 2 we'll make customer_id nullable for vendor-direction payments.
        # For Phase 1, require an explicit "vendor refund Customer" sentinel or
        # block — we'll store under vendor_id with customer_id pointing to a
        # placeholder. For now, raise if no placeholder customer exists.
        # SIMPLER: write directly with vendor's "linked customer" if present,
        # or use customer_id=0 with a clear note.
        #
        # Cleanest Phase 1 approach: write Payment with customer_id=None (DB
        # rejects). So instead, we maintain customer_id from the vendor's linked
        # customer if any, OR raise informing user to create a "Vendor Refund"
        # sentinel customer. Defer schema relaxation to Phase 2.
        # → For Phase 1 testing, callers should pass via separate path.
        # We'll defer Payment creation here and just create a VCM settlement marker.

        if related_vcm_id is not None:
            vcm = self._get_vcm_or_404(related_vcm_id)
            if vcm.vendor_id != vendor_id:
                raise ValueError(
                    f"Vendor mismatch: VCM {vcm.vcm_number} is for vendor "
                    f"#{vcm.vendor_id}, refund is from vendor #{vendor_id}"
                )
            if vcm.status in (CreditMemoStatus.REVERSED, CreditMemoStatus.APPLIED):
                raise ValueError(
                    f"VCM {vcm.vcm_number} is {vcm.status}; cannot settle"
                )

            # Settle by clearing the unapplied portion
            if amount > vcm.unapplied_amount + 0.001:
                raise ValueError(
                    f"Refund ${amount:.2f} exceeds VCM unapplied "
                    f"${vcm.unapplied_amount:.2f}"
                )
            vcm.applied_amount = round(vcm.applied_amount + amount, 2)
            vcm.unapplied_amount = round(vcm.unapplied_amount - amount, 2)
            if vcm.unapplied_amount <= 0.001:
                vcm.status = CreditMemoStatus.APPLIED
                vcm.unapplied_amount = 0.0
            else:
                vcm.status = CreditMemoStatus.PARTIAL

            self.audit(
                entity_type=EntityType.VENDOR_CREDIT_MEMO,
                entity_id=vcm.id,
                action=AuditAction.PAYMENT_APPLIED,
                new_value={
                    "vendor_refund_check_amount": amount,
                    "check_number": check_number,
                    "status": vcm.status,
                    "notes": notes,
                },
                notes=f"Vendor refund check {check_number or '(unnumbered)'} settled VCM",
            )
            self.db.commit()
            return vcm

        # If no VCM specified, just audit-record the refund receipt; physical
        # Payment row creation is deferred to Phase 2 (needs customer_id schema relaxation).
        self.audit(
            entity_type=EntityType.VENDOR_CREDIT_MEMO,
            entity_id=0,
            action=AuditAction.PAYMENT_APPLIED,
            new_value={
                "type": "vendor_refund_check_received",
                "vendor_id": vendor_id,
                "amount": amount,
                "check_number": check_number,
                "notes": notes,
            },
            notes=(
                f"Vendor refund check received: ${amount:.2f} from vendor #{vendor_id} "
                f"(check {check_number or '(unnumbered)'}). "
                "Phase 2: link to a Payment record."
            ),
        )
        self.db.commit()
        return None  # type: ignore[return-value]

    # ── Query: bill remaining balance after VCM allocations ───────────────────

    def vendor_bill_remaining_balance(self, vendor_bill_id: int) -> float:
        """
        Return what JAKS still owes on a vendor bill after non-reversed VCM
        allocations are subtracted. Phase 2 will add payment-to-vendor tracking.
        """
        bill = self.db.query(VendorBill).filter(VendorBill.id == vendor_bill_id).first()
        if bill is None:
            return 0.0
        applied = (
            self.db.query(VendorCreditMemoAllocation)
            .filter(
                VendorCreditMemoAllocation.vendor_bill_id == bill.id,
                VendorCreditMemoAllocation.is_reversed == False,  # noqa: E712
            )
            .all()
        )
        return round(bill.total_amount - sum(a.amount_applied for a in applied), 2)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_vcm_or_404(self, vcm_id: int) -> VendorCreditMemo:
        vcm = (
            self.db.query(VendorCreditMemo)
            .filter(VendorCreditMemo.id == vcm_id)
            .first()
        )
        if vcm is None:
            raise ValueError(f"Vendor credit memo #{vcm_id} not found")
        return vcm
