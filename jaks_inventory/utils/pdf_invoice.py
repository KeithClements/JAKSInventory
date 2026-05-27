"""PDF invoice generator for JAKS Diesel.

Uses ReportLab to produce professional branded invoices with:
- Company logo + info header
- Bill-to customer section
- Line items table with SKU, description, qty, price, total
- Totals block (subtotal, tax, shipping, total, paid, balance due)
- Notes / payment terms footer
"""
from __future__ import annotations

import os
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

from db import get_setting
from db import get_product_images as _get_product_images

# Unified brand palette + section builders — single source of truth
# in ``doc_theme`` so Invoice / Quote / SO / PO / Pick / Core all look
# like they came out of the same ERP printer.
from .doc_theme import (
    OLIVE, OLIVE_LIGHT, GRAY_BG, WHITE, BLACK, DARK_GRAY,
    SIZE_DOC_TITLE, SIZE_DOC_TITLE_LEADING,
    PAGE_MARGIN_L, PAGE_MARGIN_R, PAGE_MARGIN_T, PAGE_MARGIN_B,
    build_footer as _shared_footer,
    build_totals_block as _shared_totals_block,
    format_friendly_date,
    make_simple_doc,
)

# Base path for resolving relative paths
_BASE = Path(__file__).parent.parent

# Output directory
OUTPUT_DIR = _BASE / "output" / "invoices"


def _get_company_info() -> dict:
    """Read company info from DB settings with defaults."""
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


def _format_address(info: dict) -> str:
    """Build a one-line address from company info parts."""
    parts = []
    if info["address"]:
        parts.append(info["address"])
    city_state = ", ".join(filter(None, [info["city"], info["state"]]))
    if city_state:
        if info["zip"]:
            city_state += " " + info["zip"]
        parts.append(city_state)
    return ", ".join(parts) if parts else ""


