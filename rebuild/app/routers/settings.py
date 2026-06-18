from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models.setting import Setting

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/templates")

DEFAULTS: dict[str, tuple[str, str]] = {
    # ── Company ───────────────────────────────────────────────────────────────
    "company_name":                ("JAKS",    "Company Name"),
    "company_address":             ("",        "Company Address"),
    "company_phone":               ("",        "Company Phone"),
    "company_email":               ("",        "Company Email"),
    "invoice_notes":               ("",        "Default Invoice Notes"),

    # ── SKU generation ────────────────────────────────────────────────────────
    # vendor scheme = {prefix}-{vendor_code}-{part#}  (JAKS-PAI-340097) — default.
    # coded scheme  = {prefix}-{ENGINE}-{CATEGORY}-{Vseq} (JAKS-CAT-HBLT-90001).
    "sku_prefix":                  ("JAKS",    "Customer SKU prefix"),
    "sku_scheme":                  ("vendor",  "Default SKU scheme: vendor | coded"),

    # ── Pricing defaults ──────────────────────────────────────────────────────
    "cc_surcharge_pct":            ("3.0",     "Credit Card Surcharge %"),
    "default_markup_pct":          ("30.0",    "Default Markup %"),
    "default_fuel_service_charge": ("0.0",     "Default Fuel/Service Charge %"),

    # ── Document number sequences (auto-incremented by bump_counter) ──────────
    # Format: PREFIX-YEAR-NNNN  (e.g. INV-2026-0001)
    # Reset to 1 each year — current_sequence_year triggers the rollover check.
    "next_invoice_number":         ("1",       ""),
    "next_quote_number":           ("1",       ""),
    "next_so_number":              ("1",       ""),
    "next_po_number":              ("1",       ""),
    "next_ra_number":              ("1",       ""),
    "next_wc_number":              ("1",       ""),
    "next_ri_number":              ("1",       ""),   # Research Items: RI-2026-XXXX
    "next_core_slip_number":       ("1",       ""),   # Core Return Slips: CORE-2026-XXXX
    "next_vcr_number":             ("1",       ""),   # Vendor Core Returns: VCR-2026-XXXX
    "current_sequence_year":       ("2026",    ""),   # updated on first use each new year

    # ── Core charge policy ────────────────────────────────────────────────────
    "default_core_return_days":    ("30",      "Default Core Return Window (days)"),

    # ── Return policy ─────────────────────────────────────────────────────────
    "default_restock_fee_percent": ("15.0",    "Default Restocking Fee %"),

    # ── Business hours (used for EOD invoice lock) ─────────────────────────────
    "business_close_time":         ("17:00",   "Business Close Time (HH:MM, 24h)"),

    # ── QBO integration (Phase 1B) ────────────────────────────────────────────
    "qbo_client_id":               ("",        "QBO Client ID"),
    "qbo_client_secret":           ("",        "QBO Client Secret"),
    "qbo_realm_id":                ("",        "QBO Realm ID"),
    "qbo_access_token":            ("",        "QBO Access Token"),
    "qbo_refresh_token":           ("",        "QBO Refresh Token"),
    "qbo_environment":             ("sandbox", "QBO environment: sandbox | production"),
    "qbo_redirect_uri":            ("http://localhost:8000/qbo/callback",
                                    "OAuth redirect URI (register this EXACT value in your Intuit app)"),
    "qbo_token_expires_at":        ("",        "QBO access token expiry (ISO, managed automatically)"),
    "qbo_connected_at":            ("",        "QBO connected at (ISO, managed automatically)"),
    "qbo_oauth_state":             ("",        "QBO OAuth CSRF state (transient)"),
    "qbo_item_map":                ("",        "Optional JSON override: line_type → QBO income item name"),
    "qbo_push_tax":                ("true",    "Send JAKS-computed tax to QBO (off if QBO uses Automated Sales Tax)"),
    # ── QBO chart-of-accounts mapping (Settings → QBO Accounts; Phase 4) ──────
    "qbo_bill_expense_account":    ("",        "QBO account: COGS – Diesel Parts (vendor bills)"),
    "qbo_income_account":          ("",        "QBO account: Sales Income (parts)"),
    "qbo_inventory_asset_account": ("",        "QBO account: Inventory Asset"),
    "qbo_freight_in_account":      ("",        "QBO account: Freight In"),
    "qbo_freight_out_account":     ("",        "QBO account: Freight Out / Delivery"),

    # ── Shopify integration ───────────────────────────────────────────────────
    "shopify_store_url":           ("",        "Shopify Store URL"),
    "shopify_api_key":             ("",        "Shopify API Key"),
    "shopify_api_secret":          ("",        "Shopify API Secret"),
    "shopify_access_token":        ("",        "Shopify Admin API Access Token (custom app)"),

    # ── TaxJar integration ────────────────────────────────────────────────────
    "taxjar_api_key":              ("",        "TaxJar API Key"),

    # ── Anthropic / Claude (Smart Import AI categorization) ────────────────────
    "anthropic_api_key":           ("",        "Anthropic (Claude) API Key — enables AI categorize for flagged imports"),

    # ─────────────────────────────────────────────────────────────────────────
    # Phase A additions (Rounds 1–12)
    # ─────────────────────────────────────────────────────────────────────────

    # R1 — Interest defaults (per-customer override exists; this is fallback)
    "default_interest_grace_days": ("10",      "Default interest grace days"),
    "default_monthly_interest_rate":("0.0",    "Default monthly interest rate %"),

    # R3 — Core return window
    "core_return_grace_days":      ("45",      "Days customer has to return cores"),
    "core_return_reminder_threshold_pct": ("75", "% of grace before reminder"),

    # R5 — Quote follow-up offset
    "default_followup_offset_days": ("7",      "Default quote follow-up offset days"),

    # R9 — AR aging buckets (comma-separated day cutoffs)
    "ar_aging_buckets_days":       ("0,30,60,90,120", "AR aging bucket day cutoffs"),

    # R9 — Search
    "search_results_per_type":     ("10",      "Max results per type in global search"),

    # R10 — Sales tax
    "default_sales_tax_rate":      ("0.0",     "Default sales tax rate %"),
    "company_tax_jurisdiction":    ("",        "Company's tax jurisdiction (state)"),

    # R8 — Sandbox/production environment
    "jaks_env":                    ("sandbox", "sandbox | production"),
    "qbo_sandbox_prefix":          ("TEST-",   "Prefix for test records in sandbox"),

    # R10 — Time / locale
    "business_timezone":           ("America/Denver", "Local timezone"),

    # R6 — Inventory
    "allow_negative_inventory_admin_override": ("true",
        "Permit admin to allow negative inventory (with audit)"),
    "low_stock_threshold_default": ("2",       "Default low-stock alert threshold"),

    # R7 — Special orders
    "special_order_require_deposit_default": ("true",
        "Require deposit on special orders by default"),

    # R4 — Warranty reserve (JAKS-extended warranty credit source)
    "jaks_warranty_reserve_account": ("Warranty Reserve",
        "Accounting category for JAKS-absorbed warranty credits"),

    # R8 — Notification thresholds
    "notify_invoice_over_amount":  ("5000.0",  "Notify on invoices over this amount"),
    "notify_payment_over_amount":  ("5000.0",  "Notify on payments over this amount"),

    # R9 — Concurrency
    "concurrency_check_field":     ("updated_at", "Optimistic-lock check field"),

    # R12 — Communication (provider abstraction)
    "messaging_email_provider":    ("null",    "null | smtp | m365 | gmail"),
    "messaging_sms_provider":      ("null",    "null | twilio"),
    "messaging_log_only_mode":     ("true",    "Phase 1: log only, do not transmit"),
    "messaging_max_outbound_per_hour": ("100", "Sanity rate limit per hour"),
    "messaging_max_outbound_per_customer_per_day": ("3",
        "Avoid spamming a single customer"),

    # R12 — SMTP (Phase 2 use, placeholders now)
    "smtp_host":                   ("",        ""),
    "smtp_port":                   ("587",     ""),
    "smtp_username":               ("",        ""),
    "smtp_password_encrypted":     ("",        "Encrypted at rest"),
    "smtp_from_address":           ("",        ""),
    "smtp_from_name":              ("JAKS Diesel Parts", ""),
    "smtp_use_tls":                ("true",    ""),

    # R12 — Twilio (Phase 2)
    "twilio_account_sid":          ("",        ""),
    "twilio_api_key_sid":          ("",        "Optional Twilio API Key SID (SK…)"),
    "twilio_auth_token_encrypted": ("",        "Encrypted at rest"),
    "twilio_from_number":          ("",        ""),

    # New document sequences (R8, R11)
    "next_cm_number":              ("1",       "Credit Memo: CM-2026-XXXX"),
    "next_vcm_number":             ("1",       "Vendor Credit Memo: VCM-2026-XXXX"),
    "next_vr_number":              ("1",       "Vendor Return: VR-2026-XXXX"),
    "next_statement_number":       ("1",       "Customer Statement: ST-2026-XXXX"),

    # O3 — SQLite backup / restore (go-live gate, §11)
    "backup_dir":                  ("",        "Backup directory (blank → <app>/backups)"),
    "backup_retention_count":      ("10",      "Number of backups to keep"),
    "backup_on_startup":           ("true",    "Run a backup automatically on startup"),
    "backup_min_interval_hours":   ("12",      "Min hours between automatic startup backups"),
    "backup_last_run":             ("",        "Last successful backup (ISO timestamp)"),

    # §5.12 — PDF / document branding (logo set via POST /settings/logo upload)
    "company_logo_path":           ("",        "Company logo path under static/ (set via upload)"),
    "document_footer_text":        ("",        "Footer text on Quote/SO/Invoice PDFs (terms / return policy / thank-you)"),
    "document_terms_text":         ("Core charges are refundable upon return of the old core.\nFreight is additional unless noted.\nQuotes are valid for 30 days from the date above.",
                                    "Terms & conditions printed near the bottom of Quote/SO/Invoice PDFs"),
    "document_show_logo":          ("true",    "Show the company logo on document headers"),
    "document_logo_height":        ("56",      "Logo height on document headers (px, 24-160)"),

    # Company info extras
    "company_website":             ("",        "Company website URL"),

    # Pricing grid — defaults FALSE so nothing re-prices until owner flips after preview
    "markup_tiers_active":         ("false",   "Use cost-bracket markup grid instead of flat default"),

    # Customer pricing tiers — discount % off normal sell price (0.0 = no change)
    # Owner sets these once the tier names are meaningful (wholesale/fleet/dealer).
    # 'standard' is always 0 % and is never read from settings.
    "tier_wholesale_discount_pct": ("0.0",     "Wholesale tier: discount % off sell price"),
    "tier_fleet_discount_pct":     ("0.0",     "Fleet tier: discount % off sell price"),
    "tier_dealer_discount_pct":    ("0.0",     "Dealer tier: discount % off sell price"),
}

