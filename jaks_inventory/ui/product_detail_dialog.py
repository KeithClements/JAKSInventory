from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import (
    get_all_xrefs_for_product,
    get_interchanges,
    get_product_by_sku,
    get_inventory_transactions,
    get_product_locations,
    list_locations,
    assign_product_to_location,
    remove_product_from_location,
    list_serials,
    get_product_serial_count,
)

from .adjust_qty_dialog import AdjustQtyDialog


class ProductDetailDialog(QDialog):

    def _on_edit(self) -> None:
        """Open the edit product dialog."""
        from .edit_product_dialog import EditProductDialog
        dlg = EditProductDialog(sku=self._sku, parent=self)
        if dlg.exec():
            self._load_data()
            self._rebuild_ui()

    def __init__(self, *, sku: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Product: {sku}")
        self.setMinimumSize(1050, 700)
        self.resize(1100, 750)

        self._sku = sku
        self._product = None
        self._product_id = None
        self._xrefs: list[dict[str, Any]] = []
        self._transactions: list[dict[str, Any]] = []
        self._locations: list[dict[str, Any]] = []
        self._serials: list[dict[str, Any]] = []
        self._serial_counts: dict = {}
        self._txn_expanded = False

        self._load_data()
        self._build_ui()

    def _load_data(self) -> None:
        """Load product data from database."""
        try:
            self._product = get_product_by_sku(self._sku)
            self._product_id = (self._product or {}).get("id")
        except Exception as e:
            self._product = {"sku": self._sku, "error": str(e)}
        try:
            self._xrefs = get_all_xrefs_for_product(self._sku)
        except Exception:
            self._xrefs = []
        try:
            if self._product_id:
                interchanges = get_interchanges(self._product_id)
                existing = {str(x.get('number', '')).upper() for x in self._xrefs}
                for ic in interchanges:
                    num = str(ic.get('interchange_number', '') or '')
                    if num.upper() not in existing:
                        self._xrefs.append({
                            'number': num,
                            'type': ic.get('interchange_type', ''),
                            'manufacturer': ic.get('manufacturer', '') or '',
                        })
                        existing.add(num.upper())
        except Exception:
            pass
        try:
            if self._product_id:
                self._transactions = get_inventory_transactions(self._product_id, limit=50)
            else:
                self._transactions = []
        except Exception:
            self._transactions = []
        try:
            if self._product_id:
                self._locations = get_product_locations(self._product_id)
            else:
                self._locations = []
        except Exception:
            self._locations = []
        try:
            if self._product_id:
                self._serials = list_serials(product_id=self._product_id, limit=100)
                self._serial_counts = get_product_serial_count(self._product_id)
            else:
                self._serials = []
                self._serial_counts = {}
        except Exception:
            self._serials = []
            self._serial_counts = {}

    # ==================================================================
    # BUILD UI — SINGLE SCROLLABLE PAGE
    # ==================================================================
    def _build_ui(self) -> None:
        """Build the consolidated single-page dialog."""
        product = self._product or {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 8)

        # ── TOP BAR: SKU + Title + Edit button ──
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(16, 12, 16, 8)
        title = str(product.get("title", "") or self._sku)
        try:
            _hdr_core_charge = float(product.get("core_charge", 0) or 0)
            _hdr_core_sell = float(product.get("core_sell_price", 0) or 0)
        except Exception:
            _hdr_core_charge = _hdr_core_sell = 0.0
        _core_badge_html = ""
        if _hdr_core_charge > 0 or _hdr_core_sell > 0:
            _amt = _hdr_core_sell if _hdr_core_sell > 0 else _hdr_core_charge
            _core_badge_html = (
                f"&nbsp;&nbsp;<span style='background:#B8860B; color:white; "
                f"font-size:9pt; font-weight:bold; padding:2px 8px; "
                f"border-radius:8px;'>\U0001F504 CORE \u00B7 ${_amt:,.2f}</span>"
            )
        header = QLabel(
            f"<span style='font-size:16pt; font-weight:bold;'>{self._sku}</span>"
            f"&nbsp;&nbsp;<span style='font-size:12pt; color:#555;'>{title}</span>"
            f"{_core_badge_html}"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        top_bar.addWidget(header, 1)

        edit_btn = QPushButton("✏️ Edit Product")
        edit_btn.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: white; font-weight: bold;"
            " padding: 6px 16px; border-radius: 4px; font-size: 10pt; }"
            "QPushButton:hover { background-color: #1565c0; }"
        )
        edit_btn.clicked.connect(self._on_edit)
        top_bar.addWidget(edit_btn)
        outer.addLayout(top_bar)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #ddd;")
        outer.addWidget(line)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll_widget = QWidget()
        self._page_layout = QVBoxLayout(self._scroll_widget)
        self._page_layout.setContentsMargins(16, 8, 16, 8)

        self._build_page_content(product)

        scroll.setWidget(self._scroll_widget)
        outer.addWidget(scroll, 1)

        # Bottom buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 4, 16, 0)
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)

    def _build_page_content(self, product: dict) -> None:
        """Build all sections on the single page."""
        layout = self._page_layout

        # ── TOP ROW: Details left | QOH right ──
        top_row = QHBoxLayout()
        top_row.setSpacing(16)

        # ═══════════════════════════════════════
        # LEFT ZONE: Product Details + Pricing
        # ═══════════════════════════════════════
        info_frame = QFrame()
        info_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        info_frame.setStyleSheet(
            "QFrame { background:#fafafa; border:1px solid #ddd; border-radius:6px; padding:12px; }"
        )
        info_form = QFormLayout()
        info_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        info_form.setVerticalSpacing(6)

        def _val(key: str) -> str:
            return str(product.get(key, "") or "")

        def _label(text: str, bold: bool = False) -> QLabel:
            lbl = QLabel(text)
            if bold:
                lbl.setStyleSheet("font-weight: bold;")
            return lbl

        # Identity section
        info_form.addRow("Manufacturer:", _label(_val("manufacturer"), bold=True))
        info_form.addRow("Supplier:", _label(_val("supplier"), bold=True))
        info_form.addRow("Category:", _label(_val("category")))
        info_form.addRow("Condition:", _label(_val("condition")))

        # Truck info (if present)
        truck_mfr = _val("truck_manufacturer")
        truck_model = _val("truck_model")
        if truck_mfr:
            info_form.addRow("Truck Make:", _label(truck_mfr))
        if truck_model:
            info_form.addRow("Truck Model:", _label(truck_model))

        # Divider
        price_sep = QFrame()
        price_sep.setFrameShape(QFrame.Shape.HLine)
        price_sep.setStyleSheet("color: #ddd;")
        info_form.addRow(price_sep)

        # Pricing
        try:
            selling_price = float(product.get("selling_price", 0) or 0)
            pai_cost = float(product.get("pai_cost", 0) or 0)
            cost = float(product.get("cost", 0) or 0)
            core_charge = float(product.get("core_charge", 0) or 0)
            core_sell_price = float(product.get("core_sell_price", 0) or 0)
            shipping_cost = float(product.get("shipping_cost", 0) or 0)
        except Exception:
            selling_price = pai_cost = cost = core_charge = shipping_cost = 0.0
            core_sell_price = 0.0

        if selling_price > 0 and cost > 0:
            margin_pct = ((selling_price - cost) / selling_price) * 100
            margin_color = "#228B22" if margin_pct > 0 else "#dc3545"
            price_text = (
                f"<span style='font-size:12pt; font-weight:bold;'>${selling_price:,.2f}</span>"
                f"&nbsp;&nbsp;<span style='color:{margin_color}; font-weight:bold;'>"
                f"({margin_pct:.1f}% margin)</span>"
            )
        else:
            price_text = f"<span style='font-size:12pt; font-weight:bold;'>${selling_price:,.2f}</span>"
        price_label = QLabel(price_text)
        price_label.setTextFormat(Qt.TextFormat.RichText)
        info_form.addRow("Selling Price:", price_label)

        cost_updated_at = str(product.get("cost_updated_at", "") or "")[:10]
        vendor_text = f"${pai_cost:,.2f}"
        if cost_updated_at:
            vendor_text += f"  <i style='color:gray;'>(updated {cost_updated_at})</i>"
        vc_label = QLabel(vendor_text)
        vc_label.setTextFormat(Qt.TextFormat.RichText)
        info_form.addRow("Vendor Cost:", vc_label)

        if shipping_cost > 0:
            info_form.addRow("Shipping:", QLabel(f"${shipping_cost:,.2f}"))
        info_form.addRow("Landed Cost:", _label(f"${cost:,.2f}", bold=True))
        if core_charge > 0 or core_sell_price > 0:
            core_html = (
                f"<span style='color:#8B6914; font-weight:bold;'>\U0001F504 "
                f"Vendor: ${core_charge:,.2f}</span>"
                f"&nbsp;&nbsp;"
                f"<span style='color:#8B6914;'>Customer: ${core_sell_price:,.2f}</span>"
            )
            core_lbl = QLabel(core_html)
            core_lbl.setTextFormat(Qt.TextFormat.RichText)
            info_form.addRow("Core Charge:", core_lbl)

        info_frame.setLayout(info_form)
        top_row.addWidget(info_frame, 3)

        # ═══════════════════════════════════════
        # RIGHT ZONE: QOH Big Block + Actions
        # ═══════════════════════════════════════
        qty_frame = QFrame()
        qty_frame.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        qty_frame.setStyleSheet(
            "QFrame { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            "stop:0 #f9f9f9, stop:1 #eeeeee); border-radius:6px; padding:12px; "
            "border: 1px solid #ccc; }"
        )
        qty_frame.setMinimumWidth(260)
        qty_frame.setMaximumWidth(320)

        qty_on_hand = int(product.get("qty_on_hand", 0) or 0)
        min_qty = int(product.get("min_qty", 0) or 0)
        max_qty = int(product.get("max_qty", 0) or 0)

        # Reservation-aware availability — "Available" is what users can
        # actually sell. ``qty_reserved`` is maintained by open SO/invoice
        # lines and audited by ``reconcile_qty_reserved``.
        try:
            from db import get_stock_state
            _stock = get_stock_state(int(product.get("id") or 0))
        except Exception:
            _stock = {
                "on_hand": qty_on_hand, "reserved": 0,
                "available": qty_on_hand, "reservations": [],
            }
        qty_reserved = int(_stock.get("reserved") or 0)
        qty_available = int(_stock.get("available") or max(0, qty_on_hand - qty_reserved))

        if qty_available <= 0:
            qty_color = "#c62828"
            qty_status = "🔴 NO STOCK AVAILABLE"
            qty_bg = "#ffebee"
        elif min_qty > 0 and qty_available < min_qty:
            qty_color = "#e65100"
            qty_status = "🟡 BELOW MIN"
            qty_bg = "#fff3e0"
        elif qty_available <= 5:
            qty_color = "#f57c00"
            qty_status = "🟡 LOW STOCK"
            qty_bg = "#fff3e0"
        else:
            qty_color = "#2e7d32"
            qty_status = "🟢 HEALTHY"
            qty_bg = "#e8f5e9"

        # Big Available number on top.
        self._qty_label = QLabel(
            f"<div style='text-align:center; background:{qty_bg}; "
            f"border-radius:8px; padding:10px;'>"
            f"<span style='font-size:36pt; font-weight:bold; color:{qty_color};'>{qty_available}</span>"
            f"</div>"
        )
        self._qty_label.setTextFormat(Qt.TextFormat.RichText)
        self._qty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qty_label.setToolTip(
            f"Available = On Hand ({qty_on_hand}) − Reserved ({qty_reserved})"
        )

        self._qty_status = QLabel(qty_status)
        self._qty_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qty_status.setStyleSheet(f"color:{qty_color}; font-weight:bold; font-size:10pt;")

        # On Hand / Reserved breakdown with hover-detail of which docs hold
        # the reservation.
        _res_tip_parts: list[str] = []
        for _r in _stock.get("reservations") or []:
            _res_tip_parts.append(
                f"{(_r.get('doc_type') or '').upper()} "
                f"{_r.get('doc_number') or ''} "
                f"({_r.get('status') or ''}): {_r.get('quantity') or 0}"
            )
        _res_tip = "\n".join(_res_tip_parts) if _res_tip_parts else "No open reservations"

        breakdown = QLabel(
            f"<div style='text-align:center; font-size:9pt; color:#444;'>"
            f"On Hand: <b>{qty_on_hand}</b> "
            f"&nbsp;|&nbsp; Reserved: <b style='color:#b91c1c;'>{qty_reserved}</b>"
            f"</div>"
        )
        breakdown.setTextFormat(Qt.TextFormat.RichText)
        breakdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        breakdown.setToolTip(_res_tip)

        # Min / Max info row
        minmax_text = []
        if min_qty > 0:
            minmax_text.append(f"Min: {min_qty}")
        if max_qty > 0:
            minmax_text.append(f"Max: {max_qty}")
        minmax_label = QLabel("&nbsp;&nbsp;|&nbsp;&nbsp;".join(minmax_text) if minmax_text else "")
        minmax_label.setTextFormat(Qt.TextFormat.RichText)
        minmax_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        minmax_label.setStyleSheet("color:#666; font-size:9pt;")

        # Location summary (compact)
        loc_parts = []
        for loc in self._locations[:3]:
            code = str(loc.get("code", "") or "")
            lqty = int(loc.get("quantity", 0) or 0)
            if code:
                loc_parts.append(f"<b>{code}</b>: {lqty}")
        if len(self._locations) > 3:
            loc_parts.append(f"+{len(self._locations) - 3} more")
        loc_summary = QLabel(", ".join(loc_parts) if loc_parts else "<i>No location assigned</i>")
        loc_summary.setTextFormat(Qt.TextFormat.RichText)
        loc_summary.setStyleSheet("color:#666; font-size:9pt;")
        loc_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        loc_summary.setWordWrap(True)

        # Action buttons with clear labels + tooltips
        receive_btn = QPushButton("📥 Receive Inventory")
        receive_btn.setToolTip("Add inventory from PO or manual receive")
        receive_btn.clicked.connect(self._on_receive)
        receive_btn.setStyleSheet(
            "QPushButton { padding: 5px 8px; border-radius: 3px; }"
        )

        adjust_btn = QPushButton("± Adjust Stock")
        adjust_btn.setToolTip("Manual stock correction (damage, count, etc.)")
        adjust_btn.clicked.connect(lambda: self._open_adjust_dialog("adjust"))
        adjust_btn.setStyleSheet(
            "QPushButton { padding: 5px 8px; border-radius: 3px; }"
        )

        action_row = QHBoxLayout()
        action_row.addWidget(receive_btn)
        action_row.addWidget(adjust_btn)

        qty_layout = QVBoxLayout()
        qty_layout.addWidget(
            QLabel("<b style='font-size:10pt;'>Available</b>"),
            alignment=Qt.AlignmentFlag.AlignCenter,
        )
        qty_layout.addWidget(self._qty_label)
        qty_layout.addWidget(self._qty_status)
        qty_layout.addWidget(breakdown)
        qty_layout.addWidget(minmax_label)
        qty_layout.addSpacing(4)
        qty_layout.addWidget(loc_summary)
        qty_layout.addStretch()
        qty_layout.addLayout(action_row)
        qty_frame.setLayout(qty_layout)

        top_row.addWidget(qty_frame, 2)
        layout.addLayout(top_row)

        # ── CROSS-REFERENCES (inline, compact) ──
        if self._xrefs:
            xref_box = QGroupBox(f"Cross-References ({len(self._xrefs)})")
            xref_box.setStyleSheet(
                "QGroupBox { font-weight:bold; border:1px solid #ccc; border-radius:4px; "
                "margin-top:8px; padding-top:14px; } "
                "QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }"
            )
            xref_layout = QHBoxLayout(xref_box)
            xref_layout.setContentsMargins(8, 4, 8, 4)
            # Show as inline chips
            for xr in self._xrefs:
                number = str(xr.get("number", "") or "")
                ptype = str(xr.get("type", "") or "")
                chip = QLabel(f"<span style='background:#e8e8e8; padding:2px 6px; "
                              f"border-radius:3px; font-size:9pt;'>"
                              f"{number} <span style='color:#888;'>({ptype})</span></span>")
                chip.setTextFormat(Qt.TextFormat.RichText)
                xref_layout.addWidget(chip)
            xref_layout.addStretch()
            layout.addWidget(xref_box)

        # ── RECENT ACTIVITY — transactions preview ──
        txn_header_row = QHBoxLayout()
        txn_label = QLabel(f"<b>Recent Activity</b>  ({len(self._transactions)} total)")
        txn_label.setTextFormat(Qt.TextFormat.RichText)
        txn_header_row.addWidget(txn_label)
        txn_header_row.addStretch()

        self._expand_txn_btn = QPushButton("▸ Show All")
        self._expand_txn_btn.setFlat(True)
        self._expand_txn_btn.setStyleSheet("color:#4a6741; font-weight:bold; font-size:9pt;")
        self._expand_txn_btn.clicked.connect(self._toggle_txn_expand)
        if len(self._transactions) > 5:
            txn_header_row.addWidget(self._expand_txn_btn)
        else:
            self._expand_txn_btn.hide()

        layout.addLayout(txn_header_row)

        # Transaction table — shows 5 by default
        self._txn_table = QTableWidget(0, 5)
        self._txn_table.setHorizontalHeaderLabels(["Date", "Type", "Change", "Balance", "Reference"])
        self._txn_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._txn_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._txn_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._txn_table.setMaximumHeight(180)
        self._txn_table.verticalHeader().setVisible(False)
        self._txn_table.cellDoubleClicked.connect(self._on_txn_double_click)
        self._populate_transactions_table(limit=5)
        layout.addWidget(self._txn_table)

        # ── LOCATIONS — always visible with clear totals ──
        total_allocated = sum(int(loc.get("quantity", 0) or 0) for loc in self._locations)
        loc_header = f"Locations ({len(self._locations)}) — {total_allocated} allocated"
        loc_box = QGroupBox(loc_header)
        loc_box.setStyleSheet(
            "QGroupBox { font-weight:bold; border:1px solid #ccc; border-radius:4px; "
            "margin-top:8px; padding-top:14px; } "
            "QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }"
        )
        loc_inner = QVBoxLayout(loc_box)

        self._loc_table = QTableWidget(0, 4)
        self._loc_table.setHorizontalHeaderLabels(["Code", "Name", "Qty", "Primary"])
        self._loc_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._loc_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._loc_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._loc_table.verticalHeader().setVisible(False)
        self._loc_table.setMaximumHeight(120)
        self._loc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._loc_table.itemSelectionChanged.connect(self._on_location_selection_changed)
        self._populate_locations_table()
        loc_inner.addWidget(self._loc_table)

        loc_btn_row = QHBoxLayout()
        self._loc_combo = QComboBox()
        self._loc_combo.setMinimumWidth(150)
        self._refresh_available_locations()
        loc_btn_row.addWidget(QLabel("Add to:"))
        loc_btn_row.addWidget(self._loc_combo)
        loc_btn_row.addWidget(QLabel("Qty:"))
        self._loc_qty_spin = QSpinBox()
        self._loc_qty_spin.setRange(0, 99999)
        self._loc_qty_spin.setValue(0)
        loc_btn_row.addWidget(self._loc_qty_spin)
        add_loc_btn = QPushButton("➕ Assign")
        add_loc_btn.setToolTip("Assign product to this location")
        add_loc_btn.clicked.connect(self._on_add_to_location)
        loc_btn_row.addWidget(add_loc_btn)
        self._remove_loc_btn = QPushButton("✖ Remove")
        self._remove_loc_btn.setEnabled(False)
        self._remove_loc_btn.setToolTip("Remove product from selected location")
        self._remove_loc_btn.clicked.connect(self._on_remove_location)
        loc_btn_row.addWidget(self._remove_loc_btn)
        loc_btn_row.addStretch()
        loc_inner.addLayout(loc_btn_row)

        layout.addWidget(loc_box)

        # ── SERIALS — only show if product has serials or requires them ──
        requires_serial = bool(product.get("requires_serial", 0))
        if requires_serial or self._serials:
            serial_box = QGroupBox(f"Serial Numbers ({len(self._serials)})")
            serial_box.setStyleSheet(
                "QGroupBox { font-weight:bold; border:1px solid #ccc; border-radius:4px; "
                "margin-top:8px; padding-top:14px; } "
                "QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }"
            )
            serial_inner = QVBoxLayout(serial_box)

            available = self._serial_counts.get("available", 0)
            sold = self._serial_counts.get("sold", 0)
            summary = QLabel(
                f"<b>Available:</b> <span style='color:green'>{available}</span>  |  "
                f"<b>Sold:</b> <span style='color:blue'>{sold}</span>"
            )
            summary.setTextFormat(Qt.TextFormat.RichText)
            serial_inner.addWidget(summary)

            self._serial_table = QTableWidget(0, 4)
            self._serial_table.setHorizontalHeaderLabels(["Serial #", "Status", "Location", "Received"])
            self._serial_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self._serial_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self._serial_table.verticalHeader().setVisible(False)
            self._serial_table.setMaximumHeight(140)
            self._populate_serials_table()
            serial_inner.addWidget(self._serial_table)

            add_serial_btn = QPushButton("Add Serial Numbers")
            add_serial_btn.clicked.connect(self._on_add_serial)
            serial_inner.addWidget(add_serial_btn)

            layout.addWidget(serial_box)

        layout.addStretch()

    def _rebuild_ui(self) -> None:
        """Rebuild the scrollable page after data reload."""
        # Clear existing page content
        while self._page_layout.count():
            child = self._page_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                self._clear_layout(child.layout())

        self._build_page_content(self._product or {})

    def _clear_layout(self, layout) -> None:
        """Recursively clear a layout."""
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                self._clear_layout(child.layout())

    # ==================================================================
    # TRANSACTIONS
    # ==================================================================
    def _populate_transactions_table(self, limit: int | None = None) -> None:
        """Populate recent transactions, optionally limited."""
        txns = self._transactions[:limit] if limit else self._transactions
        self._txn_table.setRowCount(len(txns))

        type_icons = {"receive": "📥", "sale": "📤", "adjust": "±", "return": "↩️"}

        for row_idx, txn in enumerate(txns):
            created = str(txn.get("created_at", "") or "")[:16]
            txn_type = str(txn.get("transaction_type", "") or "")
            type_display = f"{type_icons.get(txn_type, '')} {txn_type}".strip()
            change = int(txn.get("quantity_change", 0) or 0)
            change_str = f"+{change}" if change > 0 else str(change)
            after = int(txn.get("quantity_after", 0) or 0)
            reference = str(txn.get("reference", "") or "")
            notes = str(txn.get("notes", "") or "")
            ref_display = f"{reference} {notes}".strip() if reference or notes else ""

            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._txn_table.setItem(row_idx, 0, item(created))
            self._txn_table.setItem(row_idx, 1, item(type_display))

            change_item = item(change_str)
            if change > 0:
                change_item.setForeground(QColor("green"))
            elif change < 0:
                change_item.setForeground(QColor("red"))
            self._txn_table.setItem(row_idx, 2, change_item)
            self._txn_table.setItem(row_idx, 3, item(str(after)))
            self._txn_table.setItem(row_idx, 4, item(ref_display))

        self._txn_table.resizeColumnsToContents()

    def _toggle_txn_expand(self) -> None:
        """Toggle between showing 5 recent vs all transactions."""
        self._txn_expanded = not self._txn_expanded
        if self._txn_expanded:
            self._populate_transactions_table(limit=None)
            self._txn_table.setMaximumHeight(400)
            self._expand_txn_btn.setText("▾ Show Less")
        else:
            self._populate_transactions_table(limit=5)
            self._txn_table.setMaximumHeight(180)
            self._expand_txn_btn.setText("▸ Show All")

    def _on_txn_double_click(self, row: int, column: int) -> None:
        """Double-click a transaction to open the related invoice."""
        txns = self._transactions if self._txn_expanded else self._transactions[:5]
        if row < 0 or row >= len(txns):
            return
        txn = txns[row]
        reference = str(txn.get("reference", "") or "").strip()
        txn_type = str(txn.get("transaction_type", "") or "")

        if txn_type == "sale" and reference:
            try:
                from db import get_invoice_by_number
                inv = get_invoice_by_number(reference)
                if inv:
                    from .invoice_dialog import InvoiceDialog
                    dlg = InvoiceDialog(invoice_id=inv.get("id"), parent=self)
                    dlg.exec()
                else:
                    QMessageBox.information(self, "Transaction", f"Invoice '{reference}' not found.")
            except ImportError:
                QMessageBox.information(self, "Transaction", f"Reference: {reference}")
        elif reference:
            QMessageBox.information(self, "Transaction", f"Reference: {reference}")

    # ==================================================================
    # INVENTORY ACTIONS
    # ==================================================================
    def _on_receive(self) -> None:
        """Receive inventory."""
        reply = QMessageBox.question(
            self, "Receive Inventory",
            "Is this being received against a Purchase Order?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        if reply == QMessageBox.StandardButton.Yes:
            QMessageBox.information(
                self, "Purchase Orders",
                "Use the Purchase Orders screen to receive inventory against a PO.\n\n"
                "Navigate to: Purchasing → Purchase Orders"
            )
            return
        self._open_adjust_dialog("receive")

    def _open_adjust_dialog(self, preset_type: str = "receive") -> None:
        """Open the quantity adjustment dialog."""
        dlg = AdjustQtyDialog(self._sku, parent=self)
        idx = dlg._txn_type.findData(preset_type)
        if idx >= 0:
            dlg._txn_type.setCurrentIndex(idx)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh_after_adjustment()

    def _refresh_after_adjustment(self) -> None:
        """Refresh displays after a quantity adjustment."""
        self._load_data()
        self._rebuild_ui()

    # ==================================================================
    # LOCATIONS
    # ==================================================================
    def _populate_locations_table(self) -> None:
        """Populate the locations table."""
        self._loc_table.setRowCount(len(self._locations))
        for row_idx, loc in enumerate(self._locations):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._loc_table.setItem(row_idx, 0, item(str(loc.get("code", "") or "")))
            self._loc_table.setItem(row_idx, 1, item(str(loc.get("name", "") or "")))
            qty_item = QTableWidgetItem()
            qty_item.setData(Qt.ItemDataRole.DisplayRole, int(loc.get("quantity", 0) or 0))
            self._loc_table.setItem(row_idx, 2, qty_item)
            primary_text = "✓" if loc.get("is_primary") else ""
            self._loc_table.setItem(row_idx, 3, item(primary_text))
        self._loc_table.resizeColumnsToContents()

    def _refresh_available_locations(self) -> None:
        """Refresh the available locations dropdown."""
        self._loc_combo.clear()
        try:
            all_locs = list_locations(active_only=True)
            assigned_ids = {loc.get("id") for loc in self._locations}
            for loc in all_locs:
                if loc.get("id") not in assigned_ids:
                    code = str(loc.get("code", ""))
                    name = str(loc.get("name", ""))
                    display = f"{code} - {name}" if name else code
                    self._loc_combo.addItem(display, loc.get("id"))
        except Exception:
            pass

    def _on_location_selection_changed(self) -> None:
        """Enable/disable remove button based on selection."""
        has_selection = len(self._loc_table.selectionModel().selectedRows()) > 0
        self._remove_loc_btn.setEnabled(has_selection)

    def _on_add_to_location(self) -> None:
        """Add product to selected location."""
        location_id = self._loc_combo.currentData()
        if not location_id or not self._product_id:
            return
        qty = self._loc_qty_spin.value()
        if qty <= 0:
            QMessageBox.warning(self, "Error", "Quantity must be greater than 0.")
            return
        qty_on_hand = int((self._product or {}).get("qty_on_hand", 0) or 0)
        already_allocated = sum(int(loc.get("quantity", 0) or 0) for loc in self._locations)
        remaining = qty_on_hand - already_allocated
        if qty > remaining:
            QMessageBox.warning(
                self, "Allocation Error",
                f"Cannot allocate {qty} units.\n\n"
                f"Qty on hand: {qty_on_hand}\n"
                f"Already allocated: {already_allocated}\n"
                f"Available to allocate: {max(0, remaining)}"
            )
            return
        is_primary = len(self._locations) == 0
        try:
            assign_product_to_location(
                product_id=self._product_id,
                location_id=location_id,
                quantity=qty,
                is_primary=is_primary
            )
            self._load_data()
            self._rebuild_ui()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to assign location: {e}")

    def _on_remove_location(self) -> None:
        """Remove product from selected location."""
        row = self._loc_table.currentRow()
        if row < 0 or row >= len(self._locations) or not self._product_id:
            return
        loc = self._locations[row]
        location_id = loc.get("id")
        code = loc.get("code", "")
        reply = QMessageBox.question(
            self, "Confirm Remove",
            f"Remove product from location '{code}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                remove_product_from_location(self._product_id, location_id)
                self._load_data()
                self._rebuild_ui()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to remove location: {e}")

    # ==================================================================
    # SERIALS
    # ==================================================================
    def _populate_serials_table(self) -> None:
        """Populate the serials table."""
        self._serial_table.setRowCount(len(self._serials))
        status_colors = {
            "available": QColor("#2e7d32"),
            "sold": QColor("#1565c0"),
            "reserved": QColor("#f57c00"),
            "defective": QColor("#c62828"),
        }
        for row_idx, s in enumerate(self._serials):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._serial_table.setItem(row_idx, 0, item(s.get("serial_number", "")))
            status = str(s.get("status", ""))
            status_item = item(status.title())
            if status in status_colors:
                status_item.setForeground(status_colors[status])
            self._serial_table.setItem(row_idx, 1, status_item)
            self._serial_table.setItem(row_idx, 2, item(s.get("location_code", "")))
            self._serial_table.setItem(row_idx, 3, item(str(s.get("received_date", "") or "")[:10]))
        self._serial_table.resizeColumnsToContents()

    def _on_add_serial(self) -> None:
        """Open serial dialog to add serials for this product."""
        from .serial_dialog import SerialDialog
        dlg = SerialDialog(product_id=self._product_id, parent=self)
        if dlg.exec():
            self._load_data()
            self._rebuild_ui()
