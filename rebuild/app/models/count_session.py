"""
app/models/count_session.py
===========================
§24 — Physical inventory Count Sessions.

ONE entity (`CountSession`) with a `count_type` discriminator covers all four
count styles — AUDIT, CYCLE, FULL (wall-to-wall), OPENING (initial on-hand).
Each session owns many `CountLine` rows (one per in-scope product).

Correctness heart (§24.4 — "keep operating" net-change reconciliation):
    At open, each line captures ``frozen_qty`` = product.qty_on_hand at the
    snapshot instant (kept for the printed sheet + audit). When a count is
    ENTERED, the line captures ``book_at_count`` = the LIVE on-hand at that
    moment. At post, the adjustment is ``delta = counted_qty − book_at_count``
    applied to the live ledger via InventoryService.apply_stock_delta.
    Reconciling against book-at-count (NOT the snapshot) is what makes "keep
    operating" correct in every ordering: a sale booked BEFORE the shelf was
    counted is already reflected in both book_at_count and the physical count,
    so it nets to zero variance instead of being subtracted twice; a sale booked
    AFTER the count is preserved because the delta is relative, not absolute.

Variance is DERIVED (a @property), never a stored column (§24.5).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import CountLineStatus, CountStatus, CountType
from app.database import Base


class CountSession(Base):
    """A physical-count document. See module docstring + MASTER_PLAN §24."""

    __tablename__ = "count_sessions"
    __table_args__ = (
        Index("ix_count_sessions_status", "status"),
        Index("ix_count_sessions_type", "count_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    count_type: Mapped[str] = mapped_column(String(20), nullable=False)  # CountType
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CountStatus.DRAFT
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # Single-location shop today; nullable FK keeps the multi-location door open.
    location_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_locations.id"), nullable=True
    )

    # Resolved scope filter (JSON): mode=all|filtered, category_ids, vendor_ids,
    # bin_prefix, sku_list, include_inactive, only_with_stock. Frozen once OPEN.
    scope_json: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # §24.2 — blind by default: hide frozen_qty from the counter during entry.
    blind: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # §24.2 — recount thresholds. NULL = that dimension does not trigger a recount.
    recount_threshold_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recount_threshold_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # FULL only: at post, were uncounted-in-scope lines zeroed (True) or skipped?
    zero_uncounted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Lifecycle stamps
    snapshot_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    opened_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    posted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    # Cached rollups (recomputed on record/review/post; cheap dashboard reads).
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    counted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    variance_qty_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    variance_value_total: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list[CountLine]] = relationship(
        "CountLine",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    # ── Derived ───────────────────────────────────────────────────────────────
    @property
    def is_editable(self) -> bool:
        """Scope can be changed only before the snapshot is frozen."""
        return self.status == CountStatus.DRAFT

    @property
    def is_terminal(self) -> bool:
        return self.status in (CountStatus.POSTED, CountStatus.CANCELLED)

    @property
    def progress_pct(self) -> int:
        if not self.line_count:
            return 0
        return round(100 * self.counted_count / self.line_count)

    @property
    def uncounted_count(self) -> int:
        return max(0, (self.line_count or 0) - (self.counted_count or 0))


class CountLine(Base):
    """One product in a count. Snapshot fields are frozen at open (§24.5)."""

    __tablename__ = "count_lines"
    __table_args__ = (
        Index("ix_count_lines_session_id", "session_id"),
        Index("ix_count_lines_product_id", "product_id"),
        # A product appears at most once per session (found-stock adds are guarded).
        UniqueConstraint("session_id", "product_id", name="uq_count_line_session_product"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("count_sessions.id"), nullable=False
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)

    # ── Snapshot at open (never mutated afterwards) ───────────────────────────
    sku: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    bin_location: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    frozen_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Count entry ───────────────────────────────────────────────────────────
    counted_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Live on-hand captured at the instant this line was counted. THIS — not the
    # snapshot frozen_qty — is the baseline the posted adjustment reconciles
    # against (§24.4), so documented mid-count movement is never double-counted:
    # a sale that happened BEFORE the shelf was counted is already reflected in
    # both book_at_count and the physical count, so it nets to no variance.
    book_at_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    counted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    counted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recount_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_recount: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    line_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CountLineStatus.UNCOUNTED
    )
    # Item found on the shelf that was not in the resolved scope (found stock).
    is_found: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Set on post — the ledger row this line produced (NULL if zero-variance/skipped).
    posted_txn_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_transactions.id"), nullable=True
    )

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    session: Mapped[CountSession] = relationship("CountSession", back_populates="lines")

    # ── Derived ───────────────────────────────────────────────────────────────
    @property
    def is_counted(self) -> bool:
        return self.counted_qty is not None

    @property
    def book_baseline(self) -> int:
        """The on-hand this line reconciles against: the live book captured when
        the count was entered (book_at_count); falls back to the snapshot
        (frozen_qty) for a line with no recorded count."""
        return self.book_at_count if self.book_at_count is not None else self.frozen_qty

    @property
    def variance(self) -> int | None:
        """counted − book-at-count. None until counted. This IS the posted delta
        (§24.4) — reconciling against book-at-count (not the snapshot) so a
        documented mid-count sale/receipt is not double-counted."""
        if self.counted_qty is None:
            return None
        return self.counted_qty - self.book_baseline

    @property
    def variance_value(self) -> float:
        v = self.variance
        return 0.0 if v is None else round(v * (self.unit_cost or 0.0), 2)
