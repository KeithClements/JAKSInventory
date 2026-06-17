"""
app/services/lead_conversion_service.py
========================================
JAK's Lead Finder → ERP conversion service.

This is the ERP (system-of-record) half of the cross-system Lead Finder
integration. A "Convert" in the Lead Finder POSTs a lead PACKET over localhost;
this service dedups the lead against existing customers and then LINKS to an
existing customer or CREATES a new one — but NEVER produces a guaranteed
duplicate for a real FMCSA carrier (one customer per USDOT #).

CONTRACT (shared, identical wording on both endpoints):

  find_matches(lead: dict) -> {
      "candidates": [cand, ...],     # de-duped, capped ~8
      "exact_usdot": cand | None,    # the canonical USDOT match, if any
  }

  convert(packet: dict) -> one of:
      {"action": "created", "customer_id": int,
       "customer_url": "/customers/{id}", "customer_name": str}
      {"action": "linked",  "customer_id": int,
       "customer_url": "/customers/{id}", "customer_name": str}
      {"action": "needs_review", "candidates": [cand, ...], "message": str}

  cand = {
      "customer_id": int, "company_name": str, "phone": str,
      "city": str, "state": str,
      "match_reason": "usdot" | "phone" | "name",
      "confidence": "high" | "medium" | "low",
  }

DEDUP precedence (highest confidence first):
  1. exact usdot_number               → high   / "usdot"
  2. normalized phone (last 10 digits)→ medium  / "phone"
  3. normalized company name          → medium  / "name"

CONVERT MODE behaviour:
  auto   → exact-USDOT? LINK (idempotent). else fuzzy candidates? needs_review
           (NO write). else CREATE.
  create → CREATE, but if an exact-USDOT customer already exists, LINK it
           instead (never a guaranteed duplicate).
  link   → link to link_customer_id (must exist).

Instantiate with current_user_id=None — the integration API has no logged-in
user. audit()/logged_by_id tolerate None (CustomerCallLog.logged_by_id is
nullable; BaseService.assert_can treats None as a system actor).
"""
from __future__ import annotations

import re
from datetime import datetime

from app.constants import (
    ActivityType, AuditAction, CallOutcome, CallType, CustomerType, EntityType,
)
from app.models.customer import Customer, CustomerCallLog, CustomerContact
from app.services.base import BaseService
from app.services.customer_service import CustomerService


# ── Dedup normalizers (contract-defined; intentionally NOT imported from the
#    customers router — this service owns its own copy so the integration's
#    behaviour can never drift when the router's UI heuristics change). ─────────

# Company-name suffix / noise words stripped before comparison (contract list).
_NAME_SUFFIXES: frozenset[str] = frozenset({
    "inc", "llc", "co", "corp", "ltd", "company", "the", "and", "of",
    "services", "group", "enterprises", "trucking", "transport",
    "transportation",
})


def _digits(value) -> str:
    """All digits in ``value`` as a string (drops formatting)."""
    return re.sub(r"\D", "", str(value or ""))


def _phone_key(value) -> str:
    """Last 10 digits of a phone number — the dedup key. '' when too short to
    be a real US/CA number (avoids matching on a stray 1-2 digit fragment)."""
    d = _digits(value)
    if len(d) < 10:
        return ""
    return d[-10:]


