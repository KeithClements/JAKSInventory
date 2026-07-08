"""Unified document theme + rendering helpers.

Single source of truth for typography, colors, spacing, and reusable
section builders shared by every PDF the inventory app generates
(Quotes, Sales Orders, Invoices, Purchase Orders, Pick Tickets, Core
Return Slips, Statements).

The goal is that any printable artifact JAK's emits looks like it came
from the same ERP suite: same olive accent, same Helvetica hierarchy,
same table header styling, same totals ladder, same footer.

Renderers may still draw their own bespoke sections (warranty
certificates, package-summary cards, signature blocks, etc.), but
header / customer block / line-items header / totals ladder / footer
should call into this module rather than re-implementing them.
"""
from __future__ import annotations

import re
from datetime import datetime, date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Image, Paragraph, Spacer, Table, TableStyle, SimpleDocTemplate
from reportlab.lib.pagesizes import letter

from db import get_setting

# ── COLORS ──────────────────────────────────────────────────────
OLIVE = colors.HexColor("#3d4a38")          # Primary brand
OLIVE_LIGHT = colors.HexColor("#6B8E23")
GRAY_BG = colors.HexColor("#f5f5f5")        # Alternating row shading
GRID_GRAY = colors.HexColor("#dddddd")      # Vertical column rule
FOOTER_GRAY = colors.HexColor("#999999")    # Footer text
MUTED = colors.HexColor("#666666")          # Secondary text
DARK_GRAY = colors.HexColor("#333333")      # Body
BORDER = colors.HexColor("#cccccc")         # Hairlines (footer divider)
WHITE = colors.white
BLACK = colors.black
RED = colors.HexColor("#c62828")
GREEN = colors.HexColor("#2e7d32")
BLUE = colors.HexColor("#1565c0")

# ── TYPOGRAPHY ──────────────────────────────────────────────────
FONT_BASE = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_ITALIC = "Helvetica-Oblique"

# Font size hierarchy. Used everywhere.
SIZE_TITLE = 28            # Top-right doc title ("QUOTE", "INVOICE")
SIZE_TITLE_BIG = 32        # Large variant for highest-priority docs
SIZE_SUBTITLE = 11         # Doc number under title
SIZE_HEADER_CELL = 9       # Table header text
SIZE_BODY = 10             # Default body text
SIZE_LABEL = 9             # Bold olive section labels ("BILL TO")
SIZE_SMALL = 8             # Footer, addresses, fine print
SIZE_TINY = 7              # Sub-detail rows under line items

# ── SPACING / MARGINS (inches) ──────────────────────────────────
# Canonical page margins for every printed doc. Matches the quote
# layout the user has approved, so all artifacts feel consistent.
PAGE_MARGIN_L = 0.5
PAGE_MARGIN_R = 0.5
PAGE_MARGIN_T = 0.25
PAGE_MARGIN_B = 0.3

# Canonical doc-title size. Quote/SO/Invoice/PO/Pick/Core all use this
# so the right-side header band looks identical across the suite.
SIZE_DOC_TITLE = 22
SIZE_DOC_TITLE_LEADING = 24

# Vertical rhythm — keep tight. Documents felt loose before.
GAP_AFTER_HEADER = 0.08
GAP_AFTER_DIVIDER = 0.04
GAP_AFTER_BILLTO = 0.12
GAP_AFTER_TABLE = 0.10
GAP_AFTER_TOTALS = 0.12
GAP_BEFORE_FOOTER = 0.10

# Logo footprint — slightly larger than legacy, gives more brand presence.
LOGO_W = 2.8 * inch
LOGO_H = 1.4 * inch


