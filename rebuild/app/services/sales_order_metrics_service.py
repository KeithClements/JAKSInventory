"""
app/services/sales_order_metrics_service.py
============================================
Phase 2 §5.1 / §5.2 / §5.10 — Sales-Order dashboard metrics + the SO↔PO rollup.

Read-only contracts the UI renders; this lane owns the service, never templates.

§5.1 dashboard_metrics() — the strip above the SO list:
    open_so_value · backordered_value · waiting_on_inventory · ready_to_ship ·
    on_hold · fulfilled_today · open_core_liability

§5.10 po_link_status() / po_link_map() — derive a customer-facing rollup for a SO
line linked to a PO line (off the existing linked_po_line_id): draft → ordered →
partial → received (→ cancelled). The PO model tracks placement + receipt, so the
plan's aspirational "vendor-confirmed / shipped" intermediate states collapse onto
ordered/partial until those PO fields exist — documented, not faked.

Lifecycle note: SOs never reach SOStatus.FULFILLED in this app — fulfillment IS
invoicing (OPEN/PARTIAL → INVOICED). INVOICED SOs are edit-locked, so updated_at
stays at the fulfillment moment → "fulfilled today" = SOs that hit INVOICED today.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import selectinload, joinedload

from app.constants import (
    SOStatus, SOLineStatus, SOLineSource, POStatus, CoreDirection, CoreStatus,
)
from app.models.quote import SalesOrder, SOLine
from app.models.purchase_order import POLine
from app.models.core import CoreCharge
from app.services.base import BaseService


# SOs still in flight (value/work lives here).
_ACTIVE_SO = (SOStatus.OPEN, SOStatus.PARTIAL, SOStatus.HOLD)

# Line states that mean "not fulfillable from stock yet — waiting on inbound".
_WAITING_LINE_STATUSES = frozenset({
    SOLineStatus.BACKORDER,
    SOLineStatus.AWAITING_STOCK,
    SOLineStatus.AWAITING_PO_RECEIPT,
    SOLineStatus.AWAITING_SPECIAL_ORDER_PO,
})

SO_DASHBOARD_KEYS = (
    "open_so_value", "backordered_value", "waiting_on_inventory", "ready_to_ship",
    "on_hold", "fulfilled_today", "open_core_liability", "open_count",
)

# SO↔PO rollup statuses → human labels (UI may relabel).
_ROLLUP_LABELS = {
    "draft":     "PO Draft",
    "ordered":   "Ordered",
    "partial":   "Partially Received",
    "received":  "Received",
    "cancelled": "PO Cancelled",
}


class SalesOrderMetricsService(BaseService):
    """SO dashboard metrics (§5.1) + SO↔PO rollup (§5.10). Read-only."""

    # ── §5.1 Dashboard strip ──────────────────────────────────────────────────

    def dashboard_metrics(self) -> dict:
        """Aggregate metrics for the strip above the SO list. Every
        SO_DASHBOARD_KEYS key is always present."""
        active = (
            self.db.query(SalesOrder)
            .filter(SalesOrder.status.in_(_ACTIVE_SO))
            .options(selectinload(SalesOrder.lines))
            .all()
        )
        open_so_value = 0.0
        backordered_value = 0.0
        waiting = 0
        ready = 0
        on_hold = 0
        for so in active:
            open_so_value += so.subtotal
            if so.status == SOStatus.HOLD:
                on_hold += 1
            waiting_lines = [ln for ln in so.lines if self._is_waiting(ln)]
            backordered_value += sum(ln.line_total for ln in waiting_lines)
            if so.status in (SOStatus.OPEN, SOStatus.PARTIAL):
                has_open = any(self._remaining(ln) > 0 for ln in so.lines)
                if waiting_lines:
                    waiting += 1
                elif has_open:
                    ready += 1   # work to do, nothing waiting → shippable now

        return {
            "open_so_value": round(open_so_value, 2),
            "backordered_value": round(backordered_value, 2),
            "waiting_on_inventory": waiting,
            "ready_to_ship": ready,
            "on_hold": on_hold,
            "fulfilled_today": self._fulfilled_today(),
            "open_core_liability": self._open_core_liability(),
            "open_count": len(active),
        }

    @staticmethod
    def _remaining(line: SOLine) -> int:
        return max(0, (line.qty_ordered or 0) - (line.qty_invoiced or 0) - (line.qty_cancelled or 0))

    def _is_waiting(self, line: SOLine) -> bool:
        """A line with work left that's waiting on inbound stock/PO/special order."""
        if self._remaining(line) <= 0:
            return False
        return (
            line.line_status in _WAITING_LINE_STATUSES
            or line.source == SOLineSource.BACKORDER
        )

    def _fulfilled_today(self) -> int:
        now = datetime.utcnow()
        day_start = datetime(now.year, now.month, now.day)
        day_end = day_start + timedelta(days=1)
        return (
            self.db.query(func.count(SalesOrder.id))
            .filter(
                SalesOrder.status == SOStatus.INVOICED,
                SalesOrder.updated_at >= day_start,
                SalesOrder.updated_at < day_end,
            )
            .scalar()
        ) or 0

    def _open_core_liability(self) -> float:
        """Business-wide open customer-owed core deposits (shared with the Core
        Dashboard §5.4 next round): Σ customer_unit_charge × qty_outstanding."""
        rows = (
            self.db.query(
                CoreCharge.customer_unit_charge,
                CoreCharge.qty_charged,
                CoreCharge.qty_returned,
            )
            .filter(
                CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
                CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
            )
            .all()
        )
        total = sum(
            float(unit or 0.0) * max(0, (qc or 0) - (qr or 0))
            for unit, qc, qr in rows
        )
        return round(total, 2)

    # ── §5.10 SO↔PO rollup ────────────────────────────────────────────────────

    def po_link_status(self, line: SOLine) -> dict | None:
        """Rollup for one SO line linked to a PO line (None when not linked)."""
        if not line.linked_po_line_id:
            return None
        pol = (
            self.db.query(POLine)
            .filter(POLine.id == line.linked_po_line_id)
            .options(joinedload(POLine.po))
            .first()
        )
        if pol is None:
            return None
        return self._rollup_dict(pol, line.eta_date)

    def po_link_map(self, so: SalesOrder) -> dict[int, dict]:
        """{so_line_id: rollup} for every linked line on the SO (batched, no N+1)."""
        linked = {ln.id: ln for ln in so.lines if ln.linked_po_line_id}
        if not linked:
            return {}
        pol_ids = {ln.linked_po_line_id for ln in linked.values()}
        pols = {
            p.id: p for p in (
                self.db.query(POLine)
                .filter(POLine.id.in_(pol_ids))
                .options(joinedload(POLine.po))
                .all()
            )
        }
        out: dict[int, dict] = {}
        for line_id, ln in linked.items():
            pol = pols.get(ln.linked_po_line_id)
            if pol is not None:
                out[line_id] = self._rollup_dict(pol, ln.eta_date)
        return out

    def _rollup_dict(self, pol: POLine, eta_date) -> dict:
        status = self._derive_rollup(pol)
        return {
            "status": status,
            "label": _ROLLUP_LABELS[status],
            "po_id": pol.po_id,
            "po_number": pol.po.po_number if pol.po else None,
            "qty_ordered": pol.qty_ordered,
            "qty_received": pol.qty_received,
            "eta_date": eta_date,
        }

    @staticmethod
    def _derive_rollup(pol: POLine) -> str:
        po_status = pol.po.status if pol.po else None
        if po_status == POStatus.CANCELLED or (
            pol.qty_cancelled and pol.qty_cancelled >= pol.qty_ordered
        ):
            return "cancelled"
        if pol.qty_ordered > 0 and pol.qty_received >= pol.qty_ordered:
            return "received"
        if pol.qty_received > 0:
            return "partial"
        # Nothing received yet — placed-with-vendor vs not-yet-sent.
        if po_status in (POStatus.SENT, POStatus.PARTIAL, POStatus.RECEIVED, POStatus.BILLED):
            return "ordered"
        return "draft"   # DRAFT / VERBAL_ORDER — PO not sent yet
