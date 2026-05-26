"""
app/services/suggested_sell_service.py
========================================
Suggested sell configuration management.

Suggested sells define which related products appear as chips beneath a
product's quote line, enabling fast one-click add of common accessories,
install kits, and warranty upsells.

Two chip sources at quote time:
  1. Pre-configured entries from this service (per product, sorted by sort_order)
  2. Free-add via SKU search on the quote line (no service layer needed — just
     a normal add_quote_line call with the found product)

Relationship types:
  recommended — shown as inline chip; one-click adds with default qty
  required    — shown as chip; pre-checked in the "View Related" slide-over
  optional    — NOT shown as inline chip; appears only in the slide-over
  warranty    — shown as chip; click opens the warranty tier picker inline
"""
from __future__ import annotations

from app.models.product import Product, SuggestedSell
from app.services.base import BaseService


class SuggestedSellService(BaseService):

    # ── Query ─────────────────────────────────────────────────────────────────

    def get_suggestions(self, product_id: int) -> list[SuggestedSell]:
        """
        Return all configured suggestions for a product, ordered by sort_order.
        Includes the suggested_product relationship (loaded eagerly enough for
        rendering sku, title, and cost in the chip/slide-over UI).
        """
        return (
            self.db.query(SuggestedSell)
            .filter(SuggestedSell.product_id == product_id)
            .order_by(SuggestedSell.sort_order, SuggestedSell.id)
            .all()
        )

    def get_inline_chips(self, product_id: int) -> list[SuggestedSell]:
        """
        Return only the entries that show as inline chips (recommended, required,
        warranty). Optional entries are excluded — they only appear in the
        full slide-over panel.
        """
        return (
            self.db.query(SuggestedSell)
            .filter(
                SuggestedSell.product_id == product_id,
                SuggestedSell.relationship_type.in_(
                    ["recommended", "required", "warranty"]
                ),
            )
            .order_by(SuggestedSell.sort_order, SuggestedSell.id)
            .all()
        )

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def add_suggestion(
        self,
        product_id: int,
        suggested_product_id: int,
        relationship_type: str = "recommended",
        notes: str = "",
        sort_order: int | None = None,
    ) -> SuggestedSell:
        """
        Link a suggested product to a product.
        Raises ValueError if:
          - Either product doesn't exist
          - The link already exists (duplicate pair)
          - product_id == suggested_product_id (self-reference)
        """
        if product_id == suggested_product_id:
            raise ValueError("A product cannot suggest itself.")

        product = self.db.query(Product).filter(Product.id == product_id).first()
        if not product:
            raise ValueError(f"Product {product_id} not found.")

        suggested = self.db.query(Product).filter(
            Product.id == suggested_product_id
        ).first()
        if not suggested:
            raise ValueError(f"Product {suggested_product_id} not found.")

        existing = (
            self.db.query(SuggestedSell)
            .filter(
                SuggestedSell.product_id == product_id,
                SuggestedSell.suggested_product_id == suggested_product_id,
            )
            .first()
        )
        if existing:
            raise ValueError(
                f"{suggested.sku} is already a suggested sell for this product."
            )

        # Auto-assign sort_order after the current last entry
        if sort_order is None:
            last = (
                self.db.query(SuggestedSell)
                .filter(SuggestedSell.product_id == product_id)
                .order_by(SuggestedSell.sort_order.desc())
                .first()
            )
            sort_order = (last.sort_order + 10) if last else 10

        suggestion = SuggestedSell(
            product_id=product_id,
            suggested_product_id=suggested_product_id,
            relationship_type=relationship_type,
            notes=notes,
            sort_order=sort_order,
        )
        self.db.add(suggestion)
        self.db.commit()
        self.db.refresh(suggestion)
        return suggestion

    def update_suggestion(self, suggestion_id: int, data: dict) -> SuggestedSell:
        """Update relationship_type, notes, and/or sort_order."""
        suggestion = (
            self.db.query(SuggestedSell)
            .filter(SuggestedSell.id == suggestion_id)
            .first()
        )
        if not suggestion:
            raise ValueError(f"SuggestedSell {suggestion_id} not found.")

        for field in ("relationship_type", "notes", "sort_order"):
            if field in data:
                setattr(suggestion, field, data[field])

        self.db.commit()
        return suggestion

    def remove_suggestion(self, suggestion_id: int) -> None:
        """Delete a suggested sell link."""
        suggestion = (
            self.db.query(SuggestedSell)
            .filter(SuggestedSell.id == suggestion_id)
            .first()
        )
        if not suggestion:
            raise ValueError(f"SuggestedSell {suggestion_id} not found.")
        self.db.delete(suggestion)
        self.db.commit()
