"""PDF quote generator for JAKS Diesel.

Uses ReportLab to produce professional branded quotes with:
- Company logo + info header
- Customer / quote details (ESN, VIN, job number)
- Line items table with notes, ETA, optional markers, warranty, shipping
- Totals block (subtotal, core, tax, shipping, total)
- Quote notes footer
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, HRFlowable,
)
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

from db import get_setting, get_customer
from db import get_product_images as _get_product_images

# Brand palette + typography are owned by ``doc_theme`` so every PDF
# the app emits stays visually consistent. Import the names this file
# references locally; the theme module is the single source of truth.
from .doc_theme import (
    OLIVE, OLIVE_LIGHT, GRAY_BG, WHITE, BLACK, DARK_GRAY, RED, GREEN, BLUE,
    format_eta_friendly, build_footer as _shared_footer,
)

_BASE = Path(__file__).parent.parent
OUTPUT_DIR = _BASE / "output" / "quotes"


def _company() -> dict:
    return {
        "name": get_setting("company_name", "JAK'S Diesel"),
        "address": get_setting("company_address", ""),
        "city": get_setting("company_city", "Henderson"),
        "state": get_setting("company_state", "NV"),
        "zip": get_setting("company_zip", ""),
        "phone": get_setting("company_phone", ""),
        "email": get_setting("company_email", ""),
        "logo_path": _BASE / get_setting("company_logo_path", "assets/logo.png"),
    }


def _fmt_addr(info: dict) -> str:
    parts = []
    if info["address"]:
        parts.append(info["address"])
    city_state = ", ".join(filter(None, [info["city"], info["state"]]))
    if city_state:
        if info["zip"]:
            city_state += " " + info["zip"]
        parts.append(city_state)
    return ", ".join(parts) if parts else ""


def _ensure_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def _esc(text: str) -> str:
    """Escape XML special characters for ReportLab Paragraph."""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _parent_mfr_months(parent: dict | None) -> int:
    """Return the manufacturer (supplier) warranty months for a parent line.

    Reads ``supplier_warranty_value`` + ``supplier_warranty_unit`` off the
    parent line (joined from products). Returns 0 if unavailable.
    """
    if not parent:
        return 0
    try:
        from engine.warranty import supplier_standard_warranty_for_product
        months, _, _ = supplier_standard_warranty_for_product(parent)
        return int(months or 0)
    except Exception:
        pass
    # Fallback: direct fields.
    try:
        val = int(parent.get("supplier_warranty_value") or 0)
    except Exception:
        val = 0
    unit = str(parent.get("supplier_warranty_unit") or "months").lower()
    if unit.startswith("year"):
        val *= 12
    return val


def _warranty_wording(add_months: int, mfr_months: int) -> str:
    """Customer-facing wording for an extended-warranty add-on.

    Example: 12 add-on months + 24 mfr months → "Additional 12 month
    warranty — 36 month coverage".
    """
    add_months = int(add_months or 0)
    mfr_months = int(mfr_months or 0)
    total = add_months + mfr_months
    if add_months <= 0:
        return "Extended warranty"
    if total > add_months:
        return f"Additional {add_months} month warranty — {total} month coverage"
    return f"Additional {add_months} month warranty"


def generate_quote_pdf(
    quote: dict,
    output_path: str | None = None,
    *,
    doc_title: str = "QUOTE",
    cust_label: str = "QUOTE FOR",
    show_expiration: bool = True,
    extra_intro_lines: list[str] | None = None,
) -> str:
    """Generate a professional PDF quote.

    Args:
        quote: Quote-shaped dict (also reused for sales orders) with lines,
            customer info, totals.
        output_path: Optional explicit output path.
        doc_title: Big right-side header (e.g. "QUOTE", "SALES ORDER").
        cust_label: Heading above the bill-to block ("QUOTE FOR" / "SHIP TO").
        show_expiration: Render the "Expires:" row (off for sales orders).
        extra_intro_lines: Optional extra rows (label, value tuples flattened
            as ``[label, value, label, value, ...]``) appended to the right
            details column â€” used for tracking numbers etc.

    Returns:
        Absolute path to the generated PDF file.
    """
    q_num = quote.get("quote_number", "DRAFT")

    if not output_path:
        _ensure_dir()
        safe = q_num.replace("/", "-").replace("\\", "-")
        output_path = str(OUTPUT_DIR / f"{safe}.pdf")

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.25 * inch,
        bottomMargin=0.3 * inch,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("QTitle", parent=styles["Heading1"],
                              fontSize=22, textColor=OLIVE, spaceAfter=0,
                              alignment=TA_RIGHT, fontName="Helvetica-Bold",
                              leading=24))
    styles.add(ParagraphStyle("QSub", parent=styles["Normal"],
                              fontSize=10, textColor=DARK_GRAY, alignment=TA_RIGHT,
                              leading=12))
    styles.add(ParagraphStyle("QLabel", parent=styles["Normal"],
                              fontSize=9, textColor=OLIVE, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("QValue", parent=styles["Normal"],
                              fontSize=9, textColor=DARK_GRAY, leading=11))
    styles.add(ParagraphStyle("QSmall", parent=styles["Normal"],
                              fontSize=8, textColor=colors.HexColor("#666666"),
                              leading=10))
    styles.add(ParagraphStyle("QRight", parent=styles["Normal"],
                              fontSize=9, textColor=DARK_GRAY, alignment=TA_RIGHT,
                              leading=11))
    styles.add(ParagraphStyle("QFooter", parent=styles["Normal"],
                              fontSize=7, textColor=colors.HexColor("#999999"),
                              alignment=TA_CENTER, leading=9))
    styles.add(ParagraphStyle("QNotes", parent=styles["Normal"],
                              fontSize=9, textColor=DARK_GRAY, spaceAfter=2))

    elements: list = []

    # â”€â”€ HEADER â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    elements.append(_build_header(
        quote, styles, doc_title=doc_title,
        show_expiration=show_expiration,
        extra_intro_lines=extra_intro_lines,
    ))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=OLIVE,
                               spaceAfter=0.04 * inch, spaceBefore=0.02 * inch))

    # â”€â”€ CUSTOMER + QUOTE DETAILS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    elements.append(_build_details(
        quote, styles,
        cust_label=cust_label,
        show_expiration=show_expiration,
        extra_intro_lines=extra_intro_lines,
    ))
    elements.append(Spacer(1, 0.06 * inch))

    # â”€â”€ LINE ITEMS TABLE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    elements.append(_build_line_items(quote, styles))
    elements.append(Spacer(1, 0.08 * inch))

    # â”€â”€ DECISION SECTION â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # For single-tier quotes (no B and no C) show a slim totals ladder.
    # For multi-tier quotes show ONE compact "Choose Your Repair Option"
    # comparison table â€” no redundant package cards, no redundant
    # totals ladder underneath.
    from engine.quote_tiers import classify_lines as _classify
    _t = _classify(quote.get("lines", []))
    if _t.get("has_b") or _t.get("has_c"):
        elements.append(_build_package_comparison(quote, styles))
        elements.append(Spacer(1, 0.06 * inch))
        # Slim trailer: only Expected availability + core deposit note.
        for fl in _build_decision_trailer(quote, styles):
            elements.append(fl)
    else:
        elements.append(_build_simple_totals(quote, styles))
        elements.append(Spacer(1, 0.08 * inch))

    # CC convenience-fee disclosure
    if bool(int(quote.get("cc_fee_enabled") or 0)):
        elements.append(Paragraph(
            "<i>This quote includes a 3% credit card convenience fee. "
            "Save 3% by paying with cash, check, or ACH.</i>",
            styles["QNotes"],
        ))
        elements.append(Spacer(1, 0.08 * inch))

    # â”€â”€ CUSTOMER NOTES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    cust_notes = quote.get("customer_notes")
    if cust_notes:
        elements.append(Paragraph("Customer Notes", styles["QLabel"]))
        elements.append(Spacer(1, 2))
        elements.append(Paragraph(_esc(str(cust_notes)).replace("\n", "<br/>"), styles["QNotes"]))
        elements.append(Spacer(1, 0.08 * inch))

    # â”€â”€ NOTES â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    notes = quote.get("notes")
    if notes:
        elements.append(Paragraph("Notes", styles["QLabel"]))
        elements.append(Spacer(1, 2))
        elements.append(Paragraph(_esc(str(notes)), styles["QNotes"]))
        elements.append(Spacer(1, 0.08 * inch))

    # â”€â”€ FOOTER (shared across all docs) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _footer_styles = {"footer": styles["QFooter"]}
    has_core = any(float(ln.get("core_charge") or 0) > 0 for ln in quote.get("lines", []))
    elements.extend(_shared_footer(
        _footer_styles,
        include_core_note=has_core,
        include_warranty_note=False,
    ))

    doc.build(elements)
    return os.path.abspath(output_path)


# â”€â”€ Section builders â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def _build_header(
    quote: dict,
    styles,
    *,
    doc_title: str = "QUOTE",
    show_expiration: bool = True,
    extra_intro_lines: list[str] | None = None,
) -> Table:
    """Compact ERP-style header band.

    Left:  logo + company contact (address / phone / email).
    Right: doc title + number + key meta (Date / Expires / Terms / tracking).

    Header height is about 30-40% shorter than the legacy 32pt-title /
    stacked-meta layout: the title shrinks to 22pt and the meta rows
    move out of the bill-to block and up into the header so the
    customer block can stay 1-column and short.
    """
    company = _company()
    logo_path = company["logo_path"]

    try:
        size_pct = float(get_setting("quote_logo_size_pct", "100"))
    except Exception:
        size_pct = 100.0
    size_pct = max(50.0, min(150.0, size_pct)) / 100.0
    # Compact footprint — wide enough to read, short enough to keep
    # the header band under ~0.9".
    logo_w = 2.2 * inch * size_pct
    logo_h = 0.85 * inch * size_pct

    if Path(logo_path).exists():
        logo_cell = Image(str(logo_path), width=logo_w, height=logo_h,
                          kind="proportional")
        logo_cell.hAlign = "LEFT"
    else:
        logo_cell = Paragraph(f"<b>{_esc(company['name'])}</b>", styles["QTitle"])

    # Left: logo + company contact in tight rows.
    company_lines = []
    addr = _fmt_addr(company)
    show_addr = get_setting("quote_show_company_address", "1") == "1"
    if addr and show_addr:
        company_lines.append(Paragraph(_esc(addr), styles["QSmall"]))
    contact_bits = []
    if company.get("phone"):
        contact_bits.append(_esc(company["phone"]))
    if company.get("email"):
        contact_bits.append(_esc(company["email"]))
    if contact_bits:
        company_lines.append(Paragraph(
            "  ·  ".join(contact_bits), styles["QSmall"]))

    left_content = [[logo_cell], *([[p] for p in company_lines])]
    left_table = Table(left_content, colWidths=[3.6 * inch])
    left_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    # Right: title + meta rows in a tight two-column table.
    q_date = str(quote.get("quote_date", ""))[:10]
    exp_date = str(quote.get("expiration_date", ""))[:10]
    terms = quote.get("terms", "") or ""

    meta_rows: list[tuple[str, str]] = []
    if q_date:
        from .doc_theme import format_friendly_date as _fd
        meta_rows.append(("Date:", _fd(q_date)))
    if show_expiration and exp_date:
        from .doc_theme import format_friendly_date as _fd
        meta_rows.append(("Expires:", _fd(exp_date)))
    if terms:
        meta_rows.append(("Terms:", str(terms)))

    # Caller-supplied extras (tracking #, ship via, promised date for SOs).
    if extra_intro_lines:
        it = iter(extra_intro_lines)
        for lbl in it:
            try:
                val = next(it)
            except StopIteration:
                break
            if val:
                meta_rows.append((str(lbl), str(val)))

    lbl_s = ParagraphStyle("hdrLbl", parent=styles["QSmall"],
                           fontName="Helvetica-Bold", textColor=OLIVE,
                           alignment=TA_RIGHT, fontSize=8, leading=10)
    val_s = ParagraphStyle("hdrVal", parent=styles["QValue"],
                           alignment=TA_RIGHT, fontSize=9, leading=10)

    meta_data = [[Paragraph(_esc(lbl), lbl_s), Paragraph(_esc(val), val_s)]
                 for lbl, val in meta_rows]

    meta_table = Table(meta_data, colWidths=[0.95 * inch, 1.55 * inch]) if meta_data else None
    if meta_table is not None:
        meta_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))

    right_rows = [
        [Paragraph(_esc(doc_title), styles["QTitle"])],
        [Paragraph(f"<b>{_esc(quote.get('quote_number', ''))}</b>",
                   styles["QSub"])],
    ]
    if meta_table is not None:
        right_rows.append([meta_table])

    right_table = Table(right_rows, colWidths=[2.6 * inch])
    right_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    header = Table([[left_table, right_table]], colWidths=[4.85 * inch, 2.6 * inch])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return header


def _build_details(
    quote: dict,
    styles,
    *,
    cust_label: str = "QUOTE FOR",
    show_expiration: bool = True,  # kept for signature compat; ignored
    extra_intro_lines: list[str] | None = None,  # ignored — handled in header
) -> Table:
    """Compact bill-to + equipment block.

    Left: customer name/company/address.
    Right: equipment info (Make / Model / ESN / VIN / Job #).
    Date / Expires / Terms now live in the header band, not here.
    """
    cust_lines = []
    cust_name = quote.get("customer_name", "")
    cust_company = quote.get("customer_company", "")

    cust_id = quote.get("customer_id")
    cust = get_customer(cust_id) if cust_id else None

    if cust_company:
        cust_lines.append(Paragraph(f"<b>{_esc(cust_company)}</b>", styles["QValue"]))
        if cust_name:
            cust_lines.append(Paragraph(_esc(cust_name), styles["QValue"]))
    elif cust_name:
        cust_lines.append(Paragraph(f"<b>{_esc(cust_name)}</b>", styles["QValue"]))

    if cust:
        addr = cust.get("address", "")
        city = cust.get("city", "")
        state = cust.get("state", "")
        zipcode = cust.get("zip", "")
        if addr:
            cust_lines.append(Paragraph(_esc(str(addr)), styles["QValue"]))
        csz = ", ".join(filter(None, [city, state]))
        if zipcode:
            csz += f"  {zipcode}"
        if csz.strip():
            cust_lines.append(Paragraph(_esc(csz), styles["QValue"]))
        phone = cust.get("phone", "")
        email = cust.get("email", "")
        if phone:
            cust_lines.append(Paragraph(_esc(str(phone)), styles["QSmall"]))
        if email:
            cust_lines.append(Paragraph(_esc(str(email)), styles["QSmall"]))

    bill_to_block = [
        [Paragraph(cust_label, styles["QLabel"])],
        *([[line] for line in cust_lines]),
    ]
    bill_to_table = Table(bill_to_block, colWidths=[3.6 * inch])
    bill_to_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))

    # Equipment-only on the right.
    esn = quote.get("esn", "") or ""
    vin = quote.get("vin", "") or ""
    mfr = quote.get("manufacturer", "") or ""
    job = quote.get("job_number", "") or ""
    eq_type = (quote.get("equipment_type") or "").lower()
    eq_make = quote.get("equipment_make") or mfr
    eq_model = quote.get("equipment_model") or ""

    if eq_type == "truck":
        make_label, model_label = "Truck Make:", "Truck Model:"
    elif eq_type == "engine":
        make_label, model_label = "Engine OEM:", "Engine Model:"
    else:
        make_label, model_label = "Manufacturer:", "Model:"

    eq_rows: list[tuple[str, str]] = []
    if eq_make:
        eq_rows.append((make_label, eq_make))
    if eq_model:
        eq_rows.append((model_label, eq_model))
    if esn:
        eq_rows.append(("Engine S/N:" if eq_type == "engine" else "ESN:", esn))
    if vin:
        eq_rows.append(("VIN:", vin))
    if job:
        eq_rows.append(("Job #:", job))

    detail_data = []
    for label, value in eq_rows:
        detail_data.append([
            Paragraph(f"<b>{_esc(label)}</b>", styles["QSmall"]),
            Paragraph(_esc(str(value)), styles["QValue"]),
        ])

    if detail_data:
        detail_table = Table(detail_data, colWidths=[1.05 * inch, 2.25 * inch])
        detail_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]))
    else:
        detail_table = Paragraph("", styles["QValue"])

    main = Table([[bill_to_table, detail_table]], colWidths=[4.15 * inch, 3.3 * inch])
    main.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return main


def _build_line_items(quote: dict, styles) -> Table:
    """Build line items split into three tiered sections.

    - **Option A â€” Required Parts** (olive): selected, not optional, no parent.
    - **Option B Add-Ons â€” Suggested** (green): selected, optional, no parent.
    - **Option C Add-Ons â€” Extended Warranty** (blue): selected, with parent.

    Tier classification is delegated to ``engine.quote_tiers.classify_lines``.
    """
    from engine.quote_tiers import classify_lines

    lines = quote.get("lines", [])
    tiers = classify_lines(lines)
    required = tiers["tier_a"]
    optional = tiers["tier_b"]
    warranty = tiers["tier_c"]

    th = ParagraphStyle("th", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold")
    th_c = ParagraphStyle("thc", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold",
                          alignment=TA_CENTER)
    th_r = ParagraphStyle("thr", fontSize=8, textColor=WHITE, fontName="Helvetica-Bold",
                          alignment=TA_RIGHT)

    cell = ParagraphStyle("cell", fontSize=9, textColor=DARK_GRAY)
    cell_c = ParagraphStyle("cellc", fontSize=9, textColor=DARK_GRAY, alignment=TA_CENTER)
    cell_r = ParagraphStyle("cellr", fontSize=9, textColor=DARK_GRAY, alignment=TA_RIGHT)

    show_sku = get_setting("quote_show_sku", "0") == "1"
    show_warranty = get_setting("quote_show_warranty", "1") == "1"
    # Phase 4: hide the Core Deposit column entirely when no line has a core.
    has_core_col = any(float(ln.get("core_charge") or 0) > 0 for ln in lines)

    # Quick lookup: parent line description for warranty rows.
    parent_lookup = {int(l.get("id") or 0): l for l in lines if l.get("id")}

    # Pre-scan for product images, keyed by product_id.
    has_images = False
    line_img_by_pid: dict[int, str | None] = {}
    for _ln in lines:
        _pid = _ln.get("product_id")
        if _pid and int(_pid) not in line_img_by_pid:
            _img = None
            try:
                _imgs = list(_get_product_images(int(_pid)))
                if _imgs and os.path.isfile(_imgs[0].get("path", "")):
                    _img = _imgs[0]["path"]
                    has_images = True
            except Exception:
                pass
            line_img_by_pid[int(_pid)] = _img

    def _make_header():
        hdr = []
        if has_images:
            hdr.append(Paragraph("", th))
        if show_sku:
            hdr.append(Paragraph("<b>SKU</b>", th))
        hdr.extend([
            Paragraph("<b>Description</b>", th),
            Paragraph("<b>Qty</b>", th_c),
            Paragraph("<b>Price</b>", th_r),
        ])
        if has_core_col:
            hdr.append(Paragraph("<b>Core Deposit</b>", th_r))
        hdr.append(Paragraph("<b>Total</b>", th_r))
        return hdr

    def _make_row(ln, *, parent_label: str = "", parent: dict | None = None):
        sku = str(ln.get("sku") or "")
        desc = str(ln.get("description") or ln.get("title") or "")
        qty = int(ln.get("qty") or 1)
        price = float(ln.get("unit_price") or 0)
        core = float(ln.get("core_charge") or 0)
        line_ship = float(ln.get("line_shipping") or 0)
        line_total = float(ln.get("line_total") or 0) + line_ship
        notes_raw = (ln.get("notes") or "").strip()

        is_opt = bool(ln.get("is_optional"))
        is_child = bool(ln.get("parent_line_id"))
        # For warranty (tier C) child rows, replace the raw stored
        # description with the customer-friendly "Additional N month
        # warranty — X month coverage" wording.
        if is_child and ln.get("warranty_months"):
            mfr_m = _parent_mfr_months(parent)
            desc_text = _esc(_warranty_wording(int(ln.get("warranty_months") or 0), mfr_m))
        else:
            desc_text = _esc(desc)
        if parent_label:
            # Warranty rows in tier C list the covered part inline rather
            # than relying on visual indentation under a parent row.
            desc_text = (
                f'{desc_text}<br/>'
                f'<font color="#666666" size="7"><i>Covers: {_esc(parent_label)}</i></font>'
            )
        if is_opt and not is_child:
            desc_text = f'<font color="#2e7d32">{desc_text}</font>'

        sub_details = []
        if not is_child and show_warranty:
            try:
                from engine.warranty import format_standard_warranty, supplier_standard_warranty_for_product
                std_months, _, _ = supplier_standard_warranty_for_product(ln)
                std_text = format_standard_warranty(std_months)
            except Exception:
                std_text = ""
            if std_text:
                sub_details.append(
                    f'<font color="#2e7d32" size="8"><b>{_esc(std_text)}</b></font>'
                )
        if line_ship > 0:
            sub_details.append(f'<font color="#666666" size="7">Freight: ${line_ship:,.2f}</font>')

        # Manual [ETA: ...] tag in notes wins; otherwise auto-compute.
        eta_label = ""
        plain_notes = ""
        if notes_raw:
            eta_match = re.search(r"\[ETA:[^\]]*\]", notes_raw)
            if eta_match:
                eta_label = eta_match.group()
            plain_notes = re.sub(r"\[ETA:[^\]]*\]\n?", "", notes_raw).strip()

        if not eta_label and ln.get("product_id"):
            try:
                from engine.eta import compute_line_eta
                _eta = compute_line_eta(int(ln["product_id"]), qty)
                if _eta and _eta.get("source") != "in_stock" and _eta.get("eta_text"):
                    eta_label = _eta["eta_text"]
            except Exception:
                pass

        if eta_label:
            # Customer-facing wording â€” no brackets, no "6-day lead" jargon.
            friendly = format_eta_friendly(eta_label) or _esc(eta_label)
            sub_details.append(
                f'<font color="#1565c0" size="8"><b>{_esc(friendly)}</b></font>'
            )
        if plain_notes:
            display = plain_notes[:80] + ("..." if len(plain_notes) > 80 else "")
            sub_details.append(f'<font color="#666666" size="7"><i>{_esc(display)}</i></font>')

        if sub_details:
            desc_text += "<br/>" + "<br/>".join(sub_details)

        row = []
        if has_images:
            _pid = ln.get("product_id")
            _img_path = line_img_by_pid.get(int(_pid)) if _pid else None
            if _img_path and os.path.isfile(_img_path):
                try:
                    _img_cell = Image(_img_path, width=0.45 * inch, height=0.45 * inch)
                    _img_cell.hAlign = "CENTER"
                    row.append(_img_cell)
                except Exception:
                    row.append(Paragraph("", cell))
            else:
                row.append(Paragraph("", cell))
        if show_sku:
            row.append(Paragraph(_esc(sku), cell))
        row.extend([
            Paragraph(desc_text, cell),
            Paragraph(str(qty), cell_c),
            Paragraph(f"${price:,.2f}", cell_r),
        ])
        if has_core_col:
            row.append(Paragraph(
                (f'${core:,.2f}<br/><font color="#2e7d32" size="6">'
                 f'(refundable)</font>') if core > 0 else "\u2014",
                cell_r,
            ))
        row.append(Paragraph(f"${line_total:,.2f}", cell_r))
        return row

    if has_images and show_sku:
        col_widths = [0.55 * inch, 0.7 * inch, 2.55 * inch, 0.40 * inch, 0.95 * inch, 1.20 * inch, 0.95 * inch]
    elif has_images:
        col_widths = [0.55 * inch, 3.25 * inch, 0.40 * inch, 1.05 * inch, 1.10 * inch, 0.95 * inch]
    elif show_sku:
        col_widths = [0.7 * inch, 3.10 * inch, 0.40 * inch, 0.95 * inch, 1.20 * inch, 0.95 * inch]
    else:
        col_widths = [3.80 * inch, 0.40 * inch, 1.05 * inch, 1.10 * inch, 0.95 * inch]

    if not has_core_col:
        # Drop the 5th-to-last cell (Core Deposit) and redistribute its
        # width to the Description column so descriptions can breathe.
        core_w = col_widths[-2]
        col_widths = col_widths[:-2] + [col_widths[-1]]
        # Description sits at index 2 when has_images+show_sku, index 1
        # when only one of them, index 0 otherwise.
        desc_idx = 2 if (has_images and show_sku) else (1 if (has_images or show_sku) else 0)
        col_widths[desc_idx] += core_w

    num_cols = len(col_widths)

    # â”€â”€ Build data rows â”€â”€
    data = [_make_header()]

    section_label = ParagraphStyle("seclbl", fontSize=10, textColor=OLIVE,
                                   fontName="Helvetica-Bold")

    from engine.quote_packages import RECOMMENDED_TIER

    # Section labels inside the line-items table stay generic — the
    # full package names are spelled out in the "Choose Your Repair
    # Option" comparison box below, so we don't repeat them here.
    SEC_A = "Required parts"
    SEC_B = "Suggested additions"
    SEC_C = "Extended warranty"

    # Track section-label row indices for styling. Only render the
    # "Required parts" divider when there's actually a B or C section
    # underneath — a 1-tier quote shouldn't waste a row on a label.
    a_label_idx = None
    if optional or warranty:
        a_label_idx = len(data)
        a_label_row = [Paragraph("") for _ in range(num_cols)]
        a_label_row[0] = Paragraph(f"<b>{SEC_A}</b>", section_label)
        data.append(a_label_row)

    for ln in required:
        data.append(_make_row(ln))

    b_label_idx = None
    if optional:
        b_label_idx = len(data)
        b_label_row = [Paragraph("") for _ in range(num_cols)]
        b_label_row[0] = Paragraph(
            f'<b><font color="#2e7d32">{SEC_B}</font></b>',
            section_label)
        data.append(b_label_row)
        b_body_start = len(data)
        for ln in optional:
            data.append(_make_row(ln))
        b_body_end = len(data) - 1
    else:
        b_body_start = b_body_end = None

    c_label_idx = None
    c_body_start = c_body_end = None
    if warranty:
        c_label_idx = len(data)
        c_label_row = [Paragraph("") for _ in range(num_cols)]
        c_label_row[0] = Paragraph(
            f'<b><font color="#1565c0">{SEC_C}</font></b>',
            section_label)
        data.append(c_label_row)
        c_body_start = len(data)
        for ln in warranty:
            pid = ln.get("parent_line_id")
            parent = parent_lookup.get(int(pid)) if pid else None
            parent_label = ""
            if parent:
                parent_label = (
                    str(parent.get("sku") or "")
                    or str(parent.get("description") or parent.get("title") or "")
                )
            data.append(_make_row(ln, parent_label=parent_label, parent=parent))
        c_body_end = len(data) - 1

    if len(data) == 2:
        empty_row = [Paragraph("No line items", cell)]
        empty_row.extend([Paragraph("", cell) for _ in range(num_cols - 1)])
        data.append(empty_row)

    table = Table(data, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), OLIVE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("TOPPADDING", (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, OLIVE),
        ("LINEBELOW", (0, -1), (-1, -1), 1, OLIVE),
        ("LINEAFTER", (0, 0), (-2, -1), 0.25, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]

    qty_col = num_cols - 4
    style_cmds.append(("ALIGN", (qty_col, 0), (qty_col, -1), "CENTER"))
    style_cmds.append(("ALIGN", (qty_col + 1, 0), (-1, -1), "RIGHT"))

    # Section label styling.
    label_rows: set[int] = set()
    if a_label_idx is not None:
        label_rows.add(a_label_idx)
        style_cmds.append(("SPAN", (0, a_label_idx), (-1, a_label_idx)))
        style_cmds.append(("BACKGROUND", (0, a_label_idx), (-1, a_label_idx),
                           colors.HexColor("#f0f0e8")))
        style_cmds.append(("TOPPADDING", (0, a_label_idx), (-1, a_label_idx), 6))
        style_cmds.append(("BOTTOMPADDING", (0, a_label_idx), (-1, a_label_idx), 6))

    if b_label_idx is not None:
        label_rows.add(b_label_idx)
        style_cmds.append(("SPAN", (0, b_label_idx), (-1, b_label_idx)))
        # Recommended tier gets a slightly stronger highlight + double
        # rules so it reads as the suggested middle option without
        # screaming.
        is_recommended_b = (RECOMMENDED_TIER == "B")
        style_cmds.append(("BACKGROUND", (0, b_label_idx), (-1, b_label_idx),
                           colors.HexColor("#dcedc8" if is_recommended_b else "#e8f5e9")))
        style_cmds.append(("TOPPADDING", (0, b_label_idx), (-1, b_label_idx), 8))
        style_cmds.append(("BOTTOMPADDING", (0, b_label_idx), (-1, b_label_idx), 8))
        style_cmds.append(("LINEABOVE", (0, b_label_idx), (-1, b_label_idx),
                           1.5 if is_recommended_b else 1, GREEN))
        if is_recommended_b and b_body_end is not None:
            # Subtle bottom rule under the whole recommended block.
            style_cmds.append(("LINEBELOW", (0, b_body_end), (-1, b_body_end), 1, GREEN))

    if c_label_idx is not None:
        label_rows.add(c_label_idx)
        style_cmds.append(("SPAN", (0, c_label_idx), (-1, c_label_idx)))
        style_cmds.append(("BACKGROUND", (0, c_label_idx), (-1, c_label_idx),
                           colors.HexColor("#e3f2fd")))
        style_cmds.append(("TOPPADDING", (0, c_label_idx), (-1, c_label_idx), 6))
        style_cmds.append(("BOTTOMPADDING", (0, c_label_idx), (-1, c_label_idx), 6))
        style_cmds.append(("LINEABOVE", (0, c_label_idx), (-1, c_label_idx), 1, BLUE))

    # Smaller font for B/C body rows so optional add-ons read as secondary
    # to the main "Requested Parts" section.
    if b_body_start is not None and b_body_end is not None and b_body_end >= b_body_start:
        style_cmds.append(("FONTSIZE", (0, b_body_start), (-1, b_body_end), 8))
    if c_body_start is not None and c_body_end is not None and c_body_end >= c_body_start:
        style_cmds.append(("FONTSIZE", (0, c_body_start), (-1, c_body_end), 8))

    # Alternating row colors (skip section label rows).
    stripe = 0
    for i in range(2, len(data)):
        if i in label_rows:
            continue
        stripe += 1
        if stripe % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), GRAY_BG))

    table.setStyle(TableStyle(style_cmds))
    return table


# ── Compact decision section (package comparison + simple totals) ─


def _compute_package_totals(quote: dict) -> dict:
    """Return cumulative grand-totals for tiers A, B, C plus context."""
    from engine.quote_tiers import classify_lines

    tiers = classify_lines(quote.get("lines", []))
    a_total = tiers["a_total"]
    b_addons = tiers["b_addons"]
    c_warranty = tiers["c_warranty"]

    a_shipping = sum(float(ln.get("line_shipping") or 0) for ln in tiers["tier_a"])
    parts_subtotal = a_total - a_shipping

    core_total = float(quote.get("core_total") or 0)
    tax_rate = float(quote.get("tax") or 0)
    tax_amount = parts_subtotal * (tax_rate / 100) if tax_rate > 0 else 0
    fuel_surcharge = float(quote.get("fuel_surcharge") or 0)

    opt_a = parts_subtotal + a_shipping + fuel_surcharge + tax_amount + core_total
    opt_b = opt_a + b_addons
    opt_c = opt_b + c_warranty

    return {
        "tiers": tiers,
        "parts_subtotal": parts_subtotal,
        "a_shipping": a_shipping,
        "fuel_surcharge": fuel_surcharge,
        "tax_rate": tax_rate,
        "tax_amount": tax_amount,
        "core_total": core_total,
        "opt_a": opt_a,
        "opt_b": opt_b,
        "opt_c": opt_c,
        "b_addons": b_addons,
        "c_warranty": c_warranty,
    }


def _build_package_comparison(quote: dict, styles) -> Table:
    """Single compact comparison table — replaces large package cards.

    Three (or fewer) rows: Package · Includes · Total. Recommended row
    gets a light-green background, a small RECOMMENDED badge, and a
    bold total — but stays the same height as the other rows.
    """
    from engine.quote_packages import package_name, RECOMMENDED_TIER

    t = _compute_package_totals(quote)
    tiers = t["tiers"]
    has_b = tiers.get("has_b")
    has_c = tiers.get("has_c")

    GREEN_HEX = colors.HexColor("#2e7d32")
    REC_BG = colors.HexColor("#eaf3da")  # subtle, not loud
    HDR_BG = colors.HexColor("#f3f1ea")

    title_s = ParagraphStyle("cmpTitle", fontSize=10, fontName="Helvetica-Bold",
                             textColor=OLIVE)
    th_s = ParagraphStyle("cmpTh", fontSize=8, fontName="Helvetica-Bold",
                          textColor=DARK_GRAY)
    th_r = ParagraphStyle("cmpThR", fontSize=8, fontName="Helvetica-Bold",
                          textColor=DARK_GRAY, alignment=TA_RIGHT)
    pkg_s = ParagraphStyle("cmpPkg", fontSize=9, fontName="Helvetica-Bold",
                           textColor=DARK_GRAY, leading=11)
    body_s = ParagraphStyle("cmpBody", fontSize=8, fontName="Helvetica",
                            textColor=DARK_GRAY, leading=10)
    price_s = ParagraphStyle("cmpPrice", fontSize=11, fontName="Helvetica-Bold",
                             textColor=DARK_GRAY, alignment=TA_RIGHT)
    price_rec_s = ParagraphStyle("cmpPriceRec", fontSize=12, fontName="Helvetica-Bold",
                                 textColor=GREEN_HEX, alignment=TA_RIGHT)

    # Brief "includes" copy — short, customer-facing.
    a_required = [ln for ln in tiers["tier_a"] if not ln.get("parent_line_id")]
    if len(a_required) == 1:
        a_includes = _esc(str(a_required[0].get("description") or a_required[0].get("sku") or "Base repair"))
    else:
        a_includes = f"Base repair ({len(a_required)} items)"

    b_titles = [str(ln.get("description") or ln.get("sku") or "") for ln in tiers["tier_b"]]
    if len(b_titles) == 1:
        b_extra = _esc(b_titles[0])
    elif b_titles:
        b_extra = f"+ {len(b_titles)} additions"
    else:
        b_extra = ""
    b_includes = f"+ {b_extra}" if b_extra else "Option A"

    c_bits: list[str] = []
    # Map id → line for parent lookup so we can include manufacturer
    # warranty months in the coverage total.
    _all_lines_by_id = {int(l.get("id") or 0): l for l in quote.get("lines", []) if l.get("id")}
    for ln in tiers["tier_c"]:
        months = int(ln.get("warranty_months") or 0)
        parent = _all_lines_by_id.get(int(ln.get("parent_line_id") or 0))
        if months:
            c_bits.append(_warranty_wording(months, _parent_mfr_months(parent)))
        else:
            c_bits.append(str(ln.get("description") or "Extended warranty"))
    c_extra = _esc(", ".join(c_bits)) if c_bits else "Extended warranty"
    c_includes = f"+ {c_extra}"

    # Header row
    rows: list[list] = [[
        Paragraph("<b>Package</b>", th_s),
        Paragraph("<b>Includes</b>", th_s),
        Paragraph("<b>Total</b>", th_r),
    ]]

    def _pkg_cell(letter: str, name: str, recommended: bool = False):
        badge = ""
        if recommended:
            badge = ('<br/><font size="6" color="white" backColor="#2e7d32">'
                     '&nbsp;RECOMMENDED&nbsp;</font>')
        return Paragraph(
            f"<b>Option {letter}</b> &mdash; {_esc(name)}{badge}", pkg_s)

    rows.append([
        _pkg_cell("A", package_name("A")),
        Paragraph(a_includes, body_s),
        Paragraph(f"${t['opt_a']:,.2f}", price_s),
    ])
    rec_row_idx = None
    if has_b:
        is_rec = (RECOMMENDED_TIER == "B")
        rec_row_idx = len(rows) if is_rec else None
        rows.append([
            _pkg_cell("B", package_name("B"), recommended=is_rec),
            Paragraph(b_includes, body_s),
            Paragraph(f"${t['opt_b']:,.2f}",
                      price_rec_s if is_rec else price_s),
        ])
    if has_c:
        rows.append([
            _pkg_cell("C", package_name("C")),
            Paragraph(c_includes, body_s),
            Paragraph(f"${t['opt_c']:,.2f}", price_s),
        ])

    tbl = Table(rows, colWidths=[1.6 * inch, 3.8 * inch, 1.3 * inch])
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), HDR_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 1, OLIVE),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cfd5d8")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if rec_row_idx is not None:
        style_cmds.append(("BACKGROUND", (0, rec_row_idx), (-1, rec_row_idx), REC_BG))

    tbl.setStyle(TableStyle(style_cmds))

    # Wrap with title above the table.
    wrap = Table(
        [[Paragraph("Choose Your Repair Option", title_s)], [tbl]],
        colWidths=[6.7 * inch],
    )
    wrap.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (0, 0), 2),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 0),
    ]))
    return wrap


def _build_decision_trailer(quote: dict, styles) -> list:
    """Slim notes shown under the comparison box.

    Only renders Expected availability + core-deposit / fuel-surcharge
    callouts — package totals are NOT repeated here.
    """
    out = []
    t = _compute_package_totals(quote)
    tiers = t["tiers"]

    note_s = ParagraphStyle("trailerNote", fontSize=9, fontName="Helvetica",
                            textColor=DARK_GRAY, alignment=TA_RIGHT)
    note_b = ParagraphStyle("trailerNoteB", fontSize=9, fontName="Helvetica-Bold",
                            textColor=OLIVE, alignment=TA_RIGHT)

    bits: list[str] = []

    # Expected availability across Option A lines.
    try:
        from engine.eta import compute_line_eta
        from .doc_theme import format_friendly_date
        dates = []
        for ln in tiers["tier_a"]:
            pid = ln.get("product_id")
            if not pid:
                continue
            try:
                _e = compute_line_eta(int(pid), int(ln.get("qty") or 1))
            except Exception:
                continue
            if _e and _e.get("eta_date"):
                dates.append(str(_e["eta_date"]))
        if dates:
            bits.append(f"Expected availability: <b>{_esc(format_friendly_date(max(dates)))}</b>")
    except Exception:
        pass

    if t["fuel_surcharge"] > 0:
        bits.append(f"Fuel surcharge included: <b>${t['fuel_surcharge']:,.2f}</b>")
    if t["core_total"] > 0:
        bits.append(
            f"Refundable core deposit: <b>${t['core_total']:,.2f}</b> "
            f"<font color='#666666' size='8'>(refunded on accepted core return)</font>"
        )

    if not bits:
        return out
    for b in bits:
        out.append(Paragraph(b, note_s))
    return out


def _build_simple_totals(quote: dict, styles) -> Table:
    """Slim totals ladder for single-tier (Option A only) quotes.

    Standardized hierarchy:
        Subtotal · Shipping · Fuel Surcharge · Tax · Core Deposit
        TOTAL DUE
    """
    t = _compute_package_totals(quote)
    parts_subtotal = t["parts_subtotal"]
    a_shipping = t["a_shipping"]
    fuel = t["fuel_surcharge"]
    tax_rate = t["tax_rate"]
    tax_amount = t["tax_amount"]
    core_total = t["core_total"]
    opt_a = t["opt_a"]

    rows: list[tuple[str, str, bool]] = []

    rows.append(("Subtotal:", f"${parts_subtotal:,.2f}", False))
    if a_shipping > 0:
        rows.append(("Shipping:", f"${a_shipping:,.2f}", False))
    if fuel > 0:
        rows.append(("Fuel Surcharge:", f"${fuel:,.2f}", False))
    if tax_rate > 0:
        rows.append((f"Tax ({tax_rate:.2f}%):", f"${tax_amount:,.2f}", False))
    if core_total > 0:
        rows.append(("Refundable Core Deposit:", f"${core_total:,.2f}", False))

    cc_fee_enabled = bool(int(quote.get("cc_fee_enabled") or 0))
    cc_fee_amount = float(quote.get("cc_fee_amount") or 0)
    if cc_fee_enabled and cc_fee_amount > 0:
        rows.append(("Credit Card Convenience Fee (3%):",
                     f"${cc_fee_amount:,.2f}", False))
        rows.append(("TOTAL DUE:", f"${opt_a + cc_fee_amount:,.2f}", True))
    else:
        rows.append(("TOTAL DUE:", f"${opt_a:,.2f}", True))

    # Phase 5: Expected availability sits DIRECTLY below the total —
    # right where the customer's eye lands after seeing the price.
    try:
        from engine.eta import compute_line_eta
        from .doc_theme import format_friendly_date
        dates = []
        for ln in t["tiers"]["tier_a"]:
            pid = ln.get("product_id")
            if not pid:
                continue
            try:
                _e = compute_line_eta(int(pid), int(ln.get("qty") or 1))
            except Exception:
                continue
            if _e and _e.get("eta_date"):
                dates.append(str(_e["eta_date"]))
        if dates:
            rows.append(("Expected availability:",
                         format_friendly_date(max(dates)), False))
    except Exception:
        pass

    lbl = ParagraphStyle("stLbl", fontSize=10, fontName="Helvetica",
                         textColor=DARK_GRAY, alignment=TA_RIGHT)
    val = ParagraphStyle("stVal", fontSize=10, fontName="Helvetica",
                         textColor=DARK_GRAY, alignment=TA_RIGHT)
    lbl_b = ParagraphStyle("stLblB", fontSize=13, fontName="Helvetica-Bold",
                           textColor=OLIVE, alignment=TA_RIGHT)
    val_b = ParagraphStyle("stValB", fontSize=13, fontName="Helvetica-Bold",
                           textColor=OLIVE, alignment=TA_RIGHT)

    data = []
    style_cmds = [
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    for i, (label, value, bold) in enumerate(rows):
        ls = lbl_b if bold else lbl
        vs = val_b if bold else val
        data.append([Paragraph(label, ls), Paragraph(value, vs)])
        if bold:
            style_cmds.append(("LINEABOVE", (0, i), (-1, i), 1, OLIVE))

    totals = Table(data, colWidths=[2.4 * inch, 1.3 * inch])
    totals.setStyle(TableStyle(style_cmds))

    outer = Table(
        [[Paragraph("", styles["Normal"]), totals]],
        colWidths=[3.0 * inch, 3.7 * inch],
    )
    outer.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return outer