# ── PARAGRAPH STYLES ────────────────────────────────────────────
def make_doc_styles(prefix: str = "Doc") -> dict:
    """Return a fresh stylesheet dict keyed by short names.

    The ``prefix`` keeps each renderer's styles namespaced inside the
    underlying ReportLab StyleSheet1 (which is shared across builds),
    while the returned dict gives callers a stable short-name lookup
    (``styles["label"]``, ``styles["value"]``…).
    """
    ss = getSampleStyleSheet()

    def add(name: str, **kw) -> ParagraphStyle:
        full = f"{prefix}_{name}"
        if full in ss.byName:
            return ss[full]
        ps = ParagraphStyle(full, parent=ss["Normal"], **kw)
        ss.add(ps)
        return ps

    return {
        "title": add("title", fontSize=SIZE_TITLE_BIG, textColor=OLIVE,
                     fontName=FONT_BOLD, alignment=TA_RIGHT, leading=SIZE_TITLE_BIG + 2,
                     spaceAfter=2),
        "subtitle": add("subtitle", fontSize=SIZE_SUBTITLE, textColor=DARK_GRAY,
                        alignment=TA_RIGHT),
        "label": add("label", fontSize=SIZE_LABEL, textColor=OLIVE,
                     fontName=FONT_BOLD),
        "value": add("value", fontSize=SIZE_BODY, textColor=DARK_GRAY),
        "small": add("small", fontSize=SIZE_SMALL, textColor=MUTED),
        "right": add("right", fontSize=SIZE_BODY, textColor=DARK_GRAY,
                     alignment=TA_RIGHT),
        "footer": add("footer", fontSize=SIZE_SMALL, textColor=FOOTER_GRAY,
                      alignment=TA_CENTER, leading=SIZE_SMALL + 2),
        "notes": add("notes", fontSize=SIZE_LABEL, textColor=DARK_GRAY,
                     spaceAfter=4),
    }


# ── COMPANY HELPERS ─────────────────────────────────────────────
def company_info() -> dict:
    """Resolve company settings + logo path (relative to package root)."""
    base = Path(__file__).parent.parent
    return {
        "name": get_setting("company_name", "JAK'S Diesel"),
        "address": get_setting("company_address", ""),
        "city": get_setting("company_city", "Henderson"),
        "state": get_setting("company_state", "NV"),
        "zip": get_setting("company_zip", ""),
        "phone": get_setting("company_phone", ""),
        "email": get_setting("company_email", ""),
        "website": get_setting("company_website", ""),
        "logo_path": base / get_setting("company_logo_path", "assets/logo.png"),
    }


def format_company_address(info: dict) -> str:
    parts = []
    if info.get("address"):
        parts.append(info["address"])
    city_state = ", ".join(filter(None, [info.get("city"), info.get("state")]))
    if city_state:
        if info.get("zip"):
            city_state += " " + info["zip"]
        parts.append(city_state)
    return ", ".join(parts) if parts else ""


def esc(text: str) -> str:
    """Escape XML special characters for ReportLab Paragraph."""
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ── CUSTOMER-FRIENDLY FORMATTERS ────────────────────────────────
_ETA_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def format_friendly_date(value) -> str:
    """Render any date-ish value as ``May 21, 2026``.

    Accepts ``date``/``datetime`` objects and ISO-prefix strings.
    Falls back to the original string when parsing fails.
    """
    if not value:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%B %-d, %Y") if hasattr(value, "strftime") else str(value)
    s = str(value)[:10]
    try:
        d = datetime.strptime(s, "%Y-%m-%d").date()
        # Windows %#d, POSIX %-d. Use explicit format that works on both.
        return f"{d.strftime('%B')} {d.day}, {d.year}"
    except Exception:
        return s


def format_eta_friendly(eta_text: str) -> str:
    """Convert internal ETA strings into customer-facing wording.

    Examples
    --------
    ``"ETA ~ 2026-05-21 (6-day lead)"`` →
        ``"Expected availability: May 21, 2026"``
    ``"2026-05-21"`` →
        ``"Expected availability: May 21, 2026"``
    ``"In stock"`` →
        ``"In stock"`` (passed through unchanged)
    """
    if not eta_text:
        return ""
    s = str(eta_text).strip()
    if not s:
        return ""
    # Strip leading "ETA ~ " / "ETA:" / bracketed wrapper.
    s = re.sub(r"^\[?ETA[:~\s]*", "", s, flags=re.I).rstrip("]")
    # Drop trailing "(N-day lead)" qualifier — not useful to customer.
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
    if not s:
        return ""
    # If string starts with a YYYY-MM-DD, humanize it.
    m = _ETA_DATE_RE.match(s)
    if m:
        return f"Expected availability: {format_friendly_date(m.group(1))}"
    # Anything else (e.g. "In stock", "2–3 weeks"): just label it.
    return f"Expected availability: {s}"


