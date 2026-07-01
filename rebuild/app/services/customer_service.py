"""
app/services/customer_service.py
================================
Phase 2 Customer foundations (PHASE_2_PLAN.md §4):

  • Structured flags (§4.3 / P2-D2)        — flags_for / has_flag / set_flag
  • Type-driven defaults (§4.1 / P2-D1/D6) — resolve_defaults / apply_type_defaults
  • Credit warn + hold (§4.5 / P2-D4/D5)   — would_exceed_credit / credit_status

These are CONTRACTS the UI wires against; this lane owns the service + the data
model, never the templates. Open-AR / available-credit numbers come from the
single CustomerMetricsService definition (P2-Q1) so every surface agrees.
"""
from __future__ import annotations

import re

from app.constants import (
    CustomerFlag, CustomerStatus, CustomerType, PaymentTerms, PricingTier,
    PreferredContactMethod, CUSTOMER_STORED_FLAGS,
)
from app.models.customer import Customer, CustomerTypeDefault
from app.services.base import BaseService
from app.services.customer_metrics_service import CustomerMetricsService


# ── Phone / email validation helpers (QA: dedup-by-phone + server-side email) ──
# Module-level pure functions so the router, the service, and the tests all share
# ONE normalization rule. Kept here (this lane's scoped file) rather than in the
# shared app/utils.py.

