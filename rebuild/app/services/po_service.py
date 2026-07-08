"""
app/services/po_service.py
============================
Purchase Order lifecycle — create, receive, bill (3-way match).

3-way match workflow:
  1. PO created → sent to vendor
  2. Goods arrive → POReceipt + POReceiptLine(s) created
     * Receipt auto-writes InventoryTransaction (PO_RECEIPT) for each line
     * Drop-ship receipts do NOT increment inventory
  3. Vendor bill arrives → VendorBill + VendorBillLine(s) created
     * System compares billed qty/cost to received qty/cost
     * Discrepancies flagged on VendorBillLine.has_discrepancy
  4. Bill approved → queued for QBO sync
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.constants import (
    AuditAction, EntityType, FulfillmentSource, InventoryTxnType, MatchResolution,
    Permission, POStatus, POShipToType, QBOSyncStatus, SOLineSource, SOLineStatus,
    VendorBillStatus, VendorCreditMemoTrigger,
)
from app.models.inventory import InventoryTransaction
from app.models.product import Product, ProductCostHistory
from app.models.purchase_order import (
    COST_VARIANCE_TOLERANCE,
    POLine, POReceipt, POReceiptLine,
    PurchaseOrder, VendorBill, VendorBillLine,
)
from app.models.quote import SOLine
from app.settings_utils import bump_counter
from app.services.base import BaseService, apply_product_line_defaults
from app.services.product_service import ProductService


class POService(BaseService):

    # ── 3-Way Match: pure computation (no DB writes) ──────────────────────────
    # Kept here (not the router) so the resolution methods below can call them
    # without creating a circular import.  The router delegates to these.

    # Line states that require AP review.
    _MATCH_FLAG_STATES = frozenset({"over_billed", "cost_variance"})

    # Terminal resolutions that clear the is_flag display on a line.
    _TERMINAL_RESOLUTIONS = frozenset({
        MatchResolution.ACCEPTED,
        MatchResolution.CREDITED,
        MatchResolution.CLEARED,
        MatchResolution.CORRECTED,
    })

    # Decisions that require a non-empty reason.
    _REASON_REQUIRED = frozenset({MatchResolution.REJECTED, MatchResolution.CLEARED})

    @staticmethod
    def _billed_unit_cost(line: POLine) -> float | None:
        """Average billed unit cost across all bill_lines (None if unbilled)."""
        qty = sum(bl.qty_billed for bl in line.bill_lines)
        if qty <= 0:
            return None
        amount = sum(bl.qty_billed * bl.unit_cost for bl in line.bill_lines)
        return round(amount / qty, 2)

    @classmethod
    def compute_match_line(cls, line: POLine) -> dict:
        """
        Compute the match state for a single PO line.

        Stable row-dict keys (UI contract — lane/ui-builder depends on these):
          line, ordered_qty, ordered_cost, received_qty, billed_qty, billed_cost,
          qty_var, cost_var, state, is_flag,
          resolution, resolution_reason, resolved_by_id, resolved_at,
          resolution_vcm_id, suggested_credit, can_resolve
        """
        open_qty = max(0, line.qty_ordered - line.qty_cancelled)
        billed_cost = cls._billed_unit_cost(line)
        cost_var = round((billed_cost - line.unit_cost), 2) if billed_cost is not None else 0.0

        if line.qty_billed > line.qty_received:
            raw_state = "over_billed"
        elif billed_cost is not None and abs(cost_var) >= COST_VARIANCE_TOLERANCE:
            raw_state = "cost_variance"
        elif line.qty_received < open_qty:
            raw_state = "awaiting_receipt"
        elif line.qty_billed < line.qty_received:
            raw_state = "awaiting_bill"
        else:
            raw_state = "matched"

        resolution = line.match_resolution

        # A flag is live only when raw state is a variance AND AP hasn't resolved
        # it terminally.  on_hold de-prioritises (is_flag=False).
        if raw_state in cls._MATCH_FLAG_STATES:
            if resolution in cls._TERMINAL_RESOLUTIONS:
                state = f"resolved_{resolution}"   # e.g. "resolved_accepted"
                is_flag = False
            elif resolution == MatchResolution.ON_HOLD:
                state = "on_hold"
                is_flag = False
            elif resolution == MatchResolution.REJECTED:
                state = "rejected"
                is_flag = True   # still needs AP action (waiting corrected bill)
            else:
                state = raw_state
                is_flag = True
        else:
            # Not a live variance. A line explicitly CORRECTED by AP (its PO/bill
            # numbers were edited so it reconciles) still surfaces its resolution
            # so the UI shows the "Corrected" attribution, not a plain "Matched".
            if resolution == MatchResolution.CORRECTED:
                state = "resolved_corrected"
            else:
                state = raw_state
            is_flag = False

        # Suggested credit amount for the Create-Credit shortcut in the UI
        suggested_credit: float | None = None
        if raw_state == "over_billed" and billed_cost is not None:
            qty_var = line.qty_billed - line.qty_received
            suggested_credit = round(qty_var * billed_cost, 2)
        elif raw_state == "cost_variance" and cost_var > 0 and billed_cost is not None:
            suggested_credit = round(cost_var * line.qty_billed, 2)

        return {
            "line":              line,
            "ordered_qty":       line.qty_ordered,
            "ordered_cost":      line.unit_cost,
            "received_qty":      line.qty_received,
            "billed_qty":        line.qty_billed,
            "billed_cost":       billed_cost,
            "qty_var":           line.qty_billed - line.qty_received,
            "cost_var":          cost_var,
            "state":             state,
            "is_flag":           is_flag,
            # resolution metadata
            "resolution":        resolution,
            "resolution_reason": line.match_resolution_reason,
            "resolved_by_id":    line.match_resolved_by_id,
            "resolved_at":       line.match_resolved_at,
            "resolution_vcm_id": line.match_resolution_vcm_id,
            "suggested_credit":  suggested_credit,
            "can_resolve":       raw_state in cls._MATCH_FLAG_STATES,
        }

    @classmethod
    def compute_match_summary(cls, po: PurchaseOrder) -> dict:
        """Compute match summary for all lines on a PO."""
        rows = [cls.compute_match_line(ln) for ln in po.lines]
        flagged = [r for r in rows if r["is_flag"]]
        has_activity = any(r["received_qty"] or r["billed_qty"] for r in rows)
        return {
            "rows":          rows,
            "flag_count":    len(flagged),
            "has_activity":  has_activity,
            "matched_count": sum(1 for r in rows if r["state"] == "matched"),
        }

    # ── PO Creation ───────────────────────────────────────────────────────────

    def create_po(
        self,
        vendor_id: int,
        data: dict,
        is_drop_ship: bool = False,
        drop_ship_customer_id: int | None = None,
        drop_ship_address_id: int | None = None,
    ) -> PurchaseOrder:
        """
        Create a draft PO. Generates po_number (PO-YEAR-NNNN).
        """
        year = datetime.utcnow().year
        po_number = bump_counter(self.db, "next_po_number", "PO", year)

        po = PurchaseOrder(
            po_number=po_number,
            vendor_id=vendor_id,
            status=POStatus.DRAFT,
            is_drop_ship=is_drop_ship,
            drop_ship_customer_id=drop_ship_customer_id,
            drop_ship_address_id=drop_ship_address_id,
            freight_in_cost=float(data.get("freight_in_cost", 0.0)),
            vendor_confirmation_number=data.get("vendor_confirmation_number"),
            notes=data.get("notes", ""),
            internal_notes=data.get("internal_notes", ""),
            expected_at=data.get("expected_at"),
        )
        self.db.add(po)
        self.db.flush()

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po.id,
            action=AuditAction.CREATED,
            new_value={"po_number": po_number, "vendor_id": vendor_id},
        )
        self.db.commit()
        return po

    def add_line(self, po_id: int, product_id: int | None, data: dict) -> POLine:
        """
        Add a product line to a draft PO.
        Pulls vendor cost from ProductVendorSource when product_id is given.
        """
        po = self._get_po_or_404(po_id)
        if po.status not in (POStatus.DRAFT, POStatus.VERBAL_ORDER):
            raise ValueError(f"Cannot add lines to a PO in status '{po.status}'")

        unit_cost = float(data.get("unit_cost", 0.0))

        product = None
        if product_id is not None:
            product = self.db.query(Product).filter(Product.id == product_id).first()
            # Auto-fill cost from THIS PO's vendor source (vendor-specific cost)
            if unit_cost == 0.0:
                from app.models.product import ProductVendorSource
                source = (
                    self.db.query(ProductVendorSource)
                    .filter(
                        ProductVendorSource.product_id == product_id,
                        ProductVendorSource.vendor_id == po.vendor_id,
                        ProductVendorSource.is_active == True,  # noqa: E712
                    )
                    .first()
                )
                if source:
                    unit_cost = source.vendor_cost

        # Shared product-derived defaults: description (+ cost if still 0). POs are
        # cost-only, so include_price=False. Core charge backfills from the product.
        data["unit_cost"] = unit_cost
        apply_product_line_defaults(product, data, include_price=False)
        core_charge = float(data.get("core_charge_per_unit", 0.0) or 0.0)
        if product is not None and core_charge == 0.0 and product.vendor_core_charge:
            core_charge = product.vendor_core_charge

        line = POLine(
            po_id=po_id,
            product_id=product_id,
            description=data.get("description", ""),
            qty_ordered=int(data.get("qty_ordered", 1)),
            unit_cost=float(data.get("unit_cost", 0.0)),
            core_charge_per_unit=core_charge,
            notes=data.get("notes", ""),
        )
        self.db.add(line)
        self.db.flush()
        # C9 — a line added to an already-VERBAL_ORDER PO is immediately on order.
        self._resync_on_order_if_verbal(po, product_id)
        self.db.commit()
        return line

    def create_pos_from_reorder(self, items: dict[int, int]) -> dict:
        """
        §23.3 Phase 2 — bulk "Create POs" from the Low Stock report's selection.

        Takes ``{product_id: qty_ordered}`` (the caller — the report route —
        supplies the qty; this method never re-derives the suggested-qty
        formula, which is ReportService.get_low_stock's job and is tested
        there). Groups by each product's PREFERRED ACTIVE vendor source
        (same resolution SalesOrderService.create_po_for_line uses) into ONE
        draft PO per vendor, so a reorder spanning several SKUs from the same
        vendor doesn't mint a PO per line. A product with no preferred vendor
        source is skipped (tracked, never silently dropped) — nothing to
        order it FROM.

        Returns:
          {
            "created": [{"po_id": int, "po_number": str, "vendor_name": str,
                         "line_count": int}, ...],
            "skipped_no_vendor": [{"product_id": int, "sku": str}, ...],
          }
        """
        by_vendor: dict[int, list[tuple[Product, int]]] = {}
        skipped_no_vendor: list[dict] = []

        products = (
            self.db.query(Product).filter(Product.id.in_(items.keys())).all()
            if items else []
        )
        for product in products:
            qty = int(items.get(product.id, 0))
            if qty <= 0:
                continue
            source = product.preferred_vendor_source
            if source is None:
                skipped_no_vendor.append({"product_id": product.id, "sku": product.sku})
                continue
            by_vendor.setdefault(source.vendor_id, []).append((product, qty))

        created: list[dict] = []
        for vendor_id, lines in by_vendor.items():
            po = self.create_po(
                vendor_id=vendor_id,
                data={"notes": "Auto-created from Low Stock reorder."},
            )
            for product, qty in lines:
                self.add_line(po.id, product.id, {
                    "qty_ordered": qty,
                    "description": product.title,
                })
            created.append({
                "po_id": po.id,
                "po_number": po.po_number,
                "vendor_name": po.vendor.name if po.vendor else "",
                "line_count": len(lines),
            })

        return {"created": created, "skipped_no_vendor": skipped_no_vendor}

    def send_to_vendor(self, po_id: int) -> None:
        """
        Mark PO as sent and increment Product.qty_on_order for all product lines.

        Distinction: PO created ≠ inventory on order.  PO sent = inventory on order.
        This matches real-world purchasing: a draft PO is not a commitment until sent.
        """
        po = self._get_po_or_404(po_id)
        old_status = po.status
        po.status = POStatus.SENT
        po.ordered_at = datetime.utcnow()

        for line in po.lines:
            if line.product_id and line.qty_ordered > 0:
                product = self.db.query(Product).filter(Product.id == line.product_id).first()
                if product:
                    product.qty_on_order += line.qty_ordered

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=POStatus.SENT,
        )
        self.db.commit()

    def mark_verbal_order(self, po_id: int) -> None:
        """C9 — set a PO to VERBAL_ORDER (phone order) and put its lines ON ORDER.

        Like send_to_vendor, a verbal order is a real purchasing commitment, so
        its lines must increment qty_on_order. We recompute from source via
        resync_qty_on_order (which now includes VERBAL_ORDER) rather than a raw
        += so this is idempotent and stays correct as lines are added/edited in
        the workspace. Previously verbal orders never touched qty_on_order, so
        the purchasing on-order signal was wrong for every phone order."""
        po = self._get_po_or_404(po_id)
        old_status = po.status
        po.status = POStatus.VERBAL_ORDER
        if po.ordered_at is None:
            po.ordered_at = datetime.utcnow()
        self.db.flush()
        product_svc = ProductService(self.db, self.current_user_id)
        for pid in {ln.product_id for ln in po.lines if ln.product_id}:
            product_svc.resync_qty_on_order(pid)
        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=POStatus.VERBAL_ORDER,
        )
        self.db.commit()

    def _resync_on_order_if_verbal(self, po, product_id: int | None) -> None:
        """Keep qty_on_order correct when a line on an already-VERBAL_ORDER PO is
        added/edited/deleted in the workspace. No-op for DRAFT (not yet on order)
        and SENT/PARTIAL (lines are frozen / handled by their own paths)."""
        if product_id and po.status == POStatus.VERBAL_ORDER:
            ProductService(self.db, self.current_user_id).resync_qty_on_order(product_id)

    # ── Receiving ─────────────────────────────────────────────────────────────

    def create_receipt(
        self,
        vendor_id: int,
        po_line_quantities: dict[int, int],  # {po_line_id: qty_received}
        data: dict,
    ) -> POReceipt:
        """
        Record goods receipt against one or more PO lines.

        For each line (R6, R7, R11, R3):
          1. Creates POReceiptLine
          2. Detects over-receipt (qty_received > qty_ordered) → flags line
          3. Writes InventoryTransaction (PO_RECEIPT) unless drop-ship
          4. Updates Product.qty_on_hand cache + qty_on_order
          5. Updates Product.cost via moving-weighted-average + last_cost —
             R3: at the LANDED unit cost (unit_cost + allocated freight adder)
             when the PO carries freight_in_cost; see _compute_freight_adders
             for the allocation rule. Zero/absent freight is bit-for-bit the
             pre-R3 behavior. Writes POLine.landed_cost_per_unit and a
             ProductCostHistory "landed cost" row when freight lands.
          6. FIFO-allocates received qty to linked SO lines (any qty leftover
             goes to general available stock)
          7. Records ProductCostHistory if vendor source cost differs

        Marks PO RECEIVED if all lines fully received (qty_received + qty_cancelled
        >= qty_ordered), PARTIAL otherwise.
        """
        # RBAC — receiving increments qty_on_hand and permanently alters the
        # moving-average cost. Gate it (ADMIN/BOOKKEEPING) so a SALES clerk
        # cannot mutate inventory/cost. Receiving is a warehouse/AP function.
        self.assert_can(Permission.RECEIVE_PO)

        # Guard — a receipt with no positive quantity would persist an empty
        # POReceipt header (audit noise) and touch no inventory. The per-line loop
        # below already skips ``qty <= 0`` lines, so an all-zero/blank submission
        # (scripted POST, or a receive form submitted with nothing filled in)
        # otherwise creates a phantom receipt. Reject it before anything is written.
        if not any((q or 0) > 0 for q in po_line_quantities.values()):
            raise ValueError(
                "Nothing to receive — enter a received quantity greater than "
                "zero on at least one line."
            )

        receipt = POReceipt(
            vendor_id=vendor_id,
            received_by_id=self.current_user_id,
            tracking_number=data.get("tracking_number"),
            carrier=data.get("carrier"),
            notes=data.get("notes", ""),
        )
        self.db.add(receipt)
        self.db.flush()

        product_svc = ProductService(self.db, self.current_user_id)
        # Lazy import to avoid circular reference at module load time
        from app.services.inventory_service import InventoryService
        inv_svc = InventoryService(self.db, self.current_user_id)

        # R3 — freight landing: per-unit freight adders for every freight-bearing
        # PO touched by this receipt. Zero/absent freight → empty dict, and the
        # whole receipt path below behaves exactly as before R3.
        received_line_ids = [lid for lid, q in po_line_quantities.items() if q > 0]
        received_lines = (
            self.db.query(POLine).filter(POLine.id.in_(received_line_ids)).all()
            if received_line_ids else []
        )
        freight_adders = self._compute_freight_adders(received_lines)

        # Every PO touched by this receipt, keyed by id. A single receipt can
        # span multiple POs from the same vendor (POReceipt.vendor_id;
        # POReceiptLine.po_id per line), so we must re-evaluate the status of
        # EACH involved PO after the loop — not just the last one seen.
        pos_touched: dict[int, PurchaseOrder] = {}
        for po_line_id, qty in po_line_quantities.items():
            if qty <= 0:
                continue

            po_line = self.db.query(POLine).filter(POLine.id == po_line_id).first()
            if po_line is None:
                raise ValueError(f"POLine {po_line_id} not found")

            po = po_line.po  # PO owning THIS line (may differ across lines in a multi-PO receipt)
            # A receipt carries ONE vendor_id (POReceipt) — every line must belong
            # to a PO of that same vendor. Guards the multi-PO receive path from
            # accidentally landing cross-vendor lines onto one vendor's receipt.
            if po.vendor_id != vendor_id:
                raise ValueError(
                    f"POLine {po_line_id} belongs to vendor {po.vendor_id}, not the "
                    f"receipt vendor {vendor_id}; a receipt spans a single vendor only."
                )
            pos_touched[po.id] = po
            is_drop_ship = po.is_drop_ship

            # Record receipt line (with optional per-line condition notes)
            condition_notes_map: dict[int, str] = data.get("condition_notes_map") or {}
            receipt_line = POReceiptLine(
                receipt_id=receipt.id,
                po_id=po_line.po_id,
                po_line_id=po_line_id,
                qty_received=qty,
                condition_notes=condition_notes_map.get(po_line_id),
            )
            self.db.add(receipt_line)

            # Update PO line received qty
            po_line.qty_received += qty

            # R6 — Over-receipt detection (do NOT silently inflate qty_ordered)
            if po_line.qty_received > po_line.qty_ordered:
                po_line.over_received = True
                po_line.over_received_qty = po_line.qty_received - po_line.qty_ordered
                from app.services.notification_service import NotificationService
                NotificationService.build_po_over_receipt(
                    self.db,
                    po_id=po.id,
                    po_number=po.po_number,
                    sku=po_line.description or str(po_line.product_id),
                    qty_ordered=po_line.qty_ordered,
                    qty_received=po_line.qty_received,
                )

            # R3 — freight landing: per-unit freight adder allocated from the
            # PO's freight_in_cost (0.0 when the PO carries no freight). The
            # landed unit cost (unit_cost + adder) is what actually hit COGS,
            # so record it on the PO line. The adder is per ORDERED unit, so the
            # value is identical across partial receipts (cumulative-weighted
            # average degenerates to the same number) — the write is idempotent.
            freight_adder = freight_adders.get(po_line_id, 0.0)
            if freight_adder > 0:
                po_line.landed_cost_per_unit = round(
                    po_line.unit_cost + freight_adder, 4
                )

            # Update product inventory cache + ledger (stock receipts only)
            if not is_drop_ship and po_line.product_id:
                product = self.db.query(Product).filter(Product.id == po_line.product_id).first()
                if product:
                    # Cache + PO_RECEIPT ledger row via the single qty_on_hand
                    # writer (audit risk #9). Must run BEFORE the moving-average
                    # update: _apply_moving_average_cost reads the
                    # POST-increment qty_on_hand (subtracts qty back out).
                    inv_svc.apply_stock_delta(
                        product,
                        qty,
                        InventoryTxnType.PO_RECEIPT,
                        EntityType.PO_RECEIPT,
                        receipt.id,
                        notes=f"PO {po.po_number}, line {po_line_id}",
                    )
                    product.qty_on_order = max(0, product.qty_on_order - qty)

                    # R11 — moving weighted average cost update
                    # R3: absorbs the landed unit cost (unit_cost + freight
                    # adder); freight_adder=0 is the exact pre-R3 behavior.
                    if po_line.unit_cost > 0 or freight_adder > 0:
                        inv_svc._apply_moving_average_cost(
                            product, qty, po_line.unit_cost,
                            freight_adder=freight_adder, po_id=po_line.po_id,
                        )

                # Record cost change if the PO cost differs from vendor source
                if po_line.unit_cost > 0:
                    product_svc.compare_and_record_cost_change(
                        product_id=po_line.product_id,
                        vendor_id=vendor_id,
                        new_cost=po_line.unit_cost,
                        po_id=po_line.po_id,
                    )

                # R3 — record the LANDED unit cost in cost history when freight
                # was allocated. History row ONLY — never touches the vendor
                # source quote (vendor_cost stays the bare unit cost above).
                if freight_adder > 0:
                    self.db.add(ProductCostHistory(
                        product_id=po_line.product_id,
                        vendor_id=vendor_id,
                        old_cost=po_line.unit_cost,
                        new_cost=round(po_line.unit_cost + freight_adder, 4),
                        changed_by_id=self.current_user_id,
                        po_id=po_line.po_id,
                        notes=(
                            f"PO receipt landed cost — freight included "
                            f"(+${freight_adder:.4f}/unit)"
                        ),
                    ))

                # R7 — FIFO-allocate to linked SO lines before excess goes to stock
                self._allocate_to_linked_sos(po_line_id, qty, po.po_number)

        # Mark each touched PO's status once, after all lines are processed.
        # Evaluated here (not inside the loop) to avoid redundant DB writes on
        # multi-line receipts. Each PO closes (RECEIVED) only when EVERY one of
        # its own lines is fully settled (received + cancelled >= ordered),
        # otherwise PARTIAL — evaluated independently per PO so a multi-PO
        # receipt can close one PO while leaving another partial.
        if pos_touched:
            self.db.flush()
            for po in pos_touched.values():
                all_settled = all(
                    (ln.qty_received + ln.qty_cancelled) >= ln.qty_ordered
                    for ln in po.lines
                )
                po.status = POStatus.RECEIVED if all_settled else POStatus.PARTIAL

        self.audit(
            entity_type=EntityType.PO_RECEIPT,
            entity_id=receipt.id,
            action=AuditAction.CREATED,
            new_value={"vendor_id": vendor_id, "lines": list(po_line_quantities.keys())},
        )
        self.db.commit()
        return receipt

    def _compute_freight_adders(self, received_lines: list[POLine]) -> dict[int, float]:
        """
        R3 — Allocate PO-level freight (PurchaseOrder.freight_in_cost) into a
        per-unit "freight adder" for each line of every freight-bearing PO
        touched by a receipt.

        Allocation rule (deterministic, receipt-independent):
          * Freight is spread across the FULL ORDERED quantity of every line on
            the PO, weighted by line value (qty_ordered × unit_cost):
                adder_per_unit(line) = freight × unit_cost / Σ(qty_ordered × unit_cost)
          * If the PO has no line value at all (every line zero-cost), the
            weight falls back to quantity:
                adder_per_unit = freight / Σ(qty_ordered)
          * A zero-cost line on a MIXED PO carries no value weight (adder 0) —
            its freight share lands on the costed lines, conserving the total.
          * Because the adder is per ORDERED unit, a partial receipt lands only
            the freight belonging to the units received in THAT receipt (a PO
            received in two halves lands half the freight each time), so
            multiple receipts can never over-allocate. (Over-RECEIPT beyond
            qty_ordered keeps the same per-unit adder — the R6 flag, not this
            allocator, is the control for that.)

        Returns {po_line_id: freight_adder_per_unit} (rounded to 4dp) covering
        every line of each freight-bearing PO. POs with zero/absent freight
        contribute nothing, so the receipt path stays bit-for-bit unchanged
        for them.
        """
        adders: dict[int, float] = {}
        pos_seen: dict[int, PurchaseOrder] = {}
        for ln in received_lines:
            if ln.po_id not in pos_seen:
                pos_seen[ln.po_id] = ln.po
        for po in pos_seen.values():
            freight = float(po.freight_in_cost or 0.0)
            if freight <= 0:
                continue
            total_value = sum(l.qty_ordered * l.unit_cost for l in po.lines)
            if total_value > 0:
                for l in po.lines:
                    adders[l.id] = round(freight * l.unit_cost / total_value, 4)
            else:
                total_qty = sum(l.qty_ordered for l in po.lines)
                if total_qty <= 0:
                    continue
                per_unit = round(freight / total_qty, 4)
                for l in po.lines:
                    adders[l.id] = per_unit
        return adders

    def _allocate_to_linked_sos(
        self,
        po_line_id: int,
        qty_received: int,
        po_number: str,
    ) -> int:
        """
        R7 — Allocate newly-received qty to SO lines that link to this po_line,
        oldest SO first (FIFO).

        For each linked SOLine in FIFO order:
          - Increment qty_committed on the SO line and product (the new stock
            is immediately reserved for the customer it was ordered for).
          - Advance the line's status from AWAITING_PO_RECEIPT → RESERVED_STOCK
            when its full demand has been met (partial fulfillment keeps it
            in AWAITING state).
          - Reduce qty_backordered on the product if the line was backordered.

        Returns the qty consumed by SO allocation. Remaining qty is unallocated
        (lives in general qty_available).
        """
        linked_lines = (
            self.db.query(SOLine)
            .filter(SOLine.linked_po_line_id == po_line_id)
            .order_by(SOLine.id)  # FIFO: lowest id = oldest SO
            .all()
        )
        if not linked_lines:
            return 0

        remaining = qty_received
        allocated_total = 0

        for so_line in linked_lines:
            if remaining <= 0:
                break
            outstanding = so_line.qty_ordered - so_line.qty_committed - so_line.qty_invoiced
            if outstanding <= 0:
                continue

            take = min(remaining, outstanding)
            so_line.qty_committed += take
            allocated_total += take
            remaining -= take

            # Mirror the commitment on the product cache
            if so_line.product_id:
                product = self.db.query(Product).filter(Product.id == so_line.product_id).first()
                if product:
                    product.qty_committed += take
                    product.qty_backordered = max(0, product.qty_backordered - take)

            # Advance line_status when fully met. Once the ordered qty is reserved
            # the line is no longer a backorder — clear the legacy `source` flag so
            # is_backordered / SO.has_backorder stop reporting it as outstanding.
            if so_line.qty_committed >= so_line.qty_ordered - so_line.qty_invoiced:
                so_line.line_status = SOLineStatus.RESERVED_STOCK
                so_line.source = SOLineSource.STOCK
            # else: stay in AWAITING_PO_RECEIPT for partial linked receipt

            # Write ledger row for the reservation
            txn = InventoryTransaction(
                product_id=so_line.product_id,
                transaction_type=InventoryTxnType.SO_COMMITTED,
                qty_change=-take,  # negative = removed from available
                qty_after=(self.db.query(Product)
                          .filter(Product.id == so_line.product_id).first().qty_on_hand
                          if so_line.product_id else 0),
                reference_type=EntityType.SALES_ORDER,
                reference_id=so_line.so_id,
                performed_by_id=self.current_user_id,
                notes=f"Linked-PO allocation from PO {po_number} (po_line {po_line_id})",
            )
            self.db.add(txn)

        self.db.flush()
        return allocated_total

    def reverse_receipt(self, receipt_id: int, reason: str) -> dict:
        """
        Undo a receipt: reverses qty_on_hand, qty_on_order, and — when a prior
        moving-average snapshot exists (InventoryService._apply_moving_average_cost
        now always writes one) — product.cost, then marks the whole receipt
        reversed. Whole-receipt, atomic (every line is validated BEFORE anything
        is touched), idempotent (a reversed receipt can't be reversed again).

        Deliberately scoped to the cases a reversal can be made EXACT — refuses
        with a clear reason rather than guessing:
          * any line already billed (a vendor bill has claimed this receipt's
            qty — correct it via the bill / a vendor credit instead)
          * any line that allocated stock to a linked sales order (no way to
            attribute how much of the SO's committed qty came from THIS
            receipt specifically if the line was received more than once —
            correct the sales order line manually instead)
          * anything else has touched the product's on-hand qty since this
            receipt (a later sale/adjustment/receipt would make "subtract this
            receipt back out" mathematically wrong)
        Cost is restored only when a matching ProductCostHistory snapshot
        exists — older receipts (predating this feature) may not have one;
        qty still reverses exactly, cost is left as-is with a clear note.
        """
        self.assert_can(Permission.REVERSE_PO_RECEIPT)
        if not reason.strip():
            raise ValueError("A reason is required to reverse a receipt.")

        receipt = self.db.query(POReceipt).filter(POReceipt.id == receipt_id).first()
        if receipt is None:
            raise ValueError(f"Receipt {receipt_id} not found")
        if receipt.reversed_at is not None:
            raise ValueError(f"Receipt #{receipt.id} was already reversed.")
        if not receipt.lines:
            raise ValueError(f"Receipt #{receipt.id} has no lines to reverse.")

        # Validate EVERY line before touching anything — atomic, all or nothing.
        blockers: list[str] = []
        for rl in receipt.lines:
            po_line = rl.po_line
            label = po_line.description or f"po_line {po_line.id}"
            if po_line.qty_billed > 0:
                blockers.append(
                    f"{label}: already billed ({po_line.qty_billed} units) — "
                    f"correct it via the vendor bill or a vendor credit instead."
                )
                continue
            if po_line.product_id and not po_line.po.is_drop_ship:
                has_linked_so = (
                    self.db.query(SOLine.id)
                    .filter(SOLine.linked_po_line_id == po_line.id)
                    .first() is not None
                )
                if has_linked_so:
                    blockers.append(
                        f"{label}: allocated stock to a linked sales order — "
                        f"reversal can't safely attribute how much came from "
                        f"this receipt; correct the sales order line manually instead."
                    )
                    continue
                last_txn = (
                    self.db.query(InventoryTransaction)
                    .filter(InventoryTransaction.product_id == po_line.product_id)
                    .order_by(InventoryTransaction.id.desc())
                    .first()
                )
                is_latest_this_receipt = (
                    last_txn is not None
                    and last_txn.reference_type == EntityType.PO_RECEIPT
                    and last_txn.reference_id == receipt.id
                )
                if not is_latest_this_receipt:
                    blockers.append(
                        f"{label}: other inventory activity has happened on this "
                        f"product since this receipt — use a manual inventory "
                        f"adjustment instead."
                    )
        if blockers:
            raise ValueError(
                f"Cannot reverse receipt #{receipt.id}:\n" + "\n".join(f"  - {b}" for b in blockers)
            )

        # All lines clear — reverse for real.
        from app.services.inventory_service import InventoryService
        inv_svc = InventoryService(self.db, self.current_user_id)

        pos_touched: dict[int, PurchaseOrder] = {}
        cost_notes: list[str] = []
        for rl in receipt.lines:
            po_line = rl.po_line
            po = po_line.po
            pos_touched[po.id] = po

            po_line.qty_received = max(0, po_line.qty_received - rl.qty_received)
            po_line.over_received = po_line.qty_received > po_line.qty_ordered
            po_line.over_received_qty = max(0, po_line.qty_received - po_line.qty_ordered)

            if po_line.product_id and not po.is_drop_ship and rl.qty_received:
                product = self.db.query(Product).filter(Product.id == po_line.product_id).first()
                if product:
                    inv_svc.apply_stock_delta(
                        product, -rl.qty_received, InventoryTxnType.CORRECTION,
                        EntityType.PO_RECEIPT, receipt.id,
                        notes=f"Reversed PO receipt #{receipt.id} ({po.po_number}): {reason}",
                    )
                    product.qty_on_order += rl.qty_received

                    snapshot = (
                        self.db.query(ProductCostHistory)
                        .filter(ProductCostHistory.product_id == product.id,
                                ProductCostHistory.po_id == po_line.po_id,
                                ProductCostHistory.notes == "Moving-average update on PO receipt")
                        .order_by(ProductCostHistory.id.desc())
                        .first()
                    )
                    if snapshot is not None:
                        old_avg = product.cost
                        product.cost = snapshot.old_cost
                        self.db.add(ProductCostHistory(
                            product_id=product.id, po_id=po_line.po_id,
                            old_cost=old_avg, new_cost=snapshot.old_cost,
                            changed_by_id=self.current_user_id,
                            notes=f"Reversed PO receipt #{receipt.id} — cost restored",
                        ))
                        cost_notes.append(f"{product.sku}: cost restored to {snapshot.old_cost:.4f}")
                    else:
                        cost_notes.append(
                            f"{product.sku}: cost NOT restored (predates cost-snapshot "
                            f"tracking) — verify/correct manually if needed"
                        )

        # Recompute each touched PO's status — but skip a PO the owner
        # deliberately cancelled; a receipt reversal shouldn't silently un-cancel it.
        self.db.flush()
        for po in pos_touched.values():
            if po.status == POStatus.CANCELLED:
                continue
            any_activity = any(ln.qty_received > 0 or ln.qty_cancelled > 0 for ln in po.lines)
            all_settled = all(
                (ln.qty_received + ln.qty_cancelled) >= ln.qty_ordered for ln in po.lines
            )
            po.status = (
                POStatus.RECEIVED if all_settled
                else POStatus.PARTIAL if any_activity
                else POStatus.SENT
            )

        receipt.reversed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        receipt.reversed_by_id = self.current_user_id
        receipt.reversal_reason = reason

        self.audit(
            entity_type=EntityType.PO_RECEIPT,
            entity_id=receipt.id,
            action=AuditAction.STATUS_CHANGED,
            old_value={"reversed": False},
            new_value={"reversed": True, "reason": reason, "cost_notes": cost_notes},
            notes=f"Reversed receipt #{receipt.id}: {reason}",
        )
        self.db.commit()
        return {"receipt_id": receipt.id, "cost_notes": cost_notes}

    def cancel(self, po_id: int) -> None:
        """
        Cancel a PO. Raises ValueError if already BILLED (3-way match complete).
        If the PO had on-order stock outstanding (SENT, or PARTIAL with some goods
        still unreceived), reverses the qty_on_order increment for the *outstanding*
        portion of each product line so inventory counts stay accurate.
        Idempotent: calling on an already-CANCELLED PO is a no-op.
        """
        po = self._get_po_or_404(po_id)
        if po.status == POStatus.BILLED:
            raise ValueError(
                "Cannot cancel a billed PO — the vendor bill has already been reconciled."
            )
        if po.status == POStatus.CANCELLED:
            return  # idempotent

        old_status = po.status

        # Reverse on-order counts for the still-outstanding qty. For a SENT PO with
        # no receipts/cancellations this is the full qty_ordered; for a PARTIAL PO
        # (some goods already received) only the unreceived remainder is reversed,
        # since received qty already moved out of qty_on_order at receipt time.
        if old_status in (POStatus.SENT, POStatus.PARTIAL):
            for line in po.lines:
                if not line.product_id:
                    continue
                outstanding = line.qty_ordered - line.qty_received - line.qty_cancelled
                if outstanding > 0:
                    product = self.db.query(Product).filter(Product.id == line.product_id).first()
                    if product:
                        product.qty_on_order = max(0, product.qty_on_order - outstanding)

        po.status = POStatus.CANCELLED
        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po_id,
            action=AuditAction.STATUS_CHANGED,
            old_value=old_status,
            new_value=POStatus.CANCELLED,
        )
        self.db.commit()

    def cancel_line(self, po_line_id: int, reason: str = "") -> POLine:
        """
        Cancel the outstanding (unreceived) qty on a single PO line.

        Sets qty_cancelled = qty_ordered - qty_received so the line counts as
        settled for the "all received?" check. Reduces product.qty_on_order by
        the cancelled qty. If all PO lines are now settled, marks the PO RECEIVED.
        """
        po_line = self.db.query(POLine).filter(POLine.id == po_line_id).first()
        if po_line is None:
            raise ValueError(f"POLine {po_line_id} not found")

        po = po_line.po
        if po.status not in (POStatus.SENT, POStatus.PARTIAL):
            raise ValueError(
                f"Cannot cancel a line on a PO with status '{po.status}'. "
                f"Only SENT or PARTIAL POs allow line cancellation."
            )

        outstanding = po_line.qty_ordered - po_line.qty_received - po_line.qty_cancelled
        if outstanding <= 0:
            raise ValueError("Line has no outstanding qty to cancel.")

        # Cancel the outstanding qty
        po_line.qty_cancelled += outstanding
        po_line.cancel_reason = reason or "cancelled"
        po_line.cancelled_at = datetime.utcnow()
        po_line.cancelled_by_id = self.current_user_id

        # Reduce on-order count for the product
        if po_line.product_id:
            product = self.db.query(Product).filter(Product.id == po_line.product_id).first()
            if product:
                product.qty_on_order = max(0, product.qty_on_order - outstanding)

        # Check if all lines are now settled
        self.db.flush()
        all_settled = all(
            (ln.qty_received + ln.qty_cancelled) >= ln.qty_ordered
            for ln in po.lines
        )
        if all_settled:
            po.status = POStatus.RECEIVED

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po.id,
            action=AuditAction.EDITED,
            new_value={
                "action": "cancel_line",
                "po_line_id": po_line_id,
                "qty_cancelled": outstanding,
                "reason": reason,
            },
        )
        self.db.commit()
        return po_line

    def get_unreceived_lines(self, po_id: int) -> list[POLine]:
        """Return PO lines with qty_received < qty_ordered."""
        return (
            self.db.query(POLine)
            .filter(
                POLine.po_id == po_id,
                POLine.qty_received < POLine.qty_ordered,
            )
            .all()
        )

    # Statuses a PO can still be received against (mirrors the receive route
    # guard): verbal/phone orders receive directly, SENT and PARTIAL continue.
    RECEIVABLE_STATUSES = (POStatus.VERBAL_ORDER, POStatus.SENT, POStatus.PARTIAL)

    def get_open_receivable_lines_for_vendor(self, vendor_id: int) -> list[dict]:
        """Every open PO line a vendor could still deliver, grouped by PO — the
        data behind the multi-PO "Receive Shipment" screen.

        A PO is receivable in VERBAL_ORDER / SENT / PARTIAL status. A line is
        still OPEN while its outstanding quantity is positive
        (``qty_received + qty_cancelled < qty_ordered``) — so a line whose
        remainder was cancelled correctly drops off (consistent with the
        settlement test in ``create_receipt``). POs with no open line are
        omitted. Returns ``[{"po": PurchaseOrder, "open_lines": [POLine, ...]}]``
        ordered oldest-PO-first (receive the earliest orders first)."""
        pos = (
            self.db.query(PurchaseOrder)
            .filter(
                PurchaseOrder.vendor_id == vendor_id,
                PurchaseOrder.status.in_(self.RECEIVABLE_STATUSES),
            )
            .order_by(PurchaseOrder.created_at.asc(), PurchaseOrder.id.asc())
            .all()
        )
        out: list[dict] = []
        for po in pos:
            open_lines = [
                ln for ln in po.lines
                if (ln.qty_received + ln.qty_cancelled) < ln.qty_ordered
            ]
            if open_lines:
                out.append({"po": po, "open_lines": open_lines})
        return out

    # ── Billing (3-way match) ─────────────────────────────────────────────────

    def create_vendor_bill(
        self,
        po_id: int | None,
        vendor_id: int,
        bill_number: str,
        bill_date: datetime | None,
        due_date: datetime | None,
        lines: list[dict],  # [{po_line_id, qty_billed, unit_cost}]
        freight_amount: float | None = None,
    ) -> VendorBill:
        """
        Create a vendor bill from vendor invoice.
        Flags discrepancies on any line: billed qty > received qty, cumulative
        billed qty > PO-ordered qty (D-4b — over-receipt can't authorise paying
        beyond the order), or billed unit cost varying from the PO/receipt cost.
        Risk #5 — a clean (no-discrepancy) bill lands PENDING, not APPROVED: it
        still routes through AP for an explicit Approve (approve_bill) before it
        is payable / QBO-eligible. A discrepancy bill lands DISCREPANCY as before.

        freight_amount — vendor-billed freight charged on the SAME invoice as the
        parts (e.g. PAI). It is added to total_amount so the recorded payable
        matches the vendor's invoice; it does NOT affect the 3-way match (freight
        is a known PO cost, not a line variance). When None, it defaults to the
        PO's freight_in_cost NET of freight already billed on prior bills of that
        PO (so splitting a PO across bills never double-counts freight); pass 0.0
        explicitly when a separate carrier bills the freight.

        R1-5 — rejects a duplicate bill_number for the same vendor (case/
        whitespace-insensitive): the same vendor invoice entered twice would
        otherwise create two approvable bills (double-payment risk).
        """
        normalized_bill_no = (bill_number or "").strip().lower()
        if normalized_bill_no:
            existing_bills = (
                self.db.query(VendorBill)
                .filter(VendorBill.vendor_id == vendor_id)
                .all()
            )
            dup = next(
                (b for b in existing_bills
                 if (b.bill_number or "").strip().lower() == normalized_bill_no),
                None,
            )
            if dup:
                raise ValueError(
                    f"Bill #{dup.bill_number} already exists for this vendor "
                    f"(status: {dup.status}). The same vendor invoice cannot be "
                    "entered twice."
                )

        bill = VendorBill(
            po_id=po_id,
            vendor_id=vendor_id,
            bill_number=bill_number,
            bill_date=bill_date,
            due_date=due_date,
            status=VendorBillStatus.PENDING,
            qbo_sync_status=QBOSyncStatus.PENDING,
        )
        self.db.add(bill)
        self.db.flush()

        has_qty_over_received = False
        has_qty_over_ordered = False
        has_cost_discrepancy = False
        total = 0.0

        for line_data in lines:
            po_line_id = line_data.get("po_line_id")
            qty_billed = int(line_data.get("qty_billed", 0))
            unit_cost = float(line_data.get("unit_cost", 0.0))

            po_line = self.db.query(POLine).filter(POLine.id == po_line_id).first() if po_line_id else None

            bill_line = VendorBillLine(
                bill_id=bill.id,
                po_line_id=po_line_id,
                qty_billed=qty_billed,
                unit_cost=unit_cost,
            )
            self.db.add(bill_line)

            if po_line:
                po_line.qty_billed += qty_billed
                if qty_billed > po_line.qty_received:
                    has_qty_over_received = True
                # D-4b — over-receipt (R6, allow-with-warning) can lift
                # qty_received above the PO ceiling, so the received check alone
                # lets a bill over-pay what the PO authorized. Compare the
                # CUMULATIVE billed qty (after the += above) against qty_ordered —
                # cumulative, not this-bill, so split bills can't each slip under
                # the ceiling while together they over-bill the order.
                if po_line.qty_billed > po_line.qty_ordered:
                    has_qty_over_ordered = True
                # Money bug fix — a billed unit cost that differs from the PO/
                # receipt cost beyond tolerance is a discrepancy too (vendor
                # overcharge/undercharge). Must NOT silently auto-approve.
                if abs(unit_cost - po_line.unit_cost) >= COST_VARIANCE_TOLERANCE:
                    has_cost_discrepancy = True

            total += round(qty_billed * unit_cost, 2)

        # Resolve vendor-billed freight. Default = the PO's freight NET of what
        # prior bills of the same PO already carried, so a PO split across several
        # bills never double-counts freight (and a fully-billed PO defaults to 0).
        if freight_amount is None:
            freight = 0.0
            if po_id:
                bill_po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
                if bill_po:
                    already = sum(
                        float(b.freight_amount or 0.0)
                        for b in self.db.query(VendorBill)
                        .filter(VendorBill.po_id == po_id, VendorBill.id != bill.id)
                        .all()
                    )
                    freight = max(0.0, round(float(bill_po.freight_in_cost or 0.0) - already, 2))
        else:
            freight = max(0.0, round(float(freight_amount), 2))

        has_qty_discrepancy = has_qty_over_received or has_qty_over_ordered
        has_discrepancy = has_qty_discrepancy or has_cost_discrepancy
        bill.freight_amount = freight
        bill.total_amount = round(total + freight, 2)
        # Risk #5 — a clean 3-way match no longer auto-APPROVES. A matching bill
        # still routes through AP for an explicit human Approve (approve_bill,
        # gated on APPROVE_VENDOR_BILL) before it is payable / QBO-eligible. Only
        # the DISCREPANCY path is unchanged (it already required AP resolution).
        bill.status = VendorBillStatus.DISCREPANCY if has_discrepancy else VendorBillStatus.PENDING

        if has_discrepancy:
            po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first() if po_id else None
            po_number = po.po_number if po else str(po_id)
            # Describe whichever discrepancy/-ies fired so AP knows what to review.
            _reasons = []
            if has_qty_over_received:
                _reasons.append("billed qty exceeds received qty")
            if has_qty_over_ordered:
                _reasons.append("billed qty exceeds PO-ordered qty")
            if has_cost_discrepancy:
                _reasons.append("billed unit cost differs from PO/receipt cost")
            from app.services.notification_service import NotificationService
            NotificationService.build_bill_discrepancy(
                self.db,
                bill_id=bill.id,
                po_id=po_id or 0,
                po_number=po_number,
                detail=" and ".join(_reasons) + " on one or more lines",
            )
        # Risk #5 — the PO is NOT advanced to BILLED at bill creation anymore: a
        # clean bill now lands PENDING and only advances the PO on explicit
        # approval (approve_bill → _advance_po_billed_if_done). This keeps the AP
        # gate meaningful (PO stays RECEIVED/PARTIAL until the bill is approved).

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po_id or 0,
            action=AuditAction.CREATED,
            new_value={
                "bill_number": bill_number,
                "total": bill.total_amount,
                "freight": freight,
                "discrepancy": has_discrepancy,
            },
        )
        self.db.commit()
        return bill

    def _advance_po_billed_if_done(self, po: PurchaseOrder) -> None:
        """Advance PO to BILLED when every received line has been fully billed."""
        if po.status not in (POStatus.RECEIVED, POStatus.PARTIAL):
            return
        received_lines = [ln for ln in po.lines if ln.qty_received > 0]
        if not received_lines:
            return
        if all(ln.qty_billed >= ln.qty_received for ln in received_lines):
            po.status = POStatus.BILLED

    def _resync_po_billed_status(self, po: PurchaseOrder) -> None:
        """Re-derive a PO's billed status after a bill EDIT (Phase 2). Unlike
        _advance_po_billed_if_done (one-way, used at bill approval), this also rolls
        the PO back off BILLED when an edit drops billed qty below received — so a
        corrected bill can never leave a PO stuck 'billed' when it no longer is."""
        if po.status not in (POStatus.RECEIVED, POStatus.PARTIAL, POStatus.BILLED):
            return
        received_lines = [ln for ln in po.lines if ln.qty_received > 0]
        fully_billed = bool(received_lines) and all(
            ln.qty_billed >= ln.qty_received for ln in received_lines
        )
        if fully_billed:
            po.status = POStatus.BILLED
        elif po.status == POStatus.BILLED:
            # An edit pulled billed qty below received — back out of BILLED.
            po.status = (
                POStatus.RECEIVED
                if all(ln.qty_outstanding == 0 for ln in po.lines)
                else POStatus.PARTIAL
            )

    def edit_vendor_bill(
        self,
        bill_id: int,
        reason: str,
        header: dict | None = None,
        line_edits: list[dict] | None = None,
    ) -> VendorBill:
        """
        Phase 2 — correct a posted vendor bill after creation: header fields
        (bill_number / bill_date / due_date) and per-line qty_billed / unit_cost.
        A reason is required and the change is audited old→new.

        After applying edits the 3-way match is RE-VALIDATED (VendorBillLine.
        has_discrepancy is computed live from the PO line), so:
          * any new mismatch → status DISCREPANCY (must be resolved before approval);
          * an already-APPROVED bill that stays clean keeps its approval;
          * otherwise the bill sits at PENDING.
        The owning PO's billed status is re-derived (it can roll back off BILLED).

        QBO: if the bill was already pushed (qbo_id set), it is re-flagged
        qbo_sync_status=PENDING so the correction re-syncs (the update push itself
        is wired in Phase 3).

        PAID bills are not edited here — paying is terminal in this system; correct
        a paid bill with a vendor credit instead.
        """
        self.assert_can(Permission.APPROVE_VENDOR_BILL)
        if not reason.strip():
            raise ValueError("A reason is required to edit a vendor bill.")

        bill = self.db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        if bill is None:
            raise ValueError(f"VendorBill {bill_id} not found")
        if bill.status == VendorBillStatus.PAID:
            raise ValueError(
                "This bill is already paid — issue a vendor credit to adjust it."
            )

        before_status = bill.status
        before = {
            "bill_number": bill.bill_number,
            "total": bill.total_amount,
            "freight": bill.freight_amount,
            "status": before_status,
            "lines": {bl.id: {"qty": bl.qty_billed, "cost": bl.unit_cost} for bl in bill.lines},
        }

        header = header or {}
        if "bill_number" in header:
            new_no = (header["bill_number"] or "").strip() or None
            if new_no:
                normalized = new_no.lower()
                dup = (
                    self.db.query(VendorBill)
                    .filter(
                        VendorBill.vendor_id == bill.vendor_id,
                        VendorBill.id != bill.id,
                    )
                    .all()
                )
                if any((b.bill_number or "").strip().lower() == normalized for b in dup):
                    raise ValueError(
                        f"Bill #{new_no} already exists for this vendor — a vendor "
                        "invoice number cannot be reused."
                    )
            bill.bill_number = new_no
        if "bill_date" in header:
            bill.bill_date = header["bill_date"]
        if "due_date" in header:
            bill.due_date = header["due_date"]
        if header.get("freight_amount") is not None:
            f = round(float(header["freight_amount"]), 2)
            if f < 0:
                raise ValueError("Freight cannot be negative.")
            bill.freight_amount = f

        affected_po_lines: dict[int, POLine] = {}
        for ed in (line_edits or []):
            bl = next((b for b in bill.lines if b.id == ed.get("bill_line_id")), None)
            if bl is None:
                raise ValueError(f"Bill line {ed.get('bill_line_id')} not found on this bill.")
            if "qty_billed" in ed:
                q = int(ed["qty_billed"])
                if q < 0:
                    raise ValueError("Billed qty cannot be negative.")
                bl.qty_billed = q
            if "unit_cost" in ed:
                c = round(float(ed["unit_cost"]), 2)
                if c < 0:
                    raise ValueError("Cost cannot be negative.")
                bl.unit_cost = c
            if bl.po_line is not None:
                affected_po_lines[bl.po_line.id] = bl.po_line

        # Cumulative billed qty per PO line is the sum across ALL its bill lines
        # (a line can be split across bills) — recompute from source, never +=.
        for pol in affected_po_lines.values():
            pol.qty_billed = sum(b.qty_billed for b in pol.bill_lines)

        self.db.flush()

        bill.total_amount = round(
            sum(bl.line_total for bl in bill.lines) + float(bill.freight_amount or 0.0), 2
        )
        if bill.has_discrepancy:
            bill.status = VendorBillStatus.DISCREPANCY
        elif before_status == VendorBillStatus.APPROVED:
            bill.status = VendorBillStatus.APPROVED  # re-approve if still clean
        else:
            bill.status = VendorBillStatus.PENDING

        if bill.po_id:
            po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == bill.po_id).first()
            if po:
                self._resync_po_billed_status(po)

        # Re-sync to QBO only if it was already pushed; the update itself is Phase 3.
        if bill.qbo_id:
            bill.qbo_sync_status = QBOSyncStatus.PENDING

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=bill.po_id or 0,
            action=AuditAction.EDITED,
            old_value=before,
            new_value={
                "bill_id": bill_id,
                "bill_number": bill.bill_number,
                "total": bill.total_amount,
                "freight": bill.freight_amount,
                "status": bill.status,
            },
            notes=reason.strip(),
        )
        self.db.commit()
        return bill

    def approve_bill(self, bill_id: int, override_reason: str = "") -> None:
        """
        Approve vendor bill after discrepancy review. Marks for QBO sync.

        Gate rules:
        - Requires APPROVE_VENDOR_BILL permission.
        - Bills in DISCREPANCY status cannot be approved through the normal path:
          all flagged match lines must first be resolved via resolve_match_line()
          or create_match_vendor_credit(), which transitions the bill
          DISCREPANCY → PENDING when the gate opens.
        - Resolving lines opens the gate; it does NOT approve the bill.
        - override_reason is the documented "approve anyway" escape hatch: a
          DISCREPANCY bill may be approved as-is (accepting the variance) ONLY
          when a non-empty reason is supplied. The override is recorded in the
          audit trail. Without a reason, the discrepancy gate stands and the
          existing error is raised.
        """
        self.assert_can(Permission.APPROVE_VENDOR_BILL)

        bill = self.db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        if bill is None:
            raise ValueError(f"VendorBill {bill_id} not found")

        override_reason = (override_reason or "").strip()
        is_override = False
        if bill.status == VendorBillStatus.DISCREPANCY:
            if not override_reason:
                raise ValueError(
                    "This bill has unresolved match discrepancies. "
                    "Resolve each flagged line (accept, reject, credit, or clear) "
                    "before approving."
                )
            is_override = True
        if bill.status in (VendorBillStatus.APPROVED, VendorBillStatus.PAID):
            raise ValueError(f"Bill is already {bill.status}.")

        bill.status = VendorBillStatus.APPROVED
        bill.qbo_sync_status = QBOSyncStatus.PENDING
        if bill.po_id:
            po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == bill.po_id).first()
            if po:
                self._advance_po_billed_if_done(po)

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=bill.po_id or 0,
            action=AuditAction.STATUS_CHANGED,
            new_value={
                "bill_id": bill_id,
                "status": VendorBillStatus.APPROVED,
                "discrepancy_override": is_override,
                "override_reason": override_reason or None,
            },
            notes=(f"Approved over discrepancy: {override_reason}" if is_override else None),
        )
        self.db.commit()

    def mark_bill_paid(self, bill_id: int) -> VendorBill:
        """
        Mark an APPROVED bill as PAID (R1-12 — AP reconciliation).

        Records that the vendor was paid outside the system; no money moves
        here. Only APPROVED bills are payable — PENDING/DISCREPANCY must clear
        the approval gate first, and PAID is terminal. The model has no paid-at
        column, so the payment timestamp lives in the audit row.
        """
        self.assert_can(Permission.APPROVE_VENDOR_BILL)

        bill = self.db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        if bill is None:
            raise ValueError(f"VendorBill {bill_id} not found")
        if bill.status != VendorBillStatus.APPROVED:
            raise ValueError(
                f"Only approved bills can be marked paid (bill is {bill.status})."
            )

        bill.status = VendorBillStatus.PAID
        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=bill.po_id or 0,
            action=AuditAction.STATUS_CHANGED,
            new_value={
                "bill_id": bill_id,
                "status": VendorBillStatus.PAID,
                "paid_at": datetime.utcnow().isoformat(),
            },
        )
        self.db.commit()
        return bill

    # ── 3-Way Match: AP resolution ────────────────────────────────────────────

    def resolve_match_line(
        self,
        po_line_id: int,
        decision: str,           # MatchResolution value
        reason: str = "",
    ) -> POLine:
        """
        Record an AP resolution decision on a flagged PO line.

        Valid decisions: accepted, rejected, on_hold, cleared.
        (Use create_match_vendor_credit for the 'credited' path — it sets
        match_resolution=CREDITED after creating the VCM.)

        Rules:
        - Requires Permission.APPROVE_VENDOR_BILL.
        - 'rejected' and 'cleared' require a non-empty reason.
        - Cannot set to 'credited' directly; use create_match_vendor_credit.
        - Re-deciding an already-resolved line is allowed (AP may change mind
          as long as the bill hasn't been approved yet).
        - If resolving leaves no unresolved flags on the associated bill and
          that bill is DISCREPANCY, opens the bill for explicit AP approval
          (does NOT auto-approve — _advance_bill_after_match only drops the
          block; AP still clicks Approve).
        """
        self.assert_can(Permission.APPROVE_VENDOR_BILL)

        valid_direct = {
            MatchResolution.ACCEPTED,
            MatchResolution.REJECTED,
            MatchResolution.ON_HOLD,
            MatchResolution.CLEARED,
        }
        if decision not in valid_direct:
            raise ValueError(
                f"Invalid resolution '{decision}'. "
                f"Use one of {sorted(valid_direct)}. "
                f"For vendor credit, call create_match_vendor_credit()."
            )
        if decision in self._REASON_REQUIRED and not reason.strip():
            raise ValueError(f"A reason is required when resolution is '{decision}'.")

        line = self.db.query(POLine).filter(POLine.id == po_line_id).first()
        if line is None:
            raise ValueError(f"POLine {po_line_id} not found")

        po = line.po
        old_resolution = line.match_resolution

        # Capture the raw match state before we write (for audit old_value)
        raw = self.compute_match_line(line)

        line.match_resolution = decision
        line.match_resolution_reason = reason.strip() or None
        line.match_resolved_by_id = self.current_user_id
        line.match_resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po.id,
            action=AuditAction.MATCH_RESOLVED,
            old_value={
                "resolution": old_resolution,
                "raw_state": raw["state"],
                "qty_var": raw["qty_var"],
                "cost_var": raw["cost_var"],
            },
            new_value={
                "po_line_id": po_line_id,
                "resolution": decision,
                "reason": reason.strip() or None,
            },
            notes=reason.strip() or None,
        )

        self.db.flush()

        # Check whether all flagged lines on associated DISCREPANCY bills are
        # now resolved so the explicit-approve gate can open.
        for bill in po.bills:
            if bill.status == VendorBillStatus.DISCREPANCY:
                self._advance_bill_after_match(bill)

        self.db.commit()
        return line

    def correct_match_line(
        self,
        po_line_id: int,
        bill_id: int,
        new_po_unit_cost: float | None = None,
        new_billed_qty: int | None = None,
        new_billed_unit_cost: float | None = None,
        reason: str = "",
    ) -> POLine:
        """
        Correct the actual PO/bill numbers on a flagged match line so the PO and
        bill reconcile, then clear the flag (match_resolution = CORRECTED).

        Distinct from resolve_match_line, which only records an AP *decision* and
        leaves the divergent numbers in place. This edits the data so the variance
        genuinely goes away:
          - new_po_unit_cost     → POLine.unit_cost (the price the PO authorized)
          - new_billed_qty       → the bill line's qty_billed (PO line's cumulative
                                    qty_billed is recomputed from all bill lines)
          - new_billed_unit_cost → the bill line's unit_cost
        The bill total is recomputed after any bill-side edit.

        Rules:
        - Requires Permission.APPROVE_VENDOR_BILL.
        - `reason` is mandatory — this is a money-path edit; record why.
        - MUST reconcile: after the edits the line must match — cumulative billed
          qty <= received AND <= ordered, AND the averaged billed unit cost is
          within COST_VARIANCE_TOLERANCE of the PO unit cost. If it does not
          reconcile, NOTHING is written and a ValueError explains the residual
          (the caller can fall back to Accept / Create-Credit instead).
        - Records-only: does NOT re-cost already-received inventory. The moving-
          average cost booked at receipt is intentionally left untouched.
        - Opens the explicit-approve gate (DISCREPANCY -> PENDING) when this clears
          the last live flag on the bill. Does NOT approve the bill.
        """
        self.assert_can(Permission.APPROVE_VENDOR_BILL)

        if not reason.strip():
            raise ValueError("A reason is required to correct a match line.")

        line = self.db.query(POLine).filter(POLine.id == po_line_id).first()
        if line is None:
            raise ValueError(f"POLine {po_line_id} not found")

        bill = self.db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        if bill is None:
            raise ValueError(f"VendorBill {bill_id} not found")

        bill_line = next(
            (bl for bl in bill.lines if bl.po_line_id == po_line_id), None
        )
        if bill_line is None:
            raise ValueError(
                f"Bill {bill_id} has no line for PO line {po_line_id}."
            )

        # ── Capture before-state (audit trail) ──────────────────────────────────
        before = {
            "po_unit_cost":     line.unit_cost,
            "billed_qty":       bill_line.qty_billed,
            "billed_unit_cost": bill_line.unit_cost,
            "po_qty_billed":    line.qty_billed,
            "bill_total":       bill.total_amount,
        }

        # ── Compute prospective values WITHOUT mutating, then gate ──────────────
        prospective_po_cost = (
            round(float(new_po_unit_cost), 2)
            if new_po_unit_cost is not None else line.unit_cost
        )
        prospective_bill_qty = (
            int(new_billed_qty)
            if new_billed_qty is not None else bill_line.qty_billed
        )
        prospective_bill_cost = (
            round(float(new_billed_unit_cost), 2)
            if new_billed_unit_cost is not None else bill_line.unit_cost
        )
        if prospective_bill_qty < 0:
            raise ValueError("Billed qty cannot be negative.")
        if prospective_po_cost < 0 or prospective_bill_cost < 0:
            raise ValueError("Costs cannot be negative.")

        # Recompute cumulative billed qty and the averaged billed unit cost across
        # ALL of this PO line's bill lines, substituting the edited values for the
        # target line. (Invariant: line.qty_billed == sum of its bill_lines.qty_billed.)
        cumulative_qty = 0
        total_amt = 0.0
        for bl in line.bill_lines:
            q = prospective_bill_qty if bl.id == bill_line.id else bl.qty_billed
            c = prospective_bill_cost if bl.id == bill_line.id else bl.unit_cost
            cumulative_qty += q
            total_amt += q * c
        avg_billed_cost = (
            round(total_amt / cumulative_qty, 2) if cumulative_qty > 0 else None
        )

        # ── Must-match gate ─────────────────────────────────────────────────────
        problems: list[str] = []
        if cumulative_qty > line.qty_received:
            problems.append(
                f"billed qty {cumulative_qty} still exceeds received {line.qty_received}"
            )
        if cumulative_qty > line.qty_ordered:
            problems.append(
                f"billed qty {cumulative_qty} still exceeds PO-ordered {line.qty_ordered}"
            )
        if (
            avg_billed_cost is not None
            and abs(avg_billed_cost - prospective_po_cost) >= COST_VARIANCE_TOLERANCE
        ):
            problems.append(
                f"billed cost ${avg_billed_cost:.2f} still differs from "
                f"PO cost ${prospective_po_cost:.2f}"
            )
        if problems:
            raise ValueError(
                "Correction does not reconcile: "
                + "; ".join(problems)
                + ". Adjust the numbers so the PO and bill match, "
                "or use Accept / Create Credit to keep the variance."
            )

        # ── Apply (gate passed) ─────────────────────────────────────────────────
        line.unit_cost = prospective_po_cost
        bill_line.qty_billed = prospective_bill_qty
        bill_line.unit_cost = prospective_bill_cost
        line.qty_billed = cumulative_qty

        # Bill-side edits change what we owe — recompute the bill total from lines
        # (vendor-billed freight rides on top, unchanged by a line correction).
        bill.total_amount = round(
            sum(bl.qty_billed * bl.unit_cost for bl in bill.lines)
            + float(bill.freight_amount or 0.0), 2
        )

        line.match_resolution = MatchResolution.CORRECTED
        line.match_resolution_reason = reason.strip()
        line.match_resolved_by_id = self.current_user_id
        line.match_resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)

        after = {
            "po_unit_cost":     line.unit_cost,
            "billed_qty":       bill_line.qty_billed,
            "billed_unit_cost": bill_line.unit_cost,
            "po_qty_billed":    line.qty_billed,
            "bill_total":       bill.total_amount,
        }

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=line.po_id,
            action=AuditAction.MATCH_CORRECTED,
            old_value=before,
            new_value={"po_line_id": po_line_id, "bill_id": bill_id, **after},
            notes=reason.strip(),
        )

        self.db.flush()

        # Open the approve gate if this cleared the last live flag on the bill.
        if bill.status == VendorBillStatus.DISCREPANCY:
            self._advance_bill_after_match(bill)

        self.db.commit()
        return line

    def create_match_vendor_credit(
        self,
        po_line_id: int,
        amount: float | None = None,
        trigger: str = VendorCreditMemoTrigger.OVERCHARGE,
        reason: str = "",
        apply_now: bool = False,
    ) -> object:
        """
        Create a VendorCreditMemo for a flagged PO line and mark the line
        as match_resolution=CREDITED.

        Requires Permission.APPROVE_VENDOR_BILL + Permission.ISSUE_CREDIT_MEMO.

        If amount is None, computes the suggested overage:
          - over_billed: (qty_billed - qty_received) × billed_unit_cost
          - cost_variance (vendor overcharged): (billed_cost - ordered_cost) × qty_billed
        Caller may pass an explicit amount to override.

        If apply_now=True, immediately allocates the VCM against the first
        DISCREPANCY bill on the PO (requires a bill to exist).

        Returns the created VendorCreditMemo.
        """
        self.assert_can(Permission.APPROVE_VENDOR_BILL)
        self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        line = self.db.query(POLine).filter(POLine.id == po_line_id).first()
        if line is None:
            raise ValueError(f"POLine {po_line_id} not found")

        po = line.po

        # Compute suggested amount if not supplied
        raw = self.compute_match_line(line)

        if amount is None:
            if raw["state"] == "over_billed":
                billed_cost = raw["billed_cost"] or 0.0
                amount = round((raw["qty_var"]) * billed_cost, 2)
            elif raw["state"] == "cost_variance" and raw["cost_var"] > 0:
                amount = round(raw["cost_var"] * raw["billed_qty"], 2)
            else:
                raise ValueError(
                    "Cannot auto-compute credit amount: line is not over_billed or "
                    "cost_variance (vendor-overcharged). Pass an explicit amount."
                )

        if amount <= 0:
            raise ValueError("Vendor credit amount must be positive.")

        # Find the related bill for back-reference + optional apply_now
        related_bill = next(
            (b for b in po.bills if b.status == VendorBillStatus.DISCREPANCY),
            None,
        )
        original_bill_id = related_bill.id if related_bill else None

        from app.services.vendor_credit_service import VendorCreditService
        vcm_svc = VendorCreditService(self.db, self.current_user_id)
        vcm = vcm_svc.create_vendor_credit_memo(
            vendor_id=po.vendor_id,
            trigger_type=trigger,
            amount=amount,
            original_vendor_bill_id=original_bill_id,
            reason=reason or f"Match variance on PO {po.po_number}, line {po_line_id}",
        )

        if apply_now and related_bill is not None:
            vcm_svc.apply_vendor_credit_memo(
                vcm_id=vcm.id,
                vendor_bill_id=related_bill.id,
                amount=amount,
            )

        # Mark the line as credited and link the VCM
        old_resolution = line.match_resolution
        line.match_resolution = MatchResolution.CREDITED
        line.match_resolution_vcm_id = vcm.id
        line.match_resolution_reason = reason.strip() or None
        line.match_resolved_by_id = self.current_user_id
        line.match_resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)

        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po.id,
            action=AuditAction.MATCH_RESOLVED,
            old_value={
                "resolution": old_resolution,
                "raw_state": raw["state"],
                "qty_var": raw["qty_var"],
                "cost_var": raw["cost_var"],
            },
            new_value={
                "po_line_id": po_line_id,
                "resolution": MatchResolution.CREDITED,
                "vcm_id": vcm.id,
                "vcm_number": vcm.vcm_number,
                "amount": amount,
                "apply_now": apply_now,
            },
            notes=reason.strip() or f"VCM {vcm.vcm_number} created for match variance",
        )

        self.db.flush()

        for bill in po.bills:
            if bill.status == VendorBillStatus.DISCREPANCY:
                self._advance_bill_after_match(bill)

        self.db.commit()
        return vcm

    def _advance_bill_after_match(self, bill: VendorBill) -> None:
        """
        Open the explicit-approve gate when every flagged line on a DISCREPANCY
        bill has been resolved to a terminal or on_hold state.

        Does NOT auto-approve. AP still calls approve_bill() as the final step.
        Transitions bill DISCREPANCY → PENDING (ready-to-approve) only.

        Terminal: accepted, credited, cleared.
        on_hold counts as resolved for gating purposes (AP has parked it).
        rejected: AP is still disputing — bill stays DISCREPANCY.
        unresolved: flag still live — bill stays DISCREPANCY.
        """
        if bill.status != VendorBillStatus.DISCREPANCY:
            return

        # Collect all PO lines whose bill_lines are on this bill
        flagged_line_ids = {
            bl.po_line_id for bl in bill.lines if bl.po_line_id is not None
        }
        if not flagged_line_ids:
            return

        po_lines = (
            self.db.query(POLine)
            .filter(POLine.id.in_(flagged_line_ids))
            .all()
        )

        gate_resolutions = self._TERMINAL_RESOLUTIONS | {MatchResolution.ON_HOLD}
        all_gated = all(ln.match_resolution in gate_resolutions for ln in po_lines)

        if all_gated:
            # Transition to PENDING so approve_bill() becomes available
            bill.status = VendorBillStatus.PENDING
            self.db.flush()

    def get_bills_pending_approval(self) -> list[VendorBill]:
        return (
            self.db.query(VendorBill)
            .filter(VendorBill.status.in_([
                VendorBillStatus.DISCREPANCY,
                VendorBillStatus.PENDING,
            ]))
            .order_by(VendorBill.created_at)
            .all()
        )

    # ── QBO Sync ──────────────────────────────────────────────────────────────

    def mark_bill_synced(self, bill_id: int, qbo_id: str) -> None:
        bill = self.db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        if bill:
            bill.qbo_id = qbo_id
            bill.qbo_sync_status = QBOSyncStatus.SYNCED
            bill.qbo_last_synced_at = datetime.utcnow()
            bill.qbo_sync_error = None
            self.db.commit()

    def mark_bill_sync_failed(self, bill_id: int, error: str) -> None:
        bill = self.db.query(VendorBill).filter(VendorBill.id == bill_id).first()
        if bill:
            bill.qbo_sync_status = QBOSyncStatus.ERROR
            bill.qbo_sync_error = error
            bill.qbo_sync_retry_count += 1
            self.db.commit()

    # ── Private helpers ───────────────────────────────────────────────────────

    def save_header(self, po_id: int, data: dict, submitted_updated_at: str | None = None) -> None:
        """Autosave PO header fields. Blocked only on BILLED/CANCELLED."""
        po = self._get_po_or_404(po_id)
        self.check_version(po, submitted_updated_at)
        if po.status in (POStatus.BILLED, POStatus.CANCELLED):
            raise ValueError(f"Cannot edit a {po.status} PO")
        for field in ("notes", "internal_notes", "vendor_confirmation_number"):
            if field in data:
                setattr(po, field, data[field])
        if "vendor_confirmed" in data:
            po.vendor_confirmed = bool(data["vendor_confirmed"])
        if "freight_in_cost" in data:
            new_freight = float(data["freight_in_cost"] or 0.0)
            old_freight = float(po.freight_in_cost or 0.0)
            # Only act on a real change — the header autosave resends the current
            # freight on every field blur, so an unchanged value must not write an
            # audit row.
            if abs(new_freight - old_freight) >= 0.005:
                po.freight_in_cost = new_freight
                # Freight is a money-path field: it lands in COGS at receipt and
                # feeds the vendor bill. A correction made after the PO is
                # committed (SENT onward) is audited who/when/old→new. NOTE: when
                # the PO's units are already received, this corrects the PO/bill
                # figure only — the on-hand COGS true-up rides with the bill-
                # correction phase, not this autosave.
                if po.status not in (POStatus.DRAFT, POStatus.VERBAL_ORDER):
                    self.audit(
                        entity_type=EntityType.PURCHASE_ORDER,
                        entity_id=po.id,
                        action=AuditAction.EDITED,
                        old_value=f"freight_in_cost={old_freight:.2f}",
                        new_value=f"freight_in_cost={new_freight:.2f}",
                        notes=f"Freight adjusted on {po.status} PO {po.po_number}",
                    )
        if "expected_at" in data:
            po.expected_at = data["expected_at"]
        # ── Bill-to / ship-to. ship_to_type owns which fields apply; the others
        # are cleared so a stale drop-ship address can't linger after a switch.
        if "bill_to_location_id" in data:
            po.bill_to_location_id = data["bill_to_location_id"]
        if "ship_to_type" in data:
            stype = data["ship_to_type"] or POShipToType.LOCATION
            po.ship_to_type = stype
            po.ship_to_location_id = (
                data.get("ship_to_location_id") if stype == POShipToType.LOCATION else None
            )
            po.ship_to_snapshot = (
                data.get("ship_to_snapshot") if stype == POShipToType.AD_HOC else None
            )
            if stype == POShipToType.DROP_SHIP:
                po.is_drop_ship = True
                po.drop_ship_customer_id = data.get("drop_ship_customer_id")
                po.drop_ship_address_id = data.get("drop_ship_address_id")
            else:
                po.is_drop_ship = False
                po.drop_ship_customer_id = None
                po.drop_ship_address_id = None
        self.db.commit()

    def update_line(self, line_id: int, data: dict) -> POLine:
        """Update a line's description/qty/cost/core. Only on DRAFT or VERBAL_ORDER."""
        line = self.db.query(POLine).filter(POLine.id == line_id).first()
        if line is None:
            raise ValueError(f"Line {line_id} not found")
        po = self._get_po_or_404(line.po_id)
        if po.status not in (POStatus.DRAFT, POStatus.VERBAL_ORDER):
            raise ValueError(f"Cannot edit lines on a {po.status} PO")
        # A manual unit_cost edit overrides any applied volume discount on THIS
        # line: drop its list snapshot so "Remove discount" can't later clobber
        # the hand-typed cost. Re-clicking Apply will re-snapshot at the new value.
        if "unit_cost" in data and line.list_unit_cost is not None:
            line.list_unit_cost = None
        for field in ("description", "qty_ordered", "unit_cost", "core_charge_per_unit", "notes"):
            if field in data:
                setattr(line, field, data[field])
        self.db.flush()
        # C9 — qty edit on a verbal-order line changes on-order; recompute.
        self._resync_on_order_if_verbal(po, line.product_id)
        self.db.commit()
        return line

    def correct_po_line_cost(self, line_id: int, new_unit_cost: float, reason: str) -> POLine:
        """
        Phase 2 — correct a PO line's unit cost AFTER the order is committed
        (SENT / PARTIAL / RECEIVED), e.g. a mis-keyed cost found post-send. A
        reason is required and the change is audited old→new.

        Records-only, by deliberate design (mirrors correct_match_line, which is
        documented "does NOT re-cost already-received inventory"): the moving-
        average cost booked at receipt is left untouched, because the receipt path
        is the only valid writer of product.cost (R11 Option A — see
        InventoryService._apply_moving_average_cost). The correction fixes the PO
        record and flows to the ledger through the vendor bill (and QBO), which is
        the books of record — not through a second, lot-less re-costing of on-hand
        inventory.

        Routing guards keep the money model coherent:
          * DRAFT / VERBAL_ORDER → use update_line (free editing before commit).
          * BILLED / CANCELLED   → blocked here.
          * A line already on a vendor bill → must go through correct_match_line so
            the 3-way-match invariant (PO cost == billed cost) can't be broken
            behind AP's back.
        """
        self.assert_can(Permission.APPROVE_VENDOR_BILL)
        if not reason.strip():
            raise ValueError("A reason is required to correct a PO cost.")
        line = self.db.query(POLine).filter(POLine.id == line_id).first()
        if line is None:
            raise ValueError(f"Line {line_id} not found")
        po = self._get_po_or_404(line.po_id)
        if po.status in (POStatus.DRAFT, POStatus.VERBAL_ORDER):
            raise ValueError("Use the line editor on a draft/verbal PO.")
        if po.status == POStatus.CANCELLED:
            raise ValueError("Cannot correct a line on a cancelled PO.")
        # A bill flips the PO to BILLED; either way, once a line carries bill lines
        # the cost must be fixed through Correct & Reconcile so the PO and the bill
        # stay matched (the 3-way-match invariant) rather than silently diverging.
        if po.status == POStatus.BILLED or line.bill_lines:
            raise ValueError(
                "This line is already on a vendor bill — use Correct & Reconcile "
                "on the bill so the PO and bill stay matched."
            )
        new_cost = round(float(new_unit_cost), 2)
        if new_cost < 0:
            raise ValueError("Cost cannot be negative.")
        old_cost = line.unit_cost
        if abs(new_cost - old_cost) < 0.005:
            return line  # no-op — nothing changed, write no audit row

        # A manual correction overrides any applied volume-discount snapshot on
        # this line (same rule as update_line) so "Remove discount" can't later
        # clobber the corrected cost.
        if line.list_unit_cost is not None:
            line.list_unit_cost = None
        line.unit_cost = new_cost

        note = reason.strip()
        if line.qty_received > 0:
            note += (
                f" [records-only: {line.qty_received} unit(s) already received keep "
                "their booked cost; the correction flows to the vendor bill / QBO]"
            )
        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po.id,
            action=AuditAction.EDITED,
            old_value=f"line[{line_id}].unit_cost={old_cost:.2f}",
            new_value=f"line[{line_id}].unit_cost={new_cost:.2f}",
            notes=note,
        )
        self.db.commit()
        return line

    def delete_line(self, line_id: int) -> None:
        """Delete a line. Only on DRAFT or VERBAL_ORDER."""
        line = self.db.query(POLine).filter(POLine.id == line_id).first()
        if line is None:
            raise ValueError(f"Line {line_id} not found")
        po = self._get_po_or_404(line.po_id)
        if po.status not in (POStatus.DRAFT, POStatus.VERBAL_ORDER):
            raise ValueError(f"Cannot delete lines on a {po.status} PO")
        _pid = line.product_id  # capture before delete for the verbal resync
        self.db.delete(line)
        self.db.flush()
        # C9 — removing a verbal-order line drops it from on-order.
        self._resync_on_order_if_verbal(po, _pid)
        self.db.commit()

    # ── Vendor volume discount ────────────────────────────────────────────────
    # A vendor agrees to discount every part when a PO crosses a spend threshold
    # (e.g. PAI: 5% off when the order exceeds $5,000). The rule lives on the
    # vendor as a VendorProgram (threshold_amount + discount_percent). Applying it
    # LOWERS each line's unit_cost (parts only — core charges and freight-in are
    # left alone), snapshotting the pre-discount price in POLine.list_unit_cost so
    # it is fully reversible. Lowering the real cost is deliberate: it flows into
    # landed cost / resale margin AND keeps the 3-way match clean when the vendor
    # bills the discounted price (POLine.unit_cost already matches).

    @staticmethod
    def _list_subtotal(po: PurchaseOrder) -> float:
        """Pre-discount parts subtotal — uses each line's list price when a
        discount is applied, else its current unit_cost. Eligibility is measured
        against this so applying the discount can never drop the PO back under
        its own threshold and un-qualify itself."""
        return round(
            sum(
                (ln.list_unit_cost if ln.list_unit_cost is not None else ln.unit_cost)
                * ln.qty_ordered
                for ln in po.lines
            ),
            2,
        )

    @staticmethod
    def eligible_volume_program(po: PurchaseOrder):
        """The vendor's best active volume-discount program whose threshold the
        PO's list subtotal meets, or None. 'Best' = highest discount_percent.
        Static (reads only po + po.vendor.programs) so the PO workspace context
        can call it for the nudge without spinning up a service."""
        list_subtotal = POService._list_subtotal(po)
        best = None
        for prog in (po.vendor.programs if po.vendor else []):
            if not prog.is_active:
                continue
            pct = prog.discount_percent or 0.0
            if pct <= 0:
                continue
            if list_subtotal + 1e-6 < (prog.threshold_amount or 0.0):
                continue
            if best is None or pct > (best.discount_percent or 0.0):
                best = prog
        return best

    def apply_volume_discount(self, po_id: int) -> PurchaseOrder:
        """Apply the vendor's eligible volume discount to every line. Idempotent
        re-apply recomputes from the snapshotted list price, so it also sweeps in
        any lines added since the discount was first applied."""
        po = self._get_po_or_404(po_id)
        if po.status not in (POStatus.DRAFT, POStatus.VERBAL_ORDER):
            raise ValueError(f"Cannot apply a discount to a {po.status} PO")
        program = self.eligible_volume_program(po)
        if program is None:
            raise ValueError("No eligible vendor volume discount for this PO")
        pct = float(program.discount_percent or 0.0)
        if pct <= 0:
            raise ValueError("Vendor program has no discount percent")
        for line in po.lines:
            # Snapshot the list price once; re-apply recomputes from the snapshot.
            if line.list_unit_cost is None:
                line.list_unit_cost = line.unit_cost
            line.unit_cost = round(line.list_unit_cost * (1 - pct / 100.0), 2)
        po.volume_discount_pct = pct
        self.db.flush()
        self.audit(
            entity_type=EntityType.PURCHASE_ORDER,
            entity_id=po_id,
            action=AuditAction.EDITED,
            old_value="volume discount: none",
            new_value=f"volume discount {pct:g}% applied (parts only)",
        )
        self.db.commit()
        return po

    def remove_volume_discount(self, po_id: int) -> PurchaseOrder:
        """Restore every discounted line to its pre-discount list price."""
        po = self._get_po_or_404(po_id)
        if po.status not in (POStatus.DRAFT, POStatus.VERBAL_ORDER):
            raise ValueError(f"Cannot change the discount on a {po.status} PO")
        had = po.volume_discount_pct or 0.0
        for line in po.lines:
            if line.list_unit_cost is not None:
                line.unit_cost = line.list_unit_cost
                line.list_unit_cost = None
        po.volume_discount_pct = 0.0
        self.db.flush()
        if had:
            self.audit(
                entity_type=EntityType.PURCHASE_ORDER,
                entity_id=po_id,
                action=AuditAction.EDITED,
                old_value=f"volume discount {had:g}% applied",
                new_value="volume discount: removed",
            )
        self.db.commit()
        return po

    def _get_po_or_404(self, po_id: int) -> PurchaseOrder:
        po = self.db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
        if po is None:
            raise ValueError(f"PurchaseOrder {po_id} not found")
        return po