VISIBLE_KEYS = [
    # Company info
    "company_name", "company_address", "company_phone", "company_email", "company_website",
    "invoice_notes",
    # SKU generation
    "sku_prefix", "sku_scheme",
    # Pricing
    "cc_surcharge_pct", "default_markup_pct", "default_fuel_service_charge",
    # Policy
    "default_core_return_days", "default_restock_fee_percent",
    "business_close_time",
    # Integrations
    "qbo_client_id", "qbo_client_secret", "qbo_environment", "qbo_redirect_uri",
    "qbo_push_tax",
    "shopify_store_url", "shopify_api_key", "shopify_api_secret", "shopify_access_token",
    "taxjar_api_key",
    "anthropic_api_key",
    # Messaging — Email (SMTP) + SMS (Twilio)
    "messaging_log_only_mode", "messaging_email_provider", "messaging_sms_provider",
    "messaging_max_outbound_per_hour", "messaging_max_outbound_per_customer_per_day",
    "smtp_host", "smtp_port", "smtp_username", "smtp_password_encrypted",
    "smtp_from_address", "smtp_from_name", "smtp_use_tls",
    "twilio_account_sid", "twilio_api_key_sid", "twilio_auth_token_encrypted", "twilio_from_number",
    # §5.12 — document branding (logo itself is set via POST /settings/logo)
    "document_footer_text", "document_terms_text", "document_show_logo", "document_logo_height",
]


