"""
app/services/payment_service.py
=================================
Payment recording, allocation, NSF handling, and QBO sync.

Key rules:
  - One payment can cover multiple invoices via PaymentAllocation
  - Unapplied payments sit as credit on the customer account
  - NSF: reverse payment + create NSF fee invoice
  - account_credit method draws from customer.credit_balance
  - QBO sync: payments pushed after allocation
"""
from __future__ import annotations

from datetime import datetime

from app.constants import (
    AuditAction, EntityType, InvoiceLockReason, InvoiceStatus,
    LineType, PaymentMethod, PaymentStatus,
    QBOSyncStatus,
)
from app.models.customer import Customer
from app.models.invoice import Invoice, Payment, PaymentAllocation
from app.services.base import BaseService


class PaymentService(BaseService):

    # ── Record Payment ────────────────────────────────────────────────────────

    def record_payment(
        self,
        customer_id: int,
        amount_received: float,
        payment_method: str,
        data: dict,
        invoice_ids: list[int] | None = None,
    ) -> Payment:
        """
        Record a payment and optionally allocate to one or more invoices.
        If invoice_ids is None or empty, payment sits as unapplied credit.
        Validates: sum of allocations <= amount_received.
        """
        if amount_received <= 0:
            raise ValueError("amount_received must be greater than 0")

        payment = Payment(
            customer_id=customer_id,
            payment_date=data.get("payment_date") or datetime.utcnow(),
            payment_method=payment_method,
            check_number=data.get("check_number"),
            amount_received=amount_received,
            status=PaymentStatus.APPLIED,
            notes=data.get("notes", ""),
            qbo_sync_status=QBOSyncStatus.PENDING,
        )
        self.db.add(payment)
        self.db.flush()

        if invoice_ids:
            # Auto-allocate: spread payment across invoices in order given
            remaining = amount_received
            for inv_id in invoice_ids:
                if remaining <= 0:
                    break
                invoice = self.db.query(Invoice).filter(Invoice.id == inv_id).first()
                if invoice is None or invoice.customer_id != customer_id:
                    raise ValueError(f"Invoice {inv_id} not found or belongs to different customer")
                apply = min(remaining, invoice.balance_due)
                if apply <= 0:
                    continue
                self._create_allocation(payment.id, inv_id, apply)
                remaining -= apply
                self._trigger_invoice_status_refresh(invoice.id)

        self.audit(
            entity_type=EntityType.PAYMENT,
            entity_id=payment.id,
            action=AuditAction.PAYMENT_APPLIED,
            new_value={
                "amount": amount_received,
                "method": payment_method,
                "invoices": invoice_ids or [],
            },
        )
        self.db.commit()
        return payment

    def allocate(self, payment_id: int, invoice_id: int, amount: float) -> PaymentAllocation:
        """
        Allocate (or add to existing allocation) payment amount to an invoice.
        Auto-locks invoice if fully paid after allocation.
        """
        payment = self._get_payment_or_404(payment_id)
        invoice = self.db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice is None:
            raise ValueError(f"Invoice {invoice_id} not found")

        if amount > invoice.balance_due + 0.001:
            raise ValueError(f"Amount {amount} exceeds invoice balance due {invoice.balance_due}")
        if amount > payment.amount_unallocated + 0.001:
            raise ValueError(f"Amount {amount} exceeds payment unallocated balance {payment.amount_unallocated}")

        allocation = self._create_allocation(payment_id, invoice_id, amount)
        self._trigger_invoice_status_refresh(invoice.id)
        self.db.commit()
        return allocation

    def reallocate(self, allocation_id: int, new_amount: float) -> None:
        """Adjust an existing allocation amount."""
        allocation = self.db.query(PaymentAllocation).filter(PaymentAllocation.id == allocation_id).first()
        if allocation is None:
            raise ValueError(f"PaymentAllocation {allocation_id} not found")
        if allocation.is_reversed:
            raise ValueError("Cannot reallocate a reversed allocation")

        payment = self._get_payment_or_404(allocation.payment_id)
        # Available = current unallocated + what this allocation was using
        available = payment.amount_unallocated + allocation.amount_applied
        if new_amount > available + 0.001:
            raise ValueError(f"New amount {new_amount} exceeds available {available}")

        allocation.amount_applied = new_amount
        self._trigger_invoice_status_refresh(allocation.invoice_id)
        self.db.commit()

    def deallocate(self, allocation_id: int) -> None:
        """Remove an allocation — amount returns to payment's unapplied balance."""
        allocation = self.db.query(PaymentAllocation).filter(PaymentAllocation.id == allocation_id).first()
        if allocation is None:
            raise ValueError(f"PaymentAllocation {allocation_id} not found")
        invoice = allocation.invoice
        self.db.delete(allocation)
        self.db.flush()
        self._trigger_invoice_status_refresh(invoice.id)
        self.db.commit()

    # ── NSF / Reversal ────────────────────────────────────────────────────────

    def reverse_payment(self, payment_id: int, reason: str) -> None:
        """
        Reverse a payment. Marks all allocations reversed and re-opens invoices.
        """
        payment = self._get_payment_or_404(payment_id)
        if payment.status == PaymentStatus.REVERSED:
            raise ValueError(f"Payment {payment_id} is already reversed")

        from app.services.invoice_service import InvoiceService
        inv_svc = InvoiceService(self.db, self.current_user_id)

        for allocation in payment.allocations:
            if not allocation.is_reversed:
                allocation.is_reversed = True
                # Delegate status reset to InvoiceService — sole owner of invoice.status
                inv_svc.reopen_after_payment_reversal(allocation.invoice_id)

        payment.status = PaymentStatus.REVERSED
        payment.reversed_at = datetime.utcnow()
        payment.reversal_reason = reason

        self.audit(
            entity_type=EntityType.PAYMENT,
            entity_id=payment_id,
            action=AuditAction.PAYMENT_REVERSED,
            new_value={"reason": reason},
        )
        self.db.commit()

    def process_nsf(self, payment_id: int, nsf_fee: float) -> Invoice:
        """
        Full NSF workflow:
          1. Reverse the payment
          2. Record NSF fee on payment
          3. Create new invoice for NSF fee amount
        Returns the NSF fee invoice.
        """
        payment = self._get_payment_or_404(payment_id)
        self.reverse_payment(payment_id, "nsf")

        payment.nsf_fee = nsf_fee
        payment.status = PaymentStatus.NSF
        self.db.flush()

        # Create NSF fee invoice
        from app.services.invoice_service import InvoiceService
        inv_svc = InvoiceService(self.db, self.current_user_id)
        nsf_invoice = inv_svc.create_invoice(
            customer_id=payment.customer_id,
            data={"notes": f"NSF fee — reversed payment #{payment_id}"},
            lines=[{
                "line_type": LineType.NSF_FEE,
                "description": "NSF / Returned Check Fee",
                "qty": 1,
                "unit_price": nsf_fee,
                "unit_cost": 0.0,
            }],
        )
        self.audit(
            entity_type=EntityType.PAYMENT,
            entity_id=payment_id,
            action=AuditAction.NSF,
            new_value={"nsf_fee": nsf_fee, "nsf_invoice_id": nsf_invoice.id},
        )
        self.db.commit()
        return nsf_invoice

    # ── Account Credit ────────────────────────────────────────────────────────

    def apply_account_credit(self, customer_id: int, invoice_id: int, amount: float) -> PaymentAllocation:
        """
        Apply customer.credit_balance to an invoice.
        Reduces credit_balance. Creates a Payment record with method='account_credit'.
        """
        customer = self.db.query(Customer).filter(Customer.id == customer_id).first()
        if customer is None:
            raise ValueError(f"Customer {customer_id} not found")
        if customer.credit_balance < amount - 0.001:
            raise ValueError(f"Insufficient credit balance ({customer.credit_balance}) for amount {amount}")

        invoice = self.db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice is None:
            raise ValueError(f"Invoice {invoice_id} not found")
        if amount > invoice.balance_due + 0.001:
            raise ValueError(f"Amount {amount} exceeds balance due {invoice.balance_due}")

        payment = Payment(
            customer_id=customer_id,
            payment_date=datetime.utcnow(),
            payment_method=PaymentMethod.ACCOUNT_CREDIT,
            amount_received=amount,
            status=PaymentStatus.APPLIED,
            notes="Applied from account credit balance",
            qbo_sync_status=QBOSyncStatus.PENDING,
        )
        self.db.add(payment)
        self.db.flush()

        allocation = self._create_allocation(payment.id, invoice_id, amount)
        # Delegate credit_balance mutation to CRMService — sole owner
        from app.services.crm_service import CRMService
        CRMService(self.db, self.current_user_id).deduct_credit(
            customer_id=customer_id,
            amount=amount,
            reason=f"Applied to invoice {invoice_id}",
        )
        self._trigger_invoice_status_refresh(invoice.id)

        self.audit(
            entity_type=EntityType.PAYMENT,
            entity_id=payment.id,
            action=AuditAction.PAYMENT_APPLIED,
            new_value={"method": "account_credit", "amount": amount, "invoice_id": invoice_id},
        )
        self.db.commit()
        return allocation

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_unapplied_balance(self, customer_id: int) -> float:
        """Return total unapplied payment balance for a customer."""
        payments = (
            self.db.query(Payment)
            .filter(
                Payment.customer_id == customer_id,
                Payment.status == PaymentStatus.APPLIED,
            )
            .all()
        )
        return round(sum(p.amount_unallocated for p in payments), 2)

    def get_open_invoices(self, customer_id: int) -> list[dict]:
        """Return all open (unpaid / partially paid) invoices for a customer."""
        invoices = (
            self.db.query(Invoice)
            .filter(
                Invoice.customer_id == customer_id,
                Invoice.status.in_([InvoiceStatus.OPEN, InvoiceStatus.PARTIAL]),
            )
            .order_by(Invoice.created_at)
            .all()
        )
        return [
            {
                "id": inv.id,
                "invoice_number": inv.invoice_number,
                "status": inv.status,
                "total": inv.total,
                "amount_paid": inv.amount_paid,
                "balance_due": inv.balance_due,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "is_overdue": inv.is_overdue,
            }
            for inv in invoices
        ]

    # ── QBO Sync ──────────────────────────────────────────────────────────────

    def mark_synced(self, payment_id: int, qbo_id: str) -> None:
        payment = self._get_payment_or_404(payment_id)
        payment.qbo_payment_id = qbo_id
        payment.qbo_sync_status = QBOSyncStatus.SYNCED
        payment.qbo_last_synced_at = datetime.utcnow()
        payment.qbo_sync_error = None
        self.db.commit()

    def mark_sync_failed(self, payment_id: int, error: str) -> None:
        payment = self._get_payment_or_404(payment_id)
        payment.qbo_sync_status = QBOSyncStatus.ERROR
        payment.qbo_sync_error = error
        payment.qbo_sync_retry_count += 1
        self.db.commit()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_payment_or_404(self, payment_id: int) -> Payment:
        p = self.db.query(Payment).filter(Payment.id == payment_id).first()
        if p is None:
            raise ValueError(f"Payment {payment_id} not found")
        return p

    def _create_allocation(self, payment_id: int, invoice_id: int, amount: float) -> PaymentAllocation:
        allocation = PaymentAllocation(
            payment_id=payment_id,
            invoice_id=invoice_id,
            amount_applied=round(amount, 2),
        )
        self.db.add(allocation)
        self.db.flush()
        return allocation

    def _trigger_invoice_status_refresh(self, invoice_id: int) -> None:
        """
        Delegate invoice status recalculation to InvoiceService — the sole owner
        of invoice.status mutations.  PaymentService must NOT write invoice.status
        directly; it only manages Payment and PaymentAllocation records.
        """
        from app.services.invoice_service import InvoiceService
        InvoiceService(self.db, self.current_user_id).refresh_payment_status(invoice_id)
