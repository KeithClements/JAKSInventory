"""
app/services/core_service.py
==============================
Core charge lifecycle — customer cores and vendor core returns.

Two separate charge layers:
  - customer_unit_charge: what JAKS bills the customer (on invoice)
  - vendor_unit_charge:   what JAKS paid the vendor (returned for credit)

Core margin = customer_unit_charge - vendor_unit_charge (JAKS keeps this).

Key rules:
  - Core created at invoice finalisation for products with core charges
  - Return deadline: invoice_date + default_core_return_days (from settings)
  - Partial returns: CoreReturnEvent records each return; qty_returned cumulative
  - Vendor status: pending → submitted → accepted | denied
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.constants import (
    AuditAction, CoreCreditMethod, CoreDirection,
    CoreStatus, CoreVendorStatus, EntityType,
)
from app.models.core import CoreCharge, CoreReturnEvent, CoreSlip
from app.models.customer import Customer
from app.settings_utils import get_setting_value_db as _get_setting, bump_counter
from app.services.base import BaseService


class CoreService(BaseService):

    # ── Core Creation ─────────────────────────────────────────────────────────

    def create_core_charge(
        self,
        invoice_id: int,
        invoice_line_id: int,
        product_id: int,
        qty: int,
        customer_unit_charge: float,
        vendor_unit_charge: float,
    ) -> CoreCharge:
        """
        Called by InvoiceService.finalise() for product lines with core charges.
        Sets return_deadline = invoice_date + default_core_return_days.
        """
        # Determine customer_id from the invoice
        from app.models.invoice import Invoice
        invoice = self.db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice is None:
            raise ValueError(f"Invoice {invoice_id} not found")

        return_days = int(_get_setting(self.db, "default_core_return_days", "30"))
        deadline = datetime.utcnow() + timedelta(days=return_days)

        core = CoreCharge(
            direction=CoreDirection.CUSTOMER_OWES_RETURN,
            customer_id=invoice.customer_id,
            product_id=product_id,
            invoice_line_id=invoice_line_id,
            qty_charged=qty,
            qty_returned=0,
            customer_unit_charge=customer_unit_charge,
            vendor_unit_charge=vendor_unit_charge,
            status=CoreStatus.OPEN,
            vendor_status=CoreVendorStatus.PENDING,
            return_deadline=deadline,
        )
        self.db.add(core)
        self.db.flush()
        return core

    # ── Customer Returns ──────────────────────────────────────────────────────

    def record_customer_return(
        self,
        core_charge_id: int,
        qty_returned: int,
        condition: str | None = None,
    ) -> CoreReturnEvent:
        """
        Record a partial or full core return from a customer.
        Issues account credit = qty_returned * customer_unit_charge.
        """
        core = self._get_or_404(core_charge_id)
        if core.direction != CoreDirection.CUSTOMER_OWES_RETURN:
            raise ValueError("This core charge is not a customer return direction")
        if qty_returned > core.qty_outstanding:
            raise ValueError(
                f"Cannot return {qty_returned} — only {core.qty_outstanding} outstanding"
            )

        event = CoreReturnEvent(
            core_charge_id=core_charge_id,
            qty_returned=qty_returned,
            returned_at=datetime.utcnow(),
            credit_method=CoreCreditMethod.ACCOUNT_CREDIT,
            credit_amount=round(qty_returned * core.customer_unit_charge, 2),
            processed_by_id=self.current_user_id,
            notes=condition or "",
        )
        self.db.add(event)

        core.qty_returned += qty_returned
        core.status = CoreStatus.RETURNED if core.qty_outstanding == 0 else CoreStatus.PARTIAL

        # Delegate credit balance update to CRMService — sole owner of credit_balance
        if core.customer_id and event.credit_amount > 0:
            from app.services.crm_service import CRMService
            CRMService(self.db, self.current_user_id).add_credit(
                customer_id=core.customer_id,
                amount=event.credit_amount,
                reason=f"Core return #{core_charge_id}",
            )

        self.audit(
            entity_type=EntityType.CORE_CHARGE,
            entity_id=core_charge_id,
            action=AuditAction.CORE_RECEIVED,
            new_value={"qty_returned": qty_returned, "credit": event.credit_amount},
        )
        self.db.commit()
        return event

    # ── Core Slip ─────────────────────────────────────────────────────────────

    def create_core_slip(self, core_charge_id: int) -> CoreSlip:
        """
        Generate a customer core return slip (CORE-YYYY-XXXX) for a returned charge.
        Links the CoreCharge to the new slip.  Safe to call after the return is committed.
        """
        from app.constants import CoreSlipStatus
        core = self._get_or_404(core_charge_id)
        year = datetime.utcnow().year
        slip_number = bump_counter(self.db, "next_core_slip_number", "CORE", year)
        slip = CoreSlip(
            slip_number=slip_number,
            customer_id=core.customer_id,
            invoice_id=core.credit_invoice_id,
            status=CoreSlipStatus.OPEN,
        )
        self.db.add(slip)
        self.db.flush()   # get slip.id
        core.core_slip_id = slip.id
        self.db.commit()
        self.db.refresh(slip)
        return slip

    # ── Vendor Submission ─────────────────────────────────────────────────────

    def submit_to_vendor(
        self,
        core_charge_id: int,
        tracking_number: str | None = None,
    ) -> None:
        """Mark vendor submission. Sets vendor_status = 'submitted'."""
        core = self._get_or_404(core_charge_id)
        core.core_tracking_number = tracking_number
        core.vendor_status = CoreVendorStatus.PENDING   # stays pending until vendor responds
        core.status = CoreStatus.SHIPPED_TO_VENDOR
        self.db.commit()

    def record_vendor_acceptance(self, core_charge_id: int, credit_amount: float) -> None:
        """
        Vendor accepted the core return.
        Creates a VendorCredit record so the credit appears in the vendor ledger
        and can be picked up by the QBO sync job.
        """
        from app.models.vendor import VendorCredit
        from app.constants import VendorCreditStatus, VendorCreditType

        core = self._get_or_404(core_charge_id)
        core.vendor_status = CoreVendorStatus.ACCEPTED
        core.vendor_decision_at = datetime.utcnow()
        core.status = CoreStatus.VENDOR_ACCEPTED

        if core.vendor_id and credit_amount > 0:
            vendor_credit = VendorCredit(
                vendor_id=core.vendor_id,
                credit_type=VendorCreditType.RETURN,
                amount=credit_amount,
                status=VendorCreditStatus.OPEN,
                # qbo_sync_status inherits PENDING from QBOSyncMixin default
                notes=f"Core charge #{core_charge_id} accepted by vendor",
            )
            self.db.add(vendor_credit)

        self.db.commit()

    def record_vendor_denial(
        self,
        core_charge_id: int,
        denial_reason: str,
        resolution: str,
        notes: str | None = None,
    ) -> None:
        """Vendor denied the core. resolution: 'absorbed_by_jaks' | 'charged_to_customer' | 'disputed'."""
        core = self._get_or_404(core_charge_id)
        core.vendor_status = CoreVendorStatus.REJECTED
        core.vendor_decision_at = datetime.utcnow()
        core.status = CoreStatus.VENDOR_REJECTED
        core.vendor_denial_reason = denial_reason
        core.denial_resolution = resolution
        core.denial_notes = notes or ""
        self.db.commit()

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_outstanding_cores(self, customer_id: int | None = None) -> list[CoreCharge]:
        """Return all open core charges (qty_outstanding > 0)."""
        query = (
            self.db.query(CoreCharge)
            .filter(
                CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
                CoreCharge.qty_returned < CoreCharge.qty_charged,
            )
        )
        if customer_id is not None:
            query = query.filter(CoreCharge.customer_id == customer_id)
        return query.order_by(CoreCharge.return_deadline).all()

    def get_overdue_cores(self) -> list[CoreCharge]:
        """Return all overdue cores (past return_deadline with qty_outstanding > 0)."""
        now = datetime.utcnow()
        return (
            self.db.query(CoreCharge)
            .filter(
                CoreCharge.return_deadline < now,
                CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
                CoreCharge.qty_returned < CoreCharge.qty_charged,
            )
            .order_by(CoreCharge.return_deadline)
            .all()
        )

    def get_pending_vendor_submission(self) -> list[CoreCharge]:
        """Return cores with qty_returned > 0 but not yet submitted to vendor."""
        return (
            self.db.query(CoreCharge)
            .filter(
                CoreCharge.status == CoreStatus.RETURNED,
                CoreCharge.vendor_status == CoreVendorStatus.PENDING,
            )
            .order_by(CoreCharge.updated_at)
            .all()
        )

    # ── Private ───────────────────────────────────────────────────────────────

    def _get_or_404(self, core_charge_id: int) -> CoreCharge:
        core = self.db.query(CoreCharge).filter(CoreCharge.id == core_charge_id).first()
        if core is None:
            raise ValueError(f"CoreCharge {core_charge_id} not found")
        return core
