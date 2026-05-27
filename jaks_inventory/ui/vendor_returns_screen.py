"""Vendor Returns / Vendor Cores (RGA) Screen.

This screen handles items and cores being shipped TO vendors for credit:
warranty defects, damaged shipments, wrong-part returns, and — new in
migration 107 — vendor core obligations that JAK's owes back to vendors.

NOTE (Phase 10): This screen is NOT for issuing customer core credits or
refunds. Customer core return and refund processing stays tied to the
invoice / customer core workflow. This screen only handles what JAK's
sends back to vendors and the credit JAK's expects in return.

Status flow for vendor cores
----------------------------
pending → added_to_rga → rga_created → shipped → vendor_received
        → credit_pending → credited  (terminal: credited | rejected |
                                      written_off | voided)
"""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QSplitter, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
    QWidget,
)

from db import (
    create_vendor_return, list_vendor_returns, get_vendor_return,
    update_vendor_return, add_vendor_return_line, get_vendor_return_lines,
    remove_vendor_return_line,
    get_all_vendors, get_product_by_sku,
    # Migration 107
    attach_vendor_cores_to_rga, get_vendor_core_kpis,
    transition_rga_status, get_vendor_return_audit,
    # Migration 112+
    list_outstanding_vendor_obligations, delete_vendor_return,
)

from jaks_inventory.ui.eligible_cores_picker import EligibleCoresPickerDialog


# Return types (Phase 2)
RETURN_TYPES = [
    ("warranty", "Warranty Return"),
    ("damaged", "Damaged Return"),
    ("wrong_part", "Wrong Part"),
    ("vendor_core", "Vendor Core Return"),
    ("other", "Other"),
]


# ─── Small widgets ───────────────────────────────────────────────────


class _KPICard(QFrame):
    def __init__(self, title: str, color: str = "#3d4a38", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"background: {color}; border-radius: 6px; padding: 8px;")
        self._title = QLabel(title)
        self._title.setStyleSheet("color: white; font-size: 10pt;")
        self._value = QLabel("0")
        self._value.setStyleSheet("color: white; font-size: 22pt; font-weight: bold;")
        lay = QVBoxLayout()
        lay.addWidget(self._title)
        lay.addWidget(self._value)
        self.setLayout(lay)

    def set_value(self, val: str):
        self._value.setText(str(val))


class _CreditReceivedDialog(QDialog):
    """Phase 7: capture vendor credit details + optional QBO sync flag."""

    def __init__(self, parent=None, expected: float = 0.0):
        super().__init__(parent)
        self.setWindowTitle("Mark Credit Received")
        self.resize(420, 280)
        form = QFormLayout()
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 1_000_000)
        self.amount.setDecimals(2)
        self.amount.setPrefix("$")
        self.amount.setValue(float(expected or 0))
        form.addRow("Credit Amount:", self.amount)

        self.credit_number = QLineEdit()
        self.credit_number.setPlaceholderText("Vendor credit / reference #")
        form.addRow("Credit #:", self.credit_number)

        self.credit_date = QDateEdit()
        self.credit_date.setCalendarPopup(True)
        self.credit_date.setDate(QDate.currentDate())
        form.addRow("Credit Date:", self.credit_date)

        self.notes = QTextEdit()
        self.notes.setMaximumHeight(70)
        form.addRow("Notes:", self.notes)

        self.qbo_sync = QComboBox()
        self.qbo_sync.addItems(["Sync vendor credit to QBO", "Skip QBO sync"])
        form.addRow("QBO:", self.qbo_sync)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def data(self) -> dict:
        return {
            "credit_amount": float(self.amount.value()),
            "vendor_credit_number": self.credit_number.text().strip() or None,
            "credit_received_date": self.credit_date.date().toString("yyyy-MM-dd"),
            "notes": self.notes.toPlainText().strip() or None,
            "qbo_sync": self.qbo_sync.currentIndex() == 0,
        }


