"""
app/services/core_metrics_service.py
====================================
Phase 2 §5.4 — Core Dashboard metrics. Read-only contract (mirrors the
Customer/Invoice/SO metrics services); this lane owns the service, not templates.

dashboard_metrics() returns the strip the Core Dashboard renders. It REPRODUCES
the five count tiles the existing cores/list.html already consumes (so nothing
breaks) and ADDS the §5.4 dollar figures:

  counts (existing): awaiting_return, overdue, pending_inspection, ready_to_ship,
                     awaiting_vendor
  §5.4 dollars:      outstanding_core_liability, core_credits_issued,
                     vendor_recoveries (+vendor_recovery_count), aging_value

Definitions match the cores list route's stage queries exactly. open_core_liability
shares the SalesOrderMetricsService definition (Σ customer_unit_charge ×
qty_outstanding over open CUSTOMER_OWES_RETURN cores).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func

from app.constants import (
    CoreDirection, CoreStatus, CoreVendorStatus, CoreInspectionOutcome, VCRStatus,
)
from app.models.core import CoreCharge, CoreReturnEvent, VendorCoreReturn
from app.services.base import BaseService


CORE_DASHBOARD_KEYS = (
    "awaiting_return", "overdue", "pending_inspection", "ready_to_ship",
    "awaiting_vendor", "outstanding_core_liability", "core_credits_issued",
    "vendor_recoveries", "vendor_recovery_count", "aging_value",
)

# VCRs still expecting money back from the vendor (not yet credited/closed).
_VCR_OPEN = (VCRStatus.DRAFT, VCRStatus.SHIPPED, VCRStatus.VENDOR_REVIEW, VCRStatus.DISPUTED)


class CoreMetricsService(BaseService):
    """Core Dashboard metrics (§5.4). Read-only; no audit/commit."""

    def dashboard_metrics(self) -> dict:
        now = datetime.utcnow()

        # ── Open customer cores (awaiting return) — one pass for count, overdue,
        #    outstanding liability, and aging $ ──────────────────────────────
        open_cores = (
            self.db.query(CoreCharge)
            .filter(
                CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
                CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
            )
            .all()
        )
        awaiting_return = len(open_cores)
        overdue = 0
        outstanding_liability = 0.0
        aging_value = 0.0
        for c in open_cores:
            liability = (c.customer_unit_charge or 0.0) * c.qty_outstanding
            outstanding_liability += liability
            # is_overdue: open core past its return_deadline (status != CLOSED holds here)
            if c.return_deadline is not None and now > c.return_deadline:
                overdue += 1
                aging_value += liability

        # ── Stage counts — match the cores list route's exact filters ─────────
        pending_inspection = (
            self.db.query(func.count(CoreCharge.id))
            .filter(
                CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
                CoreCharge.status.in_([CoreStatus.RETURNED, CoreStatus.PARTIAL]),
                CoreCharge.inspection_outcome == CoreInspectionOutcome.HOLD,
            )
            .scalar()
        ) or 0
        ready_to_ship = (
            self.db.query(func.count(CoreCharge.id))
            .filter(
                CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
                CoreCharge.status == CoreStatus.RETURNED,
                CoreCharge.vendor_status == CoreVendorStatus.PENDING,
                CoreCharge.inspection_outcome != CoreInspectionOutcome.HOLD,
            )
            .scalar()
        ) or 0
        awaiting_vendor = (
            self.db.query(func.count(CoreCharge.id))
            .filter(CoreCharge.status == CoreStatus.SHIPPED_TO_VENDOR)
            .scalar()
        ) or 0

        # ── Core credits issued to customers (all-time) ───────────────────────
        core_credits_issued = (
            self.db.query(func.sum(CoreReturnEvent.credit_amount)).scalar()
        ) or 0.0

        # ── Vendor recoveries — outstanding expected credit on open VCRs ──────
        open_vcrs = (
            self.db.query(VendorCoreReturn)
            .filter(VendorCoreReturn.status.in_(_VCR_OPEN))
            .all()
        )
        vendor_recoveries = sum(
            max(0.0, (v.expected_credit or 0.0) - (v.actual_credit or 0.0))
            for v in open_vcrs
        )

        return {
            # existing count tiles (cores/list.html contract)
            "awaiting_return": awaiting_return,
            "overdue": overdue,
            "pending_inspection": pending_inspection,
            "ready_to_ship": ready_to_ship,
            "awaiting_vendor": awaiting_vendor,
            # §5.4 dollar figures
            "outstanding_core_liability": round(outstanding_liability, 2),
            "core_credits_issued": round(core_credits_issued, 2),
            "vendor_recoveries": round(vendor_recoveries, 2),
            "vendor_recovery_count": len(open_vcrs),
            "aging_value": round(aging_value, 2),
        }
