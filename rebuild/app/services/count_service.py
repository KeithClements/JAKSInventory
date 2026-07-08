"""
app/services/count_service.py
=============================
§24 — Physical inventory Count Sessions (Audit · Cycle · Full · Opening).

Lifecycle (see MASTER_PLAN §24.3):
    DRAFT → OPEN (snapshot frozen) → REVIEW → POSTED   |  CANCELLED

The correctness heart is post_session (§24.4): the adjustment for each line is
``delta = counted_qty − book_at_count`` applied to the LIVE ledger through
InventoryService.apply_stock_delta — the single canonical writer of
Product.qty_on_hand. ``book_at_count`` is the live on-hand captured at the
instant the line was ENTERED (not the open-time snapshot), so a documented
mid-count sale/receipt is never double-counted regardless of whether it landed
before or after the shelf was counted. This service NEVER writes qty_on_hand
directly.

Permissions (§24.6):
    INVENTORY_COUNT          — create / scope / open / enter counts (floor staff)
    INVENTORY_COUNT_APPROVE  — review + POST (writes stock adjustments; manager)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.constants import (
    AdjustmentReason,
    AuditAction,
    CountLineStatus,
    CountStatus,
    CountType,
    EntityType,
    InventoryTxnType,
    Permission,
)
from app.models.count_session import CountLine, CountSession
from app.models.product import Product, ProductVendorSource
from app.services.base import BaseService
from app.settings_utils import bump_counter

log = logging.getLogger(__name__)

# count_type → default scope when the caller does not supply one.
_DEFAULT_SCOPE: dict[str, dict] = {
    CountType.FULL: {"mode": "all"},
    CountType.OPENING: {"mode": "all"},
    CountType.CYCLE: {"mode": "filtered"},
    CountType.AUDIT: {"mode": "filtered"},
}


class CountService(BaseService):

    # ── Loaders ───────────────────────────────────────────────────────────────

    def get_session(self, session_id: int) -> CountSession:
        session = (
            self.db.query(CountSession)
            .options(selectinload(CountSession.lines))
            .filter(CountSession.id == session_id)
            .first()
        )
        if session is None:
            raise ValueError(f"Count session {session_id} not found")
        return session

    # ── Create / scope (DRAFT) ────────────────────────────────────────────────

    def create_session(
        self,
        count_type: str,
        *,
        name: str = "",
        scope: dict | None = None,
        blind: bool = True,
        recount_threshold_qty: int | None = None,
        recount_threshold_value: float | None = None,
        location_id: int | None = None,
    ) -> CountSession:
        """Create a DRAFT count. Scope stays editable until open_session()."""
        self.assert_can(Permission.INVENTORY_COUNT)

        valid = {t.value for t in CountType}
        if count_type not in valid:
            raise ValueError(
                f"Invalid count_type {count_type!r}. Must be one of {sorted(valid)}"
            )

        scope = scope if scope is not None else dict(_DEFAULT_SCOPE.get(count_type, {}))
        year = datetime.utcnow().year
        session = CountSession(
            session_number=bump_counter(self.db, "next_count_number", "COUNT", year),
            count_type=count_type,
            status=CountStatus.DRAFT,
            name=name.strip(),
            location_id=location_id,
            scope_json=json.dumps(scope),
            blind=bool(blind),
            recount_threshold_qty=recount_threshold_qty,
            recount_threshold_value=recount_threshold_value,
            created_by_id=self.current_user_id,
        )
        self.db.add(session)
        self.db.flush()
        self.audit(
            entity_type=EntityType.COUNT_SESSION,
            entity_id=session.id,
            action=AuditAction.CREATED,
            new_value={"count_type": count_type, "number": session.session_number,
                       "scope": scope, "blind": blind},
        )
        self.db.commit()
        return session

    def update_scope(
        self,
        session_id: int,
        *,
        scope: dict | None = None,
        name: str | None = None,
        blind: bool | None = None,
        recount_threshold_qty: int | None = None,
        recount_threshold_value: float | None = None,
        _sentinel=object(),
    ) -> CountSession:
        """Edit a DRAFT session before its snapshot is frozen."""
        self.assert_can(Permission.INVENTORY_COUNT)
        session = self.get_session(session_id)
        if session.status != CountStatus.DRAFT:
            raise ValueError("Scope can only be edited while the count is a draft.")
        if scope is not None:
            session.scope_json = json.dumps(scope)
        if name is not None:
            session.name = name.strip()
        if blind is not None:
            session.blind = bool(blind)
        # thresholds: pass through (None is a legitimate "no threshold" value, so
        # these two always overwrite — the router only calls with real intent).
        session.recount_threshold_qty = recount_threshold_qty
        session.recount_threshold_value = recount_threshold_value
        self.db.commit()
        return session

    # ── Open (snapshot → OPEN) ────────────────────────────────────────────────

    def open_session(self, session_id: int) -> CountSession:
        """Freeze the snapshot: resolve scope → one CountLine per product, each
        capturing frozen_qty = qty_on_hand NOW. Scope is locked after this."""
        self.assert_can(Permission.INVENTORY_COUNT)
        session = self.get_session(session_id)
        if session.status != CountStatus.DRAFT:
            raise ValueError("Only a draft count can be opened.")

        scope = self._load_scope(session)
        products = self._resolve_scope_products(scope)
        if not products:
            raise ValueError(
                "Scope matched no products — widen the filters before opening."
            )

        now = datetime.utcnow()
        for p in products:
            # Append through the relationship (NOT db.add by FK) so the loaded
            # session.lines collection is populated — _recompute_rollups reads it.
            session.lines.append(
                CountLine(
                    product_id=p.id,
                    sku=p.sku or "",
                    description=(p.title or "")[:300],
                    bin_location=p.bin_location or "",
                    frozen_qty=p.qty_on_hand or 0,
                    unit_cost=p.cost or 0.0,
                    line_status=CountLineStatus.UNCOUNTED,
                )
            )
        session.status = CountStatus.OPEN
        session.snapshot_at = now
        session.opened_by_id = self.current_user_id
        self.db.flush()
        self._recompute_rollups(session)
        self.audit(
            entity_type=EntityType.COUNT_SESSION,
            entity_id=session.id,
            action=AuditAction.STATUS_CHANGED,
            old_value={"status": CountStatus.DRAFT},
            new_value={"status": CountStatus.OPEN, "lines": session.line_count},
            notes=f"Snapshot frozen: {session.line_count} lines",
        )
        self.db.commit()
        return session

    # ── Count entry ───────────────────────────────────────────────────────────

    def find_line(
        self, session_id: int, *, product_id: int | None = None, sku: str | None = None
    ) -> CountLine | None:
        q = self.db.query(CountLine).filter(CountLine.session_id == session_id)
        if product_id is not None:
            return q.filter(CountLine.product_id == product_id).first()
        if sku:
            sku = sku.strip()
            line = q.filter(CountLine.sku == sku).first()
            if line is not None:
                return line
            # Fallback: resolve the SKU to a product, then match by product_id
            # (covers a SKU that drifted after snapshot).
            prod = self.db.query(Product).filter(Product.sku == sku).first()
            if prod is not None:
                return q.filter(CountLine.product_id == prod.id).first()
        return None

    def record_count(
        self,
        session_id: int,
        counted_qty: int,
        *,
        product_id: int | None = None,
        sku: str | None = None,
        note: str = "",
    ) -> CountLine:
        """Enter (or re-enter) the physical count for one line. Re-entering a
        counted line increments recount_count. Flags needs_recount when the
        variance exceeds the session threshold."""
        self.assert_can(Permission.INVENTORY_COUNT)
        session = self.get_session(session_id)
        if session.status not in (CountStatus.OPEN, CountStatus.REVIEW):
            raise ValueError("Counts can only be entered on an open count.")

        try:
            counted = int(counted_qty)
        except (TypeError, ValueError):
            raise ValueError(f"Counted quantity must be a whole number: {counted_qty!r}")
        if counted < 0:
            raise ValueError("Counted quantity cannot be negative.")

        line = self.find_line(session_id, product_id=product_id, sku=sku)
        if line is None:
            raise ValueError(
                "That item is not in this count. Use 'add found item' to include it."
            )

        if line.counted_qty is not None:
            line.recount_count += 1
        line.counted_qty = counted
        # Reconcile against the LIVE book at THIS moment, not the snapshot (§24.4)
        # — so a documented sale/receipt booked before this shelf was counted is
        # not subtracted again at post.
        line.book_at_count = self._current_on_hand(line.product_id)
        line.counted_by_id = self.current_user_id
        line.counted_at = datetime.utcnow()
        if note:
            line.notes = note
        line.needs_recount = self._over_threshold(line, session)
        line.line_status = (
            CountLineStatus.RECOUNT if line.needs_recount else CountLineStatus.COUNTED
        )
        self.db.flush()
        self._recompute_rollups(session)
        self.db.commit()
        return line

    def add_found_line(
        self, session_id: int, sku: str, *, counted_qty: int | None = None
    ) -> CountLine:
        """Add a product found on the shelf that was not in the resolved scope."""
        self.assert_can(Permission.INVENTORY_COUNT)
        session = self.get_session(session_id)
        if session.status not in (CountStatus.OPEN, CountStatus.REVIEW):
            raise ValueError("Found items can only be added to an open count.")
        sku = (sku or "").strip()
        prod = self.db.query(Product).filter(Product.sku == sku).first()
        if prod is None:
            raise ValueError(f"No product found with SKU {sku!r}.")
        if self.find_line(session_id, product_id=prod.id) is not None:
            raise ValueError(f"{sku} is already in this count.")

        line = CountLine(
            product_id=prod.id,
            sku=prod.sku or "",
            description=(prod.title or "")[:300],
            bin_location=prod.bin_location or "",
            frozen_qty=prod.qty_on_hand or 0,
            unit_cost=prod.cost or 0.0,
            is_found=True,
            line_status=CountLineStatus.UNCOUNTED,
        )
        session.lines.append(line)  # relationship append keeps rollups correct
        self.db.flush()
        if counted_qty is not None:
            line.counted_qty = max(0, int(counted_qty))
            line.book_at_count = prod.qty_on_hand or 0
            line.counted_by_id = self.current_user_id
            line.counted_at = datetime.utcnow()
            line.needs_recount = self._over_threshold(line, session)
            line.line_status = (
                CountLineStatus.RECOUNT if line.needs_recount else CountLineStatus.COUNTED
            )
        self._recompute_rollups(session)
        self.db.commit()
        return line

    def accept_recount(self, session_id: int, line_id: int) -> CountLine:
        """Explicitly accept a flagged variance so it can post as-is (§24.2)."""
        self.assert_can(Permission.INVENTORY_COUNT)
        session = self.get_session(session_id)
        if session.status not in (CountStatus.OPEN, CountStatus.REVIEW):
            raise ValueError("Variances can only be accepted on an open count.")
        line = self.db.query(CountLine).filter(
            CountLine.id == line_id, CountLine.session_id == session_id
        ).first()
        if line is None:
            raise ValueError("Line not found in this count.")
        line.needs_recount = False
        if line.counted_qty is not None:
            line.line_status = CountLineStatus.COUNTED
        self.db.commit()
        return line

    # ── Review / reopen ───────────────────────────────────────────────────────

    def to_review(self, session_id: int) -> CountSession:
        self.assert_can(Permission.INVENTORY_COUNT)
        session = self.get_session(session_id)
        if session.status != CountStatus.OPEN:
            raise ValueError("Only an open count can be moved to review.")
        # needs_recount is already current — record_count maintains it on every
        # entry and accept_recount clears it. Do NOT recompute here: thresholds
        # are frozen at DRAFT, so a recompute is a no-op EXCEPT that it would
        # silently undo an earlier accept_recount decision.
        session.status = CountStatus.REVIEW
        self.db.flush()
        self._recompute_rollups(session)
        self.db.commit()
        return session

    def reopen(self, session_id: int) -> CountSession:
        self.assert_can(Permission.INVENTORY_COUNT)
        session = self.get_session(session_id)
        if session.status != CountStatus.REVIEW:
            raise ValueError("Only a count in review can be reopened for counting.")
        session.status = CountStatus.OPEN
        self.db.commit()
        return session

    # ── Post (write adjustments) ──────────────────────────────────────────────

    def post_session(self, session_id: int, *, zero_uncounted: bool = False) -> dict:
        """Write the count. For each counted line with a non-zero variance, apply
        ``delta = counted − frozen`` via InventoryService.apply_stock_delta in a
        SINGLE transaction (atomic). Zero-variance lines post with no ledger row.
        Uncounted lines are SKIPPED — unless this is a FULL count and
        ``zero_uncounted`` is set, in which case they are treated as counted 0.

        Blocks if any line still needs recount/acceptance (§24.2).
        Gate: INVENTORY_COUNT_APPROVE.
        """
        self.assert_can(Permission.INVENTORY_COUNT_APPROVE)
        from app.services.inventory_service import InventoryService

        session = self.get_session(session_id)
        if session.status not in (CountStatus.OPEN, CountStatus.REVIEW):
            raise ValueError("Only an open or in-review count can be posted.")

        pending = [l for l in session.lines if l.needs_recount]
        if pending:
            raise ValueError(
                f"{len(pending)} line(s) still need a recount or acceptance before posting."
            )

        is_opening = session.count_type == CountType.OPENING
        allow_zero = zero_uncounted and session.count_type == CountType.FULL

        # Batch-load products for the lines we will touch.
        product_ids = [l.product_id for l in session.lines]
        products = {
            p.id: p
            for p in self.db.query(Product).filter(Product.id.in_(product_ids)).all()
        }

        inv = InventoryService(self.db, self.current_user_id)
        posted = skipped = zeroed = unchanged = 0
        variance_value = 0.0

        for line in session.lines:
            product = products.get(line.product_id)
            zero_filled = False
            if line.counted_qty is None:
                # FULL + zero_uncounted: treat an uncounted in-scope line as a
                # physical 0, reconciled against its CURRENT on-hand.
                if allow_zero and product is not None:
                    line.counted_qty = 0
                    line.book_at_count = product.qty_on_hand or 0
                    zero_filled = True
                    zeroed += 1
                else:
                    line.line_status = CountLineStatus.SKIPPED
                    skipped += 1
                    continue

            if product is None:  # product deleted mid-count — cannot post this line
                line.line_status = CountLineStatus.SKIPPED
                skipped += 1
                continue

            delta = line.counted_qty - line.book_baseline
            if delta == 0:
                line.line_status = CountLineStatus.POSTED
                if not zero_filled:  # a zero-fill of an already-0 line is not "unchanged"
                    unchanged += 1
                continue

            if is_opening:
                reason = AdjustmentReason.INITIAL_INVENTORY_LOAD
            else:
                reason = (
                    AdjustmentReason.CYCLE_COUNT_OVER
                    if delta > 0
                    else AdjustmentReason.CYCLE_COUNT_SHORT
                )
            txn = inv.apply_stock_delta(
                product,
                delta,
                InventoryTxnType.INITIAL_COUNT,
                EntityType.COUNT_SESSION,
                session.id,
                notes=(
                    f"{session.session_number} {session.count_type} count — "
                    f"counted {line.counted_qty} (book {line.book_baseline})"
                ),
                reason=reason,
            )
            self.db.flush()  # populate txn.id for the line back-reference
            line.posted_txn_id = txn.id
            line.line_status = CountLineStatus.POSTED
            posted += 1
            variance_value += line.variance_value

        session.status = CountStatus.POSTED
        session.posted_at = datetime.utcnow()
        session.posted_by_id = self.current_user_id
        session.zero_uncounted = allow_zero
        self.db.flush()
        self._recompute_rollups(session)

        summary = {
            "session_number": session.session_number,
            "posted": posted,        # lines that wrote a ledger adjustment
            "unchanged": unchanged,  # counted, zero variance (no adjustment)
            "zeroed": zeroed,        # uncounted-in-scope forced to 0 (FULL only)
            "skipped": skipped,      # uncounted, left untouched
            "variance_value": round(variance_value, 2),
        }
        self.audit(
            entity_type=EntityType.COUNT_SESSION,
            entity_id=session.id,
            action=AuditAction.COUNT_POSTED,
            new_value=summary,
            notes=f"Posted {session.session_number}",
        )
        self.db.commit()
        return summary

    # ── Cancel ────────────────────────────────────────────────────────────────

    def cancel_session(self, session_id: int, *, note: str = "") -> CountSession:
        self.assert_can(Permission.INVENTORY_COUNT)
        session = self.get_session(session_id)
        if session.is_terminal:
            raise ValueError("A posted or cancelled count cannot be cancelled.")
        old = session.status
        session.status = CountStatus.CANCELLED
        if note:
            session.notes = note
        self.db.flush()
        self.audit(
            entity_type=EntityType.COUNT_SESSION,
            entity_id=session.id,
            action=AuditAction.STATUS_CHANGED,
            old_value={"status": old},
            new_value={"status": CountStatus.CANCELLED},
            notes=note or "Count cancelled",
        )
        self.db.commit()
        return session

    # ── Internals ─────────────────────────────────────────────────────────────

    def _load_scope(self, session: CountSession) -> dict:
        try:
            return json.loads(session.scope_json) if session.scope_json else {}
        except (ValueError, TypeError):
            return {}

    def _resolve_scope_products(self, scope: dict) -> list[Product]:
        """Turn a scope filter dict into the concrete product set to snapshot."""
        q = self.db.query(Product)
        if not scope.get("include_inactive"):
            q = q.filter(Product.is_active.is_(True))

        cat_ids = scope.get("category_ids")
        if cat_ids:
            q = q.filter(Product.category_id.in_(cat_ids))

        vendor_ids = scope.get("vendor_ids")
        if vendor_ids:
            sub = (
                self.db.query(ProductVendorSource.product_id)
                .filter(
                    ProductVendorSource.vendor_id.in_(vendor_ids),
                    ProductVendorSource.is_active.is_(True),
                )
                .subquery()
            )
            q = q.filter(Product.id.in_(sub))

        bin_prefix = (scope.get("bin_prefix") or "").strip()
        if bin_prefix:
            # Escape LIKE metacharacters so a bin label such as "A_1" or "50%"
            # is matched literally, not as a wildcard pattern.
            esc = bin_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            q = q.filter(Product.bin_location.like(f"{esc}%", escape="\\"))

        sku_list = scope.get("sku_list")
        if sku_list:
            cleaned = [s.strip() for s in sku_list if s and s.strip()]
            if cleaned:
                q = q.filter(Product.sku.in_(cleaned))

        if scope.get("only_with_stock"):
            # coalesce so a legacy NULL cache row isn't silently dropped by SQL's
            # UNKNOWN result for `NULL != 0`.
            q = q.filter(func.coalesce(Product.qty_on_hand, 0) != 0)

        return q.order_by(Product.bin_location, Product.sku).all()

    def _current_on_hand(self, product_id: int) -> int:
        """Live cached on-hand for a product (the book at 'now'). The cache is
        the authoritative live value — apply_stock_delta updates it in the same
        transaction and the nightly resync keeps it honest against the ledger."""
        qty = (
            self.db.query(Product.qty_on_hand)
            .filter(Product.id == product_id)
            .scalar()
        )
        return int(qty or 0)

    def _over_threshold(self, line: CountLine, session: CountSession) -> bool:
        """True when the line's variance strictly exceeds a configured threshold.
        A threshold of 0 flags ANY discrepancy; both None → never flags."""
        v = line.variance
        if not v:  # None or 0
            return False
        q_thr = session.recount_threshold_qty
        val_thr = session.recount_threshold_value
        if q_thr is None and val_thr is None:
            return False
        if q_thr is not None and abs(v) > q_thr:
            return True
        if val_thr is not None and abs(line.variance_value) > val_thr:
            return True
        return False

    def _recompute_rollups(self, session: CountSession) -> None:
        lines = session.lines
        session.line_count = len(lines)
        session.counted_count = sum(1 for l in lines if l.counted_qty is not None)
        session.variance_qty_total = sum((l.variance or 0) for l in lines)
        session.variance_value_total = round(sum(l.variance_value for l in lines), 2)
