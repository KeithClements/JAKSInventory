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

import logging
from datetime import datetime, timedelta

from app.constants import (
    AuditAction, CoreCreditMethod, CoreDenialResolution, CoreDirection,
    CoreInspectionOutcome, CoreStatus, CoreVendorStatus, EntityType,
    NotificationSeverity, NotificationType, Permission, VCRLineOutcome, VCRStatus,
)
from app.models.core import (
    CoreCharge, CoreLocation, CoreLocationMovement, CoreReturnEvent, CoreSlip,
    VendorCoreReturn, VendorCoreReturnLine,
)
from app.models.customer import Customer
from app.models.notification import Notification
from app.settings_utils import get_setting_value_db as _get_setting, bump_counter
from app.services.base import BaseService

log = logging.getLogger(__name__)


# R10 — default location name constants (matched against CoreLocation.name)
_LOC_CORE_SHELF        = "Core Shelf"
_LOC_CORE_HOLDING      = "Core Holding"
_LOC_QUESTIONABLE      = "Questionable Core"
_LOC_REJECTED          = "Rejected Core"
_LOC_IN_TRANSIT_VENDOR = "In Transit to Vendor"
_LOC_SCRAP             = "Scrap Core"

# R1-9 — credit_method sentinel: the issued account credit was reversed
# (charged back to the customer after a vendor denial). Not a CoreCreditMethod
# value on purpose — it marks "credit no longer outstanding" and makes the
# chargeback idempotent (a second chargeback sees != ACCOUNT_CREDIT and no-ops).
_CREDIT_METHOD_CHARGED_BACK = "charged_back"

# R2 — VCR statuses still in flight (mirrors CoreMetricsService._VCR_OPEN).
# A core linked to a VCR in one of these statuses cannot be batched again.
VCR_OPEN_STATUSES = (
    VCRStatus.DRAFT, VCRStatus.SHIPPED, VCRStatus.VENDOR_REVIEW, VCRStatus.DISPUTED,
)

