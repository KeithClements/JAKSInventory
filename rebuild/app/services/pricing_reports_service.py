"""
app/services/pricing_reports_service.py
=======================================
Customer-Specific Product Pricing — Phase 3 visibility reports
(CUSTOMER_PRICING_DESIGN.md §7).

Two read-only reports. Neither writes to the database.

  customer_deals(db, customer_id=None, expiring_within_days=30, today=...)
    Every ACTIVE CustomerPriceRule as a deal row: customer, scope label,
    method/value, qty break, effective window, margin-at-current-cost (PRODUCT
    scope only), below-target flag, expiring-soon flag.

  margin_leakage(db, since=None, limit=500)
    Finalized invoice product-lines selling BELOW their cost-bracket target
    margin — worst gap first. ``rule_applies`` re-resolves the customer's
    current rules against the line's product (approximate / current-state).

Both import PricingService / CustomerPriceRuleService / target_margin_for_cost
read-only. Resolution math is NEVER re-implemented here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.constants import (
    InvoiceStatus,
    LineType,
    PriceMethod,
    ScopeType,
    target_margin_for_cost,
)
from app.models.customer import Customer
from app.models.invoice import Invoice, InvoiceLine
from app.models.pricing import CustomerPriceRule
from app.models.product import Product
from app.services.pricing_service import (
    CustomerPriceRuleService,
    PricingService,
    scope_label,
)


# ── Customer Deals ──────────────────────────────────────────────────────────────

def customer_deals(
    db: Session,
    *,
    customer_id: int | None = None,
    expiring_within_days: int = 30,
    today: date | None = None,
) -> list[dict]:
    """Every ACTIVE ``CustomerPriceRule`` as a deal row, sorted customer→scope.

    A ``today`` may be passed so ``expiring_soon`` is testable without wall-clock
    time; it defaults to ``date.today()``. ``customer_id`` filters to one account.
    ``expiring_within_days`` is the window for the ``expiring_soon`` flag: a rule
    with an ``effective_to`` that is today..today+N (inclusive, and not already
    past) is flagged.

    Row shape::

        {
          rule_id, customer_id, customer_name,
          scope_type, scope_ref, scope_label,
          price_method, price_value, qty_min,
          effective_from, effective_to,
          margin_at_current_cost,   # float | None (PRODUCT scope w/ cost only)
          price_at_current_cost,    # float | None
          cost_relative,            # True for CATEGORY/BRAND/CUSTOMER scopes
          below_target,             # bool | None  (None = not computable)
          below_cost,               # bool | None
          expiring_soon,            # bool
          days_until_expiry,        # int | None
        }
    """
    today = today or date.today()
    horizon = today + timedelta(days=max(0, int(expiring_within_days)))

    q = (
        db.query(CustomerPriceRule)
        .filter(CustomerPriceRule.is_active == True)  # noqa: E712
    )
    if customer_id is not None:
        q = q.filter(CustomerPriceRule.customer_id == customer_id)
    rules = q.all()

    rule_svc = CustomerPriceRuleService(db)

    # Resolve customer names in one pass (avoid N+1 on the company_name lookup).
    cust_ids = {r.customer_id for r in rules}
    name_by_id: dict[int, str] = {}
    if cust_ids:
        for cust in db.query(Customer).filter(Customer.id.in_(cust_ids)).all():
            name_by_id[cust.id] = cust.company_name or f"Customer #{cust.id}"

    rows: list[dict] = []
    for r in rules:
        preview = rule_svc.rule_preview(r)  # margin-at-current-cost (PRODUCT only)

        expiring_soon = False
        days_until_expiry: int | None = None
        if r.effective_to is not None:
            days_until_expiry = (r.effective_to - today).days
            # Flagged when it expires within the window and is not already past.
            expiring_soon = today <= r.effective_to <= horizon

        rows.append(
            {
                "rule_id": r.id,
                "customer_id": r.customer_id,
                "customer_name": name_by_id.get(
                    r.customer_id, f"Customer #{r.customer_id}"
                ),
                "scope_type": r.scope_type,
                "scope_ref": r.scope_ref,
                "scope_label": preview["scope_label"],
                "price_method": r.price_method,
                "price_value": r.price_value,
                "qty_min": r.qty_min,
                "effective_from": r.effective_from,
                "effective_to": r.effective_to,
                "margin_at_current_cost": preview["margin_at_current_cost"],
                "price_at_current_cost": preview["price_at_current_cost"],
                "cost_relative": preview["cost_relative"],
                "below_target": preview["below_target"],
                "below_cost": preview["below_cost"],
                "expiring_soon": expiring_soon,
                "days_until_expiry": days_until_expiry,
            }
        )

    # Sort by customer name, then scope specificity (PRODUCT first), then label.
    _scope_order = {
        ScopeType.PRODUCT: 0,
        ScopeType.BRAND: 1,
        ScopeType.CATEGORY: 2,
        ScopeType.CUSTOMER: 3,
    }
    rows.sort(
        key=lambda d: (
            (d["customer_name"] or "").lower(),
            _scope_order.get(str(d["scope_type"]).upper(), 9),
            (d["scope_label"] or "").lower(),
        )
    )
    return rows


# ── Margin Leakage ──────────────────────────────────────────────────────────────

# Finalized = posted and not voided (mirrors PricingService.last_price_for).
_FINALIZED_STATUSES = (InvoiceStatus.OPEN, InvoiceStatus.PARTIAL, InvoiceStatus.PAID)


def margin_leakage(
    db: Session,
    *,
    since: date | None = None,
    limit: int = 500,
) -> list[dict]:
    """Finalized invoice product-lines that sold BELOW their target margin.

    Scans non-draft / non-void invoice lines of type PRODUCT with a positive
    line cost and a positive unit price. For each, ``margin_pct`` is
    ``(unit_price - cost) / unit_price * 100`` and a line is KEPT when that is
    strictly below ``target_margin_for_cost(cost)`` (the same cost-bracket guard
    the pricing engine warns on). Sorted worst gap first (largest
    ``target_pct - margin_pct``).

    ``rule_applies`` is whether a customer price rule CURRENTLY matches that
    line's customer + product — re-resolved live via
    ``PricingService.resolve_customer_price``. It is APPROXIMATE / current-state:
    rules (and costs) may have changed since the sale, so it answers "is this
    customer+product under a deal *today*", not "was a deal applied then".

    ``since`` filters to invoices created on/after that date (00:00). ``limit``
    caps the returned rows after sorting (default 500).

    Row shape::

        {
          invoice_id, invoice_number, invoice_date, customer_id, customer_name,
          product_id, sku, label, qty, unit_price, cost,
          margin_pct, target_pct, gap,        # gap = target_pct - margin_pct (>0)
          rule_applies,                        # bool — current-state
        }
    """
    pricing = PricingService(db)

    q = (
        db.query(InvoiceLine, Invoice)
        .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
        .filter(
            Invoice.status.in_(_FINALIZED_STATUSES),
            InvoiceLine.line_type == LineType.PRODUCT,
            InvoiceLine.unit_cost > 0,
            InvoiceLine.unit_price > 0,
        )
    )
    if since is not None:
        q = q.filter(Invoice.created_at >= datetime.combine(since, datetime.min.time()))

    # Order newest-first so the limit (applied after the leakage sort) tends to
    # keep recent leakage when the candidate set is huge.
    candidates = q.order_by(Invoice.id.desc(), InvoiceLine.id.desc()).all()

    # Cache per-(customer,product) rule re-resolution — the same pair recurs
    # across many lines and resolve_customer_price walks the rule table each call.
    customer_cache: dict[int, Customer | None] = {}
    product_cache: dict[int, Product | None] = {}
    rule_applies_cache: dict[tuple[int, int], bool] = {}

    def _customer(cid: int) -> Customer | None:
        if cid not in customer_cache:
            customer_cache[cid] = (
                db.query(Customer).filter(Customer.id == cid).first()
            )
        return customer_cache[cid]

    def _product(pid: int) -> Product | None:
        if pid not in product_cache:
            product_cache[pid] = (
                db.query(Product).filter(Product.id == pid).first()
            )
        return product_cache[pid]

    def _rule_applies(cid: int, pid: int) -> bool:
        key = (cid, pid)
        if key not in rule_applies_cache:
            cust = _customer(cid)
            prod = _product(pid)
            if cust is None or prod is None:
                rule_applies_cache[key] = False
            else:
                res = pricing.resolve_customer_price(prod, cust)
                # A rule "applies" when one matched at all — even if it couldn't
                # price (no_cost) — since the deal exists for this pair today.
                rule_applies_cache[key] = res.source_rule is not None
        return rule_applies_cache[key]

    rows: list[dict] = []
    for line, inv in candidates:
        unit_price = float(line.unit_price or 0.0)
        cost = float(line.unit_cost or 0.0)
        if unit_price <= 0 or cost <= 0:
            continue
        margin_pct = (unit_price - cost) / unit_price * 100.0
        target_pct = target_margin_for_cost(cost)
        if margin_pct >= target_pct:
            continue  # at/above target — not leaking
        gap = target_pct - margin_pct

        product = _product(line.product_id) if line.product_id else None
        sku = (product.sku if product else None) or "—"
        label = line.description or (product.title if product else "") or sku

        rule_applies = (
            _rule_applies(inv.customer_id, line.product_id)
            if line.product_id is not None
            else False
        )

        rows.append(
            {
                "invoice_id": inv.id,
                "invoice_number": inv.invoice_number,
                "invoice_date": inv.created_at,
                "customer_id": inv.customer_id,
                "customer_name": (
                    inv.customer.company_name
                    if inv.customer is not None
                    else f"Customer #{inv.customer_id}"
                ),
                "product_id": line.product_id,
                "sku": sku,
                "label": label,
                "qty": line.qty,
                "unit_price": round(unit_price, 2),
                "cost": round(cost, 2),
                "margin_pct": round(margin_pct, 1),
                "target_pct": round(target_pct, 1),
                "gap": round(gap, 1),
                "rule_applies": rule_applies,
            }
        )

    rows.sort(key=lambda d: d["gap"], reverse=True)
    if limit is not None and limit >= 0:
        rows = rows[:limit]
    return rows
