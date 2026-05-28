"""
app/services/core_service.py
==============================
Core charge lifecycle — customer cores and vendor core returns.

Two separate charge layers:
  - customer_unit_charge: what JAKS bills the customer (on invoice)
  - vendor_unit_charge:   what JAKS paid the vendor (returned for credit)

Core margin = customer_unit_charge - vendor_unit_charge (JAKS keeps this).

Key rules:
  - Core created at invoice finalisation for products with core charges
  - Return deadline: invoice_date + default_core_return_days (from settings)
  - Partial returns: CoreReturnEvent records each return; qty_returned cumulative
  - Vendor status: pending → submitted → accepted | denied
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.constants import (
    AuditAction, CoreCreditMethod, CoreDenialResolution, CoreDirection,
    CoreInspectionOutcome, CoreStatus, CoreVendorStatus, EntityType,
    NotificationSeverity, NotificationType,
)
from app.models.core import CoreCharge, CoreLocation, CoreLocationMovement, CoreReturnEvent, CoreSlip
from app.models.customer import Customer
from app.models.notification import Notification
from app.settings_utils import get_setting_value_db as _get_setting, bump_counter
from app.services.base import BaseService


# R10 — default location name constants (matched against CoreLocation.name)
_LOC_CORE_SHELF        = "Core Shelf"
_LOC_CORE_HOLDING      = "Core Holding"
_LOC_QUESTIONABLE      = "Questionable Core"
_LOC_REJECTED          = "Rejected Core"
_LOC_IN_TRANSIT_VENDOR = "In Transit to Vendor"
_LOC_SCRAP             = "Scrap Core"


class CoreService(BaseService):

    # ── Core Creation ─────────────────────────────────────────────────────────

    def create_core_charge(
        self,
        invoice_id: int,
        invoice_line_id: int,
        product_id: int,
        qty: int,
        customer_unit_charge: float,
        vendor_unit_charge: float,
    ) -> CoreCharge:
        """
        Called by InvoiceService.finalise() for product lines with core charges.
        Sets return_deadline = invoice_date + default_core_return_days.
        """
        # Determine customer_id from the invoice
        from app.models.invoice import Invoice
        invoice = self.db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if invoice is None:
            raise ValueError(f"Invoice {invoice_id} not found")

        # R3 — prefer the new core_return_grace_days setting; fall back to legacy
        _days_raw = (
            _get_setting(self.db, "core_return_grace_days", "")
            or _get_setting(self.db, "default_core_return_days", "45")
        ).strip()
        try:
            return_days = int(_days_raw) if _days_raw else 45
        except ValueError:
            return_days = 45
        deadline = datetime.utcnow() + timedelta(days=return_days)

        core = CoreCharge(
            direction=CoreDirection.CUSTOMER_OWES_RETURN,
            customer_id=invoice.customer_id,
            product_id=product_id,
            invoice_line_id=invoice_line_id,
            qty_charged=qty,
            qty_returned=0,
            customer_unit_charge=customer_unit_charge,
            vendor_unit_charge=vendor_unit_charge,
            status=CoreStatus.OPEN,
            vendor_status=CoreVendorStatus.PENDING,
            return_deadline=deadline,
            grace_days_snapshot=return_days,
        )
        self.db.add(core)
        self.db.flush()
        return core

    # ── Customer Returns ──────────────────────────────────────────────────────

    def record_customer_return(
        self,
        core_charge_id: int,
        qty_returned: int,
        condition: str | None = None,
        inspection_outcome: str = CoreInspectionOutcome.ACCEPTED,
    ) -> CoreReturnEvent:
        """
        Record a partial or full core return from a customer.

        inspection_outcome controls whether credit is issued immediately:
          ACCEPTED — core passes inspection; account credit issued now.
          HOLD     — core physically received but needs closer look; credit deferred
                     until complete_inspection() is called.
          REJECTED — core refused (wrong/damaged); no credit issued; charge closed.

        Credit is NOT issued for HOLD or REJECTED outcomes.
        """
        core = self._get_or_404(core_charge_id)
        if core.direction != CoreDirection.CUSTOMER_OWES_RETURN:
            raise ValueError("This core charge is not a customer return direction")
        if qty_returned > core.qty_outstanding:
            raise ValueError(
                f"Cannot return {qty_returned} — only {core.qty_outstanding} outstanding"
            )

        # Credit amount: only meaningful when outcome is ACCEPTED
        credit_amount = (
            round(qty_returned * core.customer_unit_charge, 2)
            if inspection_outcome == CoreInspectionOutcome.ACCEPTED
            else 0.0
        )

        event = CoreReturnEvent(
            core_charge_id=core_charge_id,
            qty_returned=qty_returned,
            returned_at=datetime.utcnow(),
            credit_method=CoreCreditMethod.ACCOUNT_CREDIT,
            credit_amount=credit_amount,
            processed_by_id=self.current_user_id,
            notes=condition or "",
        )
        self.db.add(event)

        core.qty_returned += qty_returned
        core.inspection_outcome = inspection_outcome
        core.inspected_at = datetime.utcnow()
        core.inspected_by_id = self.current_user_id

        # R10 — auto-set physical location based on inspection outcome
        location_target: str | None = None
        if inspection_outcome == CoreInspectionOutcome.REJECTED:
            # Bad core — close it; no credit issued
            core.status = CoreStatus.CLOSED
            location_target = _LOC_REJECTED
        elif inspection_outcome == CoreInspectionOutcome.HOLD:
            # Physically received but deferred — keep in PARTIAL/RETURNED
            # so it appears in the pending_inspection query, not Stage 1
            core.status = CoreStatus.RETURNED if core.qty_outstanding == 0 else CoreStatus.PARTIAL
            location_target = _LOC_CORE_HOLDING
        else:
            # ACCEPTED — normal path
            core.status = CoreStatus.RETURNED if core.qty_outstanding == 0 else CoreStatus.PARTIAL
            location_target = _LOC_CORE_SHELF

        if location_target:
            self._move_to_location_by_name(
                core,
                location_target,
                reason=f"inspection_{inspection_outcome}",
                note=condition or "",
            )

        # Issue credit only for accepted returns
        if inspection_outcome == CoreInspectionOutcome.ACCEPTED:
            if core.customer_id and credit_amount > 0:
                from app.services.crm_service import CRMService
                CRMService(self.db, self.current_user_id).add_credit(
                    customer_id=core.customer_id,
                    amount=credit_amount,
                    reason=f"Core return #{core_charge_id}",
                )

        self.audit(
            entity_type=EntityType.CORE_CHARGE,
            entity_id=core_charge_id,
            action=AuditAction.CORE_RECEIVED,
            new_value={
                "qty_returned": qty_returned,
                "inspection_outcome": inspection_outcome,
                "credit": credit_amount,
            },
        )
        self.db.commit()
        return event

    def complete_inspection(
        self,
        core_charge_id: int,
        final_outcome: str,
        notes: str | None = None,
    ) -> None:
        """
        Finalise a HOLD inspection — called from the Pending Inspection section.

        final_outcome must be ACCEPTED or REJECTED:
          ACCEPTED — issue the deferred customer credit now.
          REJECTED — close with no credit.
        """
        if final_outcome not in (CoreInspectionOutcome.ACCEPTED, CoreInspectionOutcome.REJECTED):
            raise ValueError("final_outcome must be 'accepted' or 'rejected'")

        core = self._get_or_404(core_charge_id)
        if core.inspection_outcome != CoreInspectionOutcome.HOLD:
            raise ValueError(
                f"CoreCharge {core_charge_id} is not in HOLD status (current: {core.inspection_outcome})"
            )

        core.inspection_outcome = final_outcome
        core.inspected_at = datetime.utcnow()
        core.inspected_by_id = self.current_user_id
        if notes:
            core.notes = (core.notes + "\n" + notes).strip()

        if final_outcome == CoreInspectionOutcome.ACCEPTED:
            # Issue the deferred credit
            credit_amount = round(core.qty_returned * core.customer_unit_charge, 2)
            if core.customer_id and credit_amount > 0:
                from app.services.crm_service import CRMService
                CRMService(self.db, self.current_user_id).add_credit(
                    customer_id=core.customer_id,
                    amount=credit_amount,
                    reason=f"Core return #{core_charge_id} (inspection passed)",
                )
            # Status stays RETURNED — normal progression to vendor shipment
            # R10 — accepted-after-hold moves to Core Shelf
            self._move_to_location_by_name(
                core, _LOC_CORE_SHELF,
                reason="inspection_accepted_after_hold",
                note=notes or "",
            )
        else:
            # Rejected after hold — close the charge
            core.status = CoreStatus.CLOSED
            # R10 — rejected-after-hold moves to Rejected Core
            self._move_to_location_by_name(
                core, _LOC_REJECTED,
                reason="inspection_rejected_after_hold",
                note=notes or "",
            )

        self.audit(
            entity_type=EntityType.CORE_CHARGE,
            entity_id=core_charge_id,
            action=AuditAction.CORE_RECEIVED,
            new_value={"inspection_final": final_outcome, "notes": notes},
        )
        self.db.commit()

    # ── Core Slip ─────────────────────────────────────────────────────────────

    def create_core_slip(self, core_charge_id: int) -> CoreSlip:
        """
        Generate a customer core return slip (CORE-YYYY-XXXX) for a returned charge.
        Links the CoreCharge to the new slip.  Safe to call after the return is committed.

        Raises ValueError if the core charge has no associated customer (vendor-direction
        cores do not have a customer and cannot have a customer-facing slip).
        """
        from app.constants import CoreSlipStatus
        core = self._get_or_404(core_charge_id)
        if core.customer_id is None:
            raise ValueError(
                f"CoreCharge {core_charge_id} has no customer — cannot create a customer slip"
            )
        year = datetime.utcnow().year
        slip_number = bump_counter(self.db, "next_core_slip_number", "CORE", year)
        slip = CoreSlip(
            slip_number=slip_number,
            customer_id=core.customer_id,
            invoice_id=core.credit_invoice_id,
            status=CoreSlipStatus.OPEN,
        )
        self.db.add(slip)
        self.db.flush()   # get slip.id
        core.core_slip_id = slip.id
        self.db.commit()
        self.db.refresh(slip)
        return slip

    # ── Vendor Submission ─────────────────────────────────────────────────────

    def submit_to_vendor(
        self,
        core_charge_id: int,
        tracking_number: str | None = None,
    ) -> None:
        """
        Mark vendor submission. Sets status=SHIPPED_TO_VENDOR.
        R10 — moves physical location to "In Transit to Vendor" so every open
        core has a location (no NULL while mid-shipment).
        """
        core = self._get_or_404(core_charge_id)
        core.core_tracking_number = tracking_number
        core.vendor_status = CoreVendorStatus.PENDING   # stays pending until vendor responds
        core.status = CoreStatus.SHIPPED_TO_VENDOR

        # R10 — every open core has a location, even mid-shipment
        self._move_to_location_by_name(
            core, _LOC_IN_TRANSIT_VENDOR,
            reason="shipped_to_vendor",
            note=f"tracking: {tracking_number or 'unknown'}",
        )
        self.db.commit()

    def record_vendor_acceptance(self, core_charge_id: int, credit_amount: float) -> None:
        """
        Vendor accepted the core return.
        Creates a VendorCredit record so the credit appears in the vendor ledger
        and can be picked up by the QBO sync job.
        R10 — vendor keeps the physical core; location is cleared.
        """
        from app.models.vendor import VendorCredit
        from app.constants import VendorCreditStatus, VendorCreditType

        core = self._get_or_404(core_charge_id)
        core.vendor_status = CoreVendorStatus.ACCEPTED
        core.vendor_decision_at = datetime.utcnow()
        core.status = CoreStatus.VENDOR_ACCEPTED

        if core.vendor_id and credit_amount > 0:
            vendor_credit = VendorCredit(
                vendor_id=core.vendor_id,
                credit_type=VendorCreditType.RETURN,
                amount=credit_amount,
                status=VendorCreditStatus.OPEN,
                # qbo_sync_status inherits PENDING from QBOSyncMixin default
                notes=f"Core charge #{core_charge_id} accepted by vendor",
            )
            self.db.add(vendor_credit)

        # R10 — vendor kept the physical core; record final movement out of inventory
        # by moving to a NULL/closed location. We record this as a Movement row with
        # destination = "In Transit to Vendor" cleared to NULL via direct field set
        # (no movement row needed when destination is "vendor's hands").
        old_location_id = core.location_id
        core.location_id = None
        self.db.add(CoreLocationMovement(
            core_charge_id=core.id,
            from_location_id=old_location_id,
            to_location_id=old_location_id,  # FK requires non-null; reuse from
            moved_by_user_id=self.current_user_id,
            reason="vendor_accepted_kept_core",
            note="Vendor accepted and kept the physical core",
        ))

        self.db.commit()

    def record_vendor_denial(
        self,
        core_charge_id: int,
        denial_reason: str,
        resolution: str,
        notes: str | None = None,
        physical_core_returned: bool = True,
    ) -> None:
        """
        Vendor denied the core.
        resolution: 'absorbed_by_jaks' | 'charged_to_customer' | 'disputed' | 'write_off'

        R10 — If the vendor physically returns the core (physical_core_returned=True),
        move it to Rejected Core (or Questionable Core if resolution=DISPUTED). If
        the vendor scraps/keeps it (physical_core_returned=False), location stays
        cleared.
        """
        core = self._get_or_404(core_charge_id)
        core.vendor_status = CoreVendorStatus.REJECTED
        core.vendor_decision_at = datetime.utcnow()
        core.status = CoreStatus.VENDOR_REJECTED
        core.vendor_denial_reason = denial_reason
        core.denial_resolution = resolution
        core.denial_notes = notes or ""

        if physical_core_returned:
            # Disputed cores live in Questionable until resolved; otherwise Rejected.
            dest = (
                _LOC_QUESTIONABLE
                if resolution == CoreDenialResolution.DISPUTED
                else _LOC_REJECTED
            )
            self._move_to_location_by_name(
                core, dest,
                reason=f"vendor_denied_{resolution}",
                note=denial_reason,
            )
        else:
            # Vendor kept/scrapped — clear location
            core.location_id = None

        self.db.commit()

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_outstanding_cores(self, customer_id: int | None = None) -> list[CoreCharge]:
        """Return all open core charges (qty_outstanding > 0)."""
        query = (
            self.db.query(CoreCharge)
            .filter(
                CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
                CoreCharge.qty_returned < CoreCharge.qty_charged,
            )
        )
        if customer_id is not None:
            query = query.filter(CoreCharge.customer_id == customer_id)
        return query.order_by(CoreCharge.return_deadline).all()

    def get_overdue_cores(self) -> list[CoreCharge]:
        """Return all overdue cores (past return_deadline with qty_outstanding > 0)."""
        now = datetime.utcnow()
        return (
            self.db.query(CoreCharge)
            .filter(
                CoreCharge.return_deadline < now,
                CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
                CoreCharge.qty_returned < CoreCharge.qty_charged,
            )
            .order_by(CoreCharge.return_deadline)
            .all()
        )

    def get_pending_vendor_submission(self) -> list[CoreCharge]:
        """Return cores with qty_returned > 0 but not yet submitted to vendor."""
        return (
            self.db.query(CoreCharge)
            .filter(
                CoreCharge.status == CoreStatus.RETURNED,
                CoreCharge.vendor_status == CoreVendorStatus.PENDING,
            )
            .order_by(CoreCharge.updated_at)
            .all()
        )

    # ── Location Movement (R10) ───────────────────────────────────────────────

    def move_core(
        self,
        core_charge_id: int,
        dest_location_id: int,
        reason: str = "",
        note: str = "",
    ) -> CoreLocationMovement:
        """
        R10 — Manual location move for a core. Writes a CoreLocationMovement
        row for the audit trail and updates the core's current location_id.

        Use cases: Core Shelf → Ready for PAI (when batching for vendor return),
        Core Holding → Core Shelf (after re-inspection), etc.
        """
        core = self._get_or_404(core_charge_id)
        dest_location = (
            self.db.query(CoreLocation)
            .filter(CoreLocation.id == dest_location_id)
            .first()
        )
        if dest_location is None:
            raise ValueError(f"CoreLocation {dest_location_id} not found")
        if not dest_location.is_active:
            raise ValueError(
                f"CoreLocation '{dest_location.name}' is inactive — cannot move cores to it"
            )

        old_location_id = core.location_id
        movement = CoreLocationMovement(
            core_charge_id=core.id,
            from_location_id=old_location_id,
            to_location_id=dest_location_id,
            moved_by_user_id=self.current_user_id,
            reason=reason or "manual_move",
            note=note,
        )
        self.db.add(movement)
        core.location_id = dest_location_id

        self.audit(
            entity_type=EntityType.CORE_CHARGE,
            entity_id=core.id,
            action=AuditAction.STATUS_CHANGED,
            old_value={"location_id": old_location_id},
            new_value={
                "location_id": dest_location_id,
                "location_name": dest_location.name,
                "reason": reason,
            },
        )
        self.db.commit()
        return movement

    # ── Overdue Detection (R3) ────────────────────────────────────────────────

    def mark_overdue_cores(self) -> dict:
        """
        R3 — Scan for cores past their return deadline and emit notifications.
        Idempotent: doesn't re-notify a core that was already flagged today.

        Two notification tiers:
          - OVERDUE        (severity=warning, type=core_overdue) when past deadline
          - APPROACHING    (severity=info, type=core_overdue) when past N% of grace
                           (N = core_return_reminder_threshold_pct setting, default 75)

        Returns dict with counts for monitoring/dashboards.
        """
        now = datetime.utcnow()
        threshold_pct_raw = _get_setting(
            self.db, "core_return_reminder_threshold_pct", "75"
        )
        try:
            threshold_pct = float(threshold_pct_raw)
        except (TypeError, ValueError):
            threshold_pct = 75.0

        open_cores = (
            self.db.query(CoreCharge)
            .filter(
                CoreCharge.status.in_([CoreStatus.OPEN, CoreStatus.PARTIAL]),
                CoreCharge.direction == CoreDirection.CUSTOMER_OWES_RETURN,
                CoreCharge.return_deadline.isnot(None),
            )
            .all()
        )

        overdue_n = 0
        approaching_n = 0

        for core in open_cores:
            if core.return_deadline is None:
                continue

            total_grace = core.grace_days_snapshot or 45
            elapsed_days = (now - (core.return_deadline - timedelta(days=total_grace))).days
            past_deadline = now > core.return_deadline

            if past_deadline:
                # Emit OVERDUE notification (one per day per core)
                if not self._notif_exists_today(NotificationType.CORE_OVERDUE, "core_charge", core.id, "overdue"):
                    self.db.add(Notification(
                        user_id=None,
                        severity=NotificationSeverity.WARNING,
                        notification_type=NotificationType.CORE_OVERDUE,
                        entity_type="core_charge",
                        entity_id=core.id,
                        message=(
                            f"Core charge #{core.id} is OVERDUE "
                            f"(deadline was {core.return_deadline:%Y-%m-%d})."
                        ),
                        action_url=f"/cores/{core.id}",
                    ))
                    overdue_n += 1
                continue

            # Approaching: past threshold% of the grace window
            if total_grace > 0:
                threshold_days = total_grace * (threshold_pct / 100.0)
                if elapsed_days >= threshold_days:
                    if not self._notif_exists_today(
                        NotificationType.CORE_OVERDUE, "core_charge", core.id, "approaching"
                    ):
                        days_left = (core.return_deadline - now).days
                        self.db.add(Notification(
                            user_id=None,
                            severity=NotificationSeverity.INFO,
                            notification_type=NotificationType.CORE_OVERDUE,
                            entity_type="core_charge",
                            entity_id=core.id,
                            message=(
                                f"Core charge #{core.id} approaching deadline "
                                f"({days_left} day(s) left)."
                            ),
                            action_url=f"/cores/{core.id}",
                        ))
                        approaching_n += 1

        if overdue_n or approaching_n:
            self.db.commit()

        return {
            "overdue": overdue_n,
            "approaching": approaching_n,
            "scanned": len(open_cores),
        }

    # ── Vendor Credit Difference Resolution (R3) ─────────────────────────────

    def process_vendor_credit_difference(
        self,
        core_charge_id: int,
        actual_credit: float,
        resolution: str,
        notes: str = "",
    ) -> CoreCharge:
        """
        R3 — Vendor paid less than expected (or rejected entirely).

        Records the actual_credit value and the user's chosen resolution:
          - absorbed_by_jaks: JAKS eats the loss (default)
          - charged_to_customer: pull back from customer (Phase 2 will auto-create
            a chargeback invoice line; Phase 1 just records the intent)
          - disputed: open dispute with vendor (status flag for follow-up)
          - write_off: bookkeeping write-off

        Emits a notification when actual < expected so the discrepancy is visible.
        """
        valid = {
            CoreDenialResolution.ABSORBED_BY_JAKS,
            CoreDenialResolution.CHARGED_TO_CUSTOMER,
            CoreDenialResolution.DISPUTED,
            "write_off",  # not yet in enum; Phase 2 expand
        }
        if resolution not in valid:
            raise ValueError(
                f"Invalid resolution '{resolution}'. Must be one of {sorted(valid)}"
            )

        core = self._get_or_404(core_charge_id)
        expected = round(core.vendor_unit_charge * core.qty_returned, 2)
        actual = round(actual_credit, 2)
        difference = round(expected - actual, 2)

        core.denial_resolution = resolution
        core.denial_notes = notes or core.denial_notes
        core.vendor_decision_at = datetime.utcnow()

        # If there's a shortfall, notify the user for follow-up
        if difference > 0.001:
            self.db.add(Notification(
                user_id=None,
                severity=NotificationSeverity.WARNING,
                notification_type=NotificationType.CORE_OVERDUE,  # reuse — Phase K may add CORE_DISCREPANCY
                entity_type="core_charge",
                entity_id=core.id,
                message=(
                    f"Core charge #{core.id}: vendor credit short by ${difference:.2f} "
                    f"(expected ${expected:.2f}, received ${actual:.2f}); "
                    f"resolution={resolution}."
                ),
                action_url=f"/cores/{core.id}",
            ))

        self.audit(
            entity_type=EntityType.CORE_CHARGE,
            entity_id=core.id,
            action=AuditAction.STATUS_CHANGED,
            new_value={
                "vendor_credit_expected": expected,
                "vendor_credit_actual": actual,
                "difference": difference,
                "resolution": resolution,
                "notes": notes,
            },
        )
        self.db.commit()
        return core

    # ── Private ───────────────────────────────────────────────────────────────

    def _get_or_404(self, core_charge_id: int) -> CoreCharge:
        core = self.db.query(CoreCharge).filter(CoreCharge.id == core_charge_id).first()
        if core is None:
            raise ValueError(f"CoreCharge {core_charge_id} not found")
        return core

    def _location_by_name(self, name: str) -> CoreLocation | None:
        return (
            self.db.query(CoreLocation)
            .filter(CoreLocation.name == name, CoreLocation.is_active == True)  # noqa: E712
            .first()
        )

    def _move_to_location_by_name(
        self,
        core: CoreCharge,
        dest_location_name: str,
        reason: str = "",
        note: str = "",
    ) -> None:
        """
        Internal helper — move a core to a named location. Silently no-ops if
        the location doesn't exist (so tests/dev environments without seeds
        don't crash). The seeded default locations are always present in
        production (see app.main._seed_core_locations).
        """
        dest = self._location_by_name(dest_location_name)
        if dest is None:
            return  # graceful degradation if seed is missing
        if core.location_id == dest.id:
            return  # already there

        old_location_id = core.location_id
        self.db.add(CoreLocationMovement(
            core_charge_id=core.id,
            from_location_id=old_location_id,
            to_location_id=dest.id,
            moved_by_user_id=self.current_user_id,
            reason=reason or "auto_transition",
            note=note,
        ))
        core.location_id = dest.id

    def _notif_exists_today(
        self,
        notification_type: str,
        entity_type: str,
        entity_id: int,
        marker: str,
    ) -> bool:
        """Idempotency guard for mark_overdue_cores — one notif per core per day per marker."""
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            self.db.query(Notification)
            .filter(
                Notification.notification_type == notification_type,
                Notification.entity_type == entity_type,
                Notification.entity_id == entity_id,
                Notification.created_at >= today_start,
                Notification.message.like(f"%{marker}%") if marker == "approaching"
                else Notification.message.notlike("%approaching%"),
            )
            .first()
            is not None
        )
