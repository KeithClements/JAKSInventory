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

from sqlalchemy import Float, Boolean, Integer
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