# ── HEADER ──────────────────────────────────────────────────────
def build_header(
    *,
    doc_title: str,
    doc_number: str,
    styles: dict,
    logo_size_pct: float | None = None,
    extra_right_rows: list | None = None,
) -> Table:
    """Standard top-of-page header.

    Left column: logo + company contact details.
    Right column: large doc title (olive), doc number, optional extra rows.
    """
    info = company_info()
    pct = 1.0 if logo_size_pct is None else max(0.5, min(1.5, logo_size_pct / 100))
    logo_w = LOGO_W * pct
    logo_h = LOGO_H * pct

    if Path(info["logo_path"]).exists():
        logo = Image(str(info["logo_path"]), width=logo_w, height=logo_h,
                     kind="proportional")
        logo.hAlign = "LEFT"
    else:
        logo = Paragraph(f"<b>{esc(info['name'])}</b>", styles["title"])

    company_lines = []
    show_addr = get_setting("quote_show_company_address", "1") == "1"
    addr = format_company_address(info)
    if addr and show_addr:
        company_lines.append(Paragraph(esc(addr), styles["small"]))
    if info["phone"]:
        company_lines.append(Paragraph(esc(info["phone"]), styles["small"]))
    if info["email"]:
        company_lines.append(Paragraph(esc(info["email"]), styles["small"]))
    if info.get("website"):
        company_lines.append(Paragraph(esc(info["website"]), styles["small"]))

    left = Table([[logo], *[[p] for p in company_lines]],
                 colWidths=[3.6 * inch])
    left.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))

    right_rows = [
        [Paragraph(doc_title, styles["title"])],
        [Paragraph(f"<b>{esc(doc_number)}</b>", styles["subtitle"])],
    ]
    if extra_right_rows:
        for r in extra_right_rows:
            if isinstance(r, str):
                right_rows.append([Paragraph(r, styles["subtitle"])])
            else:
                right_rows.append([r])

    right = Table(right_rows, colWidths=[2.8 * inch])
    right.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
    ]))

    header = Table([[left, right]], colWidths=[4.6 * inch, 2.8 * inch])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return header


def header_divider(space_after: float = GAP_AFTER_DIVIDER) -> HRFlowable:
    """The 2pt olive rule that lives under every header."""
    return HRFlowable(
        width="100%", thickness=2, color=OLIVE,
        spaceBefore=0.02 * inch, spaceAfter=space_after * inch,
    )


# ── LINE-ITEMS TABLE STYLE ──────────────────────────────────────
def line_items_base_style(*, num_cols: int) -> list:
    """Standard `TableStyle` commands for any line-items table.

    Renderers append their own column-specific alignments + alternating
    row shading on top of this base.
    """
    return [
        ("BACKGROUND", (0, 0), (-1, 0), OLIVE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
        ("FONTSIZE", (0, 0), (-1, 0), SIZE_HEADER_CELL),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("FONTSIZE", (0, 1), (-1, -1), SIZE_BODY),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, OLIVE),
        ("LINEBELOW", (0, -1), (-1, -1), 1, OLIVE),
        ("LINEAFTER", (0, 0), (num_cols - 2, -1), 0.25, GRID_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]


def stripe_rows(style_cmds: list, *, body_start: int, body_end: int,
                skip_rows: set[int] | None = None) -> None:
    """Append BACKGROUND commands for alternating row shading."""
    skip = skip_rows or set()
    stripe = 0
    for i in range(body_start, body_end + 1):
        if i in skip:
            continue
        stripe += 1
        if stripe % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), GRAY_BG))


