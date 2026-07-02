"""
app/services/credit_memo_service.py
====================================
R8 — Customer-side credit memos (CM-YYYY-NNNN).

A credit memo is an INDEPENDENT financial document — NOT a negative invoice.
Created from:
  - manual issuance against a locked invoice
  - accepted RA disposition
  - approved warranty claim
  - overcharge / pricing correction

Lifecycle:
  OPEN     — just created, unapplied_amount = total_amount
  PARTIAL  — some applied to invoices, some still unapplied
  APPLIED  — fully consumed (either via allocations OR via close_credit_memo
             pushing the residual to customer.credit_balance)
  REVERSED — voided; allocations reversed and any credit_balance push undone

Key R8 rules:
  - Credit memo creation does NOT automatically increase customer.credit_balance
  - credit_balance increases only when close_credit_memo() runs and there's
    unapplied amount remaining
  - May apply directly to specific invoices OR remain open as unapplied
  - Never apply credit to quotes — invoices only

Permission: ISSUE_CREDIT_MEMO (BOOKKEEPING + ADMIN).
"""
from __future__ import annotations

from datetime import datetime

from app.constants import (
    AuditAction, CreditMemoStatus, CreditMemoTrigger,
    EntityType, InvoiceStatus, Permission, QBOSyncStatus,
)
from app.models.credit_memo import (
    CreditMemo, CreditMemoLine, CreditMemoAllocation,
)
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.settings_utils import bump_counter
from app.services.base import BaseService


