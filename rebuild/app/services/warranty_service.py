"""
app/services/warranty_service.py
==================================
Warranty claim lifecycle — submission, vendor decision, customer resolution.

OWNERSHIP:
  WarrantyService owns WarrantyClaim and WarrantyClaimLine mutations.
  Customer credit: delegates to CRMService.add_credit() — sole owner of
    customer.credit_balance.
  Vendor credit recording: creates VendorCredit (JAKS-side ledger entry).

Workflow:
  draft → submitted_to_vendor → vendor_approved  → customer_credited → closed
                              ↘ vendor_denied    → customer_notified → closed

Key rules:
  - Claim number: WC-[YEAR]-[NNNN], resets yearly
  - One claim covers multiple lines (WarrantyClaimLine)
  - Partial vendor decision: some lines approved, some denied — overall status
    follows the worst outcome unless at least one line is approved
  - Customer resolution options:
      account_credit:  adds total_credit_amount to customer.credit_balance
      check:           manual check issued; system records the intent, no invoice
  - Vendor credit (JAKS side): VendorCredit record with type=WARRANTY
  - issue_refund_check: marks claim credited; physical check is issued manually.
    Credit memo invoices (negative-amount) are a Phase 2 enhancement pending
    InvoiceService support for credit memo line types.
"""
from __future__ import annotations

from datetime import datetime

from app.constants import (
    AuditAction, EntityType,
    VendorCreditStatus, VendorCreditType,
    WarrantyDecision, WarrantyResolution, WarrantyStatus,
)
from app.models.warranty import WarrantyClaim, WarrantyClaimLine
from app.settings_utils import bump_counter
from app.services.base import BaseService