def seed_settings(db: Session) -> None:
    """
    Insert missing settings on startup.
    One query to fetch all existing keys, then one bulk insert — not N queries.
    """
    existing_keys = {row.key for row in db.query(Setting.key).all()}
    new_rows = [
        Setting(key=key, value=default, label=label)
        for key, (default, label) in DEFAULTS.items()
        if key not in existing_keys
    ]
    if new_rows:
        db.add_all(new_rows)
        db.commit()


# Delegated to app.settings_utils — re-exported here so existing router
# call-sites continue to work without change.
from app.settings_utils import bump_counter, get_setting_value  # noqa: E402


# ── Pricing grid seed (idempotent) ─────────────────────────────────────────────
# Four cost-bracket tiers that match the owner's existing pricing intuition.
# Only inserts when the table is EMPTY so a hand-curated grid is never overwritten.
_DEFAULT_TIERS = [
    # (min_cost, max_cost, markup_pct, sort_order)
    (0.0,   15.0,  40.0,  1),   # under $15   → 40 %
    (15.0,  40.0,  35.0,  2),   # $15 – $40   → 35 %
    (40.0,  75.0,  32.5,  3),   # $40 – $75   → 32.5 %
    (75.0,  None,  30.0,  4),   # $75+         → 30 % (open-ended)
]


