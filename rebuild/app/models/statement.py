"""
app/models/statement.py
========================
R8 — Customer account statements.

A statement is an immutable snapshot of a customer's account activity over
a date range. Stored as a record so disputes can be resolved against what
was actually sent.

Number format: ST-YYYY-NNNN

Implementation lands in Phase I. Phase A only adds the schema.
"""
from __future__ import annotations

from datetime import datetime, date
from sqlalchemy import String, Text, Float, Integer, Date, ForeignKey, DateTime, Index, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class CustomerStatement(Base):
    __tablename__ = "customer_statements"
    __table_args__ = (
        Index("ix_statements_customer_id_generated_at", "customer_id", "generated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)

    generated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    generated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    # ── Date Range ────────────────────────────────────────────────────────────
    date_range_start: Mapped[date] = mapped_column(Date, nullable=False)
    date_range_end: Mapped[date] = mapped_column(Date, nullable=False)

    # ── Balances (snapshotted at generation) ──────────────────────────────────
    opening_balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    closing_balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_invoiced: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_paid: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_credits_applied: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_interest_accrued: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Aging Buckets ─────────────────────────────────────────────────────────
    # Keys mirror ar_aging_utils.AGING_BUCKETS:
    #   current → current_due, 1_30 → due_30, 31_60 → due_60,
    #   61_90 → due_90, over_90 → over_90.
    current_due: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    due_30: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    due_60: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    due_90: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # DEAD legacy column — the Phase-A schema guessed a due_120 terminal bucket,
    # but the runtime aging (ar_aging_utils) ends at over_90. SQLite can't drop
    # columns via the inline-migration system, so due_120 stays in place but is
    # never read or written by new code. Use over_90 below instead.
    due_120: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # R3 — terminal bucket aligned with ar_aging_utils ("over_90").
    over_90: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # ── Storage ───────────────────────────────────────────────────────────────
    pdf_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Full JSON snapshot of all line-by-line data — immutable record of what was
    # sent (R3). Dates stored as ISO strings; see StatementService.persist_statement.
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
