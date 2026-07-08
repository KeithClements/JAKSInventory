"""Lightweight Qt signal bus for cross-screen core/credit events.

Phase B.4 — UAT 1.8 #7 fix.

Used so that issuing a core credit on the Cores screen, or receiving a
core, immediately refreshes any open Customer Detail dialog (or any
other listener) without requiring the user to close & re-open the
dialog.

Usage:

    from .core_events import core_events
    core_events.core_credit_issued.emit(customer_id, credit_id)
    # or
    core_events.cores_changed.emit()

Listeners hold a strong ref via the singleton and connect normally:

    core_events.core_credit_issued.connect(self._on_credit_issued)
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class _CoreEventBus(QObject):
    """Process-wide signals for core / customer-credit changes."""

    # Emitted whenever a core's state changes (created, received, voided,
    # deleted). Carries no payload — listeners should re-query.
    cores_changed = Signal()

    # Emitted right after a customer-credit ledger row is created or
    # updated. Args: (customer_id, credit_id). Either may be 0 when
    # unknown — listeners should treat 0 as "unknown / refresh anyway".
    core_credit_issued = Signal(int, int)


# Module-level singleton. Importers all share the same QObject so
# connect() across screens works as expected.
core_events = _CoreEventBus()
