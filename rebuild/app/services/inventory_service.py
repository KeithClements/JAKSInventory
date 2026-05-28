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


class InventoryService(BaseService):

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
    ) -> None:
        """
        R11 — Update product.cost via moving weighted average; set product.last_cost.

        Formula:
          new_avg = ((qty_on_hand × current_avg) + (qty_received × receipt_unit_cost))
                    / (qty_on_hand + qty_received)

        Called WITH qty_on_hand AFTER it has been incremented (so we subtract back
        qty_received to get the pre-receipt qty for the weighting).
        """
        if qty_received <= 0 or receipt_unit_cost <= 0:
            return

        # qty_on_hand has already been incremented at this point
        qty_before = max(0, product.qty_on_hand - qty_received)
        current_avg = product.cost or 0.0

        # If we had no stock, the new cost IS the receipt cost
        if qty_before <= 0:
            product.cost = round(receipt_unit_cost, 4)
        else:
            new_avg = (
                (qty_before * current_avg) + (qty_received * receipt_unit_cost)
            ) / (qty_before + qty_received)
            product.cost = round(new_avg, 4)

        product.last_cost = round(receipt_unit_cost, 4)
