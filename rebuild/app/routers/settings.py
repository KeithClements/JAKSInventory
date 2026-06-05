from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Form, File, UploadFile, HTTPException
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

    # ── Shopify integration ───────────────────────────────────────────────────
    "shopify_store_url":           ("",        "Shopify Store URL"),
    "shopify_api_key":             ("",        "Shopify API Key"),
    "shopify_api_secret":          ("",        "Shopify API Secret"),

    # ── TaxJar integration ────────────────────────────────────────────────────
    "taxjar_api_key":              ("",        "TaxJar API Key"),

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
}

VISIBLE_KEYS = [
    # Company info
    "company_name", "company_address", "company_phone", "company_email",
    "invoice_notes",
    # Pricing
    "cc_surcharge_pct", "default_markup_pct", "default_fuel_service_charge",
    # Policy
    "default_core_return_days", "default_restock_fee_percent",
    "business_close_time",
    # Integrations
    "qbo_client_id", "qbo_client_secret", "qbo_environment", "qbo_redirect_uri",
    "qbo_push_tax",
    "shopify_store_url", "shopify_api_key", "shopify_api_secret",
    "taxjar_api_key",
    # §5.12 — document branding (logo itself is set via POST /settings/logo)
    "document_footer_text", "document_terms_text", "document_show_logo",
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


@router.get("/", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    rows = {s.key: s.value for s in db.query(Setting).all()}
    settings = [
        {"key": k, "label": DEFAULTS[k][1], "value": rows.get(k, DEFAULTS[k][0])}
        for k in VISIBLE_KEYS
    ]
    # Phase 1B — live QBO connection status + flash messages for the QBO card.
    from app.services.qbo_service import QBOSyncService
    qbo = QBOSyncService(db).connection_summary()
    qp = request.query_params
    flash = {
        "error": qp.get("error", ""),
        "qbo_connected": qp.get("qbo_connected", ""),
        "qbo_disconnected": qp.get("qbo_disconnected", ""),
        "qbo_msg": qp.get("qbo_msg", ""),
        "saved": qp.get("saved", ""),
    }
    return templates.TemplateResponse(
        request, "settings/index.html",
        {"settings": settings, "qbo": qbo, "flash": flash},
    )


@router.post("/", response_class=RedirectResponse)
async def save_settings(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    for key in VISIBLE_KEYS:
        val = form.get(key, "")
        row = db.query(Setting).filter(Setting.key == key).first()
        if row:
            row.value = val
        else:
            label = DEFAULTS.get(key, ("", key))[1]
            db.add(Setting(key=key, value=val, label=label))
    db.commit()
    return RedirectResponse("/settings/?saved=1", status_code=303)


# ── §5.12 — Company logo upload (admin-only) ──────────────────────────────────
_ALLOWED_LOGO_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB
_UPLOAD_DIR = Path(__file__).resolve().parent.parent / "static" / "uploads"


@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _admin=Depends(require_admin),
):
    """§5.12 — store an uploaded company logo under static/uploads/ and point
    company_logo_path at it (relative to static/). Admin-only. Rejects
    non-images (by extension AND declared content-type) and oversize files with
    HTTP 400. Never touches money/totals. On success redirects back to settings.
    """
    ext = Path(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()
    if ext not in _ALLOWED_LOGO_EXT or not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Logo must be an image (png/jpg/gif/webp).")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(data) > _MAX_LOGO_BYTES:
        raise HTTPException(status_code=400, detail="Logo exceeds the 2 MB size limit.")

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