def seed_markup_tiers(db: Session) -> None:
    """Insert the default markup-tier grid if the table is empty (idempotent).
    Skips if ANY tier already exists — preserves a hand-edited grid."""
    from app.models.pricing import MarkupTier
    if db.query(MarkupTier.id).first() is not None:
        return
    for min_c, max_c, pct, order in _DEFAULT_TIERS:
        db.add(MarkupTier(min_cost=min_c, max_cost=max_c, markup_pct=pct, sort_order=order))
    db.commit()


@router.get("/", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    rows = {s.key: s.value for s in db.query(Setting).all()}
    # §21 — secrets are NEVER echoed back into a form value attribute (that
    # leaks them into the page source even behind type=password). Render them
    # blank with an is_set marker; a blank submit keeps the stored value.
    _SECRET_KEYS = {"qbo_client_secret", "smtp_password_encrypted", "twilio_auth_token_encrypted"}
    settings = []
    for k in VISIBLE_KEYS:
        raw = rows.get(k, DEFAULTS[k][0])
        if k in _SECRET_KEYS:
            settings.append({"key": k, "label": DEFAULTS[k][1], "value": "",
                             "is_secret": True, "is_set": bool((raw or "").strip())})
        else:
            settings.append({"key": k, "label": DEFAULTS[k][1], "value": raw})
    # Phase 1B — live QBO connection status + flash messages for the QBO card.
    from app.services.qbo_service import QBOSyncService
    qbo = QBOSyncService(db).connection_summary()
    # AI tab — Anthropic key status + resolved model (NEVER the key value itself).
    from app.services.ai_description_service import (
        DEFAULT_MODEL, get_ai_model, get_anthropic_api_key,
    )
    ai_ctx = {
        "is_configured": bool(get_anthropic_api_key(db)),
        "model": get_ai_model(db),
        "default_model": DEFAULT_MODEL,
    }
    # Messaging tab — current config (non-secret values + is_set flags for secrets)
    _msg_keys = [
        "messaging_log_only_mode", "messaging_email_provider", "messaging_sms_provider",
        "messaging_max_outbound_per_hour", "messaging_max_outbound_per_customer_per_day",
        "smtp_host", "smtp_port", "smtp_username", "smtp_from_address", "smtp_from_name",
        "smtp_use_tls", "twilio_account_sid", "twilio_api_key_sid", "twilio_from_number",
    ]
    msg = {k: rows.get(k, DEFAULTS[k][0]) for k in _msg_keys}
    msg["smtp_password_is_set"] = bool((rows.get("smtp_password_encrypted") or "").strip())
    msg["twilio_auth_token_is_set"] = bool((rows.get("twilio_auth_token_encrypted") or "").strip())

    qp = request.query_params
    flash = {
        "error": qp.get("error", ""),
        "qbo_connected": qp.get("qbo_connected", ""),
        "qbo_disconnected": qp.get("qbo_disconnected", ""),
        "qbo_msg": qp.get("qbo_msg", ""),
        "saved": qp.get("saved", ""),
        "logo_saved": qp.get("logo_saved", ""),
        "logo_error": qp.get("logo_error", ""),
        "ai_saved": qp.get("ai_saved", ""),
        "ai_cleared": qp.get("ai_cleared", ""),
        "ai_error": qp.get("ai_error", ""),
        # §22.5 — Messaging "Send test" result (status word + optional error)
        "msg_test": qp.get("msg_test", ""),
        "msg_test_err": qp.get("msg_test_err", ""),
    }
    return templates.TemplateResponse(
        request, "settings/index.html",
        {"settings": settings, "qbo": qbo, "flash": flash, "ai": ai_ctx, "msg": msg},
    )


@router.post("/", response_class=RedirectResponse)
async def save_settings(request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)):
    form = await request.form()
    # Secrets Fernet-encrypted at rest. A blank submit means "keep the existing
    # value" (the field renders masked/empty so the admin doesn't re-type it).
    # Each key's READ path must _decrypt() (decrypt passes legacy plaintext
    # through, so this is backward-compatible):
    #   • qbo_client_secret   → app/services/qbo_client.py
    #   • shopify_access_token → app/services/shopify_service.py (C7)
    #   • anthropic_api_key    → app/services/ai_categorization_service.py
    #   • shopify_api_secret / taxjar_api_key → currently no reader (unused
    #     integrations); encrypted here purely to harden them at rest.
    _ENCRYPTED_KEYS = {
        "qbo_client_secret", "shopify_access_token",
        "shopify_api_secret", "taxjar_api_key", "anthropic_api_key",
        "smtp_password_encrypted", "twilio_auth_token_encrypted",
    }
    for key in VISIBLE_KEYS:
        val = form.get(key, "")
        if key in _ENCRYPTED_KEYS:
            sval = str(val).strip()
            if not sval:
                continue  # keep stored value
            from app.services.qbo_client import _encrypt as _qbo_encrypt
            val = _qbo_encrypt(sval)
        row = db.query(Setting).filter(Setting.key == key).first()
        if row:
            row.value = val
        else:
            label = DEFAULTS.get(key, ("", key))[1]
            db.add(Setting(key=key, value=val, label=label))
    db.commit()
    return RedirectResponse("/settings/?saved=1", status_code=303)


