"""Customer statement PDF generator for JAKS Diesel.

Generates a professional statement showing all invoices for a customer
within a date range, with running balance and aging summary.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, HRFlowable,
)
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

from db import get_setting, get_customer, get_customer_invoices

# Brand colors
OLIVE = colors.HexColor("#3d4a38")
OLIVE_LIGHT = colors.HexColor("#6B8E23")
GRAY_BG = colors.HexColor("#f5f5f5")
WHITE = colors.white
BLACK = colors.black
DARK_GRAY = colors.HexColor("#333333")

_BASE = Path(__file__).parent.parent
OUTPUT_DIR = _BASE / "output" / "statements"


def _get_company_info() -> dict:
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
    parts = []
    if info.get("address"):
        parts.append(info["address"])
    city_state = ", ".join(filter(None, [info.get("city", ""), info.get("state", "")]))
    if city_state:
        z = info.get("zip", "")
        if z:
            city_state += " " + z
        parts.append(city_state)
    return ", ".join(parts) if parts else ""


def generate_statement_pdf(
    customer_id: int,
    as_of_date: date | None = None,
    output_path: str | None = None,
) -> str:
    """Generate a customer statement PDF.

    Args:
        customer_id: The customer to generate a statement for.
        as_of_date: Statement date (defaults to today).
        output_path: Optional explicit output path.

    Returns:
        Absolute path to the generated PDF file.
    """
    asof = as_of_date or date.today()
    customer = get_customer(customer_id)
    if not customer:
        raise ValueError(f"Customer {customer_id} not found")

    invoices = get_customer_invoices(customer_id)

    # Filter to non-void invoices on or before the as_of date
    filtered = []
    for inv in invoices:
        if inv.get("status") == "void":
            continue
        inv_date = inv.get("invoice_date")
        if isinstance(inv_date, str):
            inv_date = date.fromisoformat(inv_date[:10])
        if inv_date and inv_date <= asof:
            filtered.append(inv)

    # Sort oldest first
    filtered.sort(key=lambda x: str(x.get("invoice_date", "")))

    if not output_path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        cust_name = (customer.get("name") or "customer").replace(" ", "_")
        safe = "".join(c for c in cust_name if c.isalnum() or c == "_")
        output_path = str(OUTPUT_DIR / f"statement_{safe}_{asof.isoformat()}.pdf")

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.6 * inch,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("StTitle", parent=styles["Heading1"],
                              fontSize=20, textColor=OLIVE, spaceAfter=2))
    styles.add(ParagraphStyle("StLabel", parent=styles["Normal"],
                              fontSize=9, textColor=OLIVE, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("StValue", parent=styles["Normal"],
                              fontSize=10, textColor=DARK_GRAY))
    styles.add(ParagraphStyle("StSmall", parent=styles["Normal"],
                              fontSize=8, textColor=colors.HexColor("#666666")))
    styles.add(ParagraphStyle("StRight", parent=styles["Normal"],
                              fontSize=10, textColor=DARK_GRAY, alignment=TA_RIGHT))
    styles.add(ParagraphStyle("StFooter", parent=styles["Normal"],
                              fontSize=8, textColor=colors.HexColor("#999999"),
                              alignment=TA_CENTER))

    elements = []
    company = _get_company_info()

    # ── HEADER ───────────────────────────────────────────────────
    logo_path = company["logo_path"]
    if logo_path.exists():
        logo = Image(str(logo_path), width=1.8 * inch, height=1.4 * inch,
                     kind="proportional")
        logo.hAlign = "LEFT"
    else:
        logo = Paragraph(f"<b>{company['name']}</b>", styles["StTitle"])

    company_lines = [
        Paragraph(f"<b>{company['name']}</b>", styles["StValue"]),
    ]
    addr = _format_address(company)
    if addr:
        company_lines.append(Paragraph(addr, styles["StSmall"]))
    if company["phone"]:
        company_lines.append(Paragraph(company["phone"], styles["StSmall"]))

    header_data = [[logo, company_lines]]
    header = Table(header_data, colWidths=[2.5 * inch, 4.5 * inch])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(header)
    elements.append(Spacer(1, 0.1 * inch))

    # Title
    elements.append(Paragraph("STATEMENT", styles["StTitle"]))
    elements.append(HRFlowable(width="100%", thickness=2, color=OLIVE,
                                spaceAfter=0.15 * inch, spaceBefore=0.05 * inch))

    # ── CUSTOMER + STATEMENT INFO ────────────────────────────────
    cust_lines = [
        Paragraph(customer.get("name", ""), styles["StValue"]),
    ]
    if customer.get("company"):
        cust_lines.append(Paragraph(customer["company"], styles["StSmall"]))
    if customer.get("email"):
        cust_lines.append(Paragraph(customer["email"], styles["StSmall"]))

    stmt_lines = [
        Paragraph(f"Statement Date: <b>{asof.strftime('%B %d, %Y')}</b>", styles["StValue"]),
        Paragraph(f"Customer ID: {customer_id}", styles["StSmall"]),
    ]

    info_data = [[cust_lines, stmt_lines]]
    info_table = Table(info_data, colWidths=[3.5 * inch, 3.5 * inch])
    info_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 0.25 * inch))

    # ── INVOICE TABLE ────────────────────────────────────────────
    table_data = [["Date", "Invoice #", "Status", "Total", "Paid", "Balance"]]

    total_invoiced = Decimal("0.00")
    total_paid = Decimal("0.00")
    total_balance = Decimal("0.00")

    # Aging buckets
    current_bucket = Decimal("0.00")
    bucket_30 = Decimal("0.00")
    bucket_60 = Decimal("0.00")
    bucket_90 = Decimal("0.00")

    for inv in filtered:
        inv_date = str(inv.get("invoice_date", ""))[:10]
        inv_num = inv.get("invoice_number", "")
        status = (inv.get("status") or "draft").capitalize()
        total = Decimal(str(inv.get("total") or 0))
        paid = Decimal(str(inv.get("amount_paid") or 0))
        balance = Decimal(str(inv.get("balance_due") or 0))
        if balance == 0 and total > paid:
            balance = total - paid

        total_invoiced += total
        total_paid += paid
        total_balance += balance

        # Aging
        if balance > 0 and inv_date:
            try:
                d = date.fromisoformat(inv_date)
                age = (asof - d).days
                if age <= 30:
                    current_bucket += balance
                elif age <= 60:
                    bucket_30 += balance
                elif age <= 90:
                    bucket_60 += balance
                else:
                    bucket_90 += balance
            except ValueError:
                current_bucket += balance

        table_data.append([
            inv_date,
            inv_num,
            status,
            f"${total:,.2f}",
            f"${paid:,.2f}",
            f"${balance:,.2f}",
        ])

    # Totals row
    table_data.append([
        "", "", "TOTALS",
        f"${total_invoiced:,.2f}",
        f"${total_paid:,.2f}",
        f"${total_balance:,.2f}",
    ])

    col_widths = [1.0 * inch, 1.5 * inch, 0.9 * inch, 1.1 * inch, 1.1 * inch, 1.1 * inch]
    inv_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    inv_table.setStyle(TableStyle([
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), OLIVE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        # Body
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("TOPPADDING", (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [WHITE, GRAY_BG]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        # Alignment
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        # Totals row
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8e8e8")),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 1.5, OLIVE),
    ]))
    elements.append(inv_table)
    elements.append(Spacer(1, 0.25 * inch))

    # ── AGING SUMMARY ────────────────────────────────────────────
    aging_data = [
        ["Current", "31–60 Days", "61–90 Days", "90+ Days", "Total Due"],
        [f"${current_bucket:,.2f}", f"${bucket_30:,.2f}",
         f"${bucket_60:,.2f}", f"${bucket_90:,.2f}",
         f"${total_balance:,.2f}"],
    ]
    aging_table = Table(aging_data, colWidths=[1.3 * inch] * 5)
    aging_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), OLIVE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("BACKGROUND", (0, 1), (-1, 1), WHITE),
        ("TEXTCOLOR", (0, 1), (-1, 1), DARK_GRAY),
    ]))
    elements.append(Paragraph("Aging Summary", styles["StLabel"]))
    elements.append(Spacer(1, 4))
    elements.append(aging_table)
    elements.append(Spacer(1, 0.3 * inch))

    # ── OPEN CREDITS ON FILE ─────────────────────────────────────
    # B.9: surface any unredeemed customer credits (returned cores,
    # overpayments, manual credit memos) so the customer sees what
    # they have available against future invoices.
    try:
        from db import list_customer_credits, get_open_credit_total
        open_credits = [
            r for r in (list_customer_credits(customer_id) or [])
            if str(r.get("status") or "").lower() == "open"
        ]
        open_credit_total = float(get_open_credit_total(customer_id) or 0)
    except Exception:
        open_credits = []
        open_credit_total = 0.0

    if open_credits:
        elements.append(Paragraph("Open Credits On File", styles["StLabel"]))
        elements.append(Spacer(1, 4))
        cred_data = [["Issued", "Source", "Amount", "Notes"]]
        for c in open_credits:
            issued = str(c.get("issue_date") or "")[:10]
            source = str(c.get("source_type") or "").title()
            amt = float(c.get("amount") or 0)
            notes = str(c.get("notes") or "")[:60]
            cred_data.append([
                issued, source, f"${amt:,.2f}",
                Paragraph(notes, styles["StSmall"]),
            ])
        cred_data.append([
            "", "TOTAL", f"${open_credit_total:,.2f}", "",
        ])
        cred_table = Table(
            cred_data,
            colWidths=[1.0 * inch, 1.0 * inch, 1.1 * inch, 3.6 * inch],
            repeatRows=1,
        )
        cred_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), OLIVE),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8e8e8")),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("LINEABOVE", (0, -1), (-1, -1), 1.5, OLIVE),
        ]))
        elements.append(cred_table)
        elements.append(Spacer(1, 0.3 * inch))

    # ── FOOTER ───────────────────────────────────────────────────
    elements.append(HRFlowable(width="100%", thickness=0.5,
                                color=colors.HexColor("#cccccc"),
                                spaceAfter=0.1 * inch, spaceBefore=0.1 * inch))
    payment_terms = get_setting("payment_terms", "Net 30")
    elements.append(Paragraph(
        f"Payment Terms: {payment_terms} &bull; "
        f"Please remit payment to {company['name']}",
        styles["StFooter"],
    ))

    doc.build(elements)
    return os.path.abspath(output_path)
