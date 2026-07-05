"""
app/services/inventory_service.py
==================================
R6, R11 — controlled inventory mutations.

Three operations:
  - adjust_inventory()      — qty delta with reason code; admin-only
  - transfer_inventory()    — location-only move; does NOT change qty_on_hand
  - receive_without_po()    — non-PO receipt (cash buy, trade-in, found stock)

All mutations are logged to inventory_transactions (or inventory_transfers for
location-only moves) and audit_log. qty_on_hand is NEVER mutated directly —
it always goes through these methods so the ledger stays consistent.

Moving-average cost (R11):
  new_avg = ((qty_on_hand × current_avg) + (qty_received × receipt_unit_cost))
            / (qty_on_hand + qty_received)
  - Applied on positive qty changes that include a unit_cost.
  - Negative adjustments (damaged/lost/etc.) do NOT change cost — qty only.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.constants import (
    ADJUSTMENT_REASONS_REQUIRING_NOTE,
    AdjustmentReason,
    AuditAction,
    EntityType,
    InventoryTxnType,
    Permission,
)
from app.models.inventory import InventoryTransaction
from app.models.inventory_transfer import InventoryTransfer
from app.models.product import Product
from app.services.base import BaseService

log = logging.getLogger(__name__)


class InventoryService(BaseService):

    # ── Document-flow stock writer (single entry point) ──────────────────────

    def apply_stock_delta(
        self,
        product: Product,
        delta: int,
        txn_type: str,
        reference_type: str,
        reference_id: int,
        notes: str = "",
        *,
        reason: str | None = None,
        clamp_floor_zero: bool = False,
    ) -> InventoryTransaction:
        """
        THE single writer of the Product.qty_on_hand cache for document flows
        (invoice finalize/void, PO receipt, RA return-to-stock, vendor-return
        ship). Mutates the cache AND writes the matching InventoryTransaction
        ledger row together so the two can never diverge at a call site.

        clamp_floor_zero: the CACHE floors at 0 but the ledger row still
        records the FULL delta — pre-existing contract at the invoice-finalize
        (no negative-inventory override) and vendor-return-ship call sites:
        the ledger records what the document did; those paths never drive the
        cache negative. resync_qty_on_hand reconciles any resulting gap.

        No permission gate — callers gate their own document action
        (FINALIZE_INVOICE, RECEIVE_PO, ...); this is plumbing beneath those
        gates. No flush/commit — participates in the caller's transaction.
        """
        if delta == 0:
            raise ValueError("apply_stock_delta requires a non-zero delta")

        # `or 0` guards a NULL legacy cache (matches the RA writer this absorbed).
        new_qty = (product.qty_on_hand or 0) + delta
        if clamp_floor_zero:
            new_qty = max(0, new_qty)
        product.qty_on_hand = new_qty

        txn = InventoryTransaction(
            product_id=product.id,
            transaction_type=txn_type,
            qty_change=delta,
            qty_after=product.qty_on_hand,
            reference_type=reference_type,
            reference_id=reference_id,
            reason=reason,
            performed_by_id=self.current_user_id,
            notes=notes,
        )
        self.db.add(txn)
        return txn

    # ── Nightly cache-vs-ledger resync ────────────────────────────────────────

    def resync_all_products(self) -> dict:
        """
        Re-derive every product's qty_on_hand cache from the ledger.

        CROSS-LANE CONTRACT: the nightly scheduler calls EXACTLY
        ``InventoryService(db).resync_all_products()`` and reads the
        ``checked`` / ``drifted`` / ``fixed`` keys — do not rename either.

        Drifted products are fixed via ProductService.resync_qty_on_hand
        (which audit-logs each correction). The ledger sum is compared FIRST
        so clean products produce no audit row — resync_qty_on_hand audits
        even a zero delta (right for one-off recovery, wrong for a
        20k-product nightly sweep). Commits once at the end.
        """
        from app.services.product_service import ProductService

        product_svc = ProductService(self.db, self.current_user_id)
        checked = drifted = fixed = 0
        for pid, cached_qty in (
            self.db.query(Product.id, Product.qty_on_hand).order_by(Product.id).all()
        ):
            checked += 1
            ledger_qty = product_svc.get_qty_on_hand(pid)
            if (cached_qty or 0) == ledger_qty:
                continue
            drifted += 1
            old_qty, new_qty = product_svc.resync_qty_on_hand(pid)
            log.warning(
                "resync_all_products: qty_on_hand drift on product %s — "
                "cache %s → ledger %s (fixed)",
                pid, old_qty, new_qty,
            )
            fixed += 1

        self.db.commit()
        return {"checked": checked, "drifted": drifted, "fixed": fixed}

    # ── Public API ────────────────────────────────────────────────────────────

    def adjust_inventory(
        self,
        product_id: int,
        qty_delta: int,
        reason: str,
        note: str = "",
        unit_cost: float | None = None,
    ) -> InventoryTransaction:
        """
        Mutate qty_on_hand by qty_delta (positive or negative).

        Args:
            product_id: target product
            qty_delta:  signed delta — positive adds, negative removes
            reason:     value from AdjustmentReason enum (required)
            note:       free-text reason. Required when reason ∈
                        ADJUSTMENT_REASONS_REQUIRING_NOTE (OTHER, CORRECTION).
            unit_cost:  required when qty_delta > 0 to update moving avg cost.
                        If None on a positive adjustment, current product.cost is
                        used (no recalc — the new stock is valued at existing avg).

        Permission: INVENTORY_ADJUST (admin only by default).
        Negative results are allowed — admin may intentionally drive inventory
        below zero to correct bad starting data. The audit log records every event.

        Returns the InventoryTransaction row.
        """
        self.assert_can(Permission.INVENTORY_ADJUST)

        if qty_delta == 0:
            raise ValueError("Inventory adjustment qty_delta must be non-zero")

        # Validate reason against enum
        valid_reasons = {r.value for r in AdjustmentReason}
        if reason not in valid_reasons:
            raise ValueError(
                f"Invalid adjustment reason '{reason}'. "
                f"Must be one of: {sorted(valid_reasons)}"
            )

        # Note is required for OTHER and CORRECTION
        if reason in {r.value for r in ADJUSTMENT_REASONS_REQUIRING_NOTE} and not note.strip():
            raise ValueError(
                f"A free-text note is required when reason is '{reason}'."
            )

        product = self.db.query(Product).filter(Product.id == product_id).first()
        if product is None:
            raise ValueError(f"Product {product_id} not found")

        qty_before = product.qty_on_hand

        # Update qty_on_hand
        product.qty_on_hand = qty_before + qty_delta

        # Update moving-average cost on POSITIVE adjustments when unit_cost given
        if qty_delta > 0 and unit_cost is not None and unit_cost > 0:
            self._apply_moving_average_cost(product, qty_delta, unit_cost)

        # Write the ledger row
        txn = InventoryTransaction(
            product_id=product_id,
            transaction_type=InventoryTxnType.MANUAL_ADJUSTMENT,
            qty_change=qty_delta,
            qty_after=product.qty_on_hand,
            reference_type=EntityType.INVENTORY_ADJUSTMENT,
            reference_id=0,  # adjustments aren't a separate entity (yet)
            reason=reason,
            performed_by_id=self.current_user_id,
            notes=note or f"Inventory adjustment: {reason}",
        )
        self.db.add(txn)
        self.db.flush()

        # Reference back to the new txn id for auditability
        txn.reference_id = txn.id

        self.audit(
            entity_type=EntityType.PRODUCT,
            entity_id=product_id,
            action=AuditAction.INVENTORY_ADJUSTED,
            old_value={"qty_on_hand": qty_before, "cost": product.cost},
            new_value={
                "qty_on_hand": product.qty_on_hand,
                "delta": qty_delta,
                "reason": reason,
                "note": note,
                "unit_cost": unit_cost,
                "cost_after": product.cost,
            },
        )
        from app.services.notification_service import NotificationService
        NotificationService.build_inventory_adjustment(
            self.db,
            product_id=product_id,
            sku=product.sku,
            qty_delta=qty_delta,
            reason=reason,
            adjusted_by_user_id=self.current_user_id,
        )
        self.db.commit()
        return txn

    def apply_physical_count(
        self,
        counts,
        *,
        note: str = "",
        dry_run: bool = True,
    ) -> dict:
        """Set on-hand quantities from a physical count (the go-live loading task).

        ``counts``: iterable of ``(sku, counted_qty)``. For each resolved product,
        computes ``delta = counted - current`` and applies it as a ledger-backed
        adjustment (reason=CYCLE_COUNT) so the count is auditable and the nightly
        resync can't undo it. Products whose count already matches are skipped;
        unknown SKUs are collected and NEVER created. All writes happen in ONE
        transaction (committed once) so a partial failure leaves the DB untouched.

        ``dry_run=True`` (the default) computes the plan without writing — the
        summary is identical either way, so an operator can preview then re-run with
        ``dry_run=False``. Counted stock is valued at each product's existing average
        cost (a count sets quantity, not cost).

        Returns: ``{applied, unchanged, not_found: [sku], changes: [{sku, before,
        after, delta}], dry_run}``.
        """
        self.assert_can(Permission.INVENTORY_ADJUST)

        note = note or "Physical count load"
        applied = 0
        unchanged = 0
        not_found: list[str] = []
        changes: list[dict] = []

        for raw_sku, raw_qty in counts:
            sku = str(raw_sku).strip()
            if not sku:
                continue
            try:
                counted = int(raw_qty)
            except (TypeError, ValueError):
                raise ValueError(f"Count for SKU {sku!r} is not a whole number: {raw_qty!r}")
            if counted < 0:
                raise ValueError(f"Count for SKU {sku!r} cannot be negative: {counted}")

            product = self.db.query(Product).filter(Product.sku == sku).first()
            if product is None:
                not_found.append(sku)
                continue

            before = product.qty_on_hand or 0
            delta = counted - before
            if delta == 0:
                unchanged += 1
                continue

            changes.append({"sku": sku, "before": before, "after": counted, "delta": delta})
            if not dry_run:
                self.apply_stock_delta(
                    product,
                    delta,
                    InventoryTxnType.MANUAL_ADJUSTMENT,
                    EntityType.INVENTORY_ADJUSTMENT,
                    0,
                    notes=f"{note} — counted {counted} (was {before})",
                    reason=AdjustmentReason.CYCLE_COUNT,
                )
            applied += 1

        if not dry_run and changes:
            self.db.commit()

        return {
            "applied": applied,
            "unchanged": unchanged,
            "not_found": not_found,
            "changes": changes,
            "dry_run": dry_run,
        }

    def _apply_shopify_stock_delta(
        self, product_id: int, delta_pieces: int, *,
        order_ref_id: int | None = None, order_name: str = "", note: str = "",
    ) -> InventoryTransaction | None:
        """Signed on-hand movement for a Shopify web order (negative = sale,
        positive = cancellation/refund restock). Writes a ``shopify_sale`` ledger row
        + audit and updates the cached qty_on_hand — the SAME discipline as every
        other stock movement, so a counter sale and a web sale can never double-sell
        the last unit.

        SYSTEM path: the order sync runs as user_id=None (permission bypass), so this
        deliberately does not gate on INVENTORY_ADJUST. It FLUSHES but does NOT
        commit — the caller commits all of an order's line movements together with
        the ShopifyProcessedOrder marker in one transaction, so idempotency is atomic
        (either the whole order is applied-and-marked, or none of it is). No-op
        (returns None) for a zero delta or a missing product."""
        if delta_pieces == 0:
            return None
        product = self.db.query(Product).filter(Product.id == product_id).first()
        if product is None:
            return None
        qty_before = product.qty_on_hand
        product.qty_on_hand = qty_before + int(delta_pieces)
        # reference_id holds the numeric Shopify order id (~13 digits). Safe on SQLite
        # (INTEGER is 64-bit); if this app is ever moved to a 32-bit-INT backend,
        # widen the column to BigInteger or store the id as text.
        ref_id = int(order_ref_id) if order_ref_id else None
        txn = InventoryTransaction(
            product_id=product_id,
            transaction_type=InventoryTxnType.SHOPIFY_SALE,
            qty_change=int(delta_pieces),
            qty_after=product.qty_on_hand,
            # Polymorphic ref is all-or-nothing (ck_inventory_txn_ref_complete).
            reference_type=("shopify_order" if ref_id is not None else None),
            reference_id=ref_id,
            performed_by_id=self.current_user_id,
            notes=(note or f"Shopify web order {order_name}").strip(),
        )
        self.db.add(txn)
        self.db.flush()
        self.audit(
            entity_type=EntityType.PRODUCT,
            entity_id=product_id,
            action=AuditAction.INVENTORY_ADJUSTED,
            old_value={"qty_on_hand": qty_before},
            new_value={"qty_on_hand": product.qty_on_hand,
                       "delta": int(delta_pieces), "source": "shopify_order",
                       "order": order_name},
        )
        return txn

    def record_shopify_sale(
        self, product_id: int, qty_pieces: int, *,
        order_ref_id: int | None = None, order_name: str = "",
    ) -> InventoryTransaction | None:
        """Decrement on-hand for a Shopify WEB sale. ``qty_pieces`` is already
        expanded from storefront packs to physical pieces by the caller. No-op for a
        non-positive qty."""
        if qty_pieces <= 0:
            return None
        return self._apply_shopify_stock_delta(
            product_id, -int(qty_pieces), order_ref_id=order_ref_id,
            order_name=order_name, note=f"Shopify web order {order_name}".strip())

    def record_shopify_order_reversal(
        self, product_id: int, qty_pieces: int, *,
        order_ref_id: int | None = None, order_name: str = "",
    ) -> InventoryTransaction | None:
        """Restock on-hand when a previously-decremented web order is later cancelled
        or refunded on Shopify (compensating +pieces). No-op for a non-positive qty."""
        if qty_pieces <= 0:
            return None
        return self._apply_shopify_stock_delta(
            product_id, int(qty_pieces), order_ref_id=order_ref_id,
            order_name=order_name,
            note=f"Shopify web order {order_name} cancelled — stock restored".strip())

    def transfer_inventory(
        self,
        product_id: int,
        qty: int,
        source_location_id: int | None,
        destination_location_id: int | None,
        reason: str = "",
        note: str = "",
        reference_type: str | None = None,
        reference_id: int | None = None,
    ) -> InventoryTransfer:
        """
        Move stock between locations. Does NOT change product.qty_on_hand —
        this is a location-only event.

        Permission: INVENTORY_TRANSFER (admin only by default).
        """
        self.assert_can(Permission.INVENTORY_TRANSFER)

        if qty <= 0:
            raise ValueError("Transfer qty must be positive")
        if source_location_id == destination_location_id and source_location_id is not None:
            raise ValueError("Source and destination locations must differ")

        product = self.db.query(Product).filter(Product.id == product_id).first()
        if product is None:
            raise ValueError(f"Product {product_id} not found")

        transfer = InventoryTransfer(
            product_id=product_id,
            qty=qty,
            source_location_id=source_location_id,
            destination_location_id=destination_location_id,
            reason=reason or None,
            note=note,
            reference_type=reference_type,
            reference_id=reference_id,
            performed_by_user_id=self.current_user_id,
        )
        self.db.add(transfer)
        self.db.flush()

        self.audit(
            entity_type=EntityType.INVENTORY_TRANSFER,
            entity_id=transfer.id,
            action=AuditAction.CREATED,
            new_value={
                "product_id": product_id,
                "qty": qty,
                "from": source_location_id,
                "to": destination_location_id,
                "reason": reason,
            },
        )
        self.db.commit()
        return transfer

    def receive_without_po(
        self,
        product_id: int,
        qty: int,
        unit_cost: float,
        source: str,
        note: str = "",
    ) -> InventoryTransaction:
        """
        Admin-only inventory receipt that bypasses the PO flow.

        Use cases (R10): cash buy from another shop, trade-in, found inventory,
        initial inventory load, assembled/built item, correction.

        Args:
            product_id: target product
            qty:        positive qty being received
            unit_cost:  unit cost — required, used for moving-average update
            source:     short reason code (e.g. "cash_buy", "trade_in", "found",
                        "initial_load", "built", "correction")
            note:       free-text description

        Permission: RECEIVE_WITHOUT_PO (admin only by default).
        """
        self.assert_can(Permission.RECEIVE_WITHOUT_PO)

        if qty <= 0:
            raise ValueError("Receive qty must be positive")
        if unit_cost < 0:
            raise ValueError("Unit cost cannot be negative")
        if not source.strip():
            raise ValueError("Source description is required for non-PO receipts")

        product = self.db.query(Product).filter(Product.id == product_id).first()
        if product is None:
            raise ValueError(f"Product {product_id} not found")

        qty_before = product.qty_on_hand
        cost_before = product.cost

        # Update qty_on_hand
        product.qty_on_hand = qty_before + qty

        # Update moving-average cost
        if unit_cost > 0:
            self._apply_moving_average_cost(product, qty, unit_cost)

        # Write the ledger row
        txn = InventoryTransaction(
            product_id=product_id,
            transaction_type=InventoryTxnType.MANUAL_ADJUSTMENT,  # closest existing type
            qty_change=qty,
            qty_after=product.qty_on_hand,
            reference_type=EntityType.INVENTORY_ADJUSTMENT,
            reference_id=0,
            reason=source,
            performed_by_id=self.current_user_id,
            notes=note or f"Non-PO receipt: {source}",
        )
        self.db.add(txn)
        self.db.flush()
        txn.reference_id = txn.id

        self.audit(
            entity_type=EntityType.PRODUCT,
            entity_id=product_id,
            action=AuditAction.INVENTORY_ADJUSTED,
            old_value={"qty_on_hand": qty_before, "cost": cost_before},
            new_value={
                "qty_on_hand": product.qty_on_hand,
                "qty_received": qty,
                "unit_cost": unit_cost,
                "source": source,
                "note": note,
                "cost_after": product.cost,
            },
        )
        self.db.commit()
        return txn

    # ── Internal: moving-average cost helper ──────────────────────────────────

    def _apply_moving_average_cost(
        self,
        product: Product,
        qty_received: int,
        receipt_unit_cost: float,
        freight_adder: float = 0.0,
    ) -> None:
        """
        R11 — Update product.cost via moving weighted average; set product.last_cost.

        R3 — ``freight_adder`` is the allocated freight-in cost PER UNIT for this
        receipt (computed by POService._compute_freight_adders from the PO's
        freight_in_cost). The average absorbs the LANDED unit cost
        (receipt_unit_cost + freight_adder) so product.cost / COGS reflects what
        the unit actually cost to put on the shelf. The default of 0.0 keeps
        every pre-R3 call path bit-for-bit identical (landed == receipt cost).

        Formula:
          landed    = receipt_unit_cost + freight_adder
          new_avg   = ((qty_on_hand × current_avg) + (qty_received × landed))
                      / (qty_on_hand + qty_received)

        Called WITH qty_on_hand AFTER it has been incremented (so we subtract back
        qty_received to get the pre-receipt qty for the weighting).
        """
        landed_unit_cost = receipt_unit_cost + freight_adder
        if qty_received <= 0 or landed_unit_cost <= 0:
            return

        # qty_on_hand has already been incremented at this point
        qty_before = max(0, product.qty_on_hand - qty_received)
        current_avg = product.cost or 0.0

        # If we had no stock, the new cost IS the (landed) receipt cost
        if qty_before <= 0:
            product.cost = round(landed_unit_cost, 4)
        else:
            new_avg = (
                (qty_before * current_avg) + (qty_received * landed_unit_cost)
            ) / (qty_before + qty_received)
            product.cost = round(new_avg, 4)

        product.last_cost = round(landed_unit_cost, 4)
        product.cost_source = "receipt"   # R11 Option A — only valid writer of product.cost