class CreditMemoService(BaseService):

    # ── Create ────────────────────────────────────────────────────────────────

    def create_credit_memo(
        self,
        customer_id: int,
        trigger_type: str,
        lines: list[dict],
        original_invoice_id: int | None = None,
        ra_id: int | None = None,
        warranty_claim_id: int | None = None,
        reason: str = "",
        notes: str = "",
        auto_close_to_credit: bool = False,
    ) -> CreditMemo:
        """
        Create a new credit memo.

        Args:
            customer_id:           target customer
            trigger_type:          CreditMemoTrigger value (manual, accepted_ra, etc.)
            lines:                 list of dicts — {description, qty, unit_price,
                                                    discount_pct?, product_id?,
                                                    original_invoice_line_id?, is_taxable?}
            original_invoice_id:   optional back-ref to the invoice being corrected
            ra_id:                 set when trigger=ACCEPTED_RA
            warranty_claim_id:     set when trigger=APPROVED_WARRANTY
            reason:                short reason text
            notes:                 free-text notes
            auto_close_to_credit:  if True, immediately push the full unapplied
                                   amount to customer.credit_balance and mark
                                   status=APPLIED. Used by RA/warranty triggers
                                   that don't target a specific invoice.

        Returns the created CreditMemo (committed).

        Permission: ISSUE_CREDIT_MEMO.
        """
        # Allow automatic triggers (system-attributed events) to bypass the gate.
        # When current_user_id is None, this is a system event (Phase A note).
        if self.current_user_id is not None:
            self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        if not lines:
            raise ValueError("Credit memo must have at least one line")

        # Validate customer
        customer = self.db.query(Customer).filter(Customer.id == customer_id).first()
        if customer is None:
            raise ValueError(f"Customer {customer_id} not found")

        # Validate trigger value
        valid_triggers = {t.value for t in CreditMemoTrigger}
        if trigger_type not in valid_triggers:
            raise ValueError(
                f"Invalid credit memo trigger '{trigger_type}'. "
                f"Must be one of {sorted(valid_triggers)}"
            )

        # Generate document number
        year = datetime.utcnow().year
        cm_number = bump_counter(self.db, "next_cm_number", "CM", year)

        # Compute totals from lines
        total_amount = 0.0
        for ln in lines:
            qty = int(ln.get("qty", 1))
            unit_price = float(ln.get("unit_price", 0.0))
            disc = float(ln.get("discount_pct", 0.0))
            line_total = qty * unit_price * (1 - disc / 100)
            total_amount += line_total
        total_amount = round(total_amount, 2)

        if total_amount <= 0:
            raise ValueError(
                f"Credit memo total must be positive (got ${total_amount:.2f})"
            )

        cm = CreditMemo(
            cm_number=cm_number,
            customer_id=customer_id,
            original_invoice_id=original_invoice_id,
            ra_id=ra_id,
            warranty_claim_id=warranty_claim_id,
            trigger_type=trigger_type,
            total_amount=total_amount,
            # §21 — applied_amount is computed from allocations now (no column write).
            unapplied_amount=total_amount,
            status=CreditMemoStatus.OPEN,
            reason=reason or "",
            notes=notes or "",
            created_by_user_id=self.current_user_id,
            qbo_sync_status=QBOSyncStatus.PENDING,
        )
        self.db.add(cm)
        self.db.flush()

        # Create the lines
        for ln in lines:
            self.db.add(CreditMemoLine(
                credit_memo_id=cm.id,
                original_invoice_line_id=ln.get("original_invoice_line_id"),
                product_id=ln.get("product_id"),
                description=str(ln.get("description", "")),
                qty=int(ln.get("qty", 1)),
                unit_price=float(ln.get("unit_price", 0.0)),
                discount_pct=float(ln.get("discount_pct", 0.0)),
                is_taxable=bool(ln.get("is_taxable", False)),
                tax_amount=float(ln.get("tax_amount", 0.0)),
            ))

        self.audit(
            entity_type=EntityType.CREDIT_MEMO,
            entity_id=cm.id,
            action=AuditAction.CREATED,
            new_value={
                "cm_number": cm_number,
                "customer_id": customer_id,
                "trigger": trigger_type,
                "total": total_amount,
                "original_invoice_id": original_invoice_id,
                "ra_id": ra_id,
                "warranty_claim_id": warranty_claim_id,
            },
            notes=reason,
        )
        self.db.commit()

        # Auto-close path: push the full unapplied to credit_balance now
        if auto_close_to_credit:
            self.close_credit_memo(cm.id)
            self.db.refresh(cm)

        return cm

    # ── Apply ─────────────────────────────────────────────────────────────────

    def apply_credit_memo(
        self,
        cm_id: int,
        invoice_id: int,
        amount: float,
    ) -> CreditMemoAllocation:
        """
        Allocate part (or all) of a credit memo to a specific invoice.

        Validates:
          - CM is OPEN or PARTIAL (not APPLIED, not REVERSED)
          - amount > 0
          - amount <= cm.unapplied_amount
          - amount <= invoice.balance_due
          - Invoice is not VOID

        After allocation:
          - cm.applied_amount increases, cm.unapplied_amount decreases
          - invoice.amount_paid reflects the allocation (via InvoiceService
            payment-status refresh — credit memos function like payments)
          - If cm.unapplied_amount reaches 0, status becomes APPLIED.
            Otherwise PARTIAL.
        """
        if self.current_user_id is not None:
            self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        if amount <= 0:
            raise ValueError("Allocation amount must be positive")
        amount = round(amount, 2)

        cm = self._get_cm_or_404(cm_id)
        if cm.status in (CreditMemoStatus.REVERSED, CreditMemoStatus.APPLIED):
            # APPLIED means fully consumed; can't apply more
            raise ValueError(
                f"Credit memo {cm.cm_number} is {cm.status}; cannot apply more"
            )
        if amount > cm.unapplied_amount + 0.001:
            raise ValueError(
                f"Allocation ${amount:.2f} exceeds unapplied balance "
                f"${cm.unapplied_amount:.2f}"
            )

        invoice = self.db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice is None:
            raise ValueError(f"Invoice {invoice_id} not found")
        if invoice.status == InvoiceStatus.VOID:
            raise ValueError(
                f"Invoice {invoice.invoice_number} is void; cannot apply credit memo"
            )
        if invoice.customer_id != cm.customer_id:
            raise ValueError(
                f"Customer mismatch: invoice belongs to customer #{invoice.customer_id}, "
                f"credit memo to customer #{cm.customer_id}"
            )
        if amount > invoice.balance_due + 0.001:
            raise ValueError(
                f"Allocation ${amount:.2f} exceeds invoice balance "
                f"${invoice.balance_due:.2f}"
            )

        # Credit memo allocations reduce invoice balance just like payment allocations.
        # We piggyback on the Payment model by creating a synthetic "credit memo payment"
        # — this keeps invoice.amount_paid math consistent with the existing UI/queries.
        from app.constants import PaymentDirection, PaymentMethod, PaymentStatus
        from app.models.invoice import Payment, PaymentAllocation

        payment = Payment(
            customer_id=cm.customer_id,
            payment_date=datetime.utcnow(),
            payment_method=PaymentMethod.ACCOUNT_CREDIT,   # CM behaves like account credit
            direction=PaymentDirection.INCOMING_FROM_CUSTOMER,
            amount_received=amount,
            status=PaymentStatus.APPLIED,
            notes=f"Credit memo {cm.cm_number} applied to invoice {invoice.invoice_number}",
            qbo_sync_status=QBOSyncStatus.PENDING,
        )
        self.db.add(payment)
        self.db.flush()

        pmt_alloc = PaymentAllocation(
            payment_id=payment.id,
            invoice_id=invoice.id,
            amount_applied=amount,
        )
        self.db.add(pmt_alloc)
        self.db.flush()  # assign pmt_alloc.id so the CM allocation can hard-link it

        # Also create the CreditMemoAllocation for the CM-side ledger.
        # linked_payment_allocation_id lets reverse_credit_memo unwind the exact
        # synthetic PaymentAllocation instead of guessing by notes text.
        cm_alloc = CreditMemoAllocation(
            credit_memo_id=cm.id,
            invoice_id=invoice.id,
            amount_applied=amount,
            applied_by_user_id=self.current_user_id,
            linked_payment_allocation_id=pmt_alloc.id,
        )
        self.db.add(cm_alloc)

        # Adjust CM totals — applied_amount is computed from the allocation we just
        # added (no write); only the stored unapplied_amount is decremented.
        cm.unapplied_amount = round(cm.unapplied_amount - amount, 2)
        if cm.unapplied_amount <= 0.001:
            cm.status = CreditMemoStatus.APPLIED
            cm.unapplied_amount = 0.0  # snap to zero
        else:
            cm.status = CreditMemoStatus.PARTIAL

        self.db.flush()

        # Refresh invoice status via InvoiceService (sole owner of invoice.status)
        from app.services.invoice_service import InvoiceService
        InvoiceService(self.db, self.current_user_id).refresh_payment_status(invoice.id)

        self.audit(
            entity_type=EntityType.CREDIT_MEMO,
            entity_id=cm.id,
            action=AuditAction.PAYMENT_APPLIED,
            new_value={
                "invoice_id": invoice.id,
                "amount": amount,
                "remaining_unapplied": cm.unapplied_amount,
                "status": cm.status,
            },
        )
        self.db.commit()
        return cm_alloc

    # ── Close ─────────────────────────────────────────────────────────────────

    def close_credit_memo(self, cm_id: int) -> CreditMemo:
        """
        Close the credit memo: push any remaining `unapplied_amount` to the
        customer's credit_balance via CRMService, then mark status=APPLIED.

        R8 rule: "credit_balance increases only when unapplied balance remains
        after allocation." This is the call that effects that transfer.

        Idempotent: closing an already-APPLIED CM is a no-op.
        """
        if self.current_user_id is not None:
            self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        cm = self._get_cm_or_404(cm_id)
        if cm.status == CreditMemoStatus.APPLIED:
            return cm  # idempotent
        if cm.status == CreditMemoStatus.REVERSED:
            raise ValueError(
                f"Credit memo {cm.cm_number} is reversed; cannot close"
            )

        if cm.unapplied_amount > 0.001:
            from app.services.crm_service import CRMService
            CRMService(self.db, self.current_user_id).add_credit(
                customer_id=cm.customer_id,
                amount=cm.unapplied_amount,
                reason=f"Credit memo {cm.cm_number} closed — residual to credit balance",
            )
            credited = cm.unapplied_amount
            cm.unapplied_amount = 0.0
        else:
            credited = 0.0

        cm.status = CreditMemoStatus.APPLIED
        cm.locked_at = datetime.utcnow()

        self.audit(
            entity_type=EntityType.CREDIT_MEMO,
            entity_id=cm.id,
            action=AuditAction.STATUS_CHANGED,
            new_value={
                "status": CreditMemoStatus.APPLIED,
                "credit_balance_increment": credited,
                "applied_to_invoices": cm.applied_amount,
            },
            notes="Credit memo closed",
        )
        self.db.commit()
        return cm

    # ── Reverse ───────────────────────────────────────────────────────────────

    def reverse_credit_memo(self, cm_id: int, reason: str) -> CreditMemo:
        """
        Reverse a credit memo:
          - All allocations marked is_reversed=True (CM side + the synthetic
            Payment that backs it for invoice balance math)
          - Affected invoices have their payment status refreshed (re-opens)
          - Any amount that went to credit_balance is deducted via CRMService
          - CM status = REVERSED
        """
        if self.current_user_id is not None:
            self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        cm = self._get_cm_or_404(cm_id)
        if cm.status == CreditMemoStatus.REVERSED:
            raise ValueError(f"Credit memo {cm.cm_number} is already reversed")

        # Compute how much went to credit_balance vs allocated to invoices.
        # When close ran: credit_balance += (total - applied_amount).
        # If close did NOT run, unapplied_amount > 0 still (no credit moved).
        if cm.status == CreditMemoStatus.APPLIED and cm.unapplied_amount == 0:
            balance_credited = round(cm.total_amount - cm.applied_amount, 2)
        else:
            balance_credited = 0.0

        # Reverse all CM allocations + their synthetic payment allocations
        from app.models.invoice import Payment, PaymentAllocation
        from app.services.invoice_service import InvoiceService

        inv_svc = InvoiceService(self.db, self.current_user_id)
        affected_invoice_ids: set[int] = set()

        for cm_alloc in cm.allocations:
            if cm_alloc.is_reversed:
                continue

            # Resolve the synthetic payment allocation that mirrored this CM
            # allocation. Rows written since the link column exists carry the
            # PaymentAllocation id directly; legacy NULL-link rows fall back to
            # the old notes-text heuristic. Resolve BEFORE mutating anything so
            # a failed match leaves no partial reversal in the session.
            if cm_alloc.linked_payment_allocation_id is not None:
                pa = (
                    self.db.query(PaymentAllocation)
                    .filter(
                        PaymentAllocation.id == cm_alloc.linked_payment_allocation_id,
                        PaymentAllocation.is_reversed == False,  # noqa: E712
                    )
                    .first()
                )
            else:
                # Legacy heuristic: invoice + amount + non-reversed +
                # ACCOUNT_CREDIT payment whose notes name this CM. One pmt_alloc
                # per cm_alloc by construction, so take the oldest match.
                pa = (
                    self.db.query(PaymentAllocation)
                    .join(Payment)
                    .filter(
                        PaymentAllocation.invoice_id == cm_alloc.invoice_id,
                        PaymentAllocation.amount_applied == cm_alloc.amount_applied,
                        PaymentAllocation.is_reversed == False,  # noqa: E712
                        Payment.payment_method == "account_credit",
                        Payment.notes.like(f"%{cm.cm_number}%"),
                    )
                    .order_by(PaymentAllocation.id)
                    .first()
                )

            if pa is None:
                # Never silently no-op a money reversal: the invoice would keep
                # credit it no longer has while the CM flips to REVERSED.
                raise ValueError(
                    f"Cannot reverse credit memo {cm.cm_number}: no matching "
                    f"payment allocation found for invoice #{cm_alloc.invoice_id} "
                    f"(${cm_alloc.amount_applied:.2f} applied). The synthetic "
                    f"account-credit payment could not be resolved — fix the "
                    f"allocation link before reversing."
                )

            cm_alloc.is_reversed = True
            pa.is_reversed = True
            affected_invoice_ids.add(cm_alloc.invoice_id)

        # Deduct from credit_balance if any went there via close_credit_memo
        if balance_credited > 0:
            from app.services.crm_service import CRMService
            CRMService(self.db, self.current_user_id).deduct_credit(
                customer_id=cm.customer_id,
                amount=balance_credited,
                reason=f"Reversal of credit memo {cm.cm_number}: {reason}",
            )

        # Refresh affected invoices
        for inv_id in affected_invoice_ids:
            inv_svc.reopen_after_payment_reversal(inv_id)

        cm.status = CreditMemoStatus.REVERSED

        self.audit(
            entity_type=EntityType.CREDIT_MEMO,
            entity_id=cm.id,
            action=AuditAction.STATUS_CHANGED,
            new_value={
                "status": CreditMemoStatus.REVERSED,
                "reason": reason,
                "allocations_reversed": len(affected_invoice_ids),
                "credit_balance_deducted": balance_credited,
            },
        )
        self.db.commit()
        return cm

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_cm_or_404(self, cm_id: int) -> CreditMemo:
        cm = self.db.query(CreditMemo).filter(CreditMemo.id == cm_id).first()
        if cm is None:
            raise ValueError(f"Credit memo #{cm_id} not found")
        return cm
