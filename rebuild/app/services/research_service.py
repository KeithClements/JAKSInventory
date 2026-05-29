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

    def suggest_promote_to_proven(self, cross_ref_id: int) -> bool:
        """
        R5 — Return True if the cross-reference has been used in ≥3 successful sales.

        The UI surfaces this as a "Promote to Proven" suggestion. The user must
        explicitly confirm; this method only answers the eligibility question.

        Returns False (not True) if the cross-reference is not found — caller treats
        a missing cross-ref as ineligible rather than crashing.
        """
        from app.models.product import CrossReference
        xref = (
            self.db.query(CrossReference)
            .filter(CrossReference.id == cross_ref_id)
            .first()
        )
        if xref is None:
            return False
        return (xref.successful_sale_count or 0) >= 3

    def generate_dealer_request_template(self, item: ResearchItem) -> str:
        """Returns plain-text template for copy/paste into a dealer email or message."""
        from app.services.messaging_service import MessagingService
        from app.settings_utils import get_setting_value_db
        rendered = MessagingService(self.db, self.current_user_id).render_template(
            "dealer_parts_request",
            {
                "ri_number": item.ri_number or "",
                "part_description": item.search_term or "",
                "oem_number": item.oem_number or "",
                "esn": item.esn or "",
                "engine_model": item.engine_model or "",
                "vin": item.vin or "",
                "company_phone": get_setting_value_db(self.db, "company_phone", ""),
            },
        )
        return rendered.body

    def generate_vendor_request_template(self, item: ResearchItem) -> str:
        """Returns plain-text template for copy/paste into a vendor email or message."""
        from app.services.messaging_service import MessagingService
        from app.settings_utils import get_setting_value_db
        urgency = getattr(item, "urgency", "") or ""
        rendered = MessagingService(self.db, self.current_user_id).render_template(
            "vendor_parts_request",
            {
                "ri_number": item.ri_number or "",
                "part_description": item.search_term or "",
                "oem_number": item.oem_number or "",
                "esn": item.esn or "",
                "engine_model": item.engine_model or "",
                "urgency": urgency,
                "company_phone": get_setting_value_db(self.db, "company_phone", ""),
            },
        )
        return rendered.body
