"""PDF purchase order generator for JAKS Diesel."""
from __future__ import annotations

import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
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

from db import get_setting

# Pull brand palette + section builders from the single doc theme module
# so POs stay visually consistent with Quotes/Invoices/SOs.
from .doc_theme import (
	OLIVE, DARK_GRAY, GRAY_BG, WHITE,
	SIZE_DOC_TITLE, SIZE_DOC_TITLE_LEADING,
	build_footer as _shared_footer,
	build_totals_block as _shared_totals_block,
	format_friendly_date,
	make_simple_doc,
)
_BASE = Path(__file__).parent.parent
OUTPUT_DIR = _BASE / "output" / "po_pdfs"


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
	if info.get("address"):
		parts.append(str(info["address"]))
	city_state = ", ".join([x for x in [info.get("city"), info.get("state")] if x]).strip(", ")
	if info.get("zip"):
		city_state = f"{city_state} {info['zip']}".strip()
	if city_state:
		parts.append(city_state)
	return "\n".join(parts)


def _esc(text: str) -> str:
	if not text:
		return ""
	return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _vendor_part(line: dict) -> str:
	return str(
		line.get("supplier_part_number")
		or line.get("pai_sku")
		or line.get("oem_part_number")
		or line.get("sku")
		or ""
	)


def _block_lines(text: str) -> list[str]:
	raw = str(text or "").strip()
	if not raw:
		return []
	return [ln.strip() for ln in raw.replace("\r", "").split("\n") if ln.strip()]


