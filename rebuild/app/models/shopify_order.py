from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ShopifyProcessedOrder(Base):
    """Idempotency ledger for the Shopify order-sync feed.

    Each Shopify order that has decremented ERP stock is recorded here EXACTLY
    ONCE, keyed by its Shopify order id. The poller skips any order already present,
    so a re-poll of the same window (or an at-least-once scheduler tick) can never
    double-decrement inventory. Purely a sync bookkeeping table — the authoritative
    stock movement is the InventoryTransaction row (transaction_type=shopify_sale)
    written alongside it.
    """
    __tablename__ = "shopify_processed_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    shopify_order_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False
    )
    order_name: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    processed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    # Shopify order created_at (ISO) — drives the poll watermark.
    order_created_at: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    lines_matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lines_unmatched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pieces_decremented: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Set when a previously-decremented order was later cancelled/refunded on Shopify
    # and its stock was RESTORED (compensating +pieces). NULL = not reversed. Gates
    # the reversal so an order is only ever restocked once (idempotent).
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # JSON detail of what was decremented / what didn't match (audit trail); also the
    # per-line source of truth used to restock on a later cancellation.
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
