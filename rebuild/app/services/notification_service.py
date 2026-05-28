"""
app/services/notification_service.py
======================================
Phase K — In-app notification system.

NotificationService.notify() creates rows in the `notifications` table.
The dashboard and header nav surface unacknowledged warnings/errors.
Phase 2 will add email delivery for CRITICAL severity.

Trigger sites (wired in each service's relevant method):
  - POService.create_receipt          → PO_OVER_RECEIPT
  - POService.create_vendor_bill      → VENDOR_BILL_DISCREPANCY
  - InventoryService.adjust_inventory → INVENTORY_ADJUSTMENT
  - CoreService.mark_overdue_cores    → CORE_OVERDUE
  - WarrantyService.record_vendor_decision → WARRANTY_VENDOR_DECISION
  - InvoiceService.finalise           → NEGATIVE_INVENTORY_OVERRIDE, INVOICE_OVER_THRESHOLD
  - InvoiceService.finalise           → LOW_STOCK (post-decrement check)
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.constants import NotificationSeverity, NotificationType
from app.models.notification import Notification
from app.services.base import BaseService


class NotificationService(BaseService):

    # ── Core write ────────────────────────────────────────────────────────────

    def notify(
        self,
        *,
        notification_type: str,
        message: str,
        severity: str = NotificationSeverity.INFO,
        user_id: int | None = None,
        entity_type: str | None = None,
        entity_id: int | None = None,
        action_url: str | None = None,
    ) -> Notification:
        """
        Create a notification row. user_id=None means system-wide (visible to all admins).
        Does NOT commit — caller owns the transaction.
        """
        n = Notification(
            user_id=user_id,
            severity=severity,
            notification_type=notification_type,
            entity_type=entity_type,
            entity_id=entity_id,
            message=message,
            action_url=action_url,
            created_at=datetime.utcnow(),
        )
        self.db.add(n)
        self.db.flush()
        return n

    # ── Acknowledgement ───────────────────────────────────────────────────────

    def mark_acknowledged(self, notification_id: int, user_id: int) -> Notification:
        """Mark a notification as read. Idempotent."""
        n = self._get_or_404(notification_id)
        if n.acknowledged_at is None:
            n.acknowledged_at = datetime.utcnow()
            n.acknowledged_by_user_id = user_id
            self.db.flush()
        return n

    def dismiss(self, notification_id: int) -> Notification:
        """Dismiss a notification (hide from dashboard). Idempotent."""
        n = self._get_or_404(notification_id)
        if n.dismissed_at is None:
            n.dismissed_at = datetime.utcnow()
            self.db.flush()
        return n

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_unacknowledged(
        self,
        user_id: int | None = None,
        limit: int = 50,
    ) -> list[Notification]:
        """
        Return unacknowledged, non-dismissed notifications for the given user
        (or system-wide if user_id is None). Most recent first.
        """
        q = self.db.query(Notification).filter(
            Notification.acknowledged_at.is_(None),
            Notification.dismissed_at.is_(None),
        )
        if user_id is not None:
            q = q.filter(
                (Notification.user_id == user_id) | (Notification.user_id.is_(None))
            )
        return q.order_by(Notification.created_at.desc()).limit(limit).all()

    def get_recent(self, limit: int = 100) -> list[Notification]:
        """All notifications (including acknowledged), newest first."""
        return (
            self.db.query(Notification)
            .order_by(Notification.created_at.desc())
            .limit(limit)
            .all()
        )

    # ── Typed factory helpers (keep message text here, not in callers) ────────

    @classmethod
    def build_po_over_receipt(
        cls,
        db: Session,
        po_id: int,
        po_number: str,
        sku: str,
        qty_ordered: int,
        qty_received: int,
    ) -> "NotificationService":
        """Return a service instance with the notification already staged (not committed)."""
        svc = cls(db)
        svc.notify(
            notification_type=NotificationType.PO_OVER_RECEIPT,
            severity=NotificationSeverity.WARNING,
            message=(
                f"PO {po_number} — over-receipt on {sku}: "
                f"ordered {qty_ordered}, received {qty_received}"
            ),
            entity_type="purchase_order",
            entity_id=po_id,
            action_url=f"/purchase-orders/{po_id}",
        )
        return svc

    @classmethod
    def build_bill_discrepancy(
        cls,
        db: Session,
        bill_id: int,
        po_number: str,
        detail: str,
        po_id: int = 0,
    ) -> "NotificationService":
        svc = cls(db)
        svc.notify(
            notification_type=NotificationType.VENDOR_BILL_DISCREPANCY,
            severity=NotificationSeverity.WARNING,
            message=f"Vendor bill for PO {po_number} has discrepancy: {detail}",
            entity_type="vendor_bill",
            entity_id=bill_id,
            action_url=f"/purchase-orders/{po_id}" if po_id else None,
        )
        return svc

    @classmethod
    def build_inventory_adjustment(
        cls,
        db: Session,
        product_id: int,
        sku: str,
        qty_delta: int,
        reason: str,
        adjusted_by_user_id: int | None,
    ) -> "NotificationService":
        svc = cls(db)
        direction = "+" if qty_delta > 0 else ""
        svc.notify(
            notification_type=NotificationType.INVENTORY_ADJUSTMENT,
            severity=NotificationSeverity.INFO,
            message=(
                f"Inventory adjusted for {sku}: {direction}{qty_delta} ({reason})"
            ),
            entity_type="product",
            entity_id=product_id,
            action_url=f"/products/{product_id}",
        )
        return svc

    @classmethod
    def build_core_overdue(
        cls,
        db: Session,
        core_id: int,
        core_number: str,
        customer_name: str,
        days_overdue: int,
    ) -> "NotificationService":
        svc = cls(db)
        svc.notify(
            notification_type=NotificationType.CORE_OVERDUE,
            severity=NotificationSeverity.WARNING,
            message=(
                f"Core {core_number} ({customer_name}) is {days_overdue} day(s) overdue"
            ),
            entity_type="core_charge",
            entity_id=core_id,
            action_url=f"/cores/{core_id}",
        )
        return svc

    @classmethod
    def build_warranty_decision(
        cls,
        db: Session,
        claim_id: int,
        claim_number: str,
        decision: str,
    ) -> "NotificationService":
        svc = cls(db)
        svc.notify(
            notification_type=NotificationType.WARRANTY_VENDOR_DECISION,
            severity=NotificationSeverity.INFO,
            message=f"Warranty claim {claim_number} vendor decision: {decision}",
            entity_type="warranty_claim",
            entity_id=claim_id,
            action_url=f"/warranty/{claim_id}",
        )
        return svc

    @classmethod
    def build_negative_inventory_override(
        cls,
        db: Session,
        invoice_id: int,
        invoice_number: str,
        shortages: list[dict],
    ) -> "NotificationService":
        svc = cls(db)
        skus = ", ".join(s["sku"] for s in shortages[:3])
        if len(shortages) > 3:
            skus += f" +{len(shortages) - 3} more"
        svc.notify(
            notification_type=NotificationType.NEGATIVE_INVENTORY_OVERRIDE,
            severity=NotificationSeverity.WARNING,
            message=(
                f"Invoice {invoice_number} finalized with negative inventory override ({skus})"
            ),
            entity_type="invoice",
            entity_id=invoice_id,
            action_url=f"/invoices/{invoice_id}",
        )
        return svc

    @classmethod
    def build_invoice_over_threshold(
        cls,
        db: Session,
        invoice_id: int,
        invoice_number: str,
        total: float,
        threshold: float,
    ) -> "NotificationService":
        svc = cls(db)
        svc.notify(
            notification_type=NotificationType.INVOICE_OVER_THRESHOLD,
            severity=NotificationSeverity.INFO,
            message=(
                f"Large invoice finalized: {invoice_number} = ${total:,.2f} "
                f"(threshold ${threshold:,.2f})"
            ),
            entity_type="invoice",
            entity_id=invoice_id,
            action_url=f"/invoices/{invoice_id}",
        )
        return svc

    @classmethod
    def build_low_stock(
        cls,
        db: Session,
        product_id: int,
        sku: str,
        qty_on_hand: int,
        reorder_point: int,
    ) -> "NotificationService":
        svc = cls(db)
        svc.notify(
            notification_type=NotificationType.LOW_STOCK,
            severity=NotificationSeverity.INFO,
            message=(
                f"Low stock: {sku} — {qty_on_hand} on hand (reorder at {reorder_point})"
            ),
            entity_type="product",
            entity_id=product_id,
            action_url=f"/products/{product_id}",
        )
        return svc

    # ── Private ───────────────────────────────────────────────────────────────

    def _get_or_404(self, notification_id: int) -> Notification:
        n = self.db.query(Notification).filter(Notification.id == notification_id).first()
        if n is None:
            raise ValueError(f"Notification {notification_id} not found")
        return n
