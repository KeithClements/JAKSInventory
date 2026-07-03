"""Credit agreement PDF generator for JAK'S Diesel in-house credit terms."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
)

from db import get_customer, get_credit_agreement, get_setting

# Brand colors
OLIVE = colors.HexColor("#3d4a38")
DARK_GRAY = colors.HexColor("#333333")
LIGHT_BG = colors.HexColor("#f5f5f5")

_BASE = Path(__file__).parent.parent
OUTPUT_DIR = _BASE / "output" / "credit_agreements"


def _company():
    return {
        "name": get_setting("company_name", "JAK'S Diesel"),
        "address": get_setting("company_address", ""),
        "city": get_setting("company_city", "Henderson"),
        "state": get_setting("company_state", "NV"),
        "zip": get_setting("company_zip", ""),
        "phone": get_setting("company_phone", ""),
        "email": get_setting("company_email", ""),
    }


def generate_credit_agreement_pdf(customer_id: int, output_path: str | None = None) -> str:
    """Generate credit agreement PDF for a customer.

    Returns:
        Absolute path to generated PDF.
    """
    cust = get_customer(customer_id)
    if not cust:
        raise ValueError(f"Customer {customer_id} not found")
    agreement = get_credit_agreement(customer_id)
    if not agreement:
        raise ValueError("No credit agreement on file. Save credit terms first.")

    co = _company()
    credit_limit = float(agreement.get("credit_limit") or 0)
    apr = float(agreement.get("apr") or 0)
    net_days = int(agreement.get("net_days") or 30)
    issue_date = agreement.get("issue_date") or datetime.now().strftime("%Y-%m-%d")
    guarantor = agreement.get("guarantor_name") or ""

    # Build output path
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not output_path:
        safe = (cust.get("company") or cust.get("name") or f"cust_{customer_id}").replace(
            " ", "_"
        )[:40]
        output_path = str(OUTPUT_DIR / f"Credit_Agreement_{safe}_{issue_date}.pdf")

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("CrTitle", parent=styles["Heading1"],
                              fontSize=18, textColor=OLIVE, alignment=TA_CENTER, spaceAfter=2))
    styles.add(ParagraphStyle("CrSubtitle", parent=styles["Normal"],
                              fontSize=10, textColor=DARK_GRAY, alignment=TA_CENTER))
    styles.add(ParagraphStyle("CrSection", parent=styles["Heading2"],
                              fontSize=12, textColor=OLIVE, spaceBefore=14, spaceAfter=4,
                              fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("CrBody", parent=styles["Normal"],
                              fontSize=10, textColor=DARK_GRAY, leading=14,
                              alignment=TA_JUSTIFY, spaceAfter=4))
    styles.add(ParagraphStyle("CrBold", parent=styles["Normal"],
                              fontSize=10, textColor=DARK_GRAY, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("CrSmall", parent=styles["Normal"],
                              fontSize=8, textColor=colors.HexColor("#666666"),
                              alignment=TA_CENTER))

    story: list = []
    # ──── HEADER ────
    story.append(Paragraph(f"{co['name']}", styles["CrTitle"]))
    addr_parts = [co["address"], ", ".join(filter(None, [co["city"], co["state"]])), co["zip"]]
    story.append(Paragraph(" ".join(filter(None, addr_parts)), styles["CrSubtitle"]))
    contact = " | ".join(filter(None, [co["phone"], co["email"]]))
    if contact:
        story.append(Paragraph(contact, styles["CrSubtitle"]))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=2, color=OLIVE))
    story.append(Spacer(1, 6))
    story.append(Paragraph("CREDIT TERMS AGREEMENT", styles["CrTitle"]))
    story.append(Spacer(1, 10))

    # ──── 1. APPLICANT INFORMATION ────
    story.append(Paragraph("1. APPLICANT INFORMATION", styles["CrSection"]))
    cname = cust.get("company") or cust.get("name", "")
    caddr = ", ".join(filter(None, [
        cust.get("address"), cust.get("city"), cust.get("state"), cust.get("zip")
    ]))
    applicant_data = [
        ["Business / Individual Name:", cname],
        ["Address:", caddr],
        ["Phone:", cust.get("phone") or cust.get("mobile_phone") or ""],
        ["Email:", cust.get("email") or ""],
        ["Tax ID / EIN:", cust.get("tax_id") or ""],
        ["Account Number:", str(cust.get("account_number") or "")],
        ["Agreement Date:", issue_date],
    ]
    t = Table(applicant_data, colWidths=[2.2 * inch, 4.5 * inch])
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
        ("FONT", (1, 0), (1, -1), "Helvetica", 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), DARK_GRAY),
        ("LINEBELOW", (1, 0), (1, -1), 0.5, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))

    # ──── 2. TERMS OF AGREEMENT ────
    story.append(Paragraph("2. TERMS OF AGREEMENT", styles["CrSection"]))
    story.append(Paragraph(
        f"<b>Credit Limit:</b> ${credit_limit:,.2f}<br/>"
        f"<b>Payment Terms:</b> Net {net_days} days from invoice date<br/>"
        f"<b>Finance Charge:</b> {apr}% APR applied to balances outstanding beyond {net_days} days<br/>"
        f"<b>Monthly Finance Rate:</b> {apr / 12:.4f}%",
        styles["CrBody"]
    ))
    story.append(Paragraph(
        "All invoices are due and payable within the terms stated above. "
        f"Failure to pay within {net_days} days will result in finance charges applied to "
        f"the outstanding balance at the stated APR of {apr}%. "
        "Continued delinquency may result in suspension of credit privileges and "
        "referral to a collection agency.",
        styles["CrBody"]
    ))

    # ──── 3. CORE CHARGES ────
    story.append(Paragraph("3. CORE CHARGES", styles["CrSection"]))
    story.append(Paragraph(
        "Core charges will be applied to all exchange or remanufactured parts at the time of sale. "
        "Core credits will be issued upon return of an acceptable core within 30 days. "
        "Cores must be returned in rebuildable condition—unbroken, not disassembled, "
        "and free of excessive damage. Cores not returned within 30 days will be invoiced "
        "at the core charge rate and the charge becomes non-refundable.",
        styles["CrBody"]
    ))

    # ──── 4. RETURNS & WARRANTY ────
    story.append(Paragraph("4. RETURNS & WARRANTY", styles["CrSection"]))
    story.append(Paragraph(
        "Returns are accepted within 30 days of purchase with a valid invoice number. "
        "Returned parts must be in original, unused, and sellable condition. "
        "A restocking fee of 15% may apply to non-defective returns. "
        "Defective parts will be replaced or credited after inspection. "
        "Warranty claims are subject to manufacturer terms and inspection by our team. "
        "Labor claims are NOT covered under parts warranty.",
        styles["CrBody"]
    ))

    # ──── 5. COLLECTION & DEFAULT ────
    story.append(Paragraph("5. COLLECTION & DEFAULT", styles["CrSection"]))
    story.append(Paragraph(
        "In the event of default, the Applicant agrees to pay all costs of collection, "
        "including but not limited to attorney's fees, court costs, and collection agency fees. "
        "Default is defined as failure to make payment within 60 days past the original due date. "
        f"Accounts in default will be subject to the maximum legal interest rate or {apr}% APR, "
        "whichever is lower.",
        styles["CrBody"]
    ))

    # ──── 6. PERSONAL GUARANTEE (if applicable) ────
    story.append(Paragraph("6. PERSONAL GUARANTEE", styles["CrSection"]))
    if guarantor:
        story.append(Paragraph(
            f"The undersigned, <b>{guarantor}</b>, personally and unconditionally guarantees "
            "the payment of all amounts owed under this agreement. This guarantee is continuing "
            "and applies to all future transactions under this credit account.",
            styles["CrBody"]
        ))
    else:
        story.append(Paragraph(
            "The undersigned personally and unconditionally guarantees "
            "the payment of all amounts owed under this agreement. This guarantee is continuing "
            "and applies to all future transactions under this credit account.",
            styles["CrBody"]
        ))

    # ──── 7. AUTHORIZATION ────
    story.append(Paragraph("7. AUTHORIZATION & CONSENT", styles["CrSection"]))
    story.append(Paragraph(
        f"The Applicant authorizes {co['name']} to verify any information provided, "
        "including conducting credit checks with third-party agencies. "
        "The Applicant agrees that all purchases made on this account are subject "
        "to these terms and conditions. This agreement remains in effect until "
        "terminated in writing by either party.",
        styles["CrBody"]
    ))

    # ──── 8. AGREEMENT ACCEPTANCE ────
    story.append(Spacer(1, 16))
    story.append(Paragraph("8. AGREEMENT ACCEPTANCE", styles["CrSection"]))
    story.append(Paragraph(
        "By signing below, the Applicant acknowledges that they have read, understood, "
        "and agree to all terms and conditions stated in this Credit Terms Agreement.",
        styles["CrBody"]
    ))
    story.append(Spacer(1, 20))

    sig_data = [
        ["Applicant Signature: ___________________________", f"Date: _______________"],
        [f"Printed Name: {cname}", ""],
        ["", ""],
        [f"{co['name']} Authorized Rep: ___________________________", "Date: _______________"],
    ]
    sig = Table(sig_data, colWidths=[4.2 * inch, 2.5 * inch])
    sig.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 10),
        ("TEXTCOLOR", (0, 0), (-1, -1), DARK_GRAY),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(sig)

    # ──── 9. INTERNAL USE ONLY ────
    story.append(Spacer(1, 24))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cccccc")))
    story.append(Spacer(1, 4))
    story.append(Paragraph("FOR INTERNAL USE ONLY", styles["CrSmall"]))
    int_data = [
        ["Approved By: _________________", "Date: ___________",
         f"Credit Limit: ${credit_limit:,.2f}"],
        [f"Terms: Net {net_days}", f"APR: {apr}%", "Account #: ________"],
    ]
    it = Table(int_data, colWidths=[2.4 * inch, 2.0 * inch, 2.4 * inch])
    it.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#888888")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(it)

    # Build
    doc.build(story)
    return output_path