class WarrantyService(BaseService):

    # ── Claim CRUD ────────────────────────────────────────────────────────────

    def create_claim(
        self,
        customer_id: int,
        invoice_id: int | None,
        vendor_id: int | None,
        failure_description: str,
        lines: list[dict],
        notes: str = "",
    ) -> WarrantyClaim:
        """
        Create a warranty claim in DRAFT status. Generates WC-YEAR-NNNN.
        lines: list of {invoice_line_id?, product_id?, qty_claimed, credit_amount?}
        At least one line is required.
        """
        if not lines:
            raise ValueError("Warranty claim must have at least one line")

        year = datetime.utcnow().year
        claim_number = bump_counter(self.db, "next_warranty_number", "WC", year)

        claim = WarrantyClaim(
            claim_number=claim_number,
            customer_id=customer_id,
            invoice_id=invoice_id,
            vendor_id=vendor_id,
            status=WarrantyStatus.DRAFT,
            failure_description=failure_description,
            vendor_decision=WarrantyDecision.PENDING,
            total_credit_amount=0.0,
            notes=notes,
        )
        self.db.add(claim)
        self.db.flush()

        for line_data in lines:
            self._add_claim_line_internal(claim.id, line_data)

        self.audit(
            entity_type=EntityType.WARRANTY_CLAIM,
            entity_id=claim.id,
            action=AuditAction.CREATED,
            new_value={
                "claim_number": claim_number,
                "customer_id": customer_id,
                "invoice_id": invoice_id,
                "vendor_id": vendor_id,
                "line_count": len(lines),
            },
        )
        self.db.commit()
        return claim

    def add_claim_line(self, claim_id: int, data: dict) -> WarrantyClaimLine:
        """Add a line to a DRAFT claim."""
        claim = self._get_claim_or_404(claim_id)
        if claim.status != WarrantyStatus.DRAFT:
            raise ValueError(
                f"Cannot add lines to a claim with status '{claim.status}'. "
                "Only DRAFT claims can be edited."
            )
        line = self._add_claim_line_internal(claim_id, data)
        self.db.commit()
        return line

    # ── Vendor Submission ─────────────────────────────────────────────────────

    def submit_to_vendor(self, claim_id: int) -> None:
        """
        Mark claim as submitted to vendor. Transitions DRAFT → SUBMITTED_TO_VENDOR.
        Claim must have at least one line.
        """
        claim = self._get_claim_or_404(claim_id)
        if claim.status != WarrantyStatus.DRAFT:
            raise ValueError(
                f"Claim {claim.claim_number} is '{claim.status}', not DRAFT. "
                "Only DRAFT claims can be submitted."
            )
        if not claim.claim_lines:
            raise ValueError(
                f"Claim {claim.claim_number} has no lines — add at least one before submitting"
            )

        old_status = claim.status
        claim.status = WarrantyStatus.SUBMITTED_TO_VENDOR
        claim.submitted_to_vendor_at = datetime.utcnow()

        self.audit(
            entity_type=EntityType.WARRANTY_CLAIM,
            entity_id=claim_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=WarrantyStatus.SUBMITTED_TO_VENDOR,
        )
        self.db.commit()

    def record_vendor_decision(
        self,
        claim_id: int,
        decision: str,                  # WarrantyDecision: 'approved' | 'partial' | 'denied'
        line_resolutions: list[dict],   # [{claim_line_id, approved_qty, credit_amount, resolution}]
        decision_notes: str | None = None,
    ) -> None:
        """
        Record vendor's response on each claim line.
        Updates WarrantyClaimLine.approved_qty, credit_amount, and resolution.
        Recalculates claim.total_credit_amount from line sums.
        Transitions status:
          approved/partial → VENDOR_APPROVED
          denied           → VENDOR_DENIED
        """
        claim = self._get_claim_or_404(claim_id)
        if claim.status != WarrantyStatus.SUBMITTED_TO_VENDOR:
            raise ValueError(
                f"Claim {claim.claim_number} is '{claim.status}', "
                "not SUBMITTED_TO_VENDOR. Cannot record vendor decision."
            )

        lines_by_id = {ln.id: ln for ln in claim.claim_lines}
        total_credit = 0.0

        for res in line_resolutions:
            line_id = int(res["claim_line_id"])
            line = lines_by_id.get(line_id)
            if line is None:
                raise ValueError(
                    f"WarrantyClaimLine {line_id} not found on claim {claim_id}"
                )
            line.approved_qty = int(res.get("approved_qty", 0))
            line.credit_amount = round(float(res.get("credit_amount", 0.0)), 2)
            line.resolution = res.get("resolution")
            total_credit += line.credit_amount

        claim.vendor_decision = decision
        claim.vendor_decision_at = datetime.utcnow()
        claim.vendor_decision_notes = decision_notes
        claim.total_credit_amount = round(total_credit, 2)

        old_status = claim.status
        if decision in (WarrantyDecision.APPROVED, WarrantyDecision.PARTIAL):
            claim.status = WarrantyStatus.VENDOR_APPROVED
        else:
            claim.status = WarrantyStatus.VENDOR_DENIED

        self.audit(
            entity_type=EntityType.WARRANTY_CLAIM,
            entity_id=claim_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value={
                "status": claim.status,
                "decision": decision,
                "total_credit": claim.total_credit_amount,
            },
        )
        from app.services.notification_service import NotificationService
        NotificationService.build_warranty_decision(
            self.db,
            claim_id=claim_id,
            claim_number=claim.claim_number,
            decision=decision,
        )
        self.db.commit()

    # ── Customer Resolution ───────────────────────────────────────────────────

    def credit_customer(self, claim_id: int) -> None:
        """
        Apply approved warranty credit via CreditMemo (R8).

        Creates a CreditMemo with trigger=APPROVED_WARRANTY and auto-closes it
        so the credit lands on customer.credit_balance — same net effect as the
        old direct CRMService.add_credit path, but now an auditable CM document
        exists for the warranty payout.

        Transitions claim → CUSTOMER_CREDITED.
        """
        from app.constants import CreditMemoTrigger
        from app.services.credit_memo_service import CreditMemoService

        claim = self._get_claim_or_404(claim_id)
        if claim.status != WarrantyStatus.VENDOR_APPROVED:
            raise ValueError(
                f"Claim {claim.claim_number} is '{claim.status}', not VENDOR_APPROVED. "
                "Cannot credit customer."
            )
        if claim.total_credit_amount <= 0:
            raise ValueError(
                f"Claim {claim.claim_number} has no credit amount to apply"
            )

        # R8 — issue credit memo for the warranty amount; auto-close pushes to credit balance.
        cm = CreditMemoService(self.db, self.current_user_id).create_credit_memo(
            customer_id=claim.customer_id,
            trigger_type=CreditMemoTrigger.APPROVED_WARRANTY,
            lines=[{
                "description": f"Warranty credit — claim {claim.claim_number}",
                "qty": 1,
                "unit_price": claim.total_credit_amount,
            }],
            original_invoice_id=claim.invoice_id,
            warranty_claim_id=claim.id,
            reason=f"Warranty claim {claim.claim_number}",
            auto_close_to_credit=True,
        )

        # CM service committed; now update claim status and commit.
        claim.status = WarrantyStatus.CUSTOMER_CREDITED
        claim.resolution_type = WarrantyResolution.CREDIT
        self.audit(
            entity_type=EntityType.WARRANTY_CLAIM,
            entity_id=claim_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=WarrantyStatus.VENDOR_APPROVED,
            new_value=WarrantyStatus.CUSTOMER_CREDITED,
            notes=f"credit memo {cm.cm_number}: ${claim.total_credit_amount}",
        )
        self.db.commit()

    def issue_refund_check(self, claim_id: int) -> None:
        """
        Discretionary check refund path. Marks claim as credited; the physical
        check is issued manually outside the system.

        Phase 2 note: full credit memo invoice (negative-amount) is deferred
        until InvoiceService adds credit-memo line type support.
        """
        claim = self._get_claim_or_404(claim_id)
        if claim.status != WarrantyStatus.VENDOR_APPROVED:
            raise ValueError(
                f"Claim {claim.claim_number} is '{claim.status}', not VENDOR_APPROVED"
            )

        claim.status = WarrantyStatus.CUSTOMER_CREDITED
        claim.resolution_type = WarrantyResolution.CREDIT  # check = credit in system
        self.audit(
            entity_type=EntityType.WARRANTY_CLAIM,
            entity_id=claim_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=WarrantyStatus.VENDOR_APPROVED,
            new_value=WarrantyStatus.CUSTOMER_CREDITED,
            notes=f"refund check issued: ${claim.total_credit_amount}",
        )
        self.db.commit()

    def notify_customer_of_denial(self, claim_id: int, notes: str) -> None:
        """Record that customer was notified of vendor denial."""
        claim = self._get_claim_or_404(claim_id)
        if claim.status != WarrantyStatus.VENDOR_DENIED:
            raise ValueError(
                f"Claim {claim.claim_number} is '{claim.status}', not VENDOR_DENIED"
            )
        old_notes = claim.notes
        claim.notes = f"{claim.notes}\n{notes}".strip() if claim.notes else notes
        claim.status = WarrantyStatus.CUSTOMER_NOTIFIED
        self.audit(
            entity_type=EntityType.WARRANTY_CLAIM,
            entity_id=claim_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=WarrantyStatus.VENDOR_DENIED,
            new_value=WarrantyStatus.CUSTOMER_NOTIFIED,
            notes=notes,
        )
        self.db.commit()

    def close_claim(self, claim_id: int) -> None:
        """
        Close the claim. Requires status CUSTOMER_CREDITED or CUSTOMER_NOTIFIED
        (i.e., customer resolution is complete).
        """
        claim = self._get_claim_or_404(claim_id)
        closable = (WarrantyStatus.CUSTOMER_CREDITED, WarrantyStatus.CUSTOMER_NOTIFIED)
        if claim.status not in closable:
            raise ValueError(
                f"Claim {claim.claim_number} has status '{claim.status}'. "
                f"Can only close from: {', '.join(closable)}"
            )
        old_status = claim.status
        claim.status = WarrantyStatus.CLOSED
        self.audit(
            entity_type=EntityType.WARRANTY_CLAIM,
            entity_id=claim_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=WarrantyStatus.CLOSED,
        )
        self.db.commit()

    # ── Vendor Credit Recording ───────────────────────────────────────────────

    def record_vendor_credit(
        self,
        claim_id: int,
        vendor_id: int,
        credit_amount: float,
        reference: str | None = None,
    ) -> object:
        """
        Record the credit JAKS receives from the vendor after claim approval.
        Creates a VendorCredit record (type=WARRANTY) for the vendor ledger.
        Independent of customer credit — both may be issued for the same claim.
        """
        from app.models.vendor import VendorCredit

        claim = self._get_claim_or_404(claim_id)
        if credit_amount <= 0:
            raise ValueError(f"credit_amount must be positive, got {credit_amount}")

        note_parts = [f"Warranty claim {claim.claim_number}"]
        if reference:
            note_parts.append(f"ref: {reference}")

        vendor_credit = VendorCredit(
            vendor_id=vendor_id,
            credit_type=VendorCreditType.WARRANTY,
            amount=round(credit_amount, 2),
            status=VendorCreditStatus.OPEN,
            notes=" — ".join(note_parts),
        )
        self.db.add(vendor_credit)

        self.audit(
            entity_type=EntityType.WARRANTY_CLAIM,
            entity_id=claim_id,
            action=AuditAction.CREATED,
            new_value={
                "vendor_id": vendor_id,
                "credit_amount": credit_amount,
                "type": "vendor_credit",
            },
        )
        self.db.commit()
        return vendor_credit

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_open_claims(self, customer_id: int | None = None) -> list[WarrantyClaim]:
        """Return all non-closed warranty claims, optionally filtered by customer."""
        closed_statuses = (WarrantyStatus.CLOSED, WarrantyStatus.CUSTOMER_NOTIFIED)
        query = (
            self.db.query(WarrantyClaim)
            .filter(WarrantyClaim.status.notin_(closed_statuses))
            .order_by(WarrantyClaim.claim_date.desc())
        )
        if customer_id is not None:
            query = query.filter(WarrantyClaim.customer_id == customer_id)
        return query.all()

    def get_pending_vendor_submission(self) -> list[WarrantyClaim]:
        """Return DRAFT claims that have at least one line — ready to be submitted."""
        return (
            self.db.query(WarrantyClaim)
            .filter(
                WarrantyClaim.status == WarrantyStatus.DRAFT,
                WarrantyClaim.claim_lines.any(),
            )
            .order_by(WarrantyClaim.claim_date)
            .all()
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_claim_or_404(self, claim_id: int) -> WarrantyClaim:
        claim = self.db.query(WarrantyClaim).filter(WarrantyClaim.id == claim_id).first()
        if claim is None:
            raise ValueError(f"WarrantyClaim {claim_id} not found")
        return claim

    def _add_claim_line_internal(self, claim_id: int, data: dict) -> WarrantyClaimLine:
        line = WarrantyClaimLine(
            warranty_claim_id=claim_id,
            invoice_line_id=data.get("invoice_line_id"),
            product_id=data.get("product_id"),
            qty_claimed=int(data.get("qty_claimed", 1)),
            approved_qty=0,
            credit_amount=round(float(data.get("credit_amount", 0.0)), 2),
            resolution=None,
        )
        self.db.add(line)
        self.db.flush()
        return line
