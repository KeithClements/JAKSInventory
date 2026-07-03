"""Central, data-driven status-transition rules.

All "can X go from status A to status B?" logic lives here so the UI, the
DB layer, and tests agree on a single source of truth. Keep this module
UI-free and I/O-free.

Usage::

    from engine.status_transitions import (
        QUOTE_TRANSITIONS, validate_transition, TransitionError,
    )
    validate_transition(QUOTE_TRANSITIONS, old="lost", new="sent")

The tables below intentionally allow the same-state "no-op" transition
(e.g. ``draft → draft``) so callers can rerun idempotent updates without
special-casing them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


class TransitionError(ValueError):
    """Raised when a status transition is not allowed."""


@dataclass(frozen=True)
class TransitionTable:
    """Allowed transitions for one entity.

    ``allowed`` maps *from-status* → set of valid *to-statuses*.
    ``terminal`` is the set of statuses that can only transition via an
    explicit "reopen" rule (listed in ``reopen_from``).
    """
    name: str
    allowed: Mapping[str, frozenset[str]]
    terminal: frozenset[str]
    reopen_from: Mapping[str, frozenset[str]]  # status → allowed targets when reopening

    def is_terminal(self, status: str) -> bool:
        return self._n(status) in self.terminal

    def valid_next(self, status: str) -> frozenset[str]:
        s = self._n(status)
        nxt = set(self.allowed.get(s, frozenset()))
        nxt.update(self.reopen_from.get(s, frozenset()))
        # Self-transition is always a no-op.
        nxt.add(s)
        return frozenset(nxt)

    @staticmethod
    def _n(status: str | None) -> str:
        return (status or "").strip().lower()


# ---------------------------------------------------------------------------
# Quotes
#
#   draft ─► sent ─► accepted ─► invoiced
#     │        │        │
#     │        ├──► needs_follow_up ─► (sent | accepted | lost | void | expired)
#     │        ├──► expired ─► (sent | lost | void)
#     │        └──► lost ─► (sent via "reopen")
#     └──► void  (terminal, no reopen)
#
# A status can always self-transition (idempotent saves).
# "lost" is reopenable to 'sent' via the explicit reopen flow.
# "void" is hard-terminal.
# ---------------------------------------------------------------------------
QUOTE_TRANSITIONS = TransitionTable(
    name="quote",
    allowed={
        "": frozenset({"draft", "sent"}),  # brand-new, not yet persisted
        "draft": frozenset({"sent", "needs_follow_up", "accepted", "lost", "void", "expired"}),
        "sent": frozenset({"needs_follow_up", "accepted", "lost", "void", "expired", "draft"}),
        "needs_follow_up": frozenset({"sent", "accepted", "lost", "void", "expired"}),
        "expired": frozenset({"sent", "lost", "void"}),
        "accepted": frozenset({"invoiced", "lost", "void"}),
        "invoiced": frozenset(),       # terminal (invoice has its own lifecycle)
        "lost": frozenset(),           # terminal except via reopen_from
        "void": frozenset(),           # terminal, no reopen
    },
    terminal=frozenset({"invoiced", "lost", "void"}),
    reopen_from={
        "lost": frozenset({"sent"}),
    },
)


# ---------------------------------------------------------------------------
# Sales Orders
#
#   draft → confirmed/approved → partial → picking → ready → shipped → invoiced
#                                     │
#                                     └──► cancelled (terminal, reopenable)
#
# The app uses several historical statuses ('approved', 'partial', 'picking',
# 'ready', 'shipped'); they're all permitted here so the validator can be
# retrofitted into existing code without breaking legacy records.
# ---------------------------------------------------------------------------
SO_TRANSITIONS = TransitionTable(
    name="sales_order",
    allowed={
        "": frozenset({"draft"}),
        "draft": frozenset({"confirmed", "approved", "cancelled"}),
        "confirmed": frozenset({
            "approved", "partial", "picking", "ready", "shipped",
            "invoiced", "cancelled", "draft",
        }),
        "approved": frozenset({
            "partial", "picking", "ready", "shipped", "invoiced", "cancelled",
        }),
        "partial": frozenset({
            "picking", "ready", "shipped", "invoiced", "cancelled",
        }),
        "picking": frozenset({"ready", "partial", "shipped", "cancelled"}),
        "ready": frozenset({"shipped", "invoiced", "picking", "cancelled"}),
        "shipped": frozenset({"invoiced", "cancelled"}),
        "invoiced": frozenset(),        # terminal
        "cancelled": frozenset(),       # terminal except via reopen
    },
    terminal=frozenset({"invoiced", "cancelled"}),
    reopen_from={
        "cancelled": frozenset({"draft"}),
    },
)


# ---------------------------------------------------------------------------
# Invoices
#
#   draft → sent → partial → paid
#     │      │        │
#     │      └──► overdue ─► (partial | paid | void)
#     └──► void (terminal, no reopen)
#
# A refund/credit memo is a separate document — it does NOT flip invoice
# state back. Paid is terminal.
# ---------------------------------------------------------------------------
INVOICE_TRANSITIONS = TransitionTable(
    name="invoice",
    allowed={
        "": frozenset({"draft"}),
        "draft": frozenset({"sent", "void"}),
        "sent": frozenset({"partial", "paid", "overdue", "void"}),
        "partial": frozenset({"paid", "overdue", "void"}),
        "overdue": frozenset({"partial", "paid", "void"}),
        "paid": frozenset(),   # terminal
        "void": frozenset(),   # terminal, no reopen
    },
    terminal=frozenset({"paid", "void"}),
    reopen_from={},
)


# ---------------------------------------------------------------------------
# Purchase Orders
#
# Statuses actually in use: draft, sent, partial, received, cancelled.
# 'invoice_pending' is modeled for the future 3-way-match flow but is
# optional — current code goes straight sent → partial → received.
# ---------------------------------------------------------------------------
PO_TRANSITIONS = TransitionTable(
    name="purchase_order",
    allowed={
        "": frozenset({"draft"}),
        "draft": frozenset({"sent", "cancelled"}),
        "sent": frozenset({"partial", "received", "cancelled"}),
        "partial": frozenset({"received", "cancelled"}),
        "received": frozenset({"invoice_pending", "closed"}),
        "invoice_pending": frozenset({"closed"}),
        "closed": frozenset(),      # terminal
        "cancelled": frozenset(),   # terminal
    },
    terminal=frozenset({"closed", "cancelled"}),
    reopen_from={},
)


_ALL_TABLES: dict[str, TransitionTable] = {
    t.name: t for t in (
        QUOTE_TRANSITIONS, SO_TRANSITIONS, INVOICE_TRANSITIONS, PO_TRANSITIONS,
    )
}


def get_transitions(entity: str) -> TransitionTable:
    """Return the TransitionTable for an entity name (quote/sales_order/invoice/purchase_order)."""
    key = (entity or "").strip().lower()
    if key not in _ALL_TABLES:
        raise KeyError(f"Unknown entity for status transitions: {entity!r}")
    return _ALL_TABLES[key]


def can_transition(table: TransitionTable, old: str | None, new: str) -> bool:
    """Return True if ``old → new`` is allowed on ``table``."""
    old_n = TransitionTable._n(old)
    new_n = TransitionTable._n(new)
    if not new_n:
        return False
    return new_n in table.valid_next(old_n)


def validate_transition(table: TransitionTable, *, old: str | None, new: str) -> None:
    """Raise ``TransitionError`` if the transition is not allowed.

    Use this when enforcing rules at write-time. The error message lists
    the valid next-states for the current state so UIs can surface them.
    """
    if can_transition(table, old, new):
        return
    valid = ", ".join(sorted(table.valid_next(old or "")))
    raise TransitionError(
        f"{table.name}: transition {old!r} → {new!r} not allowed. "
        f"Valid next states from {old!r}: {valid or '(none)'}."
    )


def allowed_next_statuses(entity: str, current: str | None) -> list[str]:
    """UI helper: return a sorted list of valid next statuses."""
    table = get_transitions(entity)
    return sorted(s for s in table.valid_next(current or "") if s)