# A US phone is 10 digits; an 11-digit number that starts with a leading "1"
# (the country code) is the SAME line as its 10-digit form. We strip the leading
# country-code "1" so "1-720-555-1234" and "(720) 555-1234" collapse together.
def normalize_phone(raw: str | None) -> str:
    """Return the comparable digit string for a phone number.

    Strips every non-digit (spaces, dashes, parens, dots, '+'), then drops a
    single leading US country-code '1' when the result is 11 digits so that
    "+1 (720) 555-1234", "1-720-555-1234", "720.555.1234" and "7205551234" all
    normalize to the same key. Returns "" for blank/None — callers treat an
    empty key as "no phone on file" and never collide those together."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


# Pragmatic, dependency-free email check (RFC-5322 is intentionally not the bar):
# exactly one "@", a non-empty local part with no spaces, and a domain that has
# at least one dot with a 2+ char TLD. Rejects "notanemail", "a@b", "a b@c.com".
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def is_valid_email(raw: str | None) -> bool:
    """True when ``raw`` looks like a deliverable email address. A BLANK value is
    NOT valid here — callers decide whether blank is allowed (it is, on create/
    update) and only invoke this when a value was actually provided. Stdlib-only;
    no new dependency."""
    em = (raw or "").strip()
    return bool(_EMAIL_RE.match(em))


# ── Flag storage policy ─────────────────────────────────────────────────────────
# Only these flags are persisted in the Customer.flags CSV. TAX_EXEMPT and
# TEXT_PREFERRED are DERIVED from canonical columns (is_tax_exempt /
# preferred_contact_method) so a chip can never disagree with the field that
# actually governs tax/comms behaviour. The read-side merge lives on
# Customer.flag_keys (the UI's single derivation point); these writers keep the
# CSV column in sync. Single source of the stored set is constants.CUSTOMER_STORED_FLAGS.
_STORED_FLAGS: frozenset[str] = CUSTOMER_STORED_FLAGS

# Stable display order = enum definition order.
_FLAG_ORDER: list[str] = list(CustomerFlag)


# ── In-code type-default profiles (P2-D1) ────────────────────────────────────────
# Starting defaults for a diesel-parts shop. These are the source of truth for a
# fresh install; the owner can later override per type via a customer_type_defaults
# row (Settings → Customer Defaults). resolve_defaults() overlays any DB row on top
# of these, so the service works with or without the table being seeded.
#
# card_surcharge_pct = None means "use the system cc_surcharge_pct setting".
_DEFAULT_PROFILE: dict = {
    "payment_terms": PaymentTerms.COD,
    "pricing_tier": PricingTier.STANDARD,
    "discount_pct": 0.0,
    "credit_limit": 0.0,
    "is_tax_exempt": False,
    "card_surcharge_pct": None,
    "apply_card_surcharge_default": False,
    "status_active": True,
    "same_as_billing_default": True,
}

_TYPE_DEFAULTS: dict[str, dict] = {
    CustomerType.FLEET: {
        **_DEFAULT_PROFILE,
        "payment_terms": PaymentTerms.NET_30,
        "pricing_tier": PricingTier.FLEET,
    },
    CustomerType.OWNER_OPERATOR: {
        **_DEFAULT_PROFILE,
        "payment_terms": PaymentTerms.COD,
        "pricing_tier": PricingTier.STANDARD,
        # Owner-operators frequently pay by card at the counter.
        "apply_card_surcharge_default": True,
    },
    CustomerType.REPAIR_SHOP: {
        **_DEFAULT_PROFILE,
        "payment_terms": PaymentTerms.NET_30,
        "pricing_tier": PricingTier.WHOLESALE,
    },
    CustomerType.DEALER: {
        **_DEFAULT_PROFILE,
        "payment_terms": PaymentTerms.NET_30,
        "pricing_tier": PricingTier.DEALER,
        "is_tax_exempt": True,            # resale
    },
    CustomerType.MUNICIPALITY: {
        **_DEFAULT_PROFILE,
        "payment_terms": PaymentTerms.NET_30,
        "is_tax_exempt": True,            # government
    },
    CustomerType.INTERNAL: {
        **_DEFAULT_PROFILE,
        "payment_terms": PaymentTerms.COD,
        "is_tax_exempt": True,            # internal transfers
    },
    CustomerType.OTHER: dict(_DEFAULT_PROFILE),   # global fallback
}

# Default-profile keys → Customer column names (only those that map to a real
# Customer column; apply_card_surcharge_default / same_as_billing_default are
# form-behaviour hints with no Customer column yet — §4.2).
_PROFILE_TO_COLUMN: dict[str, str] = {
    "payment_terms": "payment_terms",
    "pricing_tier": "pricing_tier",
    "discount_pct": "discount_pct",
    "credit_limit": "credit_limit",
    "is_tax_exempt": "is_tax_exempt",
    "card_surcharge_pct": "card_surcharge_pct",
    "status_active": "is_active",
}


def _normalize_type(customer_type) -> str:
    """Coerce an arbitrary value to a valid CustomerType, defaulting to OTHER."""
    try:
        return CustomerType(str(customer_type))
    except ValueError:
        return CustomerType.OTHER


class CustomerService(BaseService):
    """Customer flags, type-driven defaults, and credit warn/hold (Phase 2 §4)."""

    # ════════════════════════════════════════════════════════════════════════
    # Flags (§4.3 / P2-D2)
    # ════════════════════════════════════════════════════════════════════════

    def flags_for(self, customer: Customer) -> list[CustomerFlag]:
        """The merged, authoritative set of flags for display, as CustomerFlag
        values. Delegates to Customer.flag_keys (the single derivation point the
        chips macro also calls) so the service and template never diverge: stored
        CSV flags plus TAX_EXEMPT / TEXT_PREFERRED derived from the canonical
        columns, in stable enum order."""
        return [CustomerFlag(k) for k in customer.flag_keys]

    def has_flag(self, customer: Customer, flag: str) -> bool:
        return CustomerFlag(flag) in self.flags_for(customer)

    def set_flag(self, customer: Customer, flag: str, on: bool = True) -> None:
        """Toggle one flag, routing to the canonical field where one exists.

        TAX_EXEMPT → is_tax_exempt; TEXT_PREFERRED → preferred_contact_method;
        every other flag is stored in the Customer.flags CSV. Mutates + flushes;
        the CALLER commits (BaseService convention)."""
        flag = CustomerFlag(flag)
        if flag == CustomerFlag.TAX_EXEMPT:
            customer.is_tax_exempt = bool(on)
        elif flag == CustomerFlag.TEXT_PREFERRED:
            if on:
                customer.preferred_contact_method = PreferredContactMethod.TEXT
            elif customer.preferred_contact_method == PreferredContactMethod.TEXT:
                customer.preferred_contact_method = PreferredContactMethod.PHONE
        else:
            stored = set(self._parse_stored(customer.flags))
            if on:
                stored.add(flag)
            else:
                stored.discard(flag)
            customer.flags = self._serialize_stored(stored)
            # Sync customer_status with the Credit-Hold flag so both fields
            # always agree (go-live bug #3: dual-state misread).
            if flag == CustomerFlag.CREDIT_HOLD:
                customer.customer_status = (
                    CustomerStatus.CREDIT_HOLD if on else CustomerStatus.ACTIVE
                )
        self.db.flush()

    def set_flags(self, customer: Customer, flags: list[str]) -> None:
        """Replace the full flag set with ``flags`` (canonical + stored both
        reconciled). Any flag absent from ``flags`` is turned off — including
        TAX_EXEMPT / TEXT_PREFERRED, which it writes to their canonical columns.
        Use this only when ``flags`` is the SOLE source for all six flags
        (programmatic / tests). For the edit form — where tax-exempt and
        contact-method have their own inputs — use set_stored_flags instead."""
        wanted = {CustomerFlag(f) for f in flags}
        for flag in CustomerFlag:
            self.set_flag(customer, flag, on=(flag in wanted))

    def set_stored_flags(self, customer: Customer, flags: list[str]) -> None:
        """Replace ONLY the CSV-backed flags (Requires-PO, Credit-Hold,
        Call-first, Warranty-escalation). TAX_EXEMPT / TEXT_PREFERRED in the
        input are ignored — those stay owned by the tax-exempt checkbox and the
        contact-method select and are merged into flags_for() read-only. This is
        the safe method for bulk-saving the flags chip editor without clobbering
        tax/comms. Mutates + flushes; the CALLER commits."""
        wanted = {
            CustomerFlag(f) for f in flags
            if str(f) in _STORED_FLAGS
        }
        customer.flags = self._serialize_stored(wanted)
        # Sync customer_status with the Credit-Hold flag (go-live bug #3).
        if CustomerFlag.CREDIT_HOLD in wanted:
            customer.customer_status = CustomerStatus.CREDIT_HOLD
        elif customer.customer_status == CustomerStatus.CREDIT_HOLD:
            # Only revert if credit hold was what caused the status; leave
            # 'inactive' alone (that mirrors is_active=False, not the flag).
            customer.customer_status = CustomerStatus.ACTIVE
        self.db.flush()

    def is_on_credit_hold(self, customer: Customer) -> bool:
        """Credit-hold is a manual flag (P2-D5). Convenience reader."""
        return self.has_flag(customer, CustomerFlag.CREDIT_HOLD)

    @staticmethod
    def _parse_stored(raw: str | None) -> list[str]:
        """Parse the CSV, keeping only known stored flags (ignores blanks /
        unknown / derived values so the column can never carry junk)."""
        if not raw:
            return []
        out = []
        for token in str(raw).split(","):
            token = token.strip()
            if token in _STORED_FLAGS and token not in out:
                out.append(token)
        return out

    @staticmethod
    def _serialize_stored(flags: set[str]) -> str:
        """Serialize the stored subset only, in stable enum order."""
        return ",".join(f for f in _FLAG_ORDER if f in flags and f in _STORED_FLAGS)

    # ════════════════════════════════════════════════════════════════════════
    # Type-driven defaults (§4.1 / P2-D1, P2-D6)
    # ════════════════════════════════════════════════════════════════════════

    def resolve_defaults(self, customer_type) -> dict:
        """Resolve the default profile for a Customer Type.

        In-code defaults overlaid by any customer_type_defaults row (so the owner
        can customise without a code change). Unknown types fall back to OTHER.
        Always returns a dict including the resolved ``customer_type`` key."""
        ct = _normalize_type(customer_type)
        profile = dict(_TYPE_DEFAULTS.get(ct, _TYPE_DEFAULTS[CustomerType.OTHER]))

        row = (
            self.db.query(CustomerTypeDefault)
            .filter(CustomerTypeDefault.customer_type == ct)
            .first()
        )
        if row is not None:
            profile.update({
                "payment_terms": row.payment_terms,
                "pricing_tier": row.pricing_tier,
                "discount_pct": row.discount_pct,
                "credit_limit": row.credit_limit,
                "is_tax_exempt": row.is_tax_exempt,
                "card_surcharge_pct": row.card_surcharge_pct,
                "apply_card_surcharge_default": row.apply_card_surcharge_default,
                "status_active": row.status_active,
                "same_as_billing_default": row.same_as_billing_default,
            })
        profile["customer_type"] = ct
        return profile

    def all_type_defaults(self) -> dict[str, dict]:
        """{type: resolved-profile} for every CustomerType — for the new-customer
        form to embed and pre-fill client-side on type change."""
        return {ct.value: self.resolve_defaults(ct) for ct in CustomerType}

    def ensure_type_defaults_seeded(self) -> int:
        """Insert a customer_type_defaults row for any CustomerType that lacks
        one, from the in-code profiles, so the owner has editable rows. Idempotent
        (only fills missing types — never overwrites a customised row). Returns
        the number of rows inserted. Called at startup; resolve_defaults works
        even if this never runs (it falls back to the in-code profiles)."""
        existing = {
            row.customer_type
            for row in self.db.query(CustomerTypeDefault.customer_type).all()
        }
        inserted = 0
        for ct in CustomerType:
            if ct in existing:
                continue
            p = _TYPE_DEFAULTS.get(ct, _TYPE_DEFAULTS[CustomerType.OTHER])
            self.db.add(CustomerTypeDefault(
                customer_type=ct,
                payment_terms=p["payment_terms"],
                pricing_tier=p["pricing_tier"],
                discount_pct=p["discount_pct"],
                credit_limit=p["credit_limit"],
                is_tax_exempt=p["is_tax_exempt"],
                card_surcharge_pct=p["card_surcharge_pct"],
                apply_card_surcharge_default=p["apply_card_surcharge_default"],
                status_active=p["status_active"],
                same_as_billing_default=p["same_as_billing_default"],
            ))
            inserted += 1
        if inserted:
            self.db.commit()
        return inserted

    def apply_type_defaults(
        self,
        customer: Customer,
        customer_type,
        *,
        provided: set[str] | None = None,
    ) -> dict:
        """Stamp the type + pre-fill defaults onto a (usually new) customer.

        Always sets ``customer.customer_type``. For each profile field that maps
        to a Customer column, fills the value UNLESS the caller already set it
        explicitly (its name is in ``provided``) — so an explicit form value
        always wins over the type default. ``provided`` uses profile-field names
        (e.g. ``"payment_terms"``, ``"is_tax_exempt"``). Returns the resolved
        profile. Mutates the customer; the CALLER commits."""
        provided = provided or set()
        profile = self.resolve_defaults(customer_type)
        customer.customer_type = profile["customer_type"]
        for field, column in _PROFILE_TO_COLUMN.items():
            if field in provided:
                continue
            setattr(customer, column, profile[field])
        return profile

    # ════════════════════════════════════════════════════════════════════════
    # Credit warn + hold (§4.5 / P2-D4) — WARN ONLY, never blocks
    # ════════════════════════════════════════════════════════════════════════

    def would_exceed_credit(self, customer: Customer, amount: float) -> bool:
        """True if posting ``amount`` would push the customer's open AR above
        their credit limit. A limit of 0 means "no limit enforced" (Phase-1
        convention) → always False. This is advisory only (P2-D4): callers WARN,
        they never block."""
        limit = float(customer.credit_limit or 0.0)
        if limit <= 0:
            return False
        open_ar = CustomerMetricsService(self.db).open_ar(customer.id)
        # §23.3 Phase 1 — held account credit offsets the customer's exposure, so
        # test the prospective charge against AR NET of credits (consistent with
        # the available_credit computation in credit_status / CustomerMetrics).
        credit_balance = float(customer.credit_balance or 0.0)
        return (open_ar - credit_balance + float(amount or 0.0)) > limit + 1e-6

    def credit_status(self, customer: Customer, prospective_amount: float = 0.0) -> dict:
        """Full credit picture for a customer, optionally testing a prospective
        new charge. WARN-ONLY — never raises, never blocks (P2-D4).

        Returns::

            {
              "credit_limit": float,
              "open_ar": float,
              "available_credit": float | None,   # None = no limit set
              "on_hold": bool,                     # manual Credit-Hold flag (P2-D5)
              "over_limit": bool,                  # prospective charge exceeds limit
              "warn": bool,                        # on_hold or over_limit
              "message": str,                      # human warning ("" when no warn)
            }
        """
        limit = float(customer.credit_limit or 0.0)
        open_ar = CustomerMetricsService(self.db).open_ar(customer.id)
        # §23.3 Phase 1 — held account credit offsets AR exposure in every part of
        # the credit picture (available headroom, the over-limit test, and the
        # projected-balance message) so all three stay mutually consistent.
        credit_balance = float(customer.credit_balance or 0.0)
        has_limit = limit > 0
        available = round(limit - open_ar + credit_balance, 2) if has_limit else None
        on_hold = self.is_on_credit_hold(customer)
        over_limit = self.would_exceed_credit(customer, prospective_amount)

        messages: list[str] = []
        if on_hold:
            messages.append("Customer is on Credit Hold.")
        if over_limit:
            projected = round(open_ar - credit_balance + float(prospective_amount or 0.0), 2)
            messages.append(
                f"This would put the balance at ${projected:,.2f}, over the "
                f"${limit:,.2f} credit limit."
            )
        return {
            "credit_limit": round(limit, 2),
            "open_ar": round(open_ar, 2),
            "available_credit": available,
            "on_hold": on_hold,
            "over_limit": over_limit,
            "warn": bool(on_hold or over_limit),
            "message": " ".join(messages),
        }

    # ════════════════════════════════════════════════════════════════════════
    # Last-Contacted helper (#5)
    # ════════════════════════════════════════════════════════════════════════

    # ════════════════════════════════════════════════════════════════════════
    # Dedup helpers (QA: phone parity with the email dedup)
    # ════════════════════════════════════════════════════════════════════════

    def find_by_phone(self, phone: str | None, exclude_id: int | None = None) -> Customer | None:
        """Return an ACTIVE customer whose phone normalizes to the same digits as
        ``phone`` (ignoring spaces/dashes/parens and a leading country-code '1'),
        or None. Parallels the router's _find_customer_by_email but for phone —
        the key counter staff actually look people up by.

        A blank/normalization-empty phone is EXEMPT: it returns None so the many
        customers with no phone on file never collapse into one another. Unlike
        email this is NOT a unique DB key, so the caller treats a hit as a soft
        warn (override-able), not a hard block."""
        key = normalize_phone(phone)
        if not key:
            return None
        # Pre-filter in SQL to non-blank phones on active rows, then compare on
        # the normalized key in Python (SQLite can't strip punctuation in-query
        # and phone formats are inconsistent across imports).
        q = self.db.query(Customer).filter(
            Customer.is_active == True,  # noqa: E712
            Customer.phone != "",
        )
        if exclude_id:
            q = q.filter(Customer.id != exclude_id)
        for cand in q.all():
            if normalize_phone(cand.phone) == key:
                return cand
        return None

    def last_contacted(self, customer_id: int):
        """Return the most recent ``CustomerCallLog.logged_at`` datetime for
        this customer, or ``None`` when no call log exists.

        For single-customer use (preview panel, detail page, timeline header).
        For the customer LIST, build a single grouped query on the caller side
        to avoid N+1 — see the list route in customers.py.
        """
        from sqlalchemy import func as _func
        from app.models.customer import CustomerCallLog as _CCL
        return (
            self.db.query(_func.max(_CCL.logged_at))
            .filter(_CCL.customer_id == customer_id)
            .scalar()
        )
