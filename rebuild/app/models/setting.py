from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Setting(Base):
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # ── Default keys seeded on first run (see routers/settings.py seed_settings) ──
    #
    # ── Company ──────────────────────────────────────────────────────────────
    # company_name                  → "JAKS"
    # company_address               → ""
    # company_phone                 → ""
    # company_email                 → ""
    # invoice_notes                 → ""   (footer text on all invoices)
    #
    # ── Pricing ──────────────────────────────────────────────────────────────
    # cc_surcharge_pct              → "3.0"
    # default_markup_pct            → "30.0"
    # default_restock_fee_percent   → "15.0"
    # default_fuel_service_charge   → "0.0"
    # default_shipping_charge       → "0.0"
    #
    # ── Core Policy ──────────────────────────────────────────────────────────
    # default_core_return_days      → "30"
    #
    # ── Invoice Lock ─────────────────────────────────────────────────────────
    # business_close_time           → "23:59"   (HH:MM, local time)
    #
    # ── Sequence Counters ────────────────────────────────────────────────────
    # current_sequence_year         → "2026"    (resets counters on year change)
    # next_invoice_number           → "1"
    # next_quote_number             → "1"
    # next_so_number                → "1"
    # next_po_number                → "1"
    # next_ra_number                → "1"       (Return Authorizations)
    # next_wc_number                → "1"       (Warranty Claims)
    # next_ri_number                → "1"       (Research Items: RI-2026-XXXX)
    # next_core_slip_number         → "1"       (Core Return Slips: CORE-2026-XXXX)
    # next_vcr_number               → "1"       (Vendor Core Returns: VCR-2026-XXXX)
    #
    # ── QBO OAuth ────────────────────────────────────────────────────────────
    # qbo_client_id                 → ""
    # qbo_client_secret             → ""
    # qbo_realm_id                  → ""
    # qbo_access_token              → ""
    # qbo_refresh_token             → ""
    #
    # ── Shopify ──────────────────────────────────────────────────────────────
    # shopify_store_url             → ""
    # shopify_api_key               → ""
    # shopify_api_secret            → ""
    #
    # ── TaxJar (Phase 3) ─────────────────────────────────────────────────────
    # taxjar_api_key                → ""