# ── TOTALS LADDER ───────────────────────────────────────────────
def build_totals_block(
    rows: list[tuple[str, str, bool]],
    *,
    label_col_w: float = 2.0,
    value_col_w: float = 1.3,
    page_inset: float = 3.6,
) -> Table:
    """Right-aligned totals ladder with consistent typography.

    ``rows`` is a list of ``(label, value, is_emphasis)`` tuples. The
    emphasized row gets the olive bold treatment + 1pt olive rule above.
    Use the canonical sequence:

        Subtotal
        Shipping (or Freight)
        Fuel Surcharge
        Tax (rate%)
        Core Deposit (if any) — labelled "refundable" but not subtracted
        TOTAL DUE   ← emphasis row
    """
    lbl = ParagraphStyle("ttlLbl", fontSize=SIZE_BODY, fontName=FONT_BASE,
                         textColor=DARK_GRAY, alignment=TA_RIGHT)
    val = ParagraphStyle("ttlVal", fontSize=SIZE_BODY, fontName=FONT_BASE,
                         textColor=DARK_GRAY, alignment=TA_RIGHT)
    lbl_b = ParagraphStyle("ttlLblB", fontSize=12, fontName=FONT_BOLD,
                           textColor=OLIVE, alignment=TA_RIGHT)
    val_b = ParagraphStyle("ttlValB", fontSize=12, fontName=FONT_BOLD,
                           textColor=OLIVE, alignment=TA_RIGHT)

    data = []
    style_cmds = [
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    for idx, (label, value, bold) in enumerate(rows):
        ls = lbl_b if bold else lbl
        vs = val_b if bold else val
        data.append([Paragraph(label, ls), Paragraph(value, vs)])
        if bold:
            style_cmds.append(("LINEABOVE", (0, idx), (-1, idx), 1, OLIVE))

    totals = Table(data, colWidths=[label_col_w * inch, value_col_w * inch])
    totals.setStyle(TableStyle(style_cmds))

    outer = Table(
        [[Paragraph("", ParagraphStyle("blank")), totals]],
        colWidths=[page_inset * inch, (label_col_w + value_col_w) * inch],
    )
    outer.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return outer


# ── FOOTER ──────────────────────────────────────────────────────
def build_footer(
    styles: dict,
    *,
    thank_you: str | None = None,
    include_warranty_note: bool = False,
    include_core_note: bool = False,
) -> list:
    """Return the closing flowables for a document.

    Every renderer should call this so footers look identical across
    Quotes, SOs, Invoices, POs, etc.

    Composition (bottom-up):
        thin gray rule
        Thank-you line  (e.g. "Thank you for your business! — JAK's Diesel")
        Company website / phone / email
        Optional: warranty / core-return reminder
    """
    info = company_info()
    out = []
    out.append(HRFlowable(
        width="100%", thickness=0.5, color=BORDER,
        spaceAfter=0.05 * inch, spaceBefore=GAP_BEFORE_FOOTER * inch,
    ))

    msg = thank_you or f"Thank you for your business! — {info['name']}"
    out.append(Paragraph(esc(msg), styles["footer"]))

    contact_bits: list[str] = []
    if info.get("website"):
        contact_bits.append(info["website"])
    if info.get("phone"):
        contact_bits.append(info["phone"])
    if info.get("email"):
        contact_bits.append(info["email"])
    if contact_bits:
        out.append(Paragraph(esc("  ·  ".join(contact_bits)), styles["footer"]))

    if include_warranty_note:
        out.append(Paragraph(
            "Warranty coverage details accompany your invoice. "
            "Please retain for your records.",
            styles["footer"],
        ))
    if include_core_note:
        out.append(Paragraph(
            "Core deposits are refundable upon return of an acceptable core.",
            styles["footer"],
        ))

    return out


# ── DOCUMENT SHELL ──────────────────────────────────────────────
def make_simple_doc(output_path: str, *, pagesize=letter,
                    left=None, right=None, top=None, bottom=None) -> "SimpleDocTemplate":
    """Build a SimpleDocTemplate with the canonical margins.

    Per-doc renderers should call this so every artifact starts with
    the same page geometry (and overrides only when there's a real
    reason — e.g. landscape statements).
    """
    return SimpleDocTemplate(
        output_path,
        pagesize=pagesize,
        leftMargin=(left if left is not None else PAGE_MARGIN_L) * inch,
        rightMargin=(right if right is not None else PAGE_MARGIN_R) * inch,
        topMargin=(top if top is not None else PAGE_MARGIN_T) * inch,
        bottomMargin=(bottom if bottom is not None else PAGE_MARGIN_B) * inch,
    )


__all__ = [
    # palette
    "OLIVE", "OLIVE_LIGHT", "GRAY_BG", "GRID_GRAY", "FOOTER_GRAY", "MUTED",
    "DARK_GRAY", "BORDER", "WHITE", "BLACK", "RED", "GREEN", "BLUE",
    # typography
    "FONT_BASE", "FONT_BOLD", "FONT_ITALIC",
    "SIZE_TITLE", "SIZE_TITLE_BIG", "SIZE_SUBTITLE",
    "SIZE_HEADER_CELL", "SIZE_BODY", "SIZE_LABEL", "SIZE_SMALL", "SIZE_TINY",
    "SIZE_DOC_TITLE", "SIZE_DOC_TITLE_LEADING",
    # margins
    "PAGE_MARGIN_L", "PAGE_MARGIN_R", "PAGE_MARGIN_T", "PAGE_MARGIN_B",
    "GAP_AFTER_HEADER", "GAP_AFTER_DIVIDER", "GAP_AFTER_BILLTO",
    "GAP_AFTER_TABLE", "GAP_AFTER_TOTALS", "GAP_BEFORE_FOOTER",
    "LOGO_W", "LOGO_H",
    # helpers
    "make_doc_styles", "company_info", "format_company_address", "esc",
    "format_friendly_date", "format_eta_friendly",
    "build_header", "header_divider",
    "line_items_base_style", "stripe_rows",
    "build_totals_block", "build_footer",
    "make_simple_doc",
]
