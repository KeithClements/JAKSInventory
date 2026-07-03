"""PDF Core Return Receipt generator for JAK's Diesel.

Produces a customer-signable artifact that enumerates every core return
created when an invoice was finalized. One row per unit (qty=6 → 6 rows)
so each physical core can be checked off and tracked back to the credit
amount the customer paid.

Layout:
- Branded header (matches invoice/SO PDFs)
- Bill-to / invoice reference
- Cores table: # · SKU · Description · Serial # · Customer Credit · Due Date
- Acknowledgement paragraph
- Customer + sales rep signature blocks
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

from db import get_setting, get_invoice, get_core_returns_for_invoice

# Unified brand palette + shared section builders. Keep this in sync
# with quote_pdf / pdf_invoice / pdf_purchase_order.
from .doc_theme import (
    OLIVE, OLIVE_LIGHT, GRAY_BG, WHITE, BLACK, DARK_GRAY,
    SIZE_DOC_TITLE, SIZE_DOC_TITLE_LEADING,
    build_footer as _shared_footer,
    format_friendly_date,
    make_simple_doc,
)

_BASE = Path(__file__).parent.parent
OUTPUT_DIR = _BASE / "output" / "core_receipts"

# ── Status palette (shared between badges + per-row chips) ─────────
_STATUS_COLORS = {
    "open":     colors.HexColor("#F57C00"),  # orange
    "pending":  colors.HexColor("#F57C00"),
    "received": colors.HexColor("#1565C0"),  # blue
    "credited": colors.HexColor("#2E7D32"),  # green
    "voided":   colors.HexColor("#666666"),  # gray
    "overdue":  colors.HexColor("#C62828"),  # red
}


def _core_row_state(core: dict) -> str:
    """Normalize per-row status. Promotes 'pending' past due_date to 'overdue'."""
    raw = str(core.get("status", "") or "").lower().strip() or "open"
    if raw in {"pending", "open"}:
        due = str(core.get("due_date", "") or "")[:10]
        if due:
            try:
                today_iso = datetime.now().date().isoformat()
                if due < today_iso:
                    return "overdue"
            except Exception:
                pass
        return "open"
    return raw


def _receipt_state(cores: list[dict]) -> tuple[str, str, str]:
    """Aggregate receipt-level state from per-core statuses.

    Returns a tuple of ``(state, title_top, title_bot)`` where the two title
    strings stack as the document's two-line right-aligned header. The state
    drives the badge color via ``_STATUS_COLORS``.
    """
    if not cores:
        return "open", "CORE RETURN", "RECEIPT"
    states = [_core_row_state(c) for c in cores]
    if all(s == "voided" for s in states):
        return "voided", "CORE RETURN", "VOIDED"
    non_void = [s for s in states if s != "voided"]
    if non_void and all(s == "credited" for s in non_void):
        return "credited", "CORE CREDIT", "RECEIPT"
    if non_void and all(s in {"credited", "received"} for s in non_void) and "received" in non_void:
        return "received", "CORE RECEIVED", "CONFIRMATION"
    if any(s == "overdue" for s in non_void):
        return "overdue", "CORE RETURN", "RECEIPT"
    return "open", "CORE RETURN", "RECEIPT"


def _has_any_serial(cores: list[dict]) -> bool:
    """True when at least one core has a recorded serial number.

    Used to auto-hide the Serial column when serial tracking isn't in use
    for the cores on this receipt.
    """
    return any(str(c.get("serial_number") or "").strip() for c in cores)


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


def _format_address(info: dict) -> str:
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
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def generate_core_receipt_pdf(invoice_id: int, output_path: str | None = None) -> str:
    """Generate a printable core-return receipt for an invoice.

    Args:
        invoice_id: Invoice with one or more linked core_returns rows.
        output_path: Optional explicit output path. Defaults to
            ``output/core_receipts/CORE-<invoice_number>.pdf``.

    Returns:
        Absolute path to the generated PDF.

    Raises:
        ValueError: if the invoice does not exist or has no core returns.
    """
    invoice = get_invoice(int(invoice_id))
    if not invoice:
        raise ValueError(f"Invoice {invoice_id} not found")

    cores = get_core_returns_for_invoice(int(invoice_id)) or []
    if not cores:
        raise ValueError(f"Invoice {invoice_id} has no core returns to receipt")

    inv_num = invoice.get("invoice_number") or f"INV-{invoice_id}"

    if not output_path:
        _ensure_output_dir()
        safe = str(inv_num).replace("/", "-").replace("\\", "-")
        output_path = str(OUTPUT_DIR / f"CORE-{safe}.pdf")

    doc = make_simple_doc(output_path)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "CRTitle", parent=styles["Heading1"],
        fontSize=SIZE_DOC_TITLE, textColor=OLIVE, spaceAfter=2,
        alignment=TA_RIGHT, fontName="Helvetica-Bold", leading=SIZE_DOC_TITLE_LEADING,
    ))
    styles.add(ParagraphStyle(
        "CRSub", parent=styles["Normal"],
        fontSize=11, textColor=DARK_GRAY, alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        "CRLabel", parent=styles["Normal"],
        fontSize=9, textColor=OLIVE, fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        "CRValue", parent=styles["Normal"],
        fontSize=10, textColor=DARK_GRAY,
    ))
    styles.add(ParagraphStyle(
        "CRBody", parent=styles["Normal"],
        fontSize=9, textColor=DARK_GRAY, leading=12,
    ))
    styles.add(ParagraphStyle(
        "CRFoot", parent=styles["Normal"],
        fontSize=8, textColor=colors.HexColor("#999999"), alignment=TA_CENTER,
    ))

    elements: list = []
    elements.append(_build_header(invoice, styles))
    elements.append(Spacer(1, 0.15 * inch))
    elements.append(HRFlowable(
        width="100%", thickness=2, color=OLIVE,
        spaceAfter=0.2 * inch, spaceBefore=0.05 * inch,
    ))
    elements.append(_build_bill_to(invoice, styles))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(_build_cores_table(cores, styles))
    elements.append(Spacer(1, 0.2 * inch))

    total_credit = sum(
        float(c.get("customer_core_price") or c.get("core_charge") or 0)
        for c in cores
    )
    elements.append(Paragraph(
        f"<b>Total Customer Credit on Return:</b> "
        f"<font color='{OLIVE.hexval()}'><b>${total_credit:,.2f}</b></font> "
        f"({len(cores)} core{'s' if len(cores) != 1 else ''})",
        styles["CRValue"],
    ))
    elements.append(Spacer(1, 0.2 * inch))

    elements.append(Paragraph("<b>Acknowledgement</b>", styles["CRLabel"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "I acknowledge that the cores listed above are owed back to "
        f"{_company()['name']} as part of invoice <b>{inv_num}</b>. "
        "Cores must be returned in rebuildable condition by the due date "
        "shown. Upon return and inspection, the listed Customer Credit "
        "will be issued. Cores not returned by the due date may forfeit "
        "all or part of the credit at JAK's Diesel's sole discretion.",
        styles["CRBody"],
    ))
    elements.append(Spacer(1, 0.25 * inch))

    elements.append(_build_signatures(styles))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(_build_pickup_info(styles))
    elements.extend(_shared_footer(
        {"footer": styles["CRFoot"]},
        thank_you=f"Core Return Receipt — {_company()['name']}",
        include_core_note=True,
    ))

    doc.build(elements)
    return os.path.abspath(output_path)


# ──────────────────────────────────────────────────────────────────
# Sales-order variant
# ──────────────────────────────────────────────────────────────────

def _so_core_lines(so_id: int) -> list[dict]:
    """Synthesize per-unit core rows from SO lines for the slip.

    Mirrors the structure of ``get_core_returns_for_invoice`` so the
    same table builder can render either source. One row per unit
    (qty=3 → 3 rows) so the customer signs against each physical core.
    """
    from db import get_db
    rows: list[dict] = []
    with get_db() as conn:
        cur = conn.execute(
            """
            SELECT sol.id AS line_id, sol.product_id,
                   sol.quantity_ordered AS quantity,
                   p.sku, p.title AS product_title,
                   p.core_charge,
                   COALESCE(p.core_sell_price, 0) AS core_sell_price
            FROM sales_order_lines sol
            LEFT JOIN products p ON p.id = sol.product_id
            WHERE sol.so_id = %s
            ORDER BY sol.id
            """,
            (int(so_id),),
        ).fetchall()
    for r in cur:
        d = dict(r) if hasattr(r, "keys") else {
            "line_id": r[0], "product_id": r[1], "quantity": r[2],
            "sku": r[3], "product_title": r[4],
            "core_charge": r[5], "core_sell_price": r[6],
        }
        core = float(d.get("core_charge") or 0)
        if core <= 0:
            continue
        # The "customer credit" the customer can earn back is the core sell
        # price (what they paid). Fall back to core_charge for rows that
        # haven't set a sell price.
        credit = float(d.get("core_sell_price") or 0) or core
        qty = max(int(d.get("quantity") or 0), 0)
        sku = (d.get("sku") or "").strip()
        # Strip CORE-/RTN- pseudo SKUs if they ever leak in.
        if sku.upper().startswith(("CORE-", "RTN-")):
            continue
        for _ in range(qty):
            rows.append({
                "sku": sku,
                "product_title": d.get("product_title") or "",
                "serial_number": "",
                "customer_core_price": credit,
                "core_charge": core,
                "due_date": None,
            })
    return rows


def generate_core_slip_for_sales_order(
    so_id: int, output_path: str | None = None,
) -> str:
    """Generate a printable core-return slip for an unfinalized SO.

    Used at SO time so the customer leaves with paperwork describing the
    cores they owe back, even though no ``core_returns`` rows exist yet.
    Returns absolute path to the generated PDF.
    """
    from db import get_sales_order
    so = get_sales_order(int(so_id))
    if not so:
        raise ValueError(f"Sales order {so_id} not found")

    cores = _so_core_lines(int(so_id))
    if not cores:
        raise ValueError(f"Sales order {so_id} has no core-bearing lines")

    so_num = so.get("so_number") or so.get("order_number") or f"SO-{so_id}"

    if not output_path:
        _ensure_output_dir()
        safe = str(so_num).replace("/", "-").replace("\\", "-")
        output_path = str(OUTPUT_DIR / f"CORE-{safe}.pdf")

    # Reuse the invoice receipt's section builders by faking an
    # "invoice" dict with the bill-to fields they read.
    pseudo_invoice = {
        "id": so.get("id"),
        "invoice_number": so_num,
        "customer_name": so.get("customer_name") or so.get("customer_company"),
        "customer_address": so.get("customer_address"),
        "bill_to_name": so.get("customer_name") or so.get("customer_company"),
        "bill_to_address": so.get("customer_address"),
    }

    doc = make_simple_doc(output_path)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "CRTitle", parent=styles["Heading1"], fontSize=SIZE_DOC_TITLE,
        textColor=OLIVE, spaceAfter=2, alignment=TA_RIGHT,
        fontName="Helvetica-Bold", leading=SIZE_DOC_TITLE_LEADING,
    ))
    styles.add(ParagraphStyle(
        "CRSub", parent=styles["Normal"], fontSize=11,
        textColor=DARK_GRAY, alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        "CRLabel", parent=styles["Normal"], fontSize=9,
        textColor=OLIVE, fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        "CRValue", parent=styles["Normal"], fontSize=10, textColor=DARK_GRAY,
    ))
    styles.add(ParagraphStyle(
        "CRBody", parent=styles["Normal"], fontSize=9,
        textColor=DARK_GRAY, leading=12,
    ))
    styles.add(ParagraphStyle(
        "CRFoot", parent=styles["Normal"], fontSize=8,
        textColor=colors.HexColor("#999999"), alignment=TA_CENTER,
    ))

    elements: list = []
    # Header — re-use _build_header but swap the subtitle to the SO #.
    company = _company()
    if company["logo_path"].exists():
        logo_cell = Image(
            str(company["logo_path"]), width=2.4 * inch, height=1.8 * inch,
            kind="proportional",
        )
        logo_cell.hAlign = "LEFT"
    else:
        logo_cell = Paragraph(f"<b>{company['name']}</b>", styles["CRTitle"])

    today = datetime.now().strftime("%B %d, %Y")
    right_lines = [
        Paragraph("CORE RETURN", styles["CRTitle"]),
        Paragraph("SLIP", styles["CRTitle"]),
        Spacer(1, 4),
        Paragraph(f"<b>Sales Order {so_num}</b>", styles["CRSub"]),
        Paragraph(today, styles["CRSub"]),
    ]
    header = Table(
        [[logo_cell, right_lines]], colWidths=[3.5 * inch, 3.9 * inch],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(header)
    elements.append(Spacer(1, 0.15 * inch))
    elements.append(HRFlowable(
        width="100%", thickness=2, color=OLIVE,
        spaceAfter=0.2 * inch, spaceBefore=0.05 * inch,
    ))
    elements.append(_build_bill_to(pseudo_invoice, styles))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(_build_cores_table(cores, styles))
    elements.append(Spacer(1, 0.2 * inch))

    total_credit = sum(float(c.get("customer_core_price") or 0) for c in cores)
    elements.append(Paragraph(
        f"<b>Total Customer Credit on Return:</b> "
        f"<font color='{OLIVE.hexval()}'><b>${total_credit:,.2f}</b></font> "
        f"({len(cores)} core{'s' if len(cores) != 1 else ''})",
        styles["CRValue"],
    ))
    elements.append(Spacer(1, 0.2 * inch))

    elements.append(Paragraph("<b>Acknowledgement</b>", styles["CRLabel"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "I acknowledge that the cores listed above are owed back to "
        f"{company['name']} as part of sales order <b>{so_num}</b>. "
        "Cores must be returned in rebuildable condition. Upon return "
        "and inspection, the listed Customer Credit will be issued. "
        "A finalized core return receipt will be produced once this "
        "order is invoiced.",
        styles["CRBody"],
    ))
    elements.append(Spacer(1, 0.25 * inch))
    elements.append(_build_signatures(styles))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(_build_pickup_info(styles))
    elements.extend(_shared_footer(
        {"footer": styles["CRFoot"]},
        thank_you=(
            f"Core Return Slip — {company['name']} "
            "(preliminary; superseded by the receipt issued on invoice finalization)"
        ),
        include_core_note=True,
    ))

    doc.build(elements)
    return os.path.abspath(output_path)


# ── Section builders ──────────────────────────────────────────────


def _build_header(
    invoice: dict,
    styles,
    title_top: str = "CORE RETURN",
    title_bot: str = "RECEIPT",
    state: str = "open",
) -> Table:
    company = _company()
    logo_path = company["logo_path"]

    if logo_path.exists():
        logo_cell = Image(
            str(logo_path), width=2.4 * inch, height=1.8 * inch,
            kind="proportional",
        )
        logo_cell.hAlign = "LEFT"
    else:
        logo_cell = Paragraph(f"<b>{company['name']}</b>", styles["CRTitle"])

    inv_num = invoice.get("invoice_number") or f"INV-{invoice.get('id')}"
    today = datetime.now().strftime("%B %d, %Y")

    # Color the status badge consistently with the on-screen UI palette.
    badge_color = _STATUS_COLORS.get(state, _STATUS_COLORS["open"])
    badge_text = state.upper()
    badge_para = Paragraph(
        f"<font color='{badge_color.hexval()}' size='10'><b>● {badge_text}</b></font>",
        styles["CRSub"],
    )

    right_lines = [
        Paragraph(title_top, styles["CRTitle"]),
        Paragraph(title_bot, styles["CRTitle"]),
        Spacer(1, 2),
        badge_para,
        Spacer(1, 4),
        Paragraph(f"<b>Invoice {inv_num}</b>", styles["CRSub"]),
        Paragraph(today, styles["CRSub"]),
    ]

    table = Table(
        [[logo_cell, right_lines]],
        colWidths=[3.5 * inch, 3.9 * inch],
    )
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _build_bill_to(invoice: dict, styles) -> Table:
    cust_name = invoice.get("customer_name") or invoice.get("bill_to_name") or "Customer"
    cust_lines = [Paragraph("<b>Customer</b>", styles["CRLabel"]),
                  Paragraph(str(cust_name), styles["CRValue"])]
    addr = invoice.get("bill_to_address") or invoice.get("customer_address")
    if addr:
        cust_lines.append(Paragraph(str(addr).replace("\n", "<br/>"), styles["CRValue"]))

    company = _company()
    company_lines = [
        Paragraph(f"<b>{company['name']}</b>", styles["CRValue"]),
    ]
    addr_str = _format_address(company)
    if addr_str:
        company_lines.append(Paragraph(addr_str, styles["CRValue"]))
    if company["phone"]:
        company_lines.append(Paragraph(company["phone"], styles["CRValue"]))

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


def _build_cores_table(
    cores: list[dict],
    styles,
    show_serial: bool = True,
) -> Table:
    """Render the per-core grid.

    Columns: # · SKU · Description · [Serial #?] · Status · Received ·
    Customer Credit · Due Date. The Serial column is auto-suppressed
    when no core on the receipt has a recorded serial number so the
    visible row height stays compact.
    """
    header = ["#", "SKU", "Description"]
    if show_serial:
        header.append("Serial #")
    header.extend(["Status", "Received", "Customer Credit", "Due Date"])
    data = [header]

    for idx, core in enumerate(cores, start=1):
        sku_raw = str(core.get("sku") or "")
        # Wrap long SKUs via Paragraph so they don't blow out the column.
        sku_cell = Paragraph(sku_raw, styles["CRBody"]) if len(sku_raw) > 14 else sku_raw
        desc = str(core.get("product_title") or "")[:60]
        serial = str(core.get("serial_number") or "").strip()
        serial_cell = serial if serial else "_______________"
        credit = float(
            core.get("customer_core_price") or core.get("core_charge") or 0
        )
        due = core.get("due_date") or ""
        if due:
            try:
                due_fmt = datetime.fromisoformat(str(due).split("T")[0]).strftime("%m/%d/%Y")
            except Exception:
                due_fmt = str(due)
        else:
            due_fmt = ""

        # Received column — own dedicated cell (was a sub-line under
        # the description); credit info still surfaces below desc so
        # the receipt clearly shows the resolution on reprint.
        recv = core.get("received_date")
        if recv:
            try:
                recv_fmt = datetime.fromisoformat(
                    str(recv).split("T")[0]
                ).strftime("%m/%d/%Y")
            except Exception:
                recv_fmt = str(recv)
        else:
            recv_fmt = "\u2014"

        credit_date = core.get("credit_date")
        credit_issued = float(core.get("credit_issued") or 0)
        if credit_issued > 0 and credit_date:
            try:
                cd_fmt = datetime.fromisoformat(
                    str(credit_date).split("T")[0]
                ).strftime("%m/%d/%Y")
            except Exception:
                cd_fmt = str(credit_date)
            desc = (
                f"{desc}<br/>"
                f"<font size='7' color='#666666'>"
                f"Credit ${credit_issued:,.2f} issued {cd_fmt}"
                f"</font>"
            )

        row_state = _core_row_state(core)
        status_color = _STATUS_COLORS.get(row_state, _STATUS_COLORS["open"])
        status_cell = Paragraph(
            f"<font color='{status_color.hexval()}'><b>{row_state.upper()}</b></font>",
            styles["CRBody"],
        )

        row = [
            str(idx),
            sku_cell,
            Paragraph(desc, styles["CRBody"]),
        ]
        if show_serial:
            row.append(serial_cell)
        row.extend([
            status_cell,
            recv_fmt,
            f"${credit:,.2f}",
            due_fmt,
        ])
        data.append(row)

    # Column widths tuned to fit ~7.4" content width in either mode.
    if show_serial:
        col_widths = [
            0.3 * inch, 0.85 * inch, 1.55 * inch, 0.85 * inch,
            0.75 * inch, 0.7 * inch, 0.95 * inch, 0.75 * inch,
        ]
    else:
        col_widths = [
            0.35 * inch, 1.0 * inch, 2.05 * inch,
            0.8 * inch, 0.85 * inch, 1.0 * inch, 0.85 * inch,
        ]

    # Right-align the Customer Credit column (last-but-one).
    credit_col = len(header) - 2

    table = Table(
        data,
        colWidths=col_widths,
        repeatRows=1,
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), OLIVE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("ALIGN", (credit_col, 1), (credit_col, -1), "RIGHT"),
        ("ALIGN", (0, 1), (0, -1), "CENTER"),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, GRAY_BG]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _build_signatures(styles) -> Table:
    table = Table(
        [
            [Paragraph("<b>Customer Signature</b>", styles["CRLabel"]), "",
             Paragraph("<b>Print Name</b>", styles["CRLabel"]), "",
             Paragraph("<b>Date</b>", styles["CRLabel"])],
            [Paragraph("________________________", styles["CRBody"]), "",
             Paragraph("________________________", styles["CRBody"]), "",
             Paragraph("____________", styles["CRBody"])],
            [Spacer(1, 0.2 * inch)] * 5,
            [Paragraph("<b>Sales Rep Signature</b>", styles["CRLabel"]), "",
             Paragraph("<b>Print Name</b>", styles["CRLabel"]), "",
             Paragraph("<b>Date</b>", styles["CRLabel"])],
            [Paragraph("________________________", styles["CRBody"]), "",
             Paragraph("________________________", styles["CRBody"]), "",
             Paragraph("____________", styles["CRBody"])],
        ],
        colWidths=[2.4 * inch, 0.2 * inch, 2.4 * inch, 0.2 * inch, 1.4 * inch],
    )
    return table


def _build_pickup_info(styles) -> Table:
    """Block telling the customer where/when/how to return the cores."""
    company = _company()
    addr_str = _format_address(company) or "—"
    phone = company.get("phone") or "—"
    hours = get_setting("company_hours", "Mon–Fri 8:00 AM – 5:00 PM")
    contact = get_setting("core_return_contact", "Parts Counter")

    body = (
        f"<b>Return To:</b> {company['name']}<br/>"
        f"<b>Address:</b> {addr_str}<br/>"
        f"<b>Hours:</b> {hours}<br/>"
        f"<b>Phone:</b> {phone}<br/>"
        f"<b>Ask for:</b> {contact}<br/><br/>"
        "Bring this signed slip with the cores. Cores must be drained, "
        "capped, and in rebuildable condition. Credit is issued upon "
        "receipt and inspection."
    )
    inner = Table(
        [[Paragraph("<b>Return / Pickup Information</b>", styles["CRLabel"])],
         [Paragraph(body, styles["CRBody"])]],
        colWidths=[7.4 * inch],
    )
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), GRAY_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return inner


def _invoice_preview_core_lines(invoice_id: int) -> list[dict]:
    """Synthesize per-unit core rows from invoice lines for the preview slip.

    Used pre-finalize when ``core_returns`` rows do not yet exist. Mirrors
    the structure of ``get_core_returns_for_invoice`` so the same table
    builder can render either source.
    """
    from db import get_db
    rows: list[dict] = []
    with get_db() as conn:
        cur = conn.execute(
            """
            SELECT il.id AS line_id, il.product_id,
                   il.quantity AS quantity,
                   p.sku, p.title AS product_title,
                   p.core_charge,
                   COALESCE(p.core_sell_price, 0) AS core_sell_price
            FROM invoice_lines il
            LEFT JOIN products p ON p.id = il.product_id
            WHERE il.invoice_id = %s
            ORDER BY il.id
            """,
            (int(invoice_id),),
        ).fetchall()
    for r in cur:
        d = dict(r) if hasattr(r, "keys") else {
            "line_id": r[0], "product_id": r[1], "quantity": r[2],
            "sku": r[3], "product_title": r[4],
            "core_charge": r[5], "core_sell_price": r[6],
        }
        core = float(d.get("core_charge") or 0)
        if core <= 0:
            continue
        credit = float(d.get("core_sell_price") or 0) or core
        qty = max(int(d.get("quantity") or 0), 0)
        sku = (d.get("sku") or "").strip()
        if sku.upper().startswith(("CORE-", "RTN-")):
            continue
        for _ in range(qty):
            rows.append({
                "sku": sku,
                "product_title": d.get("product_title") or "",
                "serial_number": "",
                "customer_core_price": credit,
                "core_charge": core,
                "due_date": None,
            })
    return rows


def generate_core_slip_for_invoice_preview(
    invoice_id: int, output_path: str | None = None,
) -> str:
    """Generate a Draft-state preview core slip from invoice lines.

    Used when the invoice is still in ``draft`` and ``core_returns`` rows
    have not been materialized yet. The slip is functionally identical
    to the post-finalize receipt but flagged as preliminary.
    """
    invoice = get_invoice(int(invoice_id))
    if not invoice:
        raise ValueError(f"Invoice {invoice_id} not found")

    cores = _invoice_preview_core_lines(int(invoice_id))
    if not cores:
        raise ValueError(f"Invoice {invoice_id} has no core-bearing lines")

    inv_num = invoice.get("invoice_number") or f"INV-{invoice_id}"
    if not output_path:
        _ensure_output_dir()
        safe = str(inv_num).replace("/", "-").replace("\\", "-")
        output_path = str(OUTPUT_DIR / f"CORE-{safe}-PREVIEW.pdf")

    doc = make_simple_doc(output_path)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "CRTitle", parent=styles["Heading1"], fontSize=SIZE_DOC_TITLE, textColor=OLIVE,
        spaceAfter=2, alignment=TA_RIGHT, fontName="Helvetica-Bold", leading=SIZE_DOC_TITLE_LEADING,
    ))
    styles.add(ParagraphStyle(
        "CRSub", parent=styles["Normal"], fontSize=11,
        textColor=DARK_GRAY, alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        "CRLabel", parent=styles["Normal"], fontSize=9,
        textColor=OLIVE, fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        "CRValue", parent=styles["Normal"], fontSize=10, textColor=DARK_GRAY,
    ))
    styles.add(ParagraphStyle(
        "CRBody", parent=styles["Normal"], fontSize=9,
        textColor=DARK_GRAY, leading=12,
    ))
    styles.add(ParagraphStyle(
        "CRFoot", parent=styles["Normal"], fontSize=8,
        textColor=colors.HexColor("#999999"), alignment=TA_CENTER,
    ))

    elements: list = []
    # Preview is always pre-finalize, so the receipt-level state is "open".
    elements.append(_build_header(invoice, styles, "CORE RETURN", "RECEIPT", "open"))
    elements.append(Spacer(1, 0.15 * inch))
    elements.append(HRFlowable(
        width="100%", thickness=2, color=OLIVE,
        spaceAfter=0.2 * inch, spaceBefore=0.05 * inch,
    ))
    elements.append(_build_bill_to(invoice, styles))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(_build_cores_table(cores, styles, _has_any_serial(cores)))
    elements.append(Spacer(1, 0.2 * inch))

    total_credit = sum(float(c.get("customer_core_price") or 0) for c in cores)
    elements.append(Paragraph(
        f"<b>Total Customer Credit on Return:</b> "
        f"<font color='{OLIVE.hexval()}'><b>${total_credit:,.2f}</b></font> "
        f"({len(cores)} core{'s' if len(cores) != 1 else ''})",
        styles["CRValue"],
    ))
    elements.append(Spacer(1, 0.2 * inch))

    elements.append(Paragraph("<b>Acknowledgement</b>", styles["CRLabel"]))
    elements.append(Spacer(1, 4))
    elements.append(Paragraph(
        "I acknowledge that the cores listed above are owed back to "
        f"{_company()['name']} as part of invoice <b>{inv_num}</b>. "
        "Cores must be returned in rebuildable condition by the due date "
        "shown. Upon return and inspection, the listed Customer Credit "
        "will be issued.",
        styles["CRBody"],
    ))
    elements.append(Spacer(1, 0.25 * inch))
    elements.append(_build_signatures(styles))
    elements.append(Spacer(1, 0.2 * inch))
    elements.append(_build_pickup_info(styles))
    elements.extend(_shared_footer(
        {"footer": styles["CRFoot"]},
        thank_you=(
            f"Core Return Slip — {_company()['name']} "
            "(preliminary; superseded by the receipt issued on invoice finalization)"
        ),
        include_core_note=True,
    ))
    doc.build(elements)
    return os.path.abspath(output_path)
