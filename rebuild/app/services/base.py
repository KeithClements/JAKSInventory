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

from app.constants import Permission, UserRole
from app.models.audit import AuditLog
from app.models.user import User

log = logging.getLogger(__name__)


class PermissionDeniedError(PermissionError):
    """Raised when a user lacks the required permission for an action."""

    def __init__(self, permission: str, user_id: int | None, role: str | None = None):
        self.permission = permission
        self.user_id = user_id
        self.role = role
        msg = f"Permission denied: '{permission}' requires elevated access"
        if role:
            msg += f" (current role: {role})"
        super().__init__(msg)


class ConcurrentEditError(RuntimeError):
    """Raised when an optimistic-lock version check fails."""

    def __init__(self, entity_type: str, entity_id: int):
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(
            f"This {entity_type} (#{entity_id}) was changed by another user. "
            "Refresh and try again."
        )


# R11 — role → granted permissions matrix (Phase A: ADMIN+BOOKKEEPING are Keith/wife).
# Looked up by assert_can(); never compared directly elsewhere.
_ROLE_PERMISSIONS: dict[str, set[str]] = {
    UserRole.ADMIN: {p.value for p in Permission},  # admin gets everything
    UserRole.BOOKKEEPING: {
        Permission.REVERSE_PAYMENT,
        Permission.ISSUE_CREDIT_MEMO,
        Permission.APPROVE_VENDOR_BILL,
        Permission.REPUSH_QBO,
        Permission.VIEW_AUDIT_LOG,
        Permission.SEND_EMAIL,
    },
    UserRole.SALES: {
        Permission.SEND_EMAIL,
    },
    UserRole.READ_ONLY: set(),
}


class BaseService:
    """All services inherit from this. Provides db session + audit helper."""

    def __init__(self, db: Session, current_user_id: int | None = None) -> None:
        self.db = db
        self.current_user_id = current_user_id

    # ── Permission gating (R11) ───────────────────────────────────────────────

    def assert_can(self, permission: str, *, raise_on_deny: bool = True) -> bool:
        """
        Check whether self.current_user_id has the given Permission.
        Returns True if allowed; raises PermissionDeniedError if not (unless
        raise_on_deny=False, in which case returns False).

        Use this at the top of any service method that gates a sensitive action:
            self.assert_can(Permission.INVENTORY_ADJUST)

        Phase A: if current_user_id is None, treat as "system event" and allow.
        Phase 2 (auth): tighten this — None user means anonymous request.
        """
        if self.current_user_id is None:
            return True  # system / background job

        user = self.db.query(User).filter(User.id == self.current_user_id).first()
        if user is None:
            if raise_on_deny:
                raise PermissionDeniedError(permission, self.current_user_id, None)
            return False

        allowed = _ROLE_PERMISSIONS.get(user.role, set())
        if permission in allowed:
            return True
        if raise_on_deny:
            raise PermissionDeniedError(permission, self.current_user_id, user.role)
        return False

    # ── Optimistic locking (R9) ───────────────────────────────────────────────

    def check_version(self, record, submitted_updated_at) -> None:
        """
        Compare record.updated_at to the timestamp the client last saw.
        If they differ, the record was modified by someone else — raise
        ConcurrentEditError so the caller can prompt the user to refresh.

        Caller passes the timestamp from a hidden form field or HTMX header.
        submitted_updated_at may be a datetime or its ISO-string form.
        """
        if submitted_updated_at is None:
            return  # no version to check (legacy save path)

        current = getattr(record, "updated_at", None)
        if current is None:
            return

        # Normalize string ISO timestamps for comparison
        from datetime import datetime as _dt
        if isinstance(submitted_updated_at, str):
            try:
                submitted_updated_at = _dt.fromisoformat(submitted_updated_at)
            except ValueError:
                # Unparseable — skip the check rather than fail loudly
                log.warning("check_version: could not parse submitted_updated_at=%r", submitted_updated_at)
                return

        # SQLite stores DATETIME without microsecond precision sometimes — compare to second
        if abs((current - submitted_updated_at).total_seconds()) > 1:
            entity_type = type(record).__name__
            entity_id = getattr(record, "id", 0)
            raise ConcurrentEditError(entity_type, entity_id)

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


# ── Line-item defaults (shared by every add_line) ───────────────────────────────

def apply_product_line_defaults(product, data: dict, *, include_price: bool) -> dict:
    """
    Backfill line-item fields from a product when the caller didn't supply them.

    This is the backend half of "immediate add-on-select": the UI may POST only
    ``product_id`` + ``qty`` and still expect a complete, sensible line.  Every
    document's ``add_line`` (Quote, SO, Invoice, PO) calls this so the behaviour
    is identical across all four.

    Only fills blanks/zeros, so an explicit value from the caller always wins:
      - ``description`` ← ``product.title`` (falls back to ``sku``)
      - ``unit_cost``   ← ``product.cost``          when missing or 0
      - ``unit_price``  ← ``product.selling_price`` (customer docs only;
                          pass ``include_price=False`` for cost-only docs like POs)

    Cost SOURCE nuances (preferred-vendor cost, the PO's own vendor source cost)
    stay in each service and run BEFORE this helper; this only backfills from the
    product's cached fields when the service left ``unit_cost`` at 0.

    Mutates and returns ``data``.
    """
    if product is None:
        return data

    if not str(data.get("description", "") or "").strip():
        data["description"] = product.title or product.sku or ""

    try:
        cost = float(data.get("unit_cost", 0) or 0)
    except (TypeError, ValueError):
        cost = 0.0
    if cost == 0.0:
        data["unit_cost"] = product.cost or 0.0

    if include_price:
        try:
            price = float(data.get("unit_price", 0) or 0)
        except (TypeError, ValueError):
            price = 0.0
        if price == 0.0:
            data["unit_price"] = product.selling_price

    return data