def _name_key(value) -> str:
    """Normalized company name: lowercase, drop punctuation, drop the suffix /
    noise words from the contract list, collapse whitespace. '' when nothing
    meaningful remains (so a name made only of suffixes never matches)."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", str(value or "").lower())
    tokens = [t for t in cleaned.split() if t and t not in _NAME_SUFFIXES]
    return "".join(tokens)


# business_type (Lead Finder lead type) → ERP CustomerType (contract table).
_BUSINESS_TYPE_TO_CUSTOMER_TYPE: dict[str, str] = {
    "fleet":          CustomerType.FLEET,
    "repair_shop":    CustomerType.REPAIR_SHOP,
    "dealer":         CustomerType.DEALER,
    "owner_operator": CustomerType.OWNER_OPERATOR,
    "government":     CustomerType.MUNICIPALITY,
    "construction":   CustomerType.OTHER,
    "agriculture":    CustomerType.OTHER,
    "other":          CustomerType.OTHER,
}

# Best-effort Lead Finder activity type → ERP ActivityType (fallback NOTE).
_ACTIVITY_TYPE_MAP: dict[str, str] = {
    "call":          ActivityType.CALL,
    "phone":         ActivityType.CALL,
    "text":          ActivityType.TEXT,
    "sms":           ActivityType.TEXT,
    "email":         ActivityType.EMAIL,
    "counter_visit": ActivityType.COUNTER_VISIT,
    "visit":         ActivityType.COUNTER_VISIT,
    "note":          ActivityType.NOTE,
}

_MAX_CANDIDATES = 8


class LeadConversionService(BaseService):
    """Dedup + link-or-create the ERP customer for a Lead Finder lead."""

    # ════════════════════════════════════════════════════════════════════════
    # Public API
    # ════════════════════════════════════════════════════════════════════════

    def find_matches(self, lead: dict) -> dict:
        """Dedup PREVIEW — NO writes. Returns candidate customers for a lead
        packet's ``lead`` dict, in confidence order (USDOT > phone > name),
        de-duped by customer id and capped at ~8.

        Returns ``{"candidates": [cand, ...], "exact_usdot": cand | None}``.
        """
        lead = lead or {}
        candidates: list[dict] = []
        seen: set[int] = set()
        exact_usdot: dict | None = None

        # 1) exact USDOT — high / "usdot"
        usdot = _coerce_usdot(lead.get("usdot_number"))
        if usdot is not None:
            cust = (
                self.db.query(Customer)
                .filter(Customer.usdot_number == usdot)
                .first()
            )
            if cust is not None:
                cand = _candidate(cust, "usdot", "high")
                exact_usdot = cand
                candidates.append(cand)
                seen.add(cust.id)

        # 2) normalized phone — medium / "phone"
        phone_key = _phone_key(lead.get("phone"))
        if phone_key:
            for cust in self._customers_by_phone(phone_key):
                if cust.id in seen:
                    continue
                candidates.append(_candidate(cust, "phone", "medium"))
                seen.add(cust.id)
                if len(candidates) >= _MAX_CANDIDATES:
                    break

        # 3) normalized company name — medium / "name"
        name_key = _name_key(_company_name(lead))
        if name_key and len(candidates) < _MAX_CANDIDATES:
            for cust in self._customers_by_name(name_key):
                if cust.id in seen:
                    continue
                candidates.append(_candidate(cust, "name", "medium"))
                seen.add(cust.id)
                if len(candidates) >= _MAX_CANDIDATES:
                    break

        return {
            "candidates": candidates[:_MAX_CANDIDATES],
            "exact_usdot": exact_usdot,
        }

    def convert(self, packet: dict) -> dict:
        """Link-or-create per the contract mode logic + idempotency.

        ``packet`` = ``{"lead": {...}, "mode": "auto"|"create"|"link",
        "link_customer_id": int|null}``.

        Returns an action dict (created | linked | needs_review). Raises
        ``ValueError`` for an invalid packet (missing lead, bad link target);
        the API lane maps that to a 422.
        """
        packet = packet or {}
        lead = packet.get("lead") or {}
        if not isinstance(lead, dict) or not _company_name(lead):
            raise ValueError("packet.lead.company_name is required")

        mode = (packet.get("mode") or "auto").strip().lower()
        if mode not in {"auto", "create", "link"}:
            mode = "auto"

        # ── mode=link — explicit target ───────────────────────────────────────
        if mode == "link":
            target_id = packet.get("link_customer_id")
            if target_id is None:
                raise ValueError("link_customer_id is required when mode='link'")
            customer = (
                self.db.query(Customer)
                .filter(Customer.id == int(target_id))
                .first()
            )
            if customer is None:
                raise ValueError(f"link_customer_id {target_id} not found")
            return self._link(customer, lead)

        # Idempotency / never-duplicate-a-DOT guard, shared by auto + create:
        # an existing customer with this exact USDOT is THE canonical link.
        usdot = _coerce_usdot(lead.get("usdot_number"))
        existing_dot = None
        if usdot is not None:
            existing_dot = (
                self.db.query(Customer)
                .filter(Customer.usdot_number == usdot)
                .first()
            )
        if existing_dot is not None:
            return self._link(existing_dot, lead)

        # ── mode=create — create now (DOT guard already handled above) ────────
        if mode == "create":
            return self._create(lead)

        # ── mode=auto — fuzzy candidates short-circuit to needs_review ────────
        matches = self.find_matches(lead)
        fuzzy = [
            c for c in matches["candidates"]
            if c["match_reason"] in ("phone", "name")
        ]
        if fuzzy:
            return {
                "action": "needs_review",
                "candidates": fuzzy,
                "message": (
                    "Possible existing customer(s) found — review before "
                    "creating a new record."
                ),
            }

        return self._create(lead)

    # ════════════════════════════════════════════════════════════════════════
    # Dedup queries
    # ════════════════════════════════════════════════════════════════════════

    def _customers_by_phone(self, phone_key: str) -> list[Customer]:
        """Customers whose primary phone normalizes to ``phone_key`` (last 10
        digits). SQLite has no regexp_replace to normalize in SQL, so we load
        the (small) set of customers with a phone and confirm the normalized
        key in Python — exact last-10-digit equality."""
        rows = (
            self.db.query(Customer)
            .filter(Customer.phone.isnot(None), Customer.phone != "")
            .all()
        )
        return [c for c in rows if _phone_key(c.phone) == phone_key]

    def _customers_by_name(self, name_key: str) -> list[Customer]:
        """Customers whose company name normalizes to ``name_key``. Exact
        normalized equality (the contract's name rule) — not the router's
        soft substring heuristic, to avoid over-linking on the write path."""
        rows = (
            self.db.query(Customer)
            .filter(Customer.company_name.isnot(None), Customer.company_name != "")
            .all()
        )
        return [c for c in rows if _name_key(c.company_name) == name_key]

    # ════════════════════════════════════════════════════════════════════════
    # Write paths
    # ════════════════════════════════════════════════════════════════════════

    def _create(self, lead: dict) -> dict:
        """Build a brand-new Customer from the packet per the contract, apply
        type defaults, attach contacts + activities, commit, return created."""
        customer_type = self._customer_type_for(lead)

        addr = lead.get("address") or {}
        customer = Customer(
            company_name=_company_name(lead),
            contact_name=_primary_contact_name(lead),
            usdot_number=_coerce_usdot(lead.get("usdot_number")),
            phone=_s(lead.get("phone")),
            email=_s(lead.get("email")),
            address_line1=_s(addr.get("street")),
            city=_s(addr.get("city")),
            state=_s(addr.get("state")),
            zip_code=_s(addr.get("zip")),
            internal_notes=_compose_internal_notes(lead),
            account_number="",
        )
        self.db.add(customer)
        # Stamp type + type-driven defaults (terms / tier / tax-exempt / …).
        # We set the type explicitly above-by-default via apply_type_defaults so
        # an empty `provided` lets every default fill (matches the customer
        # create handler convention).
        CustomerService(self.db).apply_type_defaults(customer, customer_type)
        self.db.flush()  # assign customer.id for child rows + audit

        self._attach_contacts(customer, lead, existing_names=set())
        self._attach_import_activities(customer, lead)

        self.audit(
            entity_type=EntityType.CUSTOMER,
            entity_id=customer.id,
            action=AuditAction.CREATED,
            new_value={
                "company_name": customer.company_name,
                "usdot_number": customer.usdot_number,
                "source": "leadfinder",
                "lead_id": lead.get("lead_id"),
            },
            notes="Created from JAK's Lead Finder",
        )
        self.db.commit()
        return _result("created", customer)

    def _link(self, customer: Customer, lead: dict) -> dict:
        """Transfer the packet onto an existing customer: set USDOT if blank,
        append notes, merge new contacts (dedupe by name), add the import
        activity. Idempotent — re-running links the same customer again and
        never creates a second customer for the DOT."""
        usdot = _coerce_usdot(lead.get("usdot_number"))
        if usdot is not None and customer.usdot_number is None:
            customer.usdot_number = usdot

        # Append the lead's notes to internal_notes (never clobber).
        note_block = _compose_internal_notes(lead)
        if note_block:
            existing = (customer.internal_notes or "").rstrip()
            customer.internal_notes = (
                f"{existing}\n\n{note_block}" if existing else note_block
            )

        existing_names = {
            (c.name or "").strip().lower()
            for c in (customer.contacts or [])
        }
        # The quick-access primary contact name also counts as "already present".
        if customer.contact_name:
            existing_names.add(customer.contact_name.strip().lower())

        self.db.flush()
        self._attach_contacts(customer, lead, existing_names=existing_names)
        self._attach_import_activities(customer, lead)

        self.audit(
            entity_type=EntityType.CUSTOMER,
            entity_id=customer.id,
            action=AuditAction.EDITED,
            new_value={
                "linked_from": "leadfinder",
                "lead_id": lead.get("lead_id"),
                "usdot_number": customer.usdot_number,
            },
            notes="Linked from JAK's Lead Finder",
        )
        self.db.commit()
        return _result("linked", customer)

    # ── Child-row helpers ─────────────────────────────────────────────────────

    def _attach_contacts(
        self, customer: Customer, lead: dict, *, existing_names: set[str]
    ) -> None:
        """Create CustomerContact rows from the packet, preserving is_primary
        and using role as the title when title is missing. Dedupes by
        case-insensitive name against ``existing_names`` (and within the packet
        itself)."""
        seen = set(existing_names)
        for raw in (lead.get("contacts") or []):
            if not isinstance(raw, dict):
                continue
            name = _s(raw.get("name")).strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            title = _s(raw.get("title")) or _s(raw.get("role"))
            phone = _s(raw.get("phone")) or _s(raw.get("mobile"))
            self.db.add(CustomerContact(
                customer_id=customer.id,
                name=name,
                title=title or None,
                phone=phone or None,
                email=_s(raw.get("email")) or None,
                is_primary=bool(raw.get("is_primary")),
            ))
        self.db.flush()

    def _attach_import_activities(self, customer: Customer, lead: dict) -> None:
        """One 'Imported from JAK's Lead Finder' activity plus a CustomerCallLog
        row per packet activity (best-effort type map, fallback NOTE)."""
        lead_id = lead.get("lead_id")
        score = lead.get("lead_score")
        self.db.add(CustomerCallLog(
            customer_id=customer.id,
            logged_by_id=self.current_user_id,
            call_type=CallType.INBOUND,
            activity_type=ActivityType.NOTE,
            outcome=None,
            notes=(
                f"Imported from JAK's Lead Finder "
                f"(lead #{lead_id}, score {score})"
            ),
        ))

        for raw in (lead.get("activities") or []):
            if not isinstance(raw, dict):
                continue
            atype = _ACTIVITY_TYPE_MAP.get(
                str(raw.get("type") or "").strip().lower(), ActivityType.NOTE
            )
            parts = [p for p in (_s(raw.get("subject")), _s(raw.get("body"))) if p]
            note = " — ".join(parts) if parts else _s(raw.get("type")) or "Activity"
            outcome = raw.get("outcome")
            outcome = outcome if outcome in set(CallOutcome) else None
            self.db.add(CustomerCallLog(
                customer_id=customer.id,
                logged_by_id=self.current_user_id,
                call_type=CallType.INBOUND,
                activity_type=atype,
                outcome=outcome,
                notes=note,
            ))
        self.db.flush()

    # ── Mapping helpers ───────────────────────────────────────────────────────

    def _customer_type_for(self, lead: dict) -> str:
        """Map business_type → CustomerType (contract table). A concrete mapping
        (fleet/repair_shop/dealer/owner_operator/government/construction/
        agriculture) wins. For 'other' / null / unknown, fall to FLEET when the
        lead has power units, else OTHER."""
        bt = str(lead.get("business_type") or "").strip().lower()
        mapped = _BUSINESS_TYPE_TO_CUSTOMER_TYPE.get(bt)
        if mapped is not None and bt not in ("", "other"):
            return mapped
        # other / null / unknown → fleet if power_units>=1 else other
        try:
            power_units = int(lead.get("power_units") or 0)
        except (TypeError, ValueError):
            power_units = 0
        return CustomerType.FLEET if power_units >= 1 else CustomerType.OTHER


# ── Module-level helpers ─────────────────────────────────────────────────────


def _s(value) -> str:
    """None-safe string coercion (strips nothing; preserves caller content)."""
    return "" if value is None else str(value)


def _coerce_usdot(value) -> int | None:
    """Coerce a packet USDOT value to int, or None when blank/garbage."""
    if value is None:
        return None
    try:
        d = _digits(value)
        return int(d) if d else None
    except (TypeError, ValueError):
        return None


def _company_name(lead: dict) -> str:
    """Contract: company name = dba_name or legal_name or company_name."""
    return (
        _s(lead.get("dba_name")).strip()
        or _s(lead.get("legal_name")).strip()
        or _s(lead.get("company_name")).strip()
    )


def _primary_contact_name(lead: dict) -> str:
    """Primary contact name from the packet, else ''."""
    for raw in (lead.get("contacts") or []):
        if isinstance(raw, dict) and raw.get("is_primary") and raw.get("name"):
            return _s(raw["name"]).strip()
    # Fall back to the first named contact.
    for raw in (lead.get("contacts") or []):
        if isinstance(raw, dict) and raw.get("name"):
            return _s(raw["name"]).strip()
    return ""


def _compose_internal_notes(lead: dict) -> str:
    """Join the lead's notes with date headers + a final Lead Finder summary
    line (contract wording)."""
    blocks: list[str] = []
    for note in (lead.get("notes") or []):
        if not isinstance(note, dict):
            continue
        body = _s(note.get("body")).strip()
        if not body:
            continue
        created = _s(note.get("created_at")).strip()
        ntype = _s(note.get("type")).strip()
        header_bits = [b for b in (ntype, created) if b]
        header = f"[{' · '.join(header_bits)}] " if header_bits else ""
        blocks.append(f"{header}{body}")

    platforms = _s(lead.get("engine_platforms")).strip()
    engines = _s(lead.get("known_engines")).strip()
    engine_desc = platforms or engines or "n/a"
    summary = (
        f"Lead Finder: USDOT {lead.get('usdot_number')}, "
        f"power units {lead.get('power_units')}, "
        f"engines {engine_desc}, "
        f"lead score {lead.get('lead_score')}, "
        f"origin {_s(lead.get('origin'))}"
    )
    blocks.append(summary)
    return "\n".join(blocks)


def _candidate(customer: Customer, match_reason: str, confidence: str) -> dict:
    """Shape a Customer into the contract ``cand`` dict."""
    return {
        "customer_id": customer.id,
        "company_name": customer.company_name or "",
        "phone": customer.phone or "",
        "city": customer.city or "",
        "state": customer.state or "",
        "match_reason": match_reason,
        "confidence": confidence,
    }


def _result(action: str, customer: Customer) -> dict:
    """Shape the created/linked action result."""
    return {
        "action": action,
        "customer_id": customer.id,
        "customer_url": f"/customers/{customer.id}",
        "customer_name": customer.company_name or customer.display_name,
    }