def _ensure_output_dir() -> Path:
    """Create the invoices output directory if needed."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def generate_invoice_pdf(invoice: dict, output_path: str | None = None) -> str:
    """Generate a professional PDF invoice.

    Args:
        invoice: Invoice dict from get_invoice() with lines, customer info, totals.
        output_path: Optional explicit output path. If None, auto-generates in output/invoices/.

    Returns:
        Absolute path to the generated PDF file.
    """
    inv_num = invoice.get("invoice_number", "DRAFT")

    if not output_path:
        _ensure_output_dir()
        safe_name = inv_num.replace("/", "-").replace("\\", "-")
        output_path = str(OUTPUT_DIR / f"{safe_name}.pdf")

    doc = make_simple_doc(output_path)

    styles = getSampleStyleSheet()

    # Custom styles — sized to match Quote/SO/PO/Pick/Core so every
    # printed doc shares the same hierarchy (22pt title, 10pt body, 8pt fine print).
    styles.add(ParagraphStyle(
        "InvTitle", parent=styles["Heading1"],
        fontSize=SIZE_DOC_TITLE, textColor=OLIVE, spaceAfter=2,
        alignment=TA_RIGHT, fontName="Helvetica-Bold",
        leading=SIZE_DOC_TITLE_LEADING,
    ))
    styles.add(ParagraphStyle(
        "InvSubtitle", parent=styles["Normal"],
        fontSize=11, textColor=DARK_GRAY, alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        "InvLabel", parent=styles["Normal"],
        fontSize=9, textColor=OLIVE, fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        "InvValue", parent=styles["Normal"],
        fontSize=10, textColor=DARK_GRAY,
    ))
    styles.add(ParagraphStyle(
        "InvSmall", parent=styles["Normal"],
        fontSize=8, textColor=colors.HexColor("#666666"),
    ))
    styles.add(ParagraphStyle(
        "InvRight", parent=styles["Normal"],
        fontSize=10, textColor=DARK_GRAY, alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        "InvFooter", parent=styles["Normal"],
        fontSize=8, textColor=colors.HexColor("#999999"), alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        "InvNotes", parent=styles["Normal"],
        fontSize=9, textColor=DARK_GRAY, spaceAfter=4,
    ))

    elements = []

    # ── HEADER: Logo + Company info ──────────────────────────────
    elements.append(_build_header(invoice, styles))
    elements.append(Spacer(1, 0.08 * inch))

    # Olive divider line (matches Quote/SO/PO/Pick/Core)
    elements.append(HRFlowable(
        width="100%", thickness=1.5, color=OLIVE,
        spaceAfter=0.06 * inch, spaceBefore=0.02 * inch,
    ))

    # ── BILL TO + INVOICE DETAILS ────────────────────────────────
    elements.append(_build_bill_to(invoice, styles))
    elements.append(Spacer(1, 0.15 * inch))

    # ── LINE ITEMS TABLE ─────────────────────────────────────────
    elements.append(_build_line_items(invoice, styles))
    elements.append(Spacer(1, 0.12 * inch))

    # ── TOTALS ───────────────────────────────────────────────────
    elements.append(_build_totals(invoice, styles))
    elements.append(Spacer(1, 0.2 * inch))

    # CC convenience-fee disclosure
    if bool(int(invoice.get("cc_fee_enabled") or 0)):
        elements.append(Paragraph(
            "<i>This invoice includes a 3% credit card convenience fee. "
            "Save 3% by paying with cash, check, or ACH.</i>",
            styles["InvNotes"],
        ))
        elements.append(Spacer(1, 0.15 * inch))

    # ── NOTES ────────────────────────────────────────────────────
    notes = invoice.get("notes")
    if notes:
        elements.append(Paragraph("Notes", styles["InvLabel"]))
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(str(notes), styles["InvNotes"]))
        elements.append(Spacer(1, 0.15 * inch))

    # Customer-facing notes (separate from internal "Notes" above).
    cust_notes = invoice.get("customer_notes")
    if cust_notes:
        elements.append(Paragraph("Customer Notes", styles["InvLabel"]))
        elements.append(Spacer(1, 4))
        elements.append(Paragraph(
            str(cust_notes).replace("\n", "<br/>"), styles["InvNotes"],
        ))
        elements.append(Spacer(1, 0.15 * inch))

    # ── SIGNATURE BLOCK (#12) ────────────────────────────────────
    # Printed lines for the customer to sign and date on receipt.
    sig_table = Table(
        [
            [Paragraph("<b>Customer Signature</b>", styles["InvLabel"]), "",
             Paragraph("<b>Print Name</b>", styles["InvLabel"]), "",
             Paragraph("<b>Date</b>", styles["InvLabel"])],
            [Paragraph("________________________", styles["InvNotes"]), "",
             Paragraph("________________________", styles["InvNotes"]), "",
             Paragraph("____________", styles["InvNotes"])],
        ],
        colWidths=[2.4 * inch, 0.2 * inch, 2.4 * inch, 0.2 * inch, 1.4 * inch],
    )
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(sig_table)
    elements.append(Spacer(1, 0.1 * inch))

    # ── FOOTER (shared across all docs) ──────────────────────────
    _footer_styles = {"footer": styles["InvFooter"]}
    has_core = any(float(ln.get("core_charge") or 0) > 0 for ln in invoice.get("lines", []))
    elements.extend(_shared_footer(
        _footer_styles,
        include_core_note=has_core,
        include_warranty_note=True,
    ))

    doc.build(elements)
    return os.path.abspath(output_path)


# ── Section builders ─────────────────────────────────────────────


def _build_header(invoice: dict, styles) -> Table:
    """Logo on left, invoice number / date on right."""
    inv_num = invoice.get("invoice_number", "DRAFT")
    status = (invoice.get("status") or "draft").upper()

    company = _get_company_info()
    logo_path = company["logo_path"]

    # Logo — sized to match Quote/SO/PO/Pick/Core (2.2" wide).
    logo_cell: Image | Paragraph
    if logo_path.exists():
        logo_cell = Image(str(logo_path), width=2.2 * inch, height=0.85 * inch,
                          kind="proportional")
        logo_cell.hAlign = "LEFT"
    else:
        logo_cell = Paragraph(f"<b>{company['name']}</b>", styles["InvTitle"])

    # Right side: Invoice # and status
    status_color = {
        "DRAFT": "#999999", "SENT": "#d4a017", "PARTIAL": "#e67e22",
        "PAID": "#27ae60", "VOID": "#c0392b",
    }.get(status, "#333333")

    right_lines = [
        Paragraph("INVOICE", styles["InvTitle"]),
        Paragraph(f"<b>{inv_num}</b>", styles["InvSubtitle"]),
        Spacer(1, 4),
        Paragraph(
            f'<font color="{status_color}"><b>{status}</b></font>',
            styles["InvSubtitle"],
        ),
    ]

    # Company info below logo
    company_lines = []
    addr = _format_address(company)
    show_addr = get_setting("quote_show_company_address", "1") == "1"
    if addr and show_addr:
        company_lines.append(Paragraph(addr, styles["InvSmall"]))
    if company["phone"] and show_addr:
        company_lines.append(Paragraph(company["phone"], styles["InvSmall"]))
    if company["email"] and show_addr:
        company_lines.append(Paragraph(company["email"], styles["InvSmall"]))

    left_content = [[logo_cell], *([[p] for p in company_lines])]
    left_table = Table(left_content, colWidths=[3.2 * inch])
    left_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    right_content = [right_lines]
    right_table = Table([right_lines], colWidths=[3.0 * inch])
    right_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))

    header_data = [[left_table, right_table]]
    header = Table(header_data, colWidths=[4.0 * inch, 3.0 * inch])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return header


def _build_bill_to(invoice: dict, styles) -> Table:
    """Bill-to customer info on left, invoice dates on right."""
    # Customer info
    cust_lines = []
    name = invoice.get("customer_name", "")
    company = invoice.get("customer_company", "")
    if company:
        cust_lines.append(Paragraph(f"<b>{company}</b>", styles["InvValue"]))
        if name:
            cust_lines.append(Paragraph(name, styles["InvValue"]))
    elif name:
        cust_lines.append(Paragraph(f"<b>{name}</b>", styles["InvValue"]))

    addr = invoice.get("customer_address", "")
    city = invoice.get("customer_city", "")
    state = invoice.get("customer_state", "")
    zipcode = invoice.get("customer_zip", "")
    if addr:
        cust_lines.append(Paragraph(str(addr), styles["InvValue"]))
    city_state_zip = ", ".join(filter(None, [city, state]))
    if zipcode:
        city_state_zip += f"  {zipcode}"
    if city_state_zip.strip():
        cust_lines.append(Paragraph(city_state_zip, styles["InvValue"]))

    email = invoice.get("customer_email", "")
    phone = invoice.get("customer_phone", "")
    if phone:
        cust_lines.append(Paragraph(str(phone), styles["InvSmall"]))
    if email:
        cust_lines.append(Paragraph(str(email), styles["InvSmall"]))

    bill_to_block = [
        [Paragraph("BILL TO", styles["InvLabel"])],
        *([[line] for line in cust_lines]),
    ]
    bill_to_table = Table(bill_to_block, colWidths=[3.2 * inch])
    bill_to_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))

    # Invoice details (right side)
    inv_date = format_friendly_date(invoice.get("invoice_date")) if invoice.get("invoice_date") else ""
    due_date = format_friendly_date(invoice.get("due_date")) if invoice.get("due_date") else ""
    payment_method = invoice.get("payment_method", "")
    eq_type = (invoice.get("equipment_type") or "").lower()
    eq_make = invoice.get("equipment_make") or ""
    eq_model = invoice.get("equipment_model") or ""
    esn = invoice.get("esn") or ""
    vin = invoice.get("vin") or ""

    detail_rows = [
        ["Invoice Date:", inv_date],
        ["Due Date:", due_date],
    ]
    if eq_type == "truck":
        make_label, model_label = "Truck Make:", "Truck Model:"
    elif eq_type == "engine":
        make_label, model_label = "Engine OEM:", "Engine Model:"
    else:
        make_label, model_label = "Manufacturer:", "Model:"
    if eq_make:
        detail_rows.append([make_label, eq_make])
    if eq_model:
        detail_rows.append([model_label, eq_model])
    if esn:
        detail_rows.append(["Engine S/N:" if eq_type == "engine" else "ESN:", esn])
    if vin:
        detail_rows.append(["VIN:", vin])
    if payment_method:
        detail_rows.append(["Payment Method:", str(payment_method)])

    detail_data = []
    for label, value in detail_rows:
        detail_data.append([
            Paragraph(f"<b>{label}</b>", styles["InvSmall"]),
            Paragraph(str(value), styles["InvValue"]),
        ])

    detail_table = Table(detail_data, colWidths=[1.2 * inch, 2.0 * inch])
    detail_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    main_data = [[bill_to_table, detail_table]]
    main_table = Table(main_data, colWidths=[3.8 * inch, 3.2 * inch])
    main_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return main_table


def _build_line_items(invoice: dict, styles) -> Table:
    """Build the line items table with olive header and optional product images."""
    lines = invoice.get("lines", [])

    th_style = ParagraphStyle("th", fontSize=9, textColor=WHITE, fontName="Helvetica-Bold")

    # Check if any line has an image
    has_images = False
    line_images: list[str | None] = []
    for line in lines:
        product_id = line.get("product_id")
        img_path = None
        if product_id:
            try:
                imgs = list(_get_product_images(product_id))
                if imgs and os.path.isfile(imgs[0].get("path", "")):
                    img_path = imgs[0]["path"]
                    has_images = True
            except Exception:
                pass
        line_images.append(img_path)

    show_sku = get_setting("quote_show_sku", "0") == "1"
    # Header row
    header_cells = []
    if has_images:
        header_cells.append(Paragraph("", th_style))
    if show_sku:
        header_cells.append(Paragraph("<b>SKU</b>", th_style))
    header_cells.extend([
        Paragraph("<b>Description</b>", ParagraphStyle("th2", fontSize=9, textColor=WHITE, fontName="Helvetica-Bold")),
        Paragraph("<b>Qty</b>", ParagraphStyle("th3", fontSize=9, textColor=WHITE, fontName="Helvetica-Bold", alignment=TA_CENTER)),
        Paragraph("<b>Unit Price</b>", ParagraphStyle("th4", fontSize=9, textColor=WHITE, fontName="Helvetica-Bold", alignment=TA_RIGHT)),
        Paragraph("<b>Total</b>", ParagraphStyle("th5", fontSize=9, textColor=WHITE, fontName="Helvetica-Bold", alignment=TA_RIGHT)),
    ])

    data = [header_cells]

    body_style = styles["InvValue"]
    body_center = ParagraphStyle("ctr", fontSize=10, alignment=TA_CENTER, textColor=DARK_GRAY)
    body_right = ParagraphStyle("rt", fontSize=10, alignment=TA_RIGHT, textColor=DARK_GRAY)
    core_style = ParagraphStyle("core", parent=body_style, fontSize=9, textColor=colors.HexColor("#6b7280"))

    for idx, line in enumerate(lines):
        sku = str(line.get("sku") or "")
        desc = str(line.get("invoice_description") or line.get("description") or line.get("product_title") or "")
        qty = int(line.get("quantity") or 1)
        unit_price = float(line.get("unit_price") or 0)
        line_total = float(line.get("line_total") or 0)
        core_each = float(line.get("core_charge") or 0)
        core_total = core_each * qty
        is_pseudo_line = sku.upper().startswith(("CORE-", "RTN-"))

        # Append warranty sub-line to description when present
        w_months = int(line.get("warranty_months") or 0)
        if w_months > 0:
            from engine.warranty import format_warranty_long
            w_text = format_warranty_long(
                w_months,
                line.get("warranty_type") or "parts_only",
                line.get("warranty_mileage_limit"),
            )
            desc = f"{desc}<br/><font size=\"8\" color=\"#1565c0\"><i>{w_text}</i></font>"

        row = []
        if has_images:
            img_path = line_images[idx] if idx < len(line_images) else None
            if img_path and os.path.isfile(img_path):
                try:
                    img = Image(img_path, width=0.45 * inch, height=0.45 * inch)
                    img.hAlign = "CENTER"
                    row.append(img)
                except Exception:
                    row.append(Paragraph("", styles["InvValue"]))
            else:
                row.append(Paragraph("", styles["InvValue"]))

        if show_sku:
            row.append(Paragraph(sku, styles["InvValue"]))
        row.extend([
            Paragraph(desc, styles["InvValue"]),
            Paragraph(str(qty), body_center),
            Paragraph(f"${unit_price:,.2f}", body_right),
            Paragraph(f"${line_total:,.2f}", body_right),
        ])
        data.append(row)

        if core_total > 0 and not is_pseudo_line:
            core_row = []
            if has_images:
                core_row.append(Paragraph("", body_style))
            if show_sku:
                core_row.append(Paragraph("", body_style))
            core_row.extend([
                Paragraph("<i>Core Deposit - refundable on accepted core return</i>", core_style),
                Paragraph(str(qty), body_center),
                Paragraph(f"${core_each:,.2f}", body_right),
                Paragraph(f"${core_total:,.2f}", body_right),
            ])
            data.append(core_row)

    # If no lines, add placeholder
    if not lines:
        empty_row = []
        if has_images:
            empty_row.append(Paragraph("", styles["InvValue"]))
        if show_sku:
            empty_row.append(Paragraph("—", styles["InvValue"]))
        empty_row.extend([
            Paragraph("No line items", styles["InvValue"]),
            Paragraph("", styles["InvValue"]),
            Paragraph("", styles["InvValue"]),
            Paragraph("", styles["InvValue"]),
        ])
        data.append(empty_row)

    if has_images and show_sku:
        col_widths = [0.55 * inch, 1.0 * inch, 2.55 * inch, 0.6 * inch, 1.1 * inch, 1.1 * inch]
    elif has_images:
        col_widths = [0.55 * inch, 3.55 * inch, 0.6 * inch, 1.1 * inch, 1.1 * inch]
    elif show_sku:
        col_widths = [1.1 * inch, 3.0 * inch, 0.6 * inch, 1.1 * inch, 1.1 * inch]
    else:
        col_widths = [4.1 * inch, 0.6 * inch, 1.1 * inch, 1.1 * inch]

    num_cols = len(col_widths)
    table = Table(data, colWidths=col_widths, repeatRows=1)

    # Style: olive header, alternating row colors
    # Column indices shift based on image column
    qty_col = num_cols - 3
    price_col = num_cols - 2
    total_col = num_cols - 1

    style_commands = [
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), OLIVE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        # Body
        ("FONTSIZE", (0, 1), (-1, -1), 10),
        ("TOPPADDING", (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        # Grid
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, OLIVE),
        ("LINEBELOW", (0, -1), (-1, -1), 1, OLIVE),
        ("LINEAFTER", (0, 0), (-2, -1), 0.25, colors.HexColor("#dddddd")),
        # Alignment
        ("ALIGN", (qty_col, 0), (qty_col, -1), "CENTER"),
        ("ALIGN", (price_col, 0), (total_col, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]

    # Alternating row shading
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_commands.append(("BACKGROUND", (0, i), (-1, i), GRAY_BG))

    table.setStyle(TableStyle(style_commands))
    return table


def _build_totals(invoice: dict, styles) -> Table:
    """Build the totals block aligned to the right.

    Uses the shared olive ladder from ``doc_theme.build_totals_block``
    so Invoice / Quote / SO / PO all present totals identically.
    Sequence: Subtotal → Shipping → Tax → (Core Deposit) → TOTAL DUE
    → (Amount Paid) → (Balance Due).
    """
    subtotal = float(invoice.get("subtotal") or 0)
    tax_rate = float(invoice.get("tax_rate") or 0)
    tax_amount = float(invoice.get("tax_amount") or 0)
    shipping = float(invoice.get("shipping") or 0)
    total = float(invoice.get("total") or 0)
    amount_paid = float(invoice.get("amount_paid") or 0)
    balance_due = float(invoice.get("balance_due") or 0)

    # Core deposit (refundable — surfaced for transparency).
    core_total = 0.0
    for ln in invoice.get("lines", []) or []:
        sku = str(ln.get("sku") or "").upper()
        if sku.startswith(("CORE-", "RTN-")):
            continue
        qty = int(ln.get("quantity") or 0)
        core_total += float(ln.get("core_charge") or 0) * qty

    rows: list[tuple[str, str, bool]] = [
        ("Subtotal", f"${subtotal:,.2f}", False),
    ]
    if shipping > 0:
        rows.append(("Shipping", f"${shipping:,.2f}", False))
    if tax_rate > 0 or tax_amount > 0:
        tax_label = f"Tax ({tax_rate:.1f}%)" if tax_rate > 0 else "Tax"
        rows.append((tax_label, f"${tax_amount:,.2f}", False))

    cc_fee_enabled = bool(int(invoice.get("cc_fee_enabled") or 0))
    cc_fee_amount = float(invoice.get("cc_fee_amount") or 0)
    if cc_fee_enabled and cc_fee_amount > 0:
        rows.append(("Credit Card Fee (3%)", f"${cc_fee_amount:,.2f}", False))

    if core_total > 0:
        rows.append(("Core Deposit (refundable)", f"${core_total:,.2f}", False))

    rows.append(("TOTAL DUE", f"${total:,.2f}", True))

    if amount_paid > 0:
        rows.append(("Amount Paid", f"${amount_paid:,.2f}", False))
        rows.append(("Balance Due", f"${balance_due:,.2f}", True))

    return _shared_totals_block(rows, label_col_w=2.0, value_col_w=1.3, page_inset=3.7)