# ─── Screen ──────────────────────────────────────────────────────────


class VendorReturnsScreen(QWidget):
    """Combined Vendor Returns + Vendor Core (RGA) processing screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._returns: list[dict] = []
        self._obligations: list[dict] = []
        self._current_return_id: int | None = None
        self._selected_vendor_id: int | None = None  # None = All Vendors
        self._build_ui()
        self._refresh()

    # ── Layout ─────────────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)

        header = QLabel("🔄 Vendor Returns / Vendor Cores (RGA)")
        header.setStyleSheet("font-size: 16pt; font-weight: bold; padding: 8px;")
        layout.addWidget(header)

        disclaimer = QLabel(
            "ℹ Vendor-side only. This screen is NOT for issuing customer "
            "core credits or refunds — those stay on the invoice / "
            "customer core workflow."
        )
        disclaimer.setWordWrap(True)
        disclaimer.setStyleSheet(
            "color: #555; background: #fff8e1; border: 1px solid #e0c97a; "
            "padding: 6px; border-radius: 4px;"
        )
        layout.addWidget(disclaimer)

        # Vendor selector (default: All Vendors overview)
        vendor_row = QHBoxLayout()
        vendor_row.addWidget(QLabel("Vendor:"))
        self._vendor_combo = QComboBox()
        self._vendor_combo.setMinimumWidth(280)
        self._vendor_combo.addItem("— All Vendors (overview) —", None)
        try:
            for v in get_all_vendors():
                self._vendor_combo.addItem(v.get("name", ""), v.get("id"))
        except Exception:
            pass
        self._vendor_combo.currentIndexChanged.connect(
            self._on_vendor_changed
        )
        vendor_row.addWidget(self._vendor_combo)
        self._vendor_scope_label = QLabel(
            "Showing aggregate stats across all vendors."
        )
        self._vendor_scope_label.setStyleSheet("color: #666; font-style: italic;")
        vendor_row.addWidget(self._vendor_scope_label)
        vendor_row.addStretch()
        layout.addLayout(vendor_row)

        # KPIs (Phase 8 — vendor core focus)
        kpi_row = QHBoxLayout()
        self._kpi_awaiting = _KPICard("Awaiting Customer Core", "#6b4f1d")
        self._kpi_pending = _KPICard("Ready for RGA")
        self._kpi_shipped = _KPICard("Shipped to Vendor")
        self._kpi_received = _KPICard("Vendor Received")
        self._kpi_credited = _KPICard("Credited", "#2d5a2d")
        self._kpi_outstanding = _KPICard("Outstanding Vendor Core $", "#8B4513")
        for k in (self._kpi_awaiting, self._kpi_pending, self._kpi_shipped,
                  self._kpi_received, self._kpi_credited, self._kpi_outstanding):
            kpi_row.addWidget(k)
        layout.addLayout(kpi_row)

        # Toolbar
        toolbar = QHBoxLayout()
        new_btn = QPushButton("+ New Vendor Return")
        new_btn.clicked.connect(self._new_return)
        toolbar.addWidget(new_btn)
        toolbar.addWidget(QLabel("Status:"))
        self._status_filter = QComboBox()
        self._status_filter.addItems([
            "All", "pending", "rga_created", "shipped", "received",
            "credit_pending", "credited", "rejected", "written_off",
            "cancelled",
        ])
        self._status_filter.currentTextChanged.connect(lambda _: self._refresh())
        toolbar.addWidget(self._status_filter)
        toolbar.addWidget(QLabel("Type:"))
        self._type_filter = QComboBox()
        self._type_filter.addItem("All", None)
        for code, label in RETURN_TYPES:
            self._type_filter.addItem(label, code)
        self._type_filter.currentIndexChanged.connect(lambda _: self._refresh())
        toolbar.addWidget(self._type_filter)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Returns table
        self._returns_table = QTableWidget()
        self._returns_table.setColumnCount(8)
        self._returns_table.setHorizontalHeaderLabels([
            "RGA #", "Type", "Vendor", "Status", "Reason",
            "Credit", "Tracking", "Date",
        ])
        self._returns_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._returns_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._returns_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._returns_table.currentCellChanged.connect(
            lambda row, _c, _pr, _pc: self._on_return_selected(row)
        )

        # Outstanding obligations panel (NEW). Shows vendor cores JAK's
        # owes back — both 'awaiting_customer_core' (we received the PO
        # but customer hasn't returned a core yet) and 'pending' (customer
        # returned a core, ready for RGA).
        obl_panel = QWidget()
        obl_lay = QVBoxLayout(obl_panel)
        obl_lay.setContentsMargins(0, 0, 0, 0)
        obl_header = QHBoxLayout()
        self._obl_title = QLabel("Outstanding Vendor Core Obligations")
        self._obl_title.setStyleSheet("font-weight: bold; padding: 4px;")
        obl_header.addWidget(self._obl_title)
        obl_header.addStretch()
        create_rga_from_obl_btn = QPushButton(
            "→ Create RGA from Selected Obligations"
        )
        create_rga_from_obl_btn.setStyleSheet(
            "background: #2d5a2d; color: white; font-weight: bold;"
        )
        create_rga_from_obl_btn.clicked.connect(
            self._create_rga_from_obligations
        )
        obl_header.addWidget(create_rga_from_obl_btn)
        obl_lay.addLayout(obl_header)

        self._obligations_table = QTableWidget()
        self._obligations_table.setColumnCount(9)
        self._obligations_table.setHorizontalHeaderLabels([
            "Status", "Vendor", "SKU", "Product", "Qty",
            "Core $", "Src PO", "Src Invoice", "Source Customer",
        ])
        self._obligations_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._obligations_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._obligations_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._obligations_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        obl_lay.addWidget(self._obligations_table)

        # Splitter: obligations | returns | (lines + audit)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(obl_panel)
        splitter.addWidget(self._returns_table)

        bottom = QWidget()
        bottom_lay = QVBoxLayout(bottom)
        bottom_lay.setContentsMargins(0, 0, 0, 0)

        # Line items header
        lines_header = QHBoxLayout()
        lines_header.addWidget(QLabel("Return Line Items"))
        self._sku_entry = QLineEdit()
        self._sku_entry.setPlaceholderText("SKU")
        self._sku_entry.setMaximumWidth(150)
        self._qty_spin = QSpinBox()
        self._qty_spin.setRange(1, 9999)
        self._qty_spin.setMaximumWidth(80)
        add_line_btn = QPushButton("+ Add SKU")
        add_line_btn.clicked.connect(self._add_line)
        self._add_core_btn = QPushButton("+ Add Vendor Core")
        self._add_core_btn.setStyleSheet(
            "background: #2d5a2d; color: white; font-weight: bold;"
        )
        self._add_core_btn.clicked.connect(self._add_vendor_cores)
        remove_line_btn = QPushButton("- Remove")
        remove_line_btn.clicked.connect(self._remove_line)
        lines_header.addWidget(self._sku_entry)
        lines_header.addWidget(self._qty_spin)
        lines_header.addWidget(add_line_btn)
        lines_header.addWidget(self._add_core_btn)
        lines_header.addWidget(remove_line_btn)
        lines_header.addStretch()
        bottom_lay.addLayout(lines_header)

        # Lines table (Phase 4 column set)
        self._lines_table = QTableWidget()
        self._lines_table.setColumnCount(11)
        self._lines_table.setHorizontalHeaderLabels([
            "SKU", "Product", "Qty", "Vendor Core Deposit",
            "Expected Credit", "Condition", "Src Cust Core",
            "Src Invoice", "Src PO", "VCR ID", "Notes",
        ])
        self._lines_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._lines_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._lines_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._lines_table.setMaximumHeight(200)
        bottom_lay.addWidget(self._lines_table)

        # Actions (Phase 6)
        actions = QHBoxLayout()
        self._create_rga_btn = QPushButton("📋 Create RGA")
        self._create_rga_btn.clicked.connect(
            lambda: self._transition("rga_created")
        )
        self._ship_btn = QPushButton("📦 Mark Shipped")
        self._ship_btn.clicked.connect(self._mark_shipped)
        self._tracking_btn = QPushButton("🔢 Add Tracking #")
        self._tracking_btn.clicked.connect(self._add_tracking_only)
        self._receive_btn = QPushButton("✅ Vendor Received")
        self._receive_btn.clicked.connect(
            lambda: self._transition("received")
        )
        self._credit_btn = QPushButton("💰 Mark Credit Received")
        self._credit_btn.clicked.connect(self._mark_credit_received)
        self._reject_btn = QPushButton("⛔ Mark Rejected")
        self._reject_btn.clicked.connect(self._mark_rejected)
        self._writeoff_btn = QPushButton("📉 Write Off")
        self._writeoff_btn.clicked.connect(self._write_off)
        self._slip_btn = QPushButton("🖨 Print Packing Slip")
        self._slip_btn.clicked.connect(self._print_packing_slip)
        self._delete_rga_btn = QPushButton("🗑 Delete RGA")
        self._delete_rga_btn.setStyleSheet(
            "background: #8b2d2d; color: white;"
        )
        self._delete_rga_btn.clicked.connect(self._delete_current_rga)
        for b in (
            self._create_rga_btn, self._ship_btn, self._tracking_btn,
            self._receive_btn, self._credit_btn, self._reject_btn,
            self._writeoff_btn, self._slip_btn, self._delete_rga_btn,
        ):
            actions.addWidget(b)
        actions.addStretch()
        bottom_lay.addLayout(actions)

        # Audit trail (Phase 9)
        bottom_lay.addWidget(QLabel("Audit Trail"))
        self._audit_table = QTableWidget()
        self._audit_table.setColumnCount(7)
        self._audit_table.setHorizontalHeaderLabels([
            "When", "Event", "Old → New", "Tracking", "Credit",
            "User", "Notes",
        ])
        self._audit_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._audit_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._audit_table.setMaximumHeight(150)
        bottom_lay.addWidget(self._audit_table)

        splitter.addWidget(bottom)
        splitter.setSizes([180, 220, 600])
        layout.addWidget(splitter, 1)

    # ── Vendor selector ────────────────────────────────────────────
    def _on_vendor_changed(self, _idx: int):
        self._selected_vendor_id = self._vendor_combo.currentData()
        if self._selected_vendor_id is None:
            self._vendor_scope_label.setText(
                "Showing aggregate stats across all vendors."
            )
            self._obl_title.setText(
                "Outstanding Vendor Core Obligations (all vendors)"
            )
        else:
            name = self._vendor_combo.currentText()
            self._vendor_scope_label.setText(
                f"Filtered to: {name}"
            )
            self._obl_title.setText(
                f"Outstanding Vendor Core Obligations — {name}"
            )
        self._current_return_id = None
        self._lines_table.setRowCount(0)
        self._audit_table.setRowCount(0)
        self._refresh()

    # ── Refresh / Load ─────────────────────────────────────────────
    def _refresh(self):
        self._load_kpis()
        self._load_obligations()
        self._load_returns()
        if self._current_return_id:
            self._load_lines()
            self._load_audit()

    def _load_kpis(self):
        try:
            s = get_vendor_core_kpis(vendor_id=self._selected_vendor_id)
            self._kpi_awaiting.set_value(str(s.get("awaiting_customer_core", 0)))
            self._kpi_pending.set_value(str(s.get("pending_vendor_cores", 0)))
            self._kpi_shipped.set_value(str(s.get("shipped_cores", 0)))
            self._kpi_received.set_value(str(s.get("vendor_received", 0)))
            self._kpi_credited.set_value(str(s.get("credited", 0)))
            self._kpi_outstanding.set_value(
                f"${float(s.get('outstanding_vendor_core_amount', 0)):,.2f}"
            )
        except Exception:
            pass

    def _load_obligations(self):
        try:
            rows = list_outstanding_vendor_obligations(
                vendor_id=self._selected_vendor_id
            )
        except Exception:
            rows = []
        self._obligations = rows
        self._obligations_table.setRowCount(len(rows))
        for i, o in enumerate(rows):
            status = o.get("status") or ""
            status_label = {
                "awaiting_customer_core": "⏳ Awaiting Customer",
                "pending": "✅ Ready for RGA",
            }.get(status, status)
            qty = int(o.get("quantity") or 1)
            amt = float(o.get("core_amount") or 0) * qty
            cells = [
                status_label,
                str(o.get("vendor_name") or ""),
                str(o.get("sku") or ""),
                str(o.get("product_title") or ""),
                str(qty),
                f"${amt:,.2f}",
                str(o.get("po_number") or "—"),
                str(o.get("source_invoice_number") or "—"),
                str(o.get("source_customer_name") or "—"),
            ]
            for c, v in enumerate(cells):
                self._obligations_table.setItem(i, c, QTableWidgetItem(v))

    def _load_returns(self):
        status = self._status_filter.currentText()
        status_filter = None if status == "All" else status
        type_filter = self._type_filter.currentData()
        try:
            rows = list_vendor_returns(
                status=status_filter,
                vendor_id=self._selected_vendor_id,
            )
        except Exception:
            rows = []
        if type_filter:
            rows = [r for r in rows if (r.get("return_type") or "general") == type_filter]
        self._returns = rows
        self._returns_table.setRowCount(len(rows))
        type_labels = dict(RETURN_TYPES)
        for i, r in enumerate(rows):
            rtype = r.get("return_type") or "general"
            rtype_label = type_labels.get(rtype, rtype.title())
            credit = float(r.get("credit_amount", 0) or 0)
            created = str(r.get("created_at") or "")[:10]
            cells = [
                str(r.get("rga_number", "")),
                rtype_label,
                str(r.get("vendor_name", "") or ""),
                str(r.get("status", "")),
                str(r.get("reason", "") or ""),
                f"${credit:,.2f}",
                str(r.get("tracking_number", "") or ""),
                created,
            ]
            for c, v in enumerate(cells):
                self._returns_table.setItem(i, c, QTableWidgetItem(v))

    def _on_return_selected(self, row: int):
        if row < 0 or row >= len(self._returns):
            self._current_return_id = None
            self._lines_table.setRowCount(0)
            self._audit_table.setRowCount(0)
            return
        self._current_return_id = self._returns[row].get("id")
        self._load_lines()
        self._load_audit()

    def _load_lines(self):
        if not self._current_return_id:
            return
        try:
            lines = get_vendor_return_lines(self._current_return_id)
        except Exception:
            lines = []
        self._lines_table.setRowCount(len(lines))
        for i, ln in enumerate(lines):
            qty = int(ln.get("quantity", 1) or 1)
            deposit = float(ln.get("vendor_core_deposit") or ln.get("unit_cost") or 0)
            expected = float(ln.get("expected_credit") or ln.get("line_total") or 0)
            values = [
                ln.get("sku", "") or "",
                ln.get("product_title", "") or "",
                qty,
                f"${deposit:,.2f}",
                f"${expected:,.2f}",
                ln.get("condition", "") or "",
                ln.get("source_customer_core_id") or "",
                ln.get("source_invoice_id") or "",
                ln.get("source_po_id") or "",
                ln.get("vendor_core_obligation_id")
                    or ln.get("vendor_core_return_id") or "",
                ln.get("notes", "") or "",
            ]
            for c, v in enumerate(values):
                self._lines_table.setItem(i, c, QTableWidgetItem(str(v)))

    def _load_audit(self):
        if not self._current_return_id:
            return
        try:
            audit = get_vendor_return_audit(rga_id=self._current_return_id)
        except Exception:
            audit = []
        self._audit_table.setRowCount(len(audit))
        for i, a in enumerate(audit):
            when = str(a.get("created_at") or "")[:19]
            transition = ""
            if a.get("old_status") or a.get("new_status"):
                transition = f"{a.get('old_status') or '—'} → {a.get('new_status') or '—'}"
            credit = a.get("credit_amount")
            credit_s = f"${float(credit):,.2f}" if credit is not None else ""
            cells = [
                when,
                a.get("event_type") or "",
                transition,
                a.get("tracking_number") or "",
                credit_s,
                a.get("user") or "",
                a.get("notes") or "",
            ]
            for c, v in enumerate(cells):
                self._audit_table.setItem(i, c, QTableWidgetItem(str(v)))

    # ── New return ─────────────────────────────────────────────────
    def _new_return(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("New Vendor Return")
        form = QFormLayout()

        type_combo = QComboBox()
        for code, label in RETURN_TYPES:
            type_combo.addItem(label, code)
        form.addRow("Return Type:", type_combo)

        vendor_combo = QComboBox()
        try:
            for v in get_all_vendors():
                vendor_combo.addItem(v.get("name", ""), v.get("id"))
        except Exception:
            pass
        form.addRow("Vendor:", vendor_combo)

        reason_edit = QLineEdit()
        form.addRow("Reason:", reason_edit)
        notes_edit = QTextEdit()
        notes_edit.setMaximumHeight(80)
        form.addRow("Notes:", notes_edit)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Create")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)

        lay = QVBoxLayout(dlg)
        lay.addLayout(form)
        lay.addWidget(bb)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        vid = vendor_combo.currentData()
        if not vid:
            QMessageBox.warning(self, "Vendor required",
                                "Please select a vendor.")
            return
        rtype = type_combo.currentData()
        try:
            result = create_vendor_return(
                vendor_id=vid,
                reason=reason_edit.text().strip(),
                notes=notes_edit.toPlainText().strip(),
            )
            new_id = result.get("id") if isinstance(result, dict) else result
            try:
                update_vendor_return(new_id, return_type=rtype)
            except Exception:
                # update_vendor_return allowlist may not include return_type
                # — fall back to a direct UPDATE.
                from db import get_db
                with get_db() as conn:
                    conn.execute(
                        "UPDATE vendor_returns SET return_type = %s WHERE id = %s",
                        (rtype, new_id),
                    )
                    conn.commit()
            self._refresh()
            for i, r in enumerate(self._returns):
                if r.get("id") == new_id:
                    self._returns_table.selectRow(i)
                    break
            if rtype == "vendor_core":
                self._add_vendor_cores()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ── Vendor core picker ─────────────────────────────────────────
    def _add_vendor_cores(self):
        if not self._current_return_id:
            QMessageBox.warning(
                self, "Select an RGA",
                "Create or select a vendor return first."
            )
            return
        ret = get_vendor_return(self._current_return_id) or {}
        vid = ret.get("vendor_id")
        dlg = EligibleCoresPickerDialog(self, vendor_id=vid)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        ids = dlg.selected_vcr_ids()
        if not ids:
            return
        try:
            n = attach_vendor_cores_to_rga(
                self._current_return_id, ids, user=self._current_user()
            )
            QMessageBox.information(
                self, "Cores attached",
                f"Attached {n} vendor core(s) to RGA."
            )
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ── SKU line ───────────────────────────────────────────────────
    def _add_line(self):
        if not self._current_return_id:
            QMessageBox.warning(self, "Select an RGA",
                                "Select a return first.")
            return
        sku = self._sku_entry.text().strip()
        if not sku:
            return
        try:
            product = get_product_by_sku(sku)
            if not product:
                QMessageBox.warning(self, "Not Found", f"SKU '{sku}' not found.")
                return
            add_vendor_return_line(
                self._current_return_id, product["id"],
                quantity=self._qty_spin.value(),
                unit_cost=float(product.get("cost", 0) or 0),
                condition="defective",
            )
            self._sku_entry.clear()
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _remove_line(self):
        row = self._lines_table.currentRow()
        if row < 0 or not self._current_return_id:
            return
        try:
            lines = get_vendor_return_lines(self._current_return_id)
            if row < len(lines):
                remove_vendor_return_line(lines[row]["id"])
                self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ── Status transitions ─────────────────────────────────────────
    def _transition(
        self, new_status: str, *,
        tracking_number: str | None = None,
        credit_amount: float | None = None,
        credit_memo_number: str | None = None,
        notes: str | None = None,
    ):
        if not self._current_return_id:
            QMessageBox.warning(self, "Select an RGA",
                                "Select a return first.")
            return
        try:
            transition_rga_status(
                self._current_return_id, new_status,
                user=self._current_user(),
                tracking_number=tracking_number,
                credit_amount=credit_amount,
                credit_memo_number=credit_memo_number,
                notes=notes,
            )
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _mark_shipped(self):
        tracking, ok = QInputDialog.getText(
            self, "Shipping", "Tracking #:",
        )
        if not ok:
            return
        self._transition("shipped", tracking_number=tracking.strip() or None)

    def _add_tracking_only(self):
        if not self._current_return_id:
            return
        tracking, ok = QInputDialog.getText(
            self, "Tracking", "Tracking #:",
        )
        if not ok or not tracking.strip():
            return
        try:
            update_vendor_return(
                self._current_return_id,
                tracking_number=tracking.strip(),
            )
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _mark_credit_received(self):
        if not self._current_return_id:
            return
        ret = get_vendor_return(self._current_return_id) or {}
        expected = float(ret.get("credit_amount") or 0)
        dlg = _CreditReceivedDialog(self, expected=expected)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.data()
        self._transition(
            "credited",
            credit_amount=data["credit_amount"],
            credit_memo_number=data["vendor_credit_number"],
            notes=data["notes"],
        )

    def _mark_rejected(self):
        reason, ok = QInputDialog.getText(
            self, "Reject RGA", "Rejection reason:"
        )
        if not ok:
            return
        self._transition("rejected", notes=reason.strip() or None)

    def _write_off(self):
        confirm = QMessageBox.question(
            self, "Write Off",
            "Write off remaining vendor core balance? This is final.",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._transition("written_off")

    # ── Packing slip ───────────────────────────────────────────────
    def _print_packing_slip(self):
        if not self._current_return_id:
            return
        try:
            ret = get_vendor_return(self._current_return_id) or {}
            lines = get_vendor_return_lines(self._current_return_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        body = []
        body.append(f"RGA #: {ret.get('rga_number', '')}")
        body.append(f"Vendor: {ret.get('vendor_name', '')}")
        body.append(f"Type: {ret.get('return_type', 'general')}")
        body.append(f"Status: {ret.get('status', '')}")
        body.append(f"Tracking: {ret.get('tracking_number', '') or '—'}")
        body.append(f"Date: {date.today().isoformat()}")
        body.append("")
        body.append("Lines:")
        for ln in lines:
            body.append(
                f"  {ln.get('sku','')} × {ln.get('quantity',1)} — "
                f"{ln.get('product_title','')} "
                f"(${float(ln.get('expected_credit') or ln.get('line_total') or 0):,.2f})"
            )
        text = "\n".join(body)

        dlg = QDialog(self)
        dlg.setWindowTitle("Packing Slip Preview")
        dlg.resize(560, 420)
        v = QVBoxLayout(dlg)
        edit = QTextEdit()
        edit.setPlainText(text)
        edit.setReadOnly(True)
        v.addWidget(edit)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        v.addWidget(bb)
        dlg.exec()

    # ── Helpers ────────────────────────────────────────────────────
    def _current_user(self) -> str | None:
        try:
            from jaks_inventory.permissions import current_user  # type: ignore
            u = current_user()
            return getattr(u, "username", None) or str(u or "")
        except Exception:
            return None

    # ── Outstanding obligations actions ────────────────────────────
    def _create_rga_from_obligations(self):
        """Create a new RGA from selected outstanding obligations.

        All selected obligations must belong to the same vendor and must
        be in 'pending' status (cores we've already received from
        customers). Awaiting rows can't be attached \u2014 we need the
        physical core in hand first.
        """
        rows = self._obligations_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(
                self, "Pick obligations",
                "Select one or more 'Ready for RGA' rows first.",
            )
            return
        picked = [self._obligations[r.row()] for r in rows
                  if 0 <= r.row() < len(self._obligations)]
        if not picked:
            return
        not_ready = [o for o in picked if o.get("status") != "pending"]
        if not_ready:
            QMessageBox.warning(
                self, "Not ready",
                f"{len(not_ready)} selected row(s) are still awaiting a "
                "customer core return and can't be added to an RGA yet.",
            )
            return
        vendor_ids = {o.get("vendor_id") for o in picked}
        if len(vendor_ids) != 1:
            QMessageBox.warning(
                self, "Multiple vendors",
                "All selected obligations must belong to the same vendor.",
            )
            return
        vendor_id = next(iter(vendor_ids))
        vendor_name = picked[0].get("vendor_name") or ""
        reply = QMessageBox.question(
            self, "Create RGA",
            f"Create a new vendor return for {vendor_name} and attach "
            f"{len(picked)} core obligation(s)?",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            result = create_vendor_return(
                vendor_id=vendor_id,
                reason="Vendor core return",
                notes=f"Auto-created from {len(picked)} core obligation(s)",
            )
            new_id = result.get("id") if isinstance(result, dict) else result
            try:
                update_vendor_return(new_id, return_type="vendor_core")
            except Exception:
                from db import get_db
                with get_db() as conn:
                    conn.execute(
                        "UPDATE vendor_returns SET return_type = %s WHERE id = %s",
                        ("vendor_core", new_id),
                    )
                    conn.commit()
            obl_ids = [int(o["id"]) for o in picked if o.get("id")]
            n = attach_vendor_cores_to_rga(
                new_id, obl_ids, user=self._current_user()
            )
            QMessageBox.information(
                self, "RGA created",
                f"New RGA created and {n} core(s) attached.",
            )
            self._refresh()
            for i, r in enumerate(self._returns):
                if r.get("id") == new_id:
                    self._returns_table.selectRow(i)
                    break
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _delete_current_rga(self):
        if not self._current_return_id:
            QMessageBox.information(
                self, "Select RGA", "Select an RGA first.",
            )
            return
        ret = get_vendor_return(self._current_return_id) or {}
        rga_no = ret.get("rga_number") or f"#{self._current_return_id}"
        reply = QMessageBox.question(
            self, "Delete RGA",
            f"Delete RGA {rga_no}?\n\n"
            "This is only allowed if the RGA is pending/cancelled and no "
            "vendor core obligations are attached. Any attached cores "
            "must first be removed (use '- Remove' on the lines table) — "
            "this releases them back to the outstanding queue.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            res = delete_vendor_return(self._current_return_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        if not res.get("success"):
            QMessageBox.warning(
                self, "Cannot delete",
                res.get("error") or "Unknown error.",
            )
            return
        QMessageBox.information(
            self, "Deleted", f"RGA {rga_no} deleted.",
        )
        self._current_return_id = None
        self._lines_table.setRowCount(0)
        self._audit_table.setRowCount(0)
        self._refresh()
