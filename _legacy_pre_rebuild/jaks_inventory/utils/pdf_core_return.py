"""PDF packing slip + freight label generators for vendor core returns.

Phase G of the core-handling revitalization. Two outputs share the same
module so they can pull common helpers (vendor/company lookups, address
formatting, barcode rendering) without duplicating code.

Public API:

* ``generate_core_packing_slip(vendor_return_id, *, override_doc_number)``
* ``generate_core_freight_label(vendor_return_id, *, weight_lbs, box_count,
                                declared_value, carrier_hint)``

Both return the absolute path to the written PDF. Both are entirely
self-contained — no carrier API integration. The freight label is a
generic 4x6 layout designed for a hand-applied carrier sticker; the
tracking number is entered manually by the user post-ship.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.graphics.barcode import code128

from db import get_setting
from db.inventory import get_vendor, get_vendor_return, get_vendor_return_lines
from db.core_handling import (
    mark_packing_slip_generated,
    mark_freight_label_generated,
    next_packing_slip_number,
)

from .doc_theme import (
    OLIVE, DARK_GRAY, GRAY_BG, WHITE,
    SIZE_DOC_TITLE, SIZE_DOC_TITLE_LEADING,
    build_footer as _shared_footer,
    format_friendly_date,
    make_simple_doc,
)


_BASE = Path(__file__).parent.parent
PACKING_SLIP_DIR = _BASE / "output" / "core_packing_slips"


# ---------------------------------------------------------------------------
# Helpers shared by both PDF builders.
# ---------------------------------------------------------------------------

def _company() -> dict:
    return {
        "name":    get_setting("company_name",    "JAK'S Diesel"),
        "address": get_setting("company_address", ""),
        "city":    get_setting("company_city",    "Henderson"),
        "state":   get_setting("company_state",   "NV"),
        "zip":     get_setting("company_zip",     ""),
        "phone":   get_setting("company_phone",   ""),
        "email":   get_setting("company_email",   ""),
        "logo_path": _BASE / get_setting("company_logo_path", "assets/logo.png"),
    }


def _esc(text) -> str:
    s = str(text or "")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _vendor_returns_address(v: dict) -> list[str]:
    """Build a list of address lines from the vendor's RETURNS address
    columns, falling back to the regular vendor address if those are
    empty. Each line is already escaped for inclusion in a Paragraph.
    """
    name = (v.get("returns_contact_name") or v.get("contact_name") or v.get("name") or "").strip()
    line1 = (v.get("returns_address_line1") or "").strip()
    line2 = (v.get("returns_address_line2") or "").strip()
    city  = (v.get("returns_address_city")  or "").strip()
    state = (v.get("returns_address_state") or "").strip()
    zipc  = (v.get("returns_address_zip")   or "").strip()
    phone = (v.get("returns_contact_phone") or v.get("contact_phone") or "").strip()

    has_structured = any((line1, line2, city, state, zipc))
    lines: list[str] = []
    if v.get("name"):
        lines.append(str(v["name"]))
    if name and name != v.get("name"):
        lines.append(f"Attn: {name}")
    if has_structured:
        if line1: lines.append(line1)
        if line2: lines.append(line2)
        city_state = ", ".join([x for x in (city, state) if x]).strip(", ")
        if zipc:
            city_state = f"{city_state} {zipc}".strip()
        if city_state:
            lines.append(city_state)
    else:
        # Fall back to the legacy unstructured ``address`` blob.
        raw = (v.get("address") or "").strip()
        for ln in raw.replace("\r", "").split("\n"):
            ln = ln.strip()
            if ln:
                lines.append(ln)
    if phone:
        lines.append(phone)
    return lines


def _company_address_lines() -> list[str]:
    c = _company()
    lines = [c["name"]]
    if c.get("address"):
        lines.append(str(c["address"]))
    city_state = ", ".join([x for x in (c.get("city"), c.get("state")) if x]).strip(", ")
    if c.get("zip"):
        city_state = f"{city_state} {c['zip']}".strip()
    if city_state:
        lines.append(city_state)
    if c.get("phone"):
        lines.append(str(c["phone"]))
    return lines


def _ensure_dir() -> None:
    PACKING_SLIP_DIR.mkdir(parents=True, exist_ok=True)


def _load_return(vendor_return_id: int) -> tuple[dict, dict, list[dict]]:
    """Fetch the vendor_return row, its vendor, and its lines. Raises
    ``ValueError`` with a clear message when anything is missing.
    """
    vr = get_vendor_return(vendor_return_id)
    if not vr:
        raise ValueError(f"Vendor return #{vendor_return_id} not found.")
    vendor = get_vendor(int(vr.get("vendor_id") or 0))
    if not vendor:
        raise ValueError(
            f"Vendor #{vr.get('vendor_id')} for return #{vendor_return_id} not found."
        )
    lines = get_vendor_return_lines(vendor_return_id) or []
    return dict(vr), dict(vendor), [dict(ln) for ln in lines]


# ---------------------------------------------------------------------------
# Packing slip — 8.5x11, line-itemized, branded.
# ---------------------------------------------------------------------------

def generate_core_packing_slip(
    vendor_return_id: int,
    *,
    override_doc_number: Optional[str] = None,
) -> str:
    """Render a packing slip for a vendor return. The slip's document
    number (``CRP-XXXX``) is allocated from app_settings the first time
    a slip is generated for this return; subsequent regenerations reuse
    the same number but bump ``packing_slip_version``.
    """
    vr, vendor, lines = _load_return(vendor_return_id)
    _ensure_dir()

    # Reuse the existing doc number if a slip was already generated.
    doc_number = (
        override_doc_number
        or (vr.get("packing_slip_doc_number") or "").strip()
        or next_packing_slip_number()
    )
    version = int(vr.get("packing_slip_version") or 0) + 1

    safe = doc_number.replace("/", "-").replace("\\", "-")
    out_path = PACKING_SLIP_DIR / f"{safe}_v{version}.pdf"

    doc = make_simple_doc(str(out_path))
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("CSTitle", parent=styles["Heading1"],
                              fontSize=SIZE_DOC_TITLE,
                              leading=SIZE_DOC_TITLE_LEADING,
                              textColor=OLIVE, alignment=TA_RIGHT,
                              fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("CSSub",   parent=styles["Normal"], fontSize=10, textColor=DARK_GRAY))
    styles.add(ParagraphStyle("CSLabel", parent=styles["Normal"], fontSize=9,  textColor=OLIVE,
                              fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle("CSValue", parent=styles["Normal"], fontSize=10, textColor=DARK_GRAY))
    styles.add(ParagraphStyle("CSSmall", parent=styles["Normal"], fontSize=8,
                              textColor=colors.HexColor("#666666")))
    styles.add(ParagraphStyle("CSFooter", parent=styles["Normal"], fontSize=8,
                              textColor=colors.HexColor("#999999"), alignment=TA_CENTER))
    styles.add(ParagraphStyle("CSHdr", parent=styles["Normal"], fontSize=9,
                              fontName="Helvetica-Bold", textColor=WHITE, leading=12))

    story: list = []
    story.append(_packing_header(vendor, doc_number, vr, version, styles))
    story.append(Spacer(1, 0.08 * inch))
    story.append(HRFlowable(width="100%", thickness=1.5, color=OLIVE,
                            spaceAfter=0.06 * inch, spaceBefore=0.02 * inch))
    story.append(_packing_parties(vendor, styles))
    story.append(Spacer(1, 0.2 * inch))
    story.append(_packing_lines(lines, styles))
    story.append(Spacer(1, 0.2 * inch))

    # Barcode of doc number so receiving can scan it.
    bc = code128.Code128(doc_number, barHeight=0.55 * inch, barWidth=0.013 * inch)
    bc_tbl = Table([[bc], [Paragraph(_esc(doc_number), styles["CSSmall"])]],
                   colWidths=[3.0 * inch])
    bc_tbl.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(bc_tbl)

    notes = (vr.get("notes") or "").strip()
    if notes:
        story.append(Spacer(1, 0.15 * inch))
        story.append(Paragraph("Notes", styles["CSLabel"]))
        story.append(Paragraph(_esc(notes), styles["CSValue"]))

    story.append(Spacer(1, 0.25 * inch))
    story.append(HRFlowable(width="100%", thickness=0.5,
                            color=colors.HexColor("#cccccc"),
                            spaceAfter=0.1 * inch, spaceBefore=0.1 * inch))
    story.extend(_shared_footer({"footer": styles["CSFooter"]}))

    doc.build(story)

    mark_packing_slip_generated(
        vendor_return_id,
        doc_number=doc_number,
        path=str(out_path),
    )
    return os.path.abspath(str(out_path))


def _packing_header(vendor: dict, doc_number: str, vr: dict, version: int, styles) -> Table:
    company = _company()
    logo_path = company["logo_path"]
    if Path(logo_path).exists():
        logo = Image(str(logo_path), width=1.8 * inch, height=1.25 * inch, kind="proportional")
        logo.hAlign = "LEFT"
    else:
        logo = Paragraph(f"<b>{_esc(company['name'])}</b>", styles["CSTitle"])

    left_lines = [Paragraph(f"<b>{_esc(company['name'])}</b>", styles["CSValue"])]
    for ln in _company_address_lines()[1:]:
        left_lines.append(Paragraph(_esc(ln), styles["CSSmall"]))
    left_table = Table([[logo], *[[ln] for ln in left_lines]], colWidths=[2.8 * inch])
    left_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    title_text = "CORE RETURN PACKING SLIP" if version <= 1 else f"CORE RETURN PACKING SLIP (REPRINT v{version})"
    rga = vr.get("rga_number") or ""
    right = Table([
        [Paragraph(title_text, styles["CSTitle"])],
        [Paragraph(f"<b>{_esc(doc_number)}</b>", styles["CSSub"])],
        [Paragraph(f"<b>RGA #:</b> {_esc(rga)}", styles["CSSub"])],
        [Paragraph(f"<b>Date:</b> {_esc(format_friendly_date(vr.get('return_date')))}", styles["CSSub"])],
    ], colWidths=[4.2 * inch])
    right.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))

    tbl = Table([[left_table, right]], colWidths=[3.0 * inch, 4.0 * inch])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return tbl


def _packing_parties(vendor: dict, styles) -> Table:
    ship_lines = _vendor_returns_address(vendor)
    from_lines = _company_address_lines()

    ship_block = [[Paragraph("SHIP TO (Vendor Returns)", styles["CSLabel"])]]
    for ln in ship_lines:
        ship_block.append([Paragraph(_esc(ln), styles["CSValue"])])

    from_block = [[Paragraph("FROM", styles["CSLabel"])]]
    for ln in from_lines:
        from_block.append([Paragraph(_esc(ln), styles["CSValue"])])

    left = Table(ship_block, colWidths=[3.4 * inch])
    right = Table(from_block, colWidths=[3.4 * inch])
    for tbl in (left, right):
        tbl.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))

    outer = Table([[left, right]], colWidths=[3.5 * inch, 3.5 * inch])
    outer.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return outer


def _packing_lines(lines: list[dict], styles) -> Table:
    desc_style = ParagraphStyle("LineDesc", parent=styles["CSValue"],
                                fontSize=9, leading=12, wordWrap="CJK")
    rows = [[
        Paragraph("SKU",        styles["CSHdr"]),
        Paragraph("Vendor Part",styles["CSHdr"]),
        Paragraph("Description",styles["CSHdr"]),
        Paragraph("Qty",        styles["CSHdr"]),
        Paragraph("Deposit",    styles["CSHdr"]),
    ]]
    total_deposit = 0.0
    for ln in lines:
        qty = int(ln.get("quantity") or 1)
        deposit = float(ln.get("unit_cost") or 0)
        line_tot = float(ln.get("line_total") or (qty * deposit))
        total_deposit += line_tot
        sku   = ln.get("sku") or ln.get("product_sku") or ""
        vpart = ln.get("supplier_part_number") or ln.get("vendor_part") or ""
        desc  = ln.get("title") or ln.get("description") or ln.get("product_title") or ""
        rows.append([
            Paragraph(_esc(sku),   desc_style),
            Paragraph(_esc(vpart), desc_style),
            Paragraph(_esc(desc),  desc_style),
            str(qty),
            f"${line_tot:,.2f}",
        ])
    rows.append([
        "", "", Paragraph("<b>Total Refundable Deposit</b>", desc_style),
        "", f"${total_deposit:,.2f}",
    ])

    tbl = Table(rows, colWidths=[1.2 * inch, 1.2 * inch, 3.0 * inch, 0.6 * inch, 1.0 * inch])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), OLIVE),
        ("TEXTCOLOR",  (0, 0), (-1, 0), WHITE),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, 0), 9),
        ("ALIGN",      (3, 1), (4, -1), "RIGHT"),
        ("VALIGN",     (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2),
         [WHITE, GRAY_BG]),
        ("LINEBELOW",  (0, -2), (-1, -2), 0.75, colors.HexColor("#bbbbbb")),
        ("FONTNAME",   (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("LEFTPADDING",  (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


# ---------------------------------------------------------------------------
# Freight label — 4x6 generic shipping label, NO carrier API.
# ---------------------------------------------------------------------------
# A "shippable" page-size that prints well on a 4x6 thermal label or on
# 8.5x11 paper trimmed to size. The carrier sticker is hand-applied in
# the empty top-right region after generation.

_LABEL_PAGE = (4.0 * inch, 6.0 * inch)


def generate_core_freight_label(
    vendor_return_id: int,
    *,
    weight_lbs: Optional[float] = None,
    box_count: int = 1,
    declared_value: Optional[float] = None,
    carrier_hint: Optional[str] = None,
) -> str:
    """Render a generic 4x6 freight label PDF. No carrier API — the
    actual carrier sticker is applied by hand to the blank top-right
    region after print. ``carrier_hint`` is just stored as metadata so
    the UI can show "label intended for UPS" etc.; the printed label is
    carrier-neutral.
    """
    vr, vendor, lines = _load_return(vendor_return_id)
    _ensure_dir()

    doc_number = (vr.get("packing_slip_doc_number") or "").strip()
    if not doc_number:
        # The label always references the slip — generate one first
        # automatically so callers can't render a label without a slip.
        slip_path = generate_core_packing_slip(vendor_return_id)
        # Reload to pick up the freshly stamped doc_number.
        vr, vendor, lines = _load_return(vendor_return_id)
        doc_number = (vr.get("packing_slip_doc_number") or "").strip()
        _ = slip_path  # noqa: F841 — generated as a side effect

    safe = doc_number.replace("/", "-").replace("\\", "-")
    out_path = PACKING_SLIP_DIR / f"{safe}_label.pdf"

    if declared_value is None:
        try:
            declared_value = float(sum(
                float(ln.get("line_total") or 0)
                or float(ln.get("unit_cost") or 0) * int(ln.get("quantity") or 1)
                for ln in lines
            ))
        except Exception:
            declared_value = 0.0

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=_LABEL_PAGE,
        leftMargin=0.15 * inch, rightMargin=0.15 * inch,
        topMargin=0.15 * inch, bottomMargin=0.15 * inch,
        title=f"Core Return Label {doc_number}",
    )

    styles = getSampleStyleSheet()
    s_big   = ParagraphStyle("LblBig",   parent=styles["Normal"],
                             fontSize=11, leading=13, fontName="Helvetica-Bold")
    s_label = ParagraphStyle("LblLbl",   parent=styles["Normal"],
                             fontSize=7, leading=8, textColor=colors.HexColor("#666666"),
                             fontName="Helvetica-Bold")
    s_val   = ParagraphStyle("LblVal",   parent=styles["Normal"],
                             fontSize=9, leading=11)
    s_huge  = ParagraphStyle("LblHuge",  parent=styles["Normal"],
                             fontSize=14, leading=16, fontName="Helvetica-Bold",
                             alignment=TA_CENTER)
    s_small = ParagraphStyle("LblSm",    parent=styles["Normal"],
                             fontSize=7, leading=8, textColor=colors.HexColor("#666666"))

    story: list = []

    # Top stripe: carrier-sticker placeholder.
    top_left = Paragraph(
        "CARRIER LABEL<br/><font size=6>(apply sticker here)</font>",
        s_label,
    )
    top_right_text = f"BOX {1 if box_count <= 0 else 1} OF {max(box_count, 1)}"
    top = Table([[top_left, Paragraph(top_right_text, s_huge)]],
                colWidths=[2.2 * inch, 1.4 * inch], rowHeights=[1.0 * inch])
    top.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.5, colors.HexColor("#888888")),
        ("LINEAFTER", (0, 0), (0, 0), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",  (1, 0), (1, 0), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(top)
    story.append(Spacer(1, 0.08 * inch))

    # FROM block
    from_lines = _company_address_lines()
    from_para = "<br/>".join(_esc(ln) for ln in from_lines)
    story.append(Paragraph("FROM", s_label))
    story.append(Paragraph(from_para, s_val))
    story.append(Spacer(1, 0.08 * inch))

    # TO block
    to_lines = _vendor_returns_address(vendor)
    to_para = "<br/>".join(f"<b>{_esc(ln)}</b>" for ln in to_lines)
    story.append(Paragraph("SHIP TO (Vendor Returns)", s_label))
    story.append(Paragraph(to_para, s_big))
    story.append(Spacer(1, 0.1 * inch))

    # Shipment metadata row
    weight_str = f"{float(weight_lbs):,.1f} lb" if weight_lbs else "____ lb"
    value_str  = f"${declared_value:,.2f}" if declared_value else "$____"
    meta = Table([
        [Paragraph("WEIGHT", s_label),  Paragraph("BOXES", s_label),
         Paragraph("DECLARED VALUE", s_label)],
        [Paragraph(weight_str, s_big),
         Paragraph(str(max(box_count, 1)), s_big),
         Paragraph(value_str, s_big)],
    ], colWidths=[1.2 * inch, 0.9 * inch, 1.5 * inch])
    meta.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",  (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(meta)
    story.append(Spacer(1, 0.12 * inch))

    # Big barcode of the doc number.
    bc = code128.Code128(doc_number, barHeight=0.65 * inch, barWidth=0.018 * inch)
    bc_tbl = Table([[bc], [Paragraph(_esc(doc_number), s_huge)]],
                   colWidths=[3.6 * inch])
    bc_tbl.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(bc_tbl)
    story.append(Spacer(1, 0.05 * inch))

    rga = vr.get("rga_number") or ""
    if carrier_hint:
        story.append(Paragraph(
            f"Intended carrier: <b>{_esc(carrier_hint)}</b>", s_small,
        ))
    story.append(Paragraph(
        f"RGA #: <b>{_esc(rga)}</b> &nbsp;&nbsp;Vendor: <b>{_esc(vendor.get('name'))}</b>",
        s_small,
    ))
    story.append(Paragraph(
        "Generic shipping label. Apply carrier sticker in the labeled "
        "region above; record tracking number in Inventory.",
        s_small,
    ))

    doc.build(story)

    mark_freight_label_generated(
        vendor_return_id,
        path=str(out_path),
        carrier=carrier_hint,
        cost=None,
        tracking=None,
    )
    return os.path.abspath(str(out_path))
