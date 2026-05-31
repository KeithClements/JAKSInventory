"""
app/settings_utils.py
======================
Low-level helpers for reading and incrementing the settings table.
No FastAPI dependency — safe to import from services, models, and routers alike.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.setting import Setting


def get_setting_value(key: str, fallback: str = "") -> str:
    """
    Read a single setting value by key, using its own short-lived DB session.
    For use in contexts where a session is not already available (e.g. model properties).
    Prefer get_setting_value_db() when a session is already in scope.
    """
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        row = db.query(Setting).filter(Setting.key == key).first()
        return row.value if row else fallback
    finally:
        db.close()


def get_setting_value_db(db: Session, key: str, fallback: str = "") -> str:
    """Read a single setting value using an existing session (no extra connection)."""
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else fallback


def set_setting_value_db(db: Session, key: str, value: str, label: str = "") -> None:
    """Upsert a single setting value using an existing session.

    Does NOT commit — the caller controls the transaction boundary (so several
    setting writes can be batched into one commit). Creates the row if absent.
    """
    row = db.query(Setting).filter(Setting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value, label=label))


def bump_counter(db: Session, key: str, prefix: str, year: int) -> str:
    """
    Atomically increment a document sequence number and return the formatted string.

    Handles yearly rollover: if current_sequence_year differs from `year`, all
    sequence counters reset to 1 and current_sequence_year is updated.

    Example output: "INV-2026-0001"
    """
    # ── Year rollover check ────────────────────────────────────────────────────
    year_row = (
        db.query(Setting)
        .filter(Setting.key == "current_sequence_year")
        .with_for_update()
        .first()
    )
    stored_year = int(year_row.value) if year_row and year_row.value.isdigit() else year

    if stored_year != year:
        sequence_keys = [
            "next_invoice_number", "next_quote_number", "next_so_number",
            "next_po_number", "next_ra_number", "next_wc_number",
            "next_ri_number", "next_core_slip_number", "next_vcr_number",
            # Phase F/G/I — credit memo, vendor credit, vendor return, statement
            "next_cm_number", "next_vcm_number", "next_vr_number", "next_statement_number",
        ]
        db.query(Setting).filter(Setting.key.in_(sequence_keys)).update(
            {Setting.value: "1"}, synchronize_session="fetch"
        )
        if year_row:
            year_row.value = str(year)
        else:
            db.add(Setting(key="current_sequence_year", value=str(year), label=""))

    # ── Fetch and increment this counter ──────────────────────────────────────
    row = db.query(Setting).filter(Setting.key == key).with_for_update().first()
    n = int(row.value) if row and row.value.isdigit() else 1
    formatted = f"{prefix}-{year}-{n:04d}"
    if row:
        row.value = str(n + 1)
    else:
        db.add(Setting(key=key, value=str(n + 1), label=""))
    db.commit()
    return formatted