def generate_purchase_order_pdf(po: dict, output_path: str | None = None) -> str:
	po_num = po.get("po_number", "DRAFT")
	# Always ensure output directory exists, even if output_path is provided
	if not output_path:
		OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
		safe = str(po_num).replace("/", "-").replace("\\", "-")
		output_path = str(OUTPUT_DIR / f"{safe}.pdf")
	else:
		out_dir = os.path.dirname(output_path)
		if out_dir and not os.path.exists(out_dir):
			os.makedirs(out_dir, exist_ok=True)

	doc = make_simple_doc(output_path)

	styles = getSampleStyleSheet()
	styles.add(ParagraphStyle("POTitle", parent=styles["Heading1"], fontSize=SIZE_DOC_TITLE, leading=SIZE_DOC_TITLE_LEADING, textColor=OLIVE, spaceAfter=2, alignment=TA_RIGHT, fontName="Helvetica-Bold"))
	styles.add(ParagraphStyle("POSub", parent=styles["Normal"], fontSize=10, textColor=DARK_GRAY))
	styles.add(ParagraphStyle("POLabel", parent=styles["Normal"], fontSize=9, textColor=OLIVE, fontName="Helvetica-Bold"))
	styles.add(ParagraphStyle("POValue", parent=styles["Normal"], fontSize=10, textColor=DARK_GRAY))
	styles.add(ParagraphStyle("POSmall", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#666666")))
	styles.add(ParagraphStyle("PORight", parent=styles["Normal"], fontSize=10, textColor=DARK_GRAY, alignment=TA_RIGHT))
	styles.add(ParagraphStyle("POFooter", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#999999"), alignment=TA_CENTER))

	story = []
	story.append(_build_header(po, styles))
	story.append(Spacer(1, 0.08 * inch))
	story.append(HRFlowable(width="100%", thickness=1.5, color=OLIVE, spaceAfter=0.06 * inch, spaceBefore=0.02 * inch))
	story.append(_build_parties_and_details(po, styles))
	story.append(Spacer(1, 0.2 * inch))

	# ── Vendor-facing instructions block (above lines so it's noticed) ──
	vendor_message = str(po.get("vendor_message") or "").strip()
	# Toggle flags render as their own bold callouts so they don't get
	# silently appended to the user-entered message (which used to cause
	# the message to grow on every print).
	flag_lines: list[str] = []
	if int(po.get("drop_ship") or 0):
		flag_lines.append("DROP SHIP DIRECT TO CUSTOMER.")
	if int(po.get("no_tags") or 0):
		flag_lines.append("NO TAGS OR IDENTIFICATION MATERIAL PLEASE.")
	if vendor_message or flag_lines:
		story.append(Paragraph("Message to Vendor", styles["POLabel"]))
		if vendor_message:
			story.append(Paragraph(_esc(vendor_message).replace("\n", "<br/>"), styles["POValue"]))
		for fl in flag_lines:
			story.append(Paragraph(f"<b>{_esc(fl)}</b>", styles["POValue"]))
		story.append(Spacer(1, 0.15 * inch))

	story.append(_build_lines(po))
	story.append(Spacer(1, 0.2 * inch))
	story.append(_build_totals(po))

	notes = str(po.get("notes") or "").strip()
	if notes:
		story.append(Spacer(1, 0.15 * inch))
		story.append(Paragraph("Notes", styles["POLabel"]))
		story.append(Paragraph(_esc(notes), styles["POValue"]))

	# Core handling overhaul (migration 108): print the refundable-core
	# footer whenever this PO contains any core charges, so vendors and
	# bookkeepers see the deposit is refundable on return of the
	# rebuildable core.
	if _po_has_core_lines(po):
		story.append(Spacer(1, 0.12 * inch))
		story.append(Paragraph(
			"<b>Refundable upon return of rebuildable core.</b>",
			styles["POValue"],
		))

	story.append(Spacer(1, 0.2 * inch))
	story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=0.1 * inch, spaceBefore=0.1 * inch))
	story.extend(_shared_footer({"footer": styles["POFooter"]}))

	doc.build(story)
	return os.path.abspath(output_path)


def _build_header(po: dict, styles) -> Table:
	company = _company()
	logo_path = company["logo_path"]
	status = str(po.get("status") or "draft").upper()

	if Path(logo_path).exists():
		logo = Image(str(logo_path), width=1.8 * inch, height=1.25 * inch, kind="proportional")
		logo.hAlign = "LEFT"
	else:
		logo = Paragraph(f"<b>{_esc(company['name'])}</b>", styles["POTitle"])

	left_lines = [Paragraph(f"<b>{_esc(company['name'])}</b>", styles["POValue"])]
	addr = _fmt_addr(company)
	if addr:
		for ln in addr.split("\n"):
			left_lines.append(Paragraph(_esc(ln), styles["POSmall"]))
	if company.get("phone"):
		left_lines.append(Paragraph(_esc(str(company["phone"])), styles["POSmall"]))
	if company.get("email"):
		left_lines.append(Paragraph(_esc(str(company["email"])), styles["POSmall"]))
	left_table = Table([[logo], *[[ln] for ln in left_lines]], colWidths=[2.8 * inch])
	left_table.setStyle(TableStyle([
		("VALIGN", (0, 0), (-1, -1), "TOP"),
		("LEFTPADDING", (0, 0), (-1, -1), 0),
		("RIGHTPADDING", (0, 0), (-1, -1), 0),
		("TOPPADDING", (0, 0), (-1, -1), 0),
		("BOTTOMPADDING", (0, 0), (-1, -1), 2),
	]))

	right = Table([
		[Paragraph("PURCHASE ORDER", styles["POTitle"])],
		[Paragraph(f"<b>{_esc(str(po.get('po_number') or ''))}</b>", styles["POSub"])],
		[Paragraph(f"<b>Status:</b> {_esc(status)}", styles["POSub"])],
		[Paragraph(f"<b>Order Date:</b> {_esc(format_friendly_date(po.get('order_date')))}", styles["POSub"])],
		[Paragraph(f"<b>Expected:</b> {_esc(format_friendly_date(po.get('expected_date')))}", styles["POSub"])],
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


def _build_parties_and_details(po: dict, styles) -> Table:
	company = _company()
	default_addr = _fmt_addr(company)
	default_block = [company.get("name") or "", *([ln for ln in default_addr.split("\n") if ln] if default_addr else [])]
	ship_lines = _block_lines(str(po.get("ship_to") or "")) or default_block
	bill_lines = _block_lines(str(po.get("bill_to") or "")) or default_block

	left_block = [[Paragraph("SHIP TO", styles["POLabel"])], *[[Paragraph(_esc(ln), styles["POValue"])] for ln in ship_lines]]
	mid_block = [[Paragraph("BILL TO", styles["POLabel"])], *[[Paragraph(_esc(ln), styles["POValue"])] for ln in bill_lines]]

	right_rows = [
		[Paragraph("Vendor:", styles["POSmall"]), Paragraph(_esc(str(po.get("vendor_name") or "")), styles["POValue"])],
		[Paragraph("PO #:", styles["POSmall"]), Paragraph(_esc(str(po.get("po_number") or "")), styles["POValue"])],
	]
	right_table = Table(right_rows, colWidths=[0.9 * inch, 1.7 * inch])
	right_table.setStyle(TableStyle([
		("LEFTPADDING", (0, 0), (-1, -1), 0),
		("RIGHTPADDING", (0, 0), (-1, -1), 0),
		("TOPPADDING", (0, 0), (-1, -1), 2),
		("BOTTOMPADDING", (0, 0), (-1, -1), 2),
		("VALIGN", (0, 0), (-1, -1), "TOP"),
	]))

	left = Table(left_block, colWidths=[2.35 * inch])
	mid = Table(mid_block, colWidths=[2.35 * inch])
	for tbl in (left, mid):
		tbl.setStyle(TableStyle([
			("LEFTPADDING", (0, 0), (-1, -1), 0),
			("RIGHTPADDING", (0, 0), (-1, -1), 4),
			("TOPPADDING", (0, 0), (-1, -1), 1),
			("BOTTOMPADDING", (0, 0), (-1, -1), 1),
			("VALIGN", (0, 0), (-1, -1), "TOP"),
		]))

	main = Table([[left, mid, right_table]], colWidths=[2.35 * inch, 2.35 * inch, 2.3 * inch])
	main.setStyle(TableStyle([
		("VALIGN", (0, 0), (-1, -1), "TOP"),
		("LEFTPADDING", (0, 0), (-1, -1), 0),
		("RIGHTPADDING", (0, 0), (-1, -1), 0),
	]))
	return main


def _build_lines(po: dict) -> Table:
	styles = getSampleStyleSheet()
	desc_style = ParagraphStyle(
		"LineDesc",
		parent=styles["Normal"],
		fontSize=9,
		leading=12,
		wordWrap="CJK",
	)
	desc_style_core = ParagraphStyle(
		"LineDescCore",
		parent=desc_style,
		fontSize=8,
		fontName="Helvetica-Oblique",
		textColor=colors.HexColor("#6D28D9"),
	)
	hdr_style = ParagraphStyle(
		"LineHdr",
		parent=styles["Normal"],
		fontSize=9,
		fontName="Helvetica-Bold",
		textColor=WHITE,
		leading=12,
	)
	rows = [[
		Paragraph("Vendor ID", hdr_style),
		Paragraph("Description", hdr_style),
		Paragraph("Qty", hdr_style),
		Paragraph("Recv", hdr_style),
		Paragraph("Unit Cost", hdr_style),
		Paragraph("Total", hdr_style),
	]]
	# Track sub-line (core deposit) row indices so we can style them
	# distinctly (purple italic) inside the TableStyle below.
	core_sub_rows: list[int] = []
	for ln in po.get("lines", []):
		kind = str(ln.get("line_kind") or "product").lower()
		# Compose description. For auto-generated core deposit lines we
		# prefer the literal ``description`` so it always reads
		# "Refundable Core Deposit"; for regular product lines we use the
		# product title. Line-level notes (e.g. the conditional-exchange
		# warning appended by migration 108 logic) are appended on a new
		# line so the printed PO carries them.
		if kind == "core":
			# Indent marker so the child row visually nests under its parent.
			desc_main = (
				"\u21b3 " + str(
					ln.get("description")
					or ln.get("title")
					or "Refundable Core Deposit"
				)
			)
		else:
			desc_main = str(ln.get("title") or ln.get("description") or "")
		note_txt = str(ln.get("notes") or "").strip()
		desc = desc_main if not note_txt else f"{desc_main}\n{note_txt}"
		cell_style = desc_style_core if kind == "core" else desc_style
		rows.append([
			Paragraph(_esc(_vendor_part(ln)), desc_style),
			Paragraph(_esc(desc).replace("\n", "<br/>"), cell_style),
			str(int(ln.get("quantity_ordered") or 0)),
			str(int(ln.get("quantity_received") or 0)),
			f"${float(ln.get('unit_cost') or 0):,.2f}",
			f"${float(ln.get('line_total') or 0):,.2f}",
		])

		# Track this row for purple-italic sub-line styling when it is a
		# modern auto-generated core child line.
		if kind == "core":
			core_sub_rows.append(len(rows) - 1)

		# Emit a sub-line directly beneath a product row whenever it has
		# an inline core deposit. This makes the printed PO transparent
		# about the refundable liability vendors are charging without
		# requiring callers to also have sibling line_kind='core' rows.
		if kind != "core":
			core_each = float(ln.get("core_charge") or 0)
			qty = int(ln.get("quantity_ordered") or 0)
			if core_each > 0 and qty > 0:
				core_tot = float(ln.get("core_total") or (qty * core_each))
				rows.append([
					"",
					Paragraph(
						"\u21b3 CORE DEPOSIT \u2014 refundable upon return of rebuildable core",
						desc_style_core,
					),
					str(qty),
					"",
					f"${core_each:,.2f}",
					f"${core_tot:,.2f}",
				])
				core_sub_rows.append(len(rows) - 1)

	tbl = Table(rows, colWidths=[1.3 * inch, 2.9 * inch, 0.5 * inch, 0.5 * inch, 0.9 * inch, 0.85 * inch])
	style_cmds = [
		# Olive header to match every other PDF in the suite.
		("BACKGROUND", (0, 0), (-1, 0), OLIVE),
		("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
		("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
		("FONTSIZE", (0, 0), (-1, 0), 9),
		("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
		("FONTSIZE", (0, 1), (-1, -1), 9),
		("LINEBELOW", (0, 0), (-1, 0), 1.5, OLIVE),
		("LINEBELOW", (0, -1), (-1, -1), 1, OLIVE),
		("LINEAFTER", (0, 0), (-2, -1), 0.25, colors.HexColor("#dddddd")),
		("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, GRAY_BG]),
		# Description left-align; Qty centered; money right-aligned.
		("ALIGN", (0, 0), (1, -1), "LEFT"),
		("ALIGN", (2, 0), (3, -1), "CENTER"),
		("ALIGN", (4, 0), (-1, -1), "RIGHT"),
		("VALIGN", (0, 0), (-1, -1), "TOP"),
		("LEFTPADDING", (0, 0), (-1, -1), 6),
		("RIGHTPADDING", (0, 0), (-1, -1), 6),
		("TOPPADDING", (0, 0), (-1, -1), 5),
		("BOTTOMPADDING", (0, 0), (-1, -1), 5),
	]
	# Tint each core sub-line row background purple so it visually nests
	# beneath its parent product line (text/style handled by Paragraph).
	for r in core_sub_rows:
		style_cmds.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#F5F3FF")))
		style_cmds.append(("TOPPADDING", (0, r), (-1, r), 2))
		style_cmds.append(("BOTTOMPADDING", (0, r), (-1, r), 3))
	tbl.setStyle(TableStyle(style_cmds))
	return tbl


def _build_totals(po: dict) -> Table:
	"""Olive totals ladder, matching every other doc in the suite.

	Core Handling overhaul (migration 108): when the PO has any core
	charges (either inline ``core_total`` on product rows or sibling
	``line_kind='core'`` lines), the ladder shows ``Product Subtotal`` +
	``Core Charges`` + Shipping + Tax + ``TOTAL DUE`` so vendors can see
	the refundable deposit separated from the product cost.
	"""
	core_subtotal = float(po.get("core_subtotal") or 0)
	rows: list[tuple[str, str, bool]] = []
	if core_subtotal > 0:
		rows.append(("Product Subtotal", f"${float(po.get('subtotal') or 0):,.2f}", False))
		rows.append(("Core Charges", f"${core_subtotal:,.2f}", False))
	else:
		rows.append(("Subtotal", f"${float(po.get('subtotal') or 0):,.2f}", False))
	shipping = float(po.get("shipping") or 0)
	if shipping > 0:
		rows.append(("Shipping", f"${shipping:,.2f}", False))
	tax = float(po.get("tax") or 0)
	if tax > 0:
		rows.append(("Tax", f"${tax:,.2f}", False))
	total_label = "GRAND TOTAL" if core_subtotal > 0 else "TOTAL DUE"
	rows.append((total_label, f"${float(po.get('total') or 0):,.2f}", True))
	return _shared_totals_block(rows, label_col_w=2.0, value_col_w=1.3, page_inset=3.7)


def _po_has_core_lines(po: dict) -> bool:
	if float(po.get("core_subtotal") or 0) > 0:
		return True
	for ln in po.get("lines", []) or []:
		if str(ln.get("line_kind") or "").lower() == "core":
			return True
		if float(ln.get("core_total") or 0) > 0:
			return True
	return False