# ── §22.5 — Messaging "Send test" (admin-only) ────────────────────────────────
#
# Fires a one-off test message at an arbitrary address/number through the REAL
# provider so the owner can verify their SMTP / Twilio credentials from Settings.
# Honors the global ``messaging_log_only_mode`` kill-switch — under the safe-mode
# default this records a status=LOGGED_ONLY audit row and never transmits.
# Never raises (MessagingService.send_test always returns a SendResult): the
# result status (and any error) round-trips back as a ?msg_test= flash.

@router.post("/messaging/test", response_class=RedirectResponse)
async def messaging_send_test(
    request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)
):
    from urllib.parse import quote
    from app.constants import CommunicationChannel, CommunicationStatus
    from app.services.messaging_service import MessagingService

    form = await request.form()
    test_channel = str(form.get("test_channel", "email")).strip().lower()
    test_to = str(form.get("test_to", "")).strip()

    channel = (CommunicationChannel.EMAIL if test_channel == "email"
               else CommunicationChannel.SMS)
    res = MessagingService(db, current_user_id=_admin.id).send_test(
        channel=channel, to_address=test_to,
    )

    url = f"/settings/?tab=messaging&msg_test={quote(str(res.status))}"
    if res.status == CommunicationStatus.FAILED:
        url += f"&msg_test_err={quote(res.error or '')}"
    return RedirectResponse(url, status_code=303)


# ── §5.12 — Company logo upload (admin-only) ──────────────────────────────────
_ALLOWED_LOGO_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MAX_LOGO_BYTES = 8 * 1024 * 1024  # 8 MB
_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "static" / "uploads"

_PREVIEW_SAMPLE_LIMIT = 25   # max rows returned in sample


