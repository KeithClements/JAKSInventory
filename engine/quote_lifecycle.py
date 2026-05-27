"""Quote lifecycle helpers (auto-expire, etc.).

Designed to be invoked from a nightly job (see ``engine.eta_refresh``).
"""
from __future__ import annotations

from datetime import date


def expire_overdue_quotes() -> dict:
    """Flip ``status='expired'`` on open quotes past ``expiration_date``.

    "Open" = status in {draft, sent, needs_follow_up}. Returns a count
    summary suitable for logging.
    """
    from db.database import get_db

    today = date.today().isoformat()
    expired = 0
    scanned = 0
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id FROM quotes
            WHERE COALESCE(status, 'draft') IN ('draft','sent','needs_follow_up')
              AND expiration_date IS NOT NULL
              AND expiration_date != ''
              AND expiration_date < %s
            """,
            (today,),
        ).fetchall()
        for row in rows:
            scanned += 1
            qid = row["id"] if hasattr(row, "keys") else row[0]
            conn.execute(
                "UPDATE quotes SET status = 'expired' WHERE id = %s",
                (qid,),
            )
            expired += 1
        conn.commit()
    return {"scanned": scanned, "expired": expired}
