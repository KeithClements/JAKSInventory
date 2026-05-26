"""
app/services/research_service.py
=================================
Manages research items (unknown parts tracked through the quote workflow).

Number format: RI-2026-XXXX via bump_counter("next_ri_number").

Caller is responsible for committing the session.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.constants import (
    ResearchStatus, ResearchActivityType, CrossRefStatus, EntityType, AuditAction
)
from app.models.research import ResearchItem, ResearchActivityLog
from app.services.base import BaseService
from app.settings_utils import bump_counter


class ResearchService(BaseService):

    def create_research_item(
        self,
        *,
        search_term: str,
        customer_id: int | None = None,
        quote_id: int | None = None,
        quote_line_id: int | None = None,
        oem_number: str = "",
        vin: str = "",
        esn: str = "",
        engine_model: str = "",
        notes: str = "",
        urgency: str = "normal",
        callback_due_at: datetime | None = None,
    ) -> ResearchItem:
        ri_number = bump_counter(self.db, "next_ri_number", prefix="RI", year=datetime.utcnow().year)
        item = ResearchItem(
            ri_number=ri_number,
            search_term=search_term,
            customer_id=customer_id,
            quote_id=quote_id,
            quote_line_id=quote_line_id,
            oem_number=oem_number,
            vin=vin,
            esn=esn,
            engine_model=engine_model,
            notes=notes,
            urgency=urgency,
            callback_due_at=callback_due_at,
            assigned_user_id=self.current_user_id,
        )
        self.db.add(item)
        self.db.flush()
        self.audit(EntityType.QUOTE, item.id, AuditAction.CREATED)
        return item

    def update_research_status(self, item: ResearchItem, status: str, notes: str = "") -> None:
        old_status = item.status
        item.status = status
        self.log_activity(
            item,
            activity_type=ResearchActivityType.STATUS_CHANGE,
            notes=f"{old_status} → {status}" + (f": {notes}" if notes else ""),
        )

    def log_activity(
        self,
        item: ResearchItem,
        activity_type: str = ResearchActivityType.NOTE,
        notes: str = "",
    ) -> ResearchActivityLog:
        entry = ResearchActivityLog(
            research_item_id=item.id,
            activity_type=activity_type,
            notes=notes,
            logged_by_id=self.current_user_id,
        )
        self.db.add(entry)
        self.db.flush()
        return entry

    def resolve_research(
        self,
        item: ResearchItem,
        *,
        resolved_product_id: int,
        resolution_notes: str = "",
    ) -> ResearchItem:
        """
        Mark the research item resolved. Caller must prompt the user before
        creating a cross-reference (do NOT auto-create — user must confirm first).
        """
        item.status = ResearchStatus.RESOLVED
        item.resolved_at = datetime.utcnow()
        item.resolved_product_id = resolved_product_id
        item.resolution_notes = resolution_notes
        self.log_activity(
            item,
            activity_type=ResearchActivityType.FOUND_PART,
            notes=f"Resolved to product ID {resolved_product_id}. {resolution_notes}".strip(),
        )
        return item

    def generate_dealer_request_template(self, item: ResearchItem) -> str:
        """Returns plain-text template for copy/paste into a dealer email or message."""
        lines = [
            "PARTS REQUEST",
            f"Reference: {item.ri_number}",
            "",
        ]
        if item.search_term:
            lines.append(f"Part Description: {item.search_term}")
        if item.oem_number:
            lines.append(f"OEM Part Number:  {item.oem_number}")
        if item.esn:
            lines.append(f"Engine Serial Number (ESN): {item.esn}")
        if item.engine_model:
            lines.append(f"Engine Model: {item.engine_model}")
        if item.vin:
            lines.append(f"VIN: {item.vin}")
        lines += ["", "Please advise on availability and price.", "Thank you"]
        return "\n".join(lines)

    def generate_vendor_request_template(self, item: ResearchItem) -> str:
        """Returns plain-text template for copy/paste into a vendor email or message."""
        lines = [
            "PARTS AVAILABILITY INQUIRY",
            f"Reference: {item.ri_number}",
            "",
        ]
        if item.search_term:
            lines.append(f"Part Description: {item.search_term}")
        if item.oem_number:
            lines.append(f"OEM/Cross Reference: {item.oem_number}")
        if item.esn:
            lines.append(f"ESN: {item.esn}")
        if item.engine_model:
            lines.append(f"Engine: {item.engine_model}")
        lines += [
            "",
            "Please confirm availability, lead time, and pricing.",
            "Thank you",
        ]
        return "\n".join(lines)