@router.get("/pricing/preview")
def pricing_grid_preview(db: Session = Depends(get_db), _admin=Depends(require_admin)):
    """§ Pricing grid dry run — reads every product WITHOUT an explicit markup_pct
    (i.e. products that would be affected if markup_tiers_active were flipped on)
    and computes their current sell price vs the grid sell price.

    Returns:
        {
          "tiers": [...],            # current MarkupTier rows for reference
          "affected_count": int,     # products without an explicit markup
          "moved_count": int,        # those that would get a different price
          "sample": [               # up to 25 rows where old_sell != new_sell
            {sku, cost, old_sell, new_sell, delta, markup_old, markup_new}
          ]
        }
    Read-only — changes nothing regardless of markup_tiers_active flag state.
    """
    from app.models.product import Product
    from app.models.pricing import MarkupTier
    from app.services.pricing_service import PricingService
    from app.utils import calc_sell_price

    svc = PricingService(db)
    flat_default = svc.default_markup_pct()
    tiers = (
        db.query(MarkupTier)
        .filter(MarkupTier.is_active == True)   # noqa: E712
        .order_by(MarkupTier.sort_order, MarkupTier.min_cost)
        .all()
    )
    tier_dicts = [
        {"id": t.id, "min_cost": t.min_cost, "max_cost": t.max_cost,
         "markup_pct": t.markup_pct, "sort_order": t.sort_order}
        for t in tiers
    ]

    # Products without a per-product markup (the ones tiers would affect)
    candidates = (
        db.query(Product)
        .filter(Product.is_active == True, Product.markup_pct.is_(None))  # noqa: E712
        .all()
    )

    sample, moved_count = [], 0
    for p in candidates:
        cost = p.cost or 0.0
        # Current sell: flat default (since markup_pct is None)
        old_markup = flat_default
        old_sell = p.price_override if (p.price_override and p.price_override > 0) \
            else calc_sell_price(cost, old_markup)
        # Grid sell
        new_markup = svc.resolve_markup_pct_for_cost(cost)
        new_sell = p.price_override if (p.price_override and p.price_override > 0) \
            else calc_sell_price(cost, new_markup)

        delta = round(new_sell - old_sell, 2)
        if delta != 0.0:
            moved_count += 1
            if len(sample) < _PREVIEW_SAMPLE_LIMIT:
                sample.append({
                    "sku": p.sku,
                    "cost": round(cost, 2),
                    "old_sell": round(old_sell, 2),
                    "new_sell": round(new_sell, 2),
                    "delta": delta,
                    "markup_old": round(old_markup, 2),
                    "markup_new": round(new_markup, 2),
                })

    from fastapi.responses import JSONResponse
    return JSONResponse({
        "tiers": tier_dicts,
        "affected_count": len(candidates),
        "moved_count": moved_count,
        "sample": sample,
    })


