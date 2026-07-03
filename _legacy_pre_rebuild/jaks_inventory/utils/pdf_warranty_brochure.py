"""PDF Warranty Tier Brochure generator for JAK's Diesel.

A standalone one-pager that lists JAK's standard warranty tiers, what
each one covers, the claim path, and key exclusions. Hands to a
customer alongside a quote so they can pick the option they want.

The tier ladder is sourced from :mod:`engine.warranty_tiers`.
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
from engine.warranty_tiers import DEFAULT_TIERS

OLIVE = colors.HexColor("#3d4a38")
OLIVE_LIGHT = colors.HexColor("#6B8E23")
GRAY_BG = colors.HexColor("#f5f5f5")
WHITE = colors.white
DARK_GRAY = colors.HexColor("#333333")
MUTED = colors.HexColor("#666666")

_BASE = Path(__file__).parent.parent
OUTPUT_DIR = _BASE / "output" / "warranty_brochure"


def _company() -> dict:
    return {
        "name": get_setting("company_name", "JAK'S Diesel"),
        "phone": get_setting("company_phone", ""),
        "email": get_setting("company_email", ""),
        "logo_path": _BASE / get_setting("company_logo_path", "assets/logo.png"),
    }


def _styles():
    s = getSampleStyleSheet()
    if "WBTitle" not in s.byName:
        s.add(ParagraphStyle(
            "WBTitle", parent=s["Heading1"],
            fontSize=22, textColor=OLIVE, alignment=TA_RIGHT,
            fontName="Helvetica-Bold", leading=26,
        ))
        s.add(ParagraphStyle(
            "WBSub", parent=s["Normal"],
            fontSize=10, textColor=DARK_GRAY, alignment=TA_RIGHT,
        ))
        s.add(ParagraphStyle(
            "WBBody", parent=s["Normal"],
            fontSize=9, textColor=DARK_GRAY, leading=12,
        ))
        s.add(ParagraphStyle(
            "WBSection", parent=s["Normal"],
            fontSize=11, textColor=OLIVE, fontName="Helvetica-Bold",
            spaceBefore=4, spaceAfter=2,
        ))
        s.add(ParagraphStyle(
            "WBFoot", parent=s["Normal"],
            fontSize=7.5, textColor=MUTED, alignment=TA_CENTER,
        ))
        s.add(ParagraphStyle(
            "WBTier", parent=s["Normal"],
            fontSize=10, fontName="Helvetica-Bold", textColor=OLIVE,
        ))
        s.add(ParagraphStyle(
            "WBTierBlurb", parent=s["Normal"],
            fontSize=9, textColor=DARK_GRAY, leading=11,
        ))
    return s


def _header(styles) -> Table:
    company = _company()
    if company["logo_path"].exists():
        logo = Image(
            str(company["logo_path"]), width=2.2 * inch, height=1.65 * inch,
            kind="proportional",
        )
        logo.hAlign = "LEFT"
    else:
        logo = Paragraph(f"<b>{company['name']}</b>", styles["WBTitle"])

    right = [
        Paragraph("WARRANTY", styles["WBTitle"]),
        Paragraph("OPTIONS", styles["WBTitle"]),
        Spacer(1, 4),
        Paragraph("Choose the coverage that fits", styles["WBSub"]),
        Paragraph(datetime.now().strftime("%B %Y"), styles["WBSub"]),
    ]
    t = Table([[logo, right]], colWidths=[3.5 * inch, 3.9 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _tier_table(styles) -> Table:
    header = ["Tier", "Term", "Coverage", "Mileage", "What you get"]
    data = [[Paragraph(f"<b>{h}</b>", styles["WBBody"]) for h in header]]
    for tier in DEFAULT_TIERS:
        coverage = "Parts & Labor" if tier.coverage_type == "parts_labor" else "Parts Only"
        mileage = (
            f"{tier.mileage_limit:,} mi"
            if tier.mileage_limit and tier.mileage_limit > 0
            else "Unlimited"
        )
        data.append([
            Paragraph(tier.label, styles["WBTier"]),
            Paragraph(f"{tier.months} mo", styles["WBBody"]),
            Paragraph(coverage, styles["WBBody"]),
            Paragraph(mileage, styles["WBBody"]),
            Paragraph(tier.blurb, styles["WBTierBlurb"]),
        ])
    t = Table(
        data,
        colWidths=[
            1.7 * inch, 0.6 * inch, 1.0 * inch, 0.9 * inch, 3.2 * inch,
        ],
        repeatRows=1,
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), OLIVE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, 0), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, GRAY_BG]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def _footer_block(styles) -> list:
    company = _company()
    contact = []
    if company["phone"]:
        contact.append(company["phone"])
    if company["email"]:
        contact.append(company["email"])
    contact_str = " &middot; ".join(contact)

    return [
        Paragraph("Pricing", styles["WBSection"]),
        Paragraph(
            "Pricing is calculated as a percentage of the covered part&#39;s "
            "selling price, scaled by the term you choose. Your sales rep "
            "will quote the exact dollar amount on the quote line. Standard "
            "Parts-Only coverage is included with every qualifying purchase "
            "at no charge.",
            styles["WBBody"],
        ),
        Spacer(1, 6),
        Paragraph("How claims work", styles["WBSection"]),
        Paragraph(
            "Contact JAK&#39;s within 30 days of a failure. We&#39;ll issue "
            "an RMA, inspect the part, and either repair, replace, or "
            "credit per the terms of your certificate. The certificate "
            "issued at the time of sale governs the exact remedy.",
            styles["WBBody"],
        ),
        Spacer(1, 6),
        Paragraph("Key exclusions", styles["WBSection"]),
        Paragraph(
            "No warranty covers abuse, misuse, neglect, accident, improper "
            "installation, unauthorized modification, lack of timely "
            "maintenance, or damage from extreme operating conditions. "
            "Third-party (resold) components carry the original "
            "manufacturer&#39;s warranty only.",
            styles["WBBody"],
        ),
        Spacer(1, 8),
        HRFlowable(
            width="100%", thickness=0.5, color=colors.HexColor("#cccccc"),
            spaceAfter=4, spaceBefore=4,
        ),
        Paragraph(
            f"{company['name']}"
            + (f" &middot; {contact_str}" if contact_str else "")
            + " &middot; This brochure is informational; the certificate "
            "issued with your invoice governs.",
            styles["WBFoot"],
        ),
    ]


def _ensure_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def generate_warranty_brochure(output_path: str | None = None) -> str:
    """Render the JAK's Warranty Options brochure. Returns absolute path."""
    if not output_path:
        _ensure_dir()
        output_path = str(OUTPUT_DIR / "JAKS_Warranty_Options.pdf")

    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )
    styles = _styles()
    flow: list = []
    flow.append(_header(styles))
    flow.append(Spacer(1, 0.12 * inch))
    flow.append(HRFlowable(
        width="100%", thickness=2, color=OLIVE,
        spaceAfter=0.15 * inch, spaceBefore=0.05 * inch,
    ))
    flow.append(Paragraph(
        "JAK&#39;s offers four warranty tiers. Standard Parts-Only "
        "coverage is included with every qualifying purchase. Extended "
        "tiers are optional add-ons that can be selected when the quote "
        "is built or before the invoice is finalized. The certificate "
        "issued at the time of sale states the exact terms for your part.",
        styles["WBBody"],
    ))
    flow.append(Spacer(1, 0.15 * inch))
    flow.append(_tier_table(styles))
    flow.append(Spacer(1, 0.15 * inch))
    flow.extend(_footer_block(styles))

    doc.build(flow)
    return os.path.abspath(output_path)
