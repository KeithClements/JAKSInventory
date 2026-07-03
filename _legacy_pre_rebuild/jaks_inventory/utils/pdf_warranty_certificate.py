"""PDF Warranty Certificate generator for JAK's Diesel.

Produces a one-page-per-warranty-line certificate that:

* Identifies the customer, invoice, and covered item.
* States the coverage period, mileage cap, and coverage type.
* Includes a JAK's-authored policy block (sections from
  ``engine.warranty_tiers.policy_sections``).
* Carries a unique certificate number (``CRT-{invoice}-{line}``).
* Provides customer + JAK's signature blocks.

Also exposes a multi-line generator that combines every certificate on
an invoice into a single PDF, used by the invoice screen's "Print
Warranty" button and the email-attach path.
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
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image,
    HRFlowable, PageBreak, KeepTogether,
)
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

from db import (
    get_setting,
    get_customer_warranty,
    list_warranties_for_invoice,
    get_invoice,
)
from engine.warranty_tiers import policy_sections, short_terms_summary

# Brand palette (mirrors pdf_invoice / pdf_core_receipt)
OLIVE = colors.HexColor("#3d4a38")
OLIVE_LIGHT = colors.HexColor("#6B8E23")
GRAY_BG = colors.HexColor("#f5f5f5")
WHITE = colors.white
DARK_GRAY = colors.HexColor("#333333")
MUTED = colors.HexColor("#666666")

_BASE = Path(__file__).parent.parent
OUTPUT_DIR = _BASE / "output" / "warranty_certificates"


def _company() -> dict:
    return {
        "name": get_setting("company_name", "JAK'S Diesel"),
        "address": get_setting("company_address", ""),
        "city": get_setting("company_city", ""),
        "state": get_setting("company_state", ""),
        "zip": get_setting("company_zip", ""),
        "phone": get_setting("company_phone", ""),
        "email": get_setting("company_email", ""),
        "logo_path": _BASE / get_setting("company_logo_path", "assets/logo.png"),
    }


def _format_date(value) -> str:
    if not value:
        return ""
    try:
        if isinstance(value, str):
            return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%B %d, %Y")
        if hasattr(value, "year"):
            return value.strftime("%B %d, %Y")
    except Exception:
        pass
    return str(value)


def _coverage_label(ctype: str) -> str:
    return "Parts & Labor" if (ctype or "").lower() == "parts_labor" else "Parts Only"


def _mileage_label(limit) -> str:
    try:
        if limit is None or int(limit) <= 0:
            return "Unlimited"
        return f"{int(limit):,} miles"
    except Exception:
        return "Unlimited"


def _ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def _build_styles():
    styles = getSampleStyleSheet()
    if "WCTitle" not in styles.byName:
        styles.add(ParagraphStyle(
            "WCTitle", parent=styles["Heading1"],
            fontSize=24, textColor=OLIVE, spaceAfter=2,
            alignment=TA_RIGHT, fontName="Helvetica-Bold", leading=28,
        ))
        styles.add(ParagraphStyle(
            "WCSub", parent=styles["Normal"],
            fontSize=10, textColor=DARK_GRAY, alignment=TA_RIGHT,
        ))
        styles.add(ParagraphStyle(
            "WCLabel", parent=styles["Normal"],
            fontSize=9, textColor=OLIVE, fontName="Helvetica-Bold",
        ))
        styles.add(ParagraphStyle(
            "WCValue", parent=styles["Normal"],
            fontSize=10, textColor=DARK_GRAY,
        ))
        styles.add(ParagraphStyle(
            "WCBody", parent=styles["Normal"],
            fontSize=8.5, textColor=DARK_GRAY, leading=11,
        ))
        styles.add(ParagraphStyle(
            "WCSection", parent=styles["Normal"],
            fontSize=9, textColor=OLIVE, fontName="Helvetica-Bold",
            spaceBefore=4, spaceAfter=2,
        ))
        styles.add(ParagraphStyle(
            "WCFoot", parent=styles["Normal"],
            fontSize=7.5, textColor=MUTED, alignment=TA_CENTER,
        ))
        styles.add(ParagraphStyle(
            "WCSummary", parent=styles["Normal"],
            fontSize=10, textColor=DARK_GRAY, fontName="Helvetica-Bold",
            leading=13, alignment=TA_CENTER,
        ))
    return styles


def _header(warranty: dict, styles) -> Table:
    company = _company()
    logo_path = company["logo_path"]
    if logo_path.exists():
        logo_cell = Image(
            str(logo_path), width=2.2 * inch, height=1.65 * inch,
            kind="proportional",
        )
        logo_cell.hAlign = "LEFT"
    else:
        logo_cell = Paragraph(f"<b>{company['name']}</b>", styles["WCTitle"])

    cert_no = warranty.get("certificate_number") or "—"
    inv_num = warranty.get("invoice_number") or warranty.get("invoice_id") or "—"

    right_lines = [
        Paragraph("WARRANTY", styles["WCTitle"]),
        Paragraph("CERTIFICATE", styles["WCTitle"]),
        Spacer(1, 4),
        Paragraph(f"<b>Certificate {cert_no}</b>", styles["WCSub"]),
        Paragraph(f"Invoice {inv_num}", styles["WCSub"]),
    ]
    table = Table(
        [[logo_cell, right_lines]],
        colWidths=[3.5 * inch, 3.9 * inch],
    )
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _customer_block(warranty: dict, styles) -> Table:
    cust_name = (
        warranty.get("customer_company")
        or warranty.get("customer_name")
        or "Customer"
    )
    cust_lines = [
        Paragraph("<b>Issued To</b>", styles["WCLabel"]),
        Paragraph(str(cust_name), styles["WCValue"]),
    ]
    addr = warranty.get("customer_address")
    if addr:
        cust_lines.append(Paragraph(str(addr).replace("\n", "<br/>"), styles["WCValue"]))
    csz = ", ".join(filter(None, [
        warranty.get("customer_city"),
        warranty.get("customer_state"),
    ]))
    if warranty.get("customer_zip"):
        csz = (csz + " " + str(warranty["customer_zip"])).strip()
    if csz:
        cust_lines.append(Paragraph(csz, styles["WCValue"]))

    company = _company()
    company_lines = [
        Paragraph("<b>Issued By</b>", styles["WCLabel"]),
        Paragraph(f"<b>{company['name']}</b>", styles["WCValue"]),
    ]
    addr_str = ", ".join(filter(None, [
        company["address"], company["city"], company["state"], company["zip"],
    ]))
    if addr_str:
        company_lines.append(Paragraph(addr_str, styles["WCValue"]))
    if company["phone"]:
        company_lines.append(Paragraph(company["phone"], styles["WCValue"]))
    if company["email"]:
        company_lines.append(Paragraph(company["email"], styles["WCValue"]))

    table = Table(
        [[cust_lines, company_lines]],
        colWidths=[3.7 * inch, 3.7 * inch],
    )
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _coverage_box(warranty: dict, styles) -> Table:
    months = int(warranty.get("coverage_months") or 0)
    ctype = warranty.get("coverage_type") or "parts_only"
    rows = [
        ["Item", str(warranty.get("description") or "")[:80]],
        ["SKU", str(warranty.get("sku") or "—")],
        ["Quantity Covered", str(warranty.get("qty") or 1)],
        ["Coverage Type", _coverage_label(ctype)],
        ["Coverage Period", f"{months} months"],
        ["Mileage Limit", _mileage_label(warranty.get("mileage_limit"))],
        ["Effective Date", _format_date(warranty.get("start_date"))],
        ["Expiration Date", _format_date(warranty.get("end_date"))],
    ]
    data = [
        [Paragraph(f"<b>{k}</b>", styles["WCLabel"]),
         Paragraph(str(v), styles["WCValue"])]
        for k, v in rows
    ]
    t = Table(data, colWidths=[1.8 * inch, 5.6 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GRAY_BG),
        ("BOX", (0, 0), (-1, -1), 0.6, OLIVE),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, colors.HexColor("#dddddd")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _policy_block(warranty: dict, styles) -> list:
    out: list = []
    sections = policy_sections(
        coverage_type=warranty.get("coverage_type") or "parts_only",
        months=int(warranty.get("coverage_months") or 0),
    )
    for heading, body in sections:
        out.append(Paragraph(heading, styles["WCSection"]))
        out.append(Paragraph(body, styles["WCBody"]))
    return out


def _signatures(styles) -> Table:
    return Table(
        [
            [Paragraph("<b>Customer Signature</b>", styles["WCLabel"]), "",
             Paragraph("<b>Print Name</b>", styles["WCLabel"]), "",
             Paragraph("<b>Date</b>", styles["WCLabel"])],
            [Paragraph("________________________", styles["WCBody"]), "",
             Paragraph("________________________", styles["WCBody"]), "",
             Paragraph("____________", styles["WCBody"])],
            [Spacer(1, 0.18 * inch)] * 5,
            [Paragraph("<b>JAK's Authorized Signature</b>", styles["WCLabel"]), "",
             Paragraph("<b>Print Name</b>", styles["WCLabel"]), "",
             Paragraph("<b>Date</b>", styles["WCLabel"])],
            [Paragraph("________________________", styles["WCBody"]), "",
             Paragraph("________________________", styles["WCBody"]), "",
             Paragraph("____________", styles["WCBody"])],
        ],
        colWidths=[2.4 * inch, 0.2 * inch, 2.4 * inch, 0.2 * inch, 1.4 * inch],
    )


def _build_certificate_flowables(warranty: dict, styles) -> list:
    months = int(warranty.get("coverage_months") or 0)
    ctype = warranty.get("coverage_type") or "parts_only"

    elements: list = []
    elements.append(_header(warranty, styles))
    elements.append(Spacer(1, 0.12 * inch))
    elements.append(HRFlowable(
        width="100%", thickness=2, color=OLIVE,
        spaceAfter=0.15 * inch, spaceBefore=0.05 * inch,
    ))
    elements.append(_customer_block(warranty, styles))
    elements.append(Spacer(1, 0.15 * inch))
    elements.append(Paragraph(
        short_terms_summary(coverage_type=ctype, months=months),
        styles["WCSummary"],
    ))
    elements.append(Spacer(1, 0.1 * inch))
    elements.append(_coverage_box(warranty, styles))
    elements.append(Spacer(1, 0.18 * inch))
    elements.extend(_policy_block(warranty, styles))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(_signatures(styles))
    elements.append(Spacer(1, 0.1 * inch))
    elements.append(HRFlowable(
        width="100%", thickness=0.5, color=colors.HexColor("#cccccc"),
        spaceAfter=0.06 * inch, spaceBefore=0.06 * inch,
    ))
    cert_no = warranty.get("certificate_number") or ""
    elements.append(Paragraph(
        f"Warranty Certificate {cert_no} \u2014 {_company()['name']} "
        "\u2014 Keep with your records.",
        styles["WCFoot"],
    ))
    return elements


def generate_warranty_certificate(
    warranty_id: int, output_path: str | None = None,
) -> str:
    """Generate a single-warranty certificate PDF."""
    warranty = get_customer_warranty(int(warranty_id))
    if not warranty:
        raise ValueError(f"Warranty {warranty_id} not found")

    cert_no = warranty.get("certificate_number") or f"CRT-{warranty_id}"
    if not output_path:
        _ensure_output_dir()
        safe = str(cert_no).replace("/", "-").replace("\\", "-")
        output_path = str(OUTPUT_DIR / f"{safe}.pdf")

    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )
    styles = _build_styles()
    doc.build(_build_certificate_flowables(warranty, styles))
    return os.path.abspath(output_path)


def generate_warranty_certificates_for_invoice(
    invoice_id: int, output_path: str | None = None,
) -> str:
    """Generate one combined PDF (one cert per page) for an invoice.

    Returns the absolute path. Raises ``ValueError`` if the invoice has
    no warranty certificates.
    """
    invoice = get_invoice(int(invoice_id))
    if not invoice:
        raise ValueError(f"Invoice {invoice_id} not found")
    warranties = list_warranties_for_invoice(int(invoice_id)) or []
    if not warranties:
        raise ValueError(
            f"Invoice {invoice_id} has no warranty certificates to print"
        )

    # Hydrate each warranty with the customer + invoice context the
    # certificate layout expects (get_customer_warranty does this for a
    # single ID; replicate quickly here so we make one DB pass per cert).
    hydrated: list[dict] = []
    for w in warranties:
        full = get_customer_warranty(int(w.get("id")))
        if full:
            hydrated.append(full)

    if not output_path:
        _ensure_output_dir()
        inv_num = invoice.get("invoice_number") or f"INV-{invoice_id}"
        safe = str(inv_num).replace("/", "-").replace("\\", "-")
        output_path = str(OUTPUT_DIR / f"WARRANTY-{safe}.pdf")

    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )
    styles = _build_styles()
    flow: list = []
    for idx, w in enumerate(hydrated):
        if idx > 0:
            flow.append(PageBreak())
        flow.extend(_build_certificate_flowables(w, styles))
    doc.build(flow)
    return os.path.abspath(output_path)