@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """§5.12 — store an uploaded company logo under static/uploads/ and point
    company_logo_path at it (relative to static/). Admin-only. Rejects
    non-images (by extension AND declared content-type) and oversize files.

    The upload form is a full-page POST, so validation failures redirect back to
    the Company tab with a friendly ``?logo_error=`` flash rather than dumping a
    raw JSON error at the user. Never touches money/totals.
    """
    from urllib.parse import urlencode

    def _back(msg: str) -> RedirectResponse:
        return RedirectResponse(f"/settings/?{urlencode({'logo_error': msg})}",
                                status_code=303)

    ext = Path(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()
    if ext not in _ALLOWED_LOGO_EXT or not content_type.startswith("image/"):
        return _back("Logo must be an image file — PNG, JPG, GIF or WebP.")

    data = await file.read()
    if not data:
        return _back("That file was empty — please choose an image.")
    if len(data) > _MAX_LOGO_BYTES:
        max_mb = _MAX_LOGO_BYTES // (1024 * 1024)
        return _back(f"Logo is too large ({len(data) / 1024 / 1024:.1f} MB). "
                     f"The limit is {max_mb} MB — please use a smaller PNG or JPG.")

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = f"company_logo_{uuid.uuid4().hex}{ext}"
    (_UPLOAD_DIR / safe_name).write_bytes(data)

    rel_path = f"uploads/{safe_name}"   # relative to static/ → /static/uploads/...
    row = db.query(Setting).filter(Setting.key == "company_logo_path").first()
    if row:
        row.value = rel_path
    else:
        db.add(Setting(key="company_logo_path", value=rel_path,
                       label=DEFAULTS["company_logo_path"][1]))
    db.commit()
    return RedirectResponse("/settings/?logo_saved=1", status_code=303)


# ── AI (Anthropic / Claude) — encrypted-at-rest API key + model ──────────────
#
# Lives on its own page because the Anthropic key is FERNET-ENCRYPTED at rest
# (different storage shape than every other VISIBLE_KEYS setting) AND must
# never be echoed back in any response. The main settings form intentionally
# stays plain KV — adding the AI key there would either (a) round-trip the
# plaintext through the HTML form (leak) or (b) special-case one field.
# A dedicated tiny form keeps the security contract obvious.

@router.get("/ai", response_class=HTMLResponse)
def settings_ai_page(request: Request, db: Session = Depends(get_db)):
    """AI settings page. Shows whether a key is configured (NEVER the value)."""
    from app.services.ai_description_service import (
        DEFAULT_MODEL, get_ai_model, get_anthropic_api_key,
    )
    qp = request.query_params
    ctx = {
        "is_configured": bool(get_anthropic_api_key(db)),
        "model": get_ai_model(db),
        "default_model": DEFAULT_MODEL,
        "saved": qp.get("saved", ""),
        "cleared": qp.get("cleared", ""),
        "error": qp.get("error", ""),
    }
    return templates.TemplateResponse(request, "settings/ai.html", ctx)


@router.post("/ai", response_class=RedirectResponse)
async def settings_ai_save(
    request: Request,
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """Save the Anthropic API key (Fernet-encrypted) + model.

    A blank ``anthropic_api_key`` field is treated as "no change" so the user
    can update the model without re-entering the key. Tick the ``clear`` flag
    (or use the dedicated Clear button) to wipe it.
    """
    from app.services.ai_description_service import (
        set_anthropic_api_key, set_ai_model,
    )
    from urllib.parse import urlencode

    form = await request.form()
    raw_key = str(form.get("anthropic_api_key", "") or "").strip()
    clear = str(form.get("clear", "") or "").strip().lower() in {"1", "true", "on", "yes"}
    model = str(form.get("ai_description_model", "") or "").strip()

    # Where to bounce on save — same page if it was the standalone /settings/ai
    # form, otherwise the main settings tab so the flash sticks next to the AI tab.
    referer = (request.headers.get("referer") or "").lower()
    standalone = referer.endswith("/settings/ai") or referer.endswith("/settings/ai/")

    try:
        if clear:
            set_anthropic_api_key(db, "")
        elif raw_key:
            set_anthropic_api_key(db, raw_key)
        # else: leave key untouched (user is just editing the model)
        set_ai_model(db, model)
        db.commit()
    except Exception as exc:  # noqa: BLE001 — surface a friendly flash
        db.rollback()
        target = "/settings/ai" if standalone else "/settings/"
        params = {"error" if standalone else "ai_error": f"Could not save: {exc}"}
        if not standalone:
            params["tab"] = "ai"
        return RedirectResponse(
            f"{target}?{urlencode(params)}",
            status_code=303,
        )

    if standalone:
        flash = "cleared=1" if clear else "saved=1"
        return RedirectResponse(f"/settings/ai?{flash}", status_code=303)
    flash = "ai_cleared=1" if clear else "ai_saved=1"
    return RedirectResponse(f"/settings/?tab=ai&{flash}", status_code=303)


# ── Company Locations (PO bill-to / ship-to address book) ─────────────────────

@router.get("/locations", response_class=HTMLResponse)
def settings_locations(request: Request, db: Session = Depends(get_db)):
    from app.models.company_location import CompanyLocation
    locs = (
        db.query(CompanyLocation)
        .order_by(CompanyLocation.is_active.desc(),
                  CompanyLocation.is_primary.desc(),
                  CompanyLocation.name)
        .all()
    )
    qp = request.query_params
    return templates.TemplateResponse(request, "settings/locations.html", {
        "locations": locs,
        "saved": qp.get("saved", ""),
        "error": qp.get("error", ""),
    })


@router.post("/locations", response_class=RedirectResponse)
async def settings_locations_create(
    request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)
):
    from app.models.company_location import CompanyLocation
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return RedirectResponse("/settings/locations?error=Name+is+required", status_code=303)
    want_primary = str(form.get("is_primary", "")).strip().lower() in {"1", "true", "on", "yes"}
    loc = CompanyLocation(
        name=name,
        contact_name=str(form.get("contact_name", "")).strip(),
        address_line1=str(form.get("address_line1", "")).strip(),
        address_line2=str(form.get("address_line2", "")).strip(),
        city=str(form.get("city", "")).strip(),
        state=str(form.get("state", "")).strip(),
        zip_code=str(form.get("zip_code", "")).strip(),
        country=str(form.get("country", "")).strip(),
        phone=str(form.get("phone", "")).strip(),
        is_active=True,
    )
    db.add(loc)
    db.flush()
    # First location is always the primary; otherwise honour the checkbox.
    is_first = db.query(CompanyLocation).filter(CompanyLocation.is_active == True).count() == 1  # noqa: E712
    if want_primary or is_first:
        for other in db.query(CompanyLocation).filter(CompanyLocation.id != loc.id,
                                                      CompanyLocation.is_primary == True).all():  # noqa: E712
            other.is_primary = False
        loc.is_primary = True
    db.commit()
    return RedirectResponse("/settings/locations?saved=1", status_code=303)


@router.post("/locations/{loc_id}/primary", response_class=RedirectResponse)
def settings_location_primary(
    loc_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)
):
    from app.models.company_location import CompanyLocation
    loc = db.query(CompanyLocation).filter(CompanyLocation.id == loc_id).first()
    if loc is not None:
        for other in db.query(CompanyLocation).filter(CompanyLocation.is_primary == True).all():  # noqa: E712
            other.is_primary = False
        loc.is_primary = True
        loc.is_active = True
        db.commit()
    return RedirectResponse("/settings/locations?saved=1", status_code=303)


