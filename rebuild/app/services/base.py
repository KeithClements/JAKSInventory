"""
app/services/base.py
====================
Base service class + AuditService.

Every service inherits from BaseService to get:
  - self.db  (SQLAlchemy Session)
  - self.audit(...)  (writes + flushes an AuditLog row, caller commits)

AuditService adds audit_and_commit() for standalone use (system events,
background jobs) where there is no outer transaction to commit.

COMMIT SEMANTICS — two distinct patterns:
  1. BaseService.audit()         → adds + flushes; CALLER must commit.
     Use inside service methods that do other DB work in the same transaction.
  2. AuditService.audit_and_commit() → adds + commits immediately.
     Use for standalone audit writes where no other work is in flight.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditLog

log = logging.getLogger(__name__)


class BaseService:
    """All services inherit from this. Provides db session + audit helper."""

    def __init__(self, db: Session, current_user_id: int | None = None) -> None:
        self.db = db
        self.current_user_id = current_user_id

    def audit(
        self,
        entity_type: str,
        entity_id: int,
        action: str,  # AuditAction value
        old_value: Any = None,
        new_value: Any = None,
        notes: str | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        """
        Write a single AuditLog row, flush it into the current transaction,
        and return it. The CALLER is responsible for committing.

        Flushing (not committing) ensures the row is included when the outer
        transaction commits and is not silently lost if an exception occurs
        before that commit.
        """
        row = AuditLog(
            user_id=self.current_user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            old_value=_serialize(old_value),
            new_value=_serialize(new_value),
            notes=notes,
            ip_address=ip_address,
            changed_at=datetime.utcnow(),
        )
        self.db.add(row)
        self.db.flush()  # part of the outer transaction — not committed yet
        return row


class AuditService(BaseService):
    """
    Standalone audit writer for system events and background jobs where
    there is no enclosing service transaction.

    Example:
        AuditService(db, current_user_id=None).audit_and_commit(
            entity_type="invoice",
            entity_id=42,
            action=AuditAction.LOCKED,
            notes="Auto-locked at EOD",
        )
    """

    def audit_and_commit(
        self,
        entity_type: str,
        entity_id: int,
        action: str,
        old_value: Any = None,
        new_value: Any = None,
        notes: str | None = None,
    ) -> None:
        """Write an audit row and commit immediately (standalone use only)."""
        self.audit(entity_type, entity_id, action, old_value, new_value, notes)
        self.db.commit()


# ── Private helpers ────────────────────────────────────────────────────────────

def _serialize(v: Any) -> str | None:
    """
    Safely convert a value to a JSON string for storage in audit_log columns.
    Falls back to str() only for known non-serializable types (Decimal, datetime, etc.).
    Raises on unexpected failures so the caller hears about data issues.
    """
    if v is None:
        return None
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, default=str)
    except (TypeError, ValueError) as exc:
        log.warning("audit _serialize fell back to str(): %s — %s", type(v).__name__, exc)
        return str(v)