# R2 — audit entity tag for VendorCoreReturn rows (no EntityType member exists;
# the audit column is a plain string — matches the literal "core_charge" tags
# already used for Notification rows in this file).
_ENTITY_VCR = "vendor_core_return"


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

        # §21 — stamp the product's preferred-vendor id at creation so the core
        # can be grouped by vendor on the Ready-to-Ship / VCR board without a
        # later lookup (previously vendor_id stayed NULL until VCR batch time,
        # making multi-vendor cores untrackable at return time).
        from app.models.product import Product
        _prod = self.db.query(Product).filter(Product.id == product_id).first()
        _pref = _prod.preferred_vendor_source if _prod else None
        core_vendor_id = _pref.vendor_id if _pref else None

        core = CoreCharge(
            direction=CoreDirection.CUSTOMER_OWES_RETURN,
            customer_id=invoice.customer_id,
            vendor_id=core_vendor_id,
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
            # R1-9 — link back to the originating invoice so printed core slips
            # (CoreSlip.invoice_id is copied from this in create_core_slip)
            # carry an invoice reference instead of always being NULL.
            credit_invoice_id=invoice_id,
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
                # BUG-4 fix: stamp the credit so a later issue_core_credit() call
                # can detect this core was already credited and refuse to double it.
                core.credit_issued_at = datetime.utcnow()
                core.credit_method = CoreCreditMethod.ACCOUNT_CREDIT

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

        # Owner decision 2026-06-16 — for an ACCEPTED return, push the issued
        # account credit straight onto the core's originating invoice (if it is
        # still owing) so the customer only owes the parts balance. See helper.
        if inspection_outcome == CoreInspectionOutcome.ACCEPTED:
            self._auto_apply_core_credit_to_invoice(core, credit_amount)

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
                # BUG-4/R1-9 — stamp exactly like record_customer_return does so
                # issue_core_credit won't double-credit and a later vendor-denial
                # chargeback can find the issued account credit.
                core.credit_issued_at = datetime.utcnow()
                core.credit_method = CoreCreditMethod.ACCOUNT_CREDIT
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

        # Owner decision 2026-06-16 — once a held core is accepted, apply the
        # now-issued account credit onto its originating invoice (see helper).
        if final_outcome == CoreInspectionOutcome.ACCEPTED:
            applied_amount = round(core.qty_returned * core.customer_unit_charge, 2)
            self._auto_apply_core_credit_to_invoice(core, applied_amount)

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

        # R10 — vendor kept the physical core; clear its location.
        # BUG-2 fix: only record a movement row when the core actually had a prior
        # location to move FROM. CoreLocationMovement.to_location_id is NOT NULL, so
        # a core that reached vendor-acceptance without ever being routed
        # (location_id is None — e.g. direct acceptance, no submit_to_vendor) would
        # otherwise insert to_location_id=None and raise an IntegrityError. With no
        # prior location there is no physical movement to record, so we skip the row.
        old_location_id = core.location_id
        core.location_id = None
        if old_location_id is not None:
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

        # R1-9 — staff chose to pass the loss to the customer: claw back the
        # account credit issued when the core was returned/accepted. Idempotent
        # no-op when no credit was ever issued or it was already charged back.
        if resolution == CoreDenialResolution.CHARGED_TO_CUSTOMER:
            self._charge_back_customer_credit(core)

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

    # ── Vendor Core Return batches (R2) ──────────────────────────────────────

    def create_vcr(
        self,
        vendor_id: int,
        core_charge_ids: list[int],
        notes: str = "",
    ) -> VendorCoreReturn:
        """
        R2 — Batch ready-to-ship cores into one VendorCoreReturn (VCR-YYYY-XXXX)
        so 10-20 cores go back to the vendor in one box with one document.

        Validates every core is in the ready-to-ship stage (RETURNED + vendor
        PENDING + not HOLD — the exact cores-list/metrics filter), belongs to
        this vendor (cores with no vendor_id adopt it — customer cores are
        created without one), and is not already on an open VCR. Snapshots one
        VendorCoreReturnLine per core (the print doc renders vcr.lines) and
        computes expected_credit = Σ vendor_unit_charge × qty_returned.

        Cores stay status=RETURNED until ship_vcr(); the cores list hides
        open-VCR cores from the ready-to-ship checklist instead.
        """
        self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        from app.models.vendor import Vendor
        vendor = self.db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if vendor is None:
            raise ValueError(f"Vendor {vendor_id} not found")
        if not core_charge_ids:
            raise ValueError("Select at least one core to batch")

        # Dedupe while preserving submission order (double-posted checkboxes)
        cores: list[CoreCharge] = []
        seen: set[int] = set()
        for cid in core_charge_ids:
            if cid in seen:
                continue
            seen.add(cid)
            core = self._get_or_404(cid)
            if (
                core.direction != CoreDirection.CUSTOMER_OWES_RETURN
                or core.status != CoreStatus.RETURNED
                or core.vendor_status != CoreVendorStatus.PENDING
                or core.inspection_outcome == CoreInspectionOutcome.HOLD
            ):
                raise ValueError(
                    f"Core #{core.id} is not ready to ship — cannot batch it onto a VCR"
                )
            if core.vendor_id is not None and core.vendor_id != vendor_id:
                raise ValueError(
                    f"Core #{core.id} belongs to a different vendor — one VCR per vendor"
                )
            if core.vcr_id is not None:
                linked = (
                    self.db.query(VendorCoreReturn)
                    .filter(VendorCoreReturn.id == core.vcr_id)
                    .first()
                )
                if linked is not None and linked.status in VCR_OPEN_STATUSES:
                    raise ValueError(
                        f"Core #{core.id} is already batched on open VCR {linked.vcr_number}"
                    )
            cores.append(core)

        year = datetime.utcnow().year
        vcr_number = bump_counter(self.db, "next_vcr_number", "VCR", year)
        vcr = VendorCoreReturn(
            vcr_number=vcr_number,
            vendor_id=vendor_id,
            status=VCRStatus.DRAFT,
            expected_credit=0.0,
            # No dedicated notes column — creation notes live in resolution_notes
            # (free text; the vendor decision appends rather than overwrites).
            resolution_notes=(notes or "").strip(),
            created_by_id=self.current_user_id,
        )
        self.db.add(vcr)
        self.db.flush()  # get vcr.id for the lines + core links

        expected_total = 0.0
        for core in cores:
            qty = core.qty_returned  # ready-to-ship = fully returned
            unit = core.vendor_unit_charge or 0.0
            expected_total += qty * unit
            product = core.product
            # Vendor-facing part #: prefer their own part number, fall back to SKU
            pvs = product.preferred_vendor_source if product else None
            part_number = (
                (pvs.vendor_part_number if pvs else "") or (product.sku if product else "")
            )
            self.db.add(VendorCoreReturnLine(
                vcr_id=vcr.id,
                core_charge_id=core.id,
                part_number=part_number or "",
                description=(product.title if product else "") or "",
                qty=qty,
                expected_unit_credit=unit,
                actual_unit_credit=0.0,
                vendor_outcome=VCRLineOutcome.PENDING,
            ))
            core.vcr_id = vcr.id
            if core.vendor_id is None:
                core.vendor_id = vendor_id

        vcr.expected_credit = round(expected_total, 2)

        self.audit(
            entity_type=_ENTITY_VCR,
            entity_id=vcr.id,
            action=AuditAction.CREATED,
            new_value={
                "vcr_number": vcr_number,
                "vendor_id": vendor_id,
                "core_charge_ids": [c.id for c in cores],
                "expected_credit": vcr.expected_credit,
            },
            notes=notes or None,
        )
        self.db.commit()
        self.db.refresh(vcr)
        return vcr

    def ship_vcr(
        self,
        vcr_id: int,
        tracking_number: str = "",
        rma_number: str = "",
    ) -> VendorCoreReturn:
        """
        R2 — Mark the batch physically shipped: DRAFT → SHIPPED, stamps
        shipped_at/tracking/RMA, and moves every core through the same
        transition submit_to_vendor() applies (SHIPPED_TO_VENDOR + In Transit
        location) — but in ONE transaction for the whole box.
        """
        self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        vcr = self._get_vcr_or_404(vcr_id)
        if vcr.status != VCRStatus.DRAFT:
            raise ValueError(
                f"VCR {vcr.vcr_number} has already shipped (status: {vcr.status})"
            )

        tracking = (tracking_number or "").strip()
        vcr.tracking_number = tracking
        if (rma_number or "").strip():
            vcr.rma_number = rma_number.strip()
        vcr.status = VCRStatus.SHIPPED
        vcr.shipped_at = datetime.utcnow()

        for core in vcr.core_charges:
            # Mirrors submit_to_vendor() field-for-field; kept inline so a
            # failure mid-batch rolls the whole shipment back atomically.
            core.core_tracking_number = tracking or None
            core.vendor_status = CoreVendorStatus.PENDING
            core.status = CoreStatus.SHIPPED_TO_VENDOR
            self._move_to_location_by_name(
                core, _LOC_IN_TRANSIT_VENDOR,
                reason="shipped_to_vendor_vcr",
                note=f"VCR {vcr.vcr_number} — tracking: {tracking or 'unknown'}",
            )

        self.audit(
            entity_type=_ENTITY_VCR,
            entity_id=vcr.id,
            action=AuditAction.STATUS_CHANGED,
            new_value={
                "vcr_number": vcr.vcr_number,
                "status": VCRStatus.SHIPPED,
                "tracking_number": tracking,
                "core_count": len(vcr.core_charges),
            },
        )
        self.db.commit()
        return vcr

    def record_vcr_vendor_decision(
        self,
        vcr_id: int,
        actual_credit: float,
        denied_core_ids: list[int] | None = None,
        denial_reason: str = "",
        denial_resolution: str = CoreDenialResolution.ABSORBED_BY_JAKS,
        notes: str = "",
    ) -> VendorCoreReturn:
        """
        R2 — Record the vendor's decision on a shipped batch.

        Per-core money flows REUSE the single-core paths so their guards stay
        the single source of truth:
          - cores NOT in denied_core_ids → record_vendor_acceptance() (one
            VendorCredit row each, at the expected per-core amount);
          - denied cores → record_vendor_denial() (R1-9 chargeback fires once
            when denial_resolution=CHARGED_TO_CUSTOMER; idempotent).
        Cores already decided individually (vendor_status != PENDING) are
        skipped — never double-credited or double-charged.

        actual_credit is the lump credit the vendor actually issued for the
        batch; the shortfall lands in credit_difference for reconciliation.
        Final status: DISPUTED when denials are being disputed with the vendor,
        otherwise CREDITED (settled — including absorbed/charged-back denials).
        """
        self.assert_can(Permission.ISSUE_CREDIT_MEMO)

        vcr = self._get_vcr_or_404(vcr_id)
        if vcr.status == VCRStatus.DRAFT:
            raise ValueError(
                f"VCR {vcr.vcr_number} has not shipped yet — ship it before recording a decision"
            )
        if vcr.status in (VCRStatus.CREDITED, VCRStatus.CLOSED):
            raise ValueError(
                f"VCR {vcr.vcr_number} is already settled (status: {vcr.status})"
            )

        valid_resolutions = {
            CoreDenialResolution.ABSORBED_BY_JAKS,
            CoreDenialResolution.CHARGED_TO_CUSTOMER,
            CoreDenialResolution.DISPUTED,
        }
        if denial_resolution not in valid_resolutions:
            raise ValueError(
                f"Invalid denial_resolution '{denial_resolution}'. "
                f"Must be one of {sorted(valid_resolutions)}"
            )

        denied = {int(i) for i in (denied_core_ids or [])}
        reason = (denial_reason or "").strip() or f"Denied by vendor on {vcr.vcr_number}"
        lines_by_core = {ln.core_charge_id: ln for ln in vcr.lines}

        accepted_n = denied_n = 0
        for core in list(vcr.core_charges):
            if core.vendor_status != CoreVendorStatus.PENDING:
                continue  # already decided (e.g. single-core flow) — don't double-handle
            line = lines_by_core.get(core.id)
            if core.id in denied:
                self.record_vendor_denial(
                    core_charge_id=core.id,
                    denial_reason=reason,
                    resolution=denial_resolution,
                    notes=notes or None,
                )
                if line is not None:
                    line.vendor_outcome = VCRLineOutcome.REJECTED
                    line.actual_unit_credit = 0.0
                denied_n += 1
            else:
                expected = round((core.vendor_unit_charge or 0.0) * core.qty_returned, 2)
                self.record_vendor_acceptance(
                    core_charge_id=core.id, credit_amount=expected,
                )
                if line is not None:
                    line.vendor_outcome = VCRLineOutcome.ACCEPTED
                    line.actual_unit_credit = core.vendor_unit_charge or 0.0
                accepted_n += 1

        vcr.actual_credit = round(float(actual_credit or 0.0), 2)
        vcr.credit_difference = round(
            (vcr.expected_credit or 0.0) - vcr.actual_credit, 2
        )
        vcr.vendor_decision_at = datetime.utcnow()
        vcr.resolution = denial_resolution if denied_n else None
        if (notes or "").strip():
            vcr.resolution_notes = (
                (vcr.resolution_notes or "") + "\n" + notes.strip()
            ).strip()
        vcr.status = (
            VCRStatus.DISPUTED
            if (denied_n and denial_resolution == CoreDenialResolution.DISPUTED)
            else VCRStatus.CREDITED
        )

        self.audit(
            entity_type=_ENTITY_VCR,
            entity_id=vcr.id,
            action=AuditAction.STATUS_CHANGED,
            new_value={
                "vcr_number": vcr.vcr_number,
                "status": vcr.status,
                "actual_credit": vcr.actual_credit,
                "credit_difference": vcr.credit_difference,
                "accepted": accepted_n,
                "denied": denied_n,
                "denial_resolution": denial_resolution if denied_n else None,
            },
            notes=notes or None,
        )
        self.db.commit()
        self.db.refresh(vcr)
        return vcr

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
          - charged_to_customer: pull back from customer — reverses the account
            credit issued for this core, CAPPED at the vendor shortfall
            (expected − actual; owner decision 2026-06-10). A chargeback
            invoice line for credits already spent is still Phase 2.
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

        # R1-9 — claw back the customer's account credit, CAPPED AT THE
        # SHORTFALL (owner decision 2026-06-10): the customer is charged what
        # the vendor shorted us, not the whole credit. Idempotent no-op when
        # none was issued or it was already charged back (e.g. via a prior
        # record_vendor_denial with the same resolution).
        charged_back = 0.0
        if resolution == CoreDenialResolution.CHARGED_TO_CUSTOMER:
            charged_back = self._charge_back_customer_credit(core, max_amount=difference)

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
                "charged_back_to_customer": charged_back,
                "notes": notes,
            },
        )
        self.db.commit()
        return core

    # ── Core Credit Issuance (R3) ────────────────────────────────────────────

    def issue_core_credit(
        self,
        core_charge_id: int,
        credit_method: str,
        check_number: str | None = None,
        notes: str = "",
    ) -> None:
        """
        R3 — Issue credit to the customer for a returned core.

        credit_method values (CoreCreditMethod enum):
          ACCOUNT_CREDIT — increment customer.credit_balance via CRMService.
          CHECK          — create a Payment row with direction=REFUND_TO_CUSTOMER.
                           Does NOT touch credit_balance; wife cuts the check manually.
          HOLD           — record the intent but make no financial change yet.

        Call this when you want to issue credit independently of the inspection
        flow (e.g., for HOLD inspections resolved later, or when the credit
        method should differ from the default ACCOUNT_CREDIT issued by
        record_customer_return/complete_inspection).

        Raises ValueError if:
          - core is not found
          - credit_method is invalid
          - core has no associated customer
          - core.qty_returned is 0 (nothing to credit)
        """
        valid_methods = {
            CoreCreditMethod.ACCOUNT_CREDIT,
            CoreCreditMethod.CHECK,
            CoreCreditMethod.HOLD,
        }
        if credit_method not in valid_methods:
            raise ValueError(
                f"Invalid credit_method '{credit_method}'. "
                f"Must be one of {sorted(valid_methods)}"
            )

        core = self._get_or_404(core_charge_id)
        if core.customer_id is None:
            raise ValueError(
                f"CoreCharge {core_charge_id} has no customer — cannot issue credit"
            )
        if core.qty_returned <= 0:
            raise ValueError(
                f"CoreCharge {core_charge_id} has no returned qty — nothing to credit"
            )

        # BUG-4 fix: idempotency guard. record_customer_return(ACCEPTED) and
        # complete_inspection(ACCEPTED) already issue the customer's account credit
        # and stamp credit_issued_at. Without this guard, calling issue_core_credit
        # afterward credits the customer a SECOND time for the same returned core.
        # If credit was already issued, this is a no-op (financial action skipped);
        # to change the method, reverse the original credit first.
        if core.credit_issued_at is not None:
            log.info(
                "issue_core_credit: CoreCharge %s already credited at %s (method=%s)"
                " — skipping to avoid double credit",
                core_charge_id, core.credit_issued_at, core.credit_method,
            )
            return

        credit_amount = round(core.qty_returned * core.customer_unit_charge, 2)

        if credit_method == CoreCreditMethod.ACCOUNT_CREDIT:
            from app.services.crm_service import CRMService
            CRMService(self.db, self.current_user_id).add_credit(
                customer_id=core.customer_id,
                amount=credit_amount,
                reason=(
                    f"Core return credit (ACCOUNT_CREDIT) for charge #{core_charge_id}"
                    + (f" — {notes}" if notes else "")
                ),
            )
            # Stamp so a subsequent call is a no-op (see guard above).
            core.credit_issued_at = datetime.utcnow()
            core.credit_method = CoreCreditMethod.ACCOUNT_CREDIT

        elif credit_method == CoreCreditMethod.CHECK:
            # Record an outbound payment (wife cuts the actual check)
            from app.constants import PaymentDirection, PaymentMethod, PaymentStatus, QBOSyncStatus
            from app.models.invoice import Payment
            refund = Payment(
                customer_id=core.customer_id,
                payment_date=datetime.utcnow(),
                payment_method=PaymentMethod.CHECK,
                direction=PaymentDirection.REFUND_TO_CUSTOMER,
                check_number=check_number,
                amount_received=-round(credit_amount, 2),  # negative = outflow
                status=PaymentStatus.APPLIED,
                notes=(
                    f"Core return refund check for charge #{core_charge_id}"
                    + (f" — {notes}" if notes else "")
                ),
                qbo_sync_status=QBOSyncStatus.PENDING,
            )
            self.db.add(refund)
            # Stamp so a subsequent call is a no-op (see guard above).
            core.credit_issued_at = datetime.utcnow()
            core.credit_method = CoreCreditMethod.CHECK

        # HOLD: no financial action — just record the intent in the audit log

        self.audit(
            entity_type=EntityType.CORE_CHARGE,
            entity_id=core_charge_id,
            action=AuditAction.STATUS_CHANGED,
            new_value={
                "action": "issue_core_credit",
                "credit_method": credit_method,
                "credit_amount": credit_amount,
                "check_number": check_number,
                "notes": notes,
            },
        )
        self.db.commit()

        # Owner decision 2026-06-16 — an account-credit issuance auto-applies to
        # the core's originating invoice when it is still owing (see helper).
        if credit_method == CoreCreditMethod.ACCOUNT_CREDIT:
            self._auto_apply_core_credit_to_invoice(core, credit_amount)

    # ── Private ───────────────────────────────────────────────────────────────

    def _auto_apply_core_credit_to_invoice(
        self, core: CoreCharge, credit_amount: float
    ) -> float:
        """Owner decision 2026-06-16 — immediately apply a just-issued ACCOUNT_CREDIT
        core-return credit to the core's originating invoice so the customer only
        owes the parts balance (the cores were settled at drop-off), instead of
        leaving the credit floating on their account to chase later.

        Returns the amount actually applied (0.0 when nothing was). It is a no-op —
        the credit simply stays on the customer's account, exactly as before — when:
          - the core isn't linked to an originating invoice (credit_invoice_id),
          - that invoice isn't a finalized, still-owing invoice (OPEN / PARTIAL),
          - or there is nothing applicable (already paid, or no credit available).

        Runs as its own committed step AFTER the credit has been issued; any
        failure here is logged and swallowed so it can NEVER roll back or block
        the core return itself — the credit is still safely on the account.
        """
        if (
            credit_amount <= 0.005
            or core.customer_id is None
            or core.credit_invoice_id is None
        ):
            return 0.0

        from app.constants import InvoiceStatus
        from app.models.customer import Customer
        from app.models.invoice import Invoice

        invoice = (
            self.db.query(Invoice)
            .filter(Invoice.id == core.credit_invoice_id)
            .first()
        )
        # Only auto-apply to a finalized invoice that still owes — never touch a
        # DRAFT, VOID, or already-PAID invoice (those leave the credit floating).
        if invoice is None or invoice.status not in (
            InvoiceStatus.OPEN,
            InvoiceStatus.PARTIAL,
        ):
            return 0.0

        # Clamp to what is actually owed AND actually available. The credit was
        # just added, but the customer's balance could have been negative before
        # (e.g. a prior vendor-denial chargeback), so never apply more than they
        # truly hold or the invoice truly owes.
        customer = (
            self.db.query(Customer).filter(Customer.id == core.customer_id).first()
        )
        available = max(0.0, customer.credit_balance if customer else 0.0)
        apply_amount = round(min(credit_amount, invoice.balance_due, available), 2)
        if apply_amount <= 0.005:
            return 0.0

        try:
            from app.services.payment_service import PaymentService
            PaymentService(self.db, self.current_user_id).apply_account_credit(
                customer_id=core.customer_id,
                invoice_id=invoice.id,
                amount=apply_amount,
            )
            log.info(
                "Auto-applied $%.2f core-return credit (core %s) to invoice %s",
                apply_amount, core.id, invoice.id,
            )
            return apply_amount
        except Exception:
            log.exception(
                "Auto-apply of core-return credit failed (core %s -> invoice %s);"
                " credit remains on the customer account",
                core.id, invoice.id,
            )
            return 0.0

    def _charge_back_customer_credit(
        self, core: CoreCharge, max_amount: float | None = None
    ) -> float:
        """
        R1-9 — Reverse the customer account credit previously issued for this
        core when a vendor denial/shortfall is resolved CHARGED_TO_CUSTOMER.

        max_amount caps the reversal: a PARTIAL vendor shortfall passes the
        shortfall through (owner decision 2026-06-10 — charge the customer
        what the vendor shorted us, never the whole credit), while an outright
        denial (record_vendor_denial) omits it and reverses the full credit.

        Idempotent: only fires when an ACCOUNT_CREDIT was actually issued
        (credit_issued_at stamped AND credit_method == ACCOUNT_CREDIT); after
        reversal credit_method becomes _CREDIT_METHOD_CHARGED_BACK so a second
        call no-ops. CHECK credits are NOT auto-reversed (the refund check
        already went out — a chargeback invoice line is Phase 2). Deducts via
        CRMService (sole owner of credit_balance), allowing the balance to go
        negative when the customer already spent the credit — the negative
        balance records that they owe it back.

        Returns the amount charged back (0.0 when nothing to reverse).
        """
        if core.customer_id is None or core.credit_issued_at is None:
            return 0.0
        if core.credit_method != CoreCreditMethod.ACCOUNT_CREDIT:
            return 0.0  # CHECK/HOLD credit, or already charged back

        # Amount issued: positive ACCEPTED return events carry the exact
        # credited amounts; complete_inspection/issue_core_credit credit
        # qty_returned * unit without an event amount — fall back to that.
        # Each chargeback writes a NEGATIVE event below, so a second
        # return→denial cycle nets out what was already reversed instead of
        # double-deducting round one.
        events = list(core.return_events)
        gross = round(sum(e.credit_amount for e in events if e.credit_amount > 0), 2)
        if gross <= 0:
            gross = round(core.qty_returned * core.customer_unit_charge, 2)
        already_reversed = round(-sum(e.credit_amount for e in events if e.credit_amount < 0), 2)
        issued = round(gross - already_reversed, 2)
        if max_amount is not None:
            issued = round(min(issued, max(0.0, max_amount)), 2)
        if issued <= 0:
            return 0.0

        # Mark BEFORE deducting — deduct_credit commits, so the sentinel lands
        # in the same transaction as the balance change (no re-entry window
        # where the deduction is committed but the idempotency marker is not).
        core.credit_method = _CREDIT_METHOD_CHARGED_BACK
        self.db.add(CoreReturnEvent(
            core_charge_id=core.id,
            qty_returned=0,
            credit_method=_CREDIT_METHOD_CHARGED_BACK,
            credit_amount=-issued,
            processed_by_id=self.current_user_id,
            notes="Chargeback — vendor denial charged to customer",
        ))
        from app.services.crm_service import CRMService
        CRMService(self.db, self.current_user_id).deduct_credit(
            customer_id=core.customer_id,
            amount=issued,
            reason=f"Core charge #{core.id} chargeback — vendor denial charged to customer",
            allow_negative=True,
        )
        self.audit(
            entity_type=EntityType.CORE_CHARGE,
            entity_id=core.id,
            action=AuditAction.STATUS_CHANGED,
            new_value={
                "action": "customer_chargeback",
                "amount": issued,
                "resolution": CoreDenialResolution.CHARGED_TO_CUSTOMER,
            },
        )
        return issued

    def _get_or_404(self, core_charge_id: int) -> CoreCharge:
        core = self.db.query(CoreCharge).filter(CoreCharge.id == core_charge_id).first()
        if core is None:
            raise ValueError(f"CoreCharge {core_charge_id} not found")
        return core

    def _get_vcr_or_404(self, vcr_id: int) -> VendorCoreReturn:
        vcr = (
            self.db.query(VendorCoreReturn)
            .filter(VendorCoreReturn.id == vcr_id)
            .first()
        )
        if vcr is None:
            raise ValueError(f"VendorCoreReturn {vcr_id} not found")
        return vcr

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
