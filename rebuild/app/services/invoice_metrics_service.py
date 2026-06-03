"""
app/services/invoice_metrics_service.py
========================================
Phase 2 §5.8 / P2-D3 — the Invoice Intelligence panel contract.

Produces the per-invoice context the workspace shows while invoicing. profit$ and
margin% are gated behind the per-user Show-margin toggle on the CLIENT (P2-D3);
the backend just supplies the numbers — no role plumbing.

Contract = the keys the UI macro invoice_intelligence_panel(m) reads
(app/templates/macros/intelligence.html):
    profit · margin_pct · core_liability · warranty_exposure ·
    customer_lifetime_sales
Don't rename them.

Definitions:
  • profit / margin_pct — on the GOODS only (PARTS_LINE_TYPES: product/misc/
    warranty), net of the invoice-level discount. Cores (pass-through deposits),
    freight (billed at cost), tax and fees are excluded so margin isn't distorted
    — they surface in their own cells. margin% = profit / net-parts-revenue.
  • core_liability — open customer-owed core deposits on THIS invoice
    (customer_unit_charge × qty_outstanding), same join finalise() stamps and the
    After-Sale card uses. The deposit the customer can still recover.
  • warranty_exposure — total_credit_amount of NON-terminal warranty claims linked
    to this invoice (what JAKS may still pay out).
  • customer_lifetime_sales — the single P2-Q1 number from CustomerMetricsService,
    so the invoice panel and the customer panel agree.
"""
from __future__ import annotations

from app.constants import (
    PARTS_LINE_TYPES, CoreDirection, CoreStatus, WarrantyStatus,
)
from app.models.invoice import Invoice, InvoiceLine
from app.models.core import CoreCharge
from app.models.warranty import WarrantyClaim
from app.services.base import BaseService
from app.services.customer_metrics_service import CustomerMetricsService


INVOICE_METRIC_KEYS = (
    "profit", "margin_pct", "core_liability", "warranty_exposure",
    "customer_lifetime_sales",
)

# Warranty claim statuses that are resolved — no further exposure.
_WARRANTY_TERMINAL = frozenset({
    WarrantyStatus.VENDOR_DENIED,
    WarrantyStatus.CUSTOMER_CREDITED,
    WarrantyStatus.CUSTOMER_NOTIFIED,
    WarrantyStatus.CLOSED,
})


class InvoiceMetricsService(BaseService):
    """Invoice Intelligence panel metrics (§5.8). Read-only; no audit/commit."""

    def intelligence_for(self, invoice) -> dict:
        """Full intelligence dict for one invoice (id or Invoice instance).
        Every INVOICE_METRIC_KEYS key is always present."""
        invc = (
            invoice if isinstance(invoice, Invoice)
            else self.db.query(Invoice).filter(Invoice.id == int(invoice)).first()
        )
        if invc is None:
            return {k: 0.0 for k in INVOICE_METRIC_KEYS}

        profit, margin_pct = self._profit_and_margin(invc)
        return {
            "profit": profit,
            "margin_pct": margin_pct,
            "core_liability": self._core_liability(invc),
            "warranty_exposure": self._warranty_exposure(invc),
            "customer_lifetime_sales": self._customer_lifetime(invc),
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    def _profit_and_margin(self, invc: Invoice) -> tuple[float, float]:
        """Profit$ and margin% on the parts (goods) only, net of the invoice
        discount. Mirrors the quote preview's revenue-minus-cost basis."""
        parts = [ln for ln in invc.lines if ln.line_type in PARTS_LINE_TYPES]
        revenue_gross = sum(ln.line_total for ln in parts)
        cost = sum((ln.unit_cost or 0.0) * (ln.qty or 0) for ln in parts)
        # invoice.discount_amount is the invoice-level discount, applied to parts.
        revenue_net = revenue_gross - (invc.discount_amount or 0.0)
        profit = round(revenue_net - cost, 2)
        margin_pct = round(profit / revenue_net * 100, 1) if revenue_net > 0 else 0.0
        return profit, margin_pct

    def _core_liability(self, invc: Invoice) -> float:
        """Open customer-owed core deposits on this invoice (same join the
        After-Sale card / finalise stamp use)."""
        cores = (
            self.db.query(CoreCharge)
            .join(InvoiceLine, CoreCharge.invoice_line_id == InvoiceLine.id)
            .filter(
                InvoiceLine.invoice_id == invc.id,
                CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
                CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
            )
            .all()
        )
        return round(sum(c.customer_unit_charge * c.qty_outstanding for c in cores), 2)

    def _warranty_exposure(self, invc: Invoice) -> float:
        """Credit amount of still-open warranty claims tied to this invoice."""
        claims = (
            self.db.query(WarrantyClaim)
            .filter(
                WarrantyClaim.invoice_id == invc.id,
                WarrantyClaim.status.notin_(_WARRANTY_TERMINAL),
            )
            .all()
        )
        return round(sum(c.total_credit_amount or 0.0 for c in claims), 2)

    def _customer_lifetime(self, invc: Invoice) -> float:
        """The P2-Q1 lifetime number — shared with the customer panel."""
        if not invc.customer_id:
            return 0.0
        return CustomerMetricsService(self.db).metrics_for(invc.customer_id)["lifetime_sales"]
