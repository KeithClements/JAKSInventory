"""
app/models/pricing.py
=====================
MarkupTier — cost-bracket based markup grid.

Each row covers products whose COGS falls in [min_cost, max_cost).
max_cost NULL = open-ended (the highest tier). Tiers must not overlap;
the service validates this on lookup by using the first matching row.

The grid is only active when the markup_tiers_active Setting = "true".
When inactive, PricingService.resolve_markup_pct falls through to the
flat default_markup_pct setting — no re-pricing happens on import.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Float, Boolean, Integer, String, Text, Date, DateTime, ForeignKey, Index, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MarkupTier(Base):
    __tablename__ = "markup_tiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Cost bracket: [min_cost, max_cost). max_cost NULL = no upper bound.
    min_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    markup_pct: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def matches(self, cost: float) -> bool:
        """True when `cost` falls in this tier's bracket."""
        if cost < self.min_cost:
            return False
        if self.max_cost is not None and cost >= self.max_cost:
            return False
        return True

    def __repr__(self) -> str:
        hi = f"{self.max_cost}" if self.max_cost is not None else "∞"
        return f"<MarkupTier [{self.min_cost}, {hi}) → {self.markup_pct}%>"


class CustomerPriceRule(Base):
    """Customer-Specific Product Pricing (CUSTOMER_PRICING_DESIGN.md §3).

    A per-customer cost-plus pricing rule. One rule blankets a SKU, a category
    (and its descendants), a brand, or the whole customer. Resolved at line-add
    time by ``PricingService.resolve_customer_price`` as "Step 0" of the pricing
    waterfall — when a rule matches it sets the line price; otherwise the existing
    tier/markup/override waterfall runs unchanged (fully backward-compatible).

    Math is cost-based only (markup% OR margin% against the moving-avg
    ``product.cost``) so every price self-corrects on receipt — no fixed-dollar
    and no %-off-list. The margin guard (below_target / below_cost) only WARNS;
    it never blocks the sale.

    Precedence: PRODUCT > BRAND/CATEGORY > CUSTOMER. Within a tier the higher
    qualifying ``qty_min`` wins, then the newest rule. A brand-vs-category tie
    resolves to whichever rule covers the FEWEST SKUs (owner-confirmed default).

    Expired (date-window) and inactive rules are ignored in pricing but KEPT for
    history/audit — deactivation is a soft ``is_active=False`` toggle.
    """
    __tablename__ = "customer_price_rule"
    __table_args__ = (
        Index("ix_customer_price_rule_customer_id", "customer_id"),
        Index(
            "ix_customer_price_rule_customer_scope",
            "customer_id", "scope_type", "scope_ref",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Indexed via the explicit Index in __table_args__ below (don't add index=True
    # here — it would create a second same-named index and collide on create_all).
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id"), nullable=False
    )

    # Scope: PRODUCT | CATEGORY | BRAND | CUSTOMER. scope_ref is the product id,
    # category id, or brand string (stored as text); NULL for a whole-customer rule.
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Cost-plus method: 'markup' (cost × (1 + value/100)) or 'margin'
    # (cost / (1 - value/100)). price_value is the percent.
    price_method: Mapped[str] = mapped_column(String(20), nullable=False)
    price_value: Mapped[float] = mapped_column(Float, nullable=False)

    # Optional volume break: rule only qualifies when line qty >= qty_min.
    # NULL = applies at any qty.
    qty_min: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Optional date window (NULL = open-ended). Dated rules auto-expire.
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<CustomerPriceRule cust={self.customer_id} {self.scope_type}"
            f":{self.scope_ref} {self.price_method}={self.price_value}>"
        )