@router.post("/locations/{loc_id}/delete", response_class=RedirectResponse)
def settings_location_delete(
    loc_id: int, db: Session = Depends(get_db), _admin=Depends(require_admin)
):
    from app.models.company_location import CompanyLocation
    loc = db.query(CompanyLocation).filter(CompanyLocation.id == loc_id).first()
    # Never remove the primary — the owner must promote another location first.
    if loc is not None and not loc.is_primary:
        loc.is_active = False
        db.commit()
        return RedirectResponse("/settings/locations?saved=1", status_code=303)
    return RedirectResponse(
        "/settings/locations?error=Set+another+location+as+primary+before+removing+this+one",
        status_code=303,
    )


# ── QBO chart-of-accounts mapping (Settings → QBO Accounts) ───────────────────

@router.get("/qbo-accounts", response_class=HTMLResponse)
def settings_qbo_accounts(request: Request, db: Session = Depends(get_db)):
    """Map ERP concepts (COGS, Income, Inventory Asset, Freight) to real QBO
    accounts — pulled live from the connected company's chart of accounts."""
    from app.services.qbo_service import QBOSyncService, QBO_ACCOUNT_FIELDS
    from app.services import qbo_client as qc

    connected = qc.is_connected(db)
    svc = QBOSyncService(db)
    fetch = svc.list_accounts() if connected else {
        "ok": False, "error": "QuickBooks isn't connected yet — connect it in Settings first."
    }
    accounts = fetch.get("accounts", []) if fetch.get("ok") else []
    mappings = svc.account_mappings()
    names = {a["name"] for a in accounts}
    # Per-mapping verification: True (found) / False (set but missing) for each set value.
    verify = {k: (v in names) for k, v in mappings.items() if v}

    qp = request.query_params
    return templates.TemplateResponse(request, "settings/qbo_accounts.html", {
        "connected": connected,
        "fetch_ok": bool(fetch.get("ok")),
        "fetch_error": fetch.get("error", ""),
        "accounts": accounts,
        "fields": QBO_ACCOUNT_FIELDS,
        "mappings": mappings,
        "verify": verify,
        "saved": qp.get("saved", ""),
        "error": qp.get("error", ""),
    })


@router.post("/qbo-accounts", response_class=RedirectResponse)
async def settings_qbo_accounts_save(
    request: Request, db: Session = Depends(get_db), _admin=Depends(require_admin)
):
    from app.services.qbo_service import QBO_ACCOUNT_FIELDS
    from app.settings_utils import set_setting_value_db

    form = await request.form()
    for f in QBO_ACCOUNT_FIELDS:
        set_setting_value_db(
            db, f["key"], str(form.get(f["key"], "")).strip(),
            f"QBO account mapping: {f['label']}",
        )
    db.commit()
    return RedirectResponse("/settings/qbo-accounts?saved=1", status_code=303)
