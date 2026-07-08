"""Receive against PO workflow screen."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QSpinBox,
    QMessageBox,
    QHeaderView,
    QComboBox,
    QFrame,
    QGridLayout,
    QCheckBox,
    QGroupBox,
    QFormLayout,
    QScrollArea,
    QWidget,
)
from PySide6.QtGui import QColor

from db import get_purchase_order, receive_po_line, list_locations, get_setting
from db import (
    list_vendor_core_obligations,
    update_vendor_core_obligation,
)


class ReceiveDialog(QDialog):
    """Dialog for receiving inventory against a Purchase Order."""

    def __init__(self, po_id: int, parent=None):
        """Initialize dialog.
        
        Args:
            po_id: Purchase Order ID
            parent: Parent widget
        """
        super().__init__(parent)
        self._po_id = po_id
        self._po = get_purchase_order(po_id)
        self._receive_inputs: dict[int, QSpinBox] = {}  # line_id -> spinbox
        self._row_by_line_id: dict[int, int] = {}
        # Core handling overhaul (migration 108): controls keyed by
        # vendor_core_obligations.id so _on_receive can roll the answers
        # into status transitions.
        self._core_obligations: list[dict] = []
        self._core_charged_checks: dict[int, "QCheckBox"] = {}
        self._core_status_combos: dict[int, QComboBox] = {}
        self._core_credit_inputs: dict[int, QLineEdit] = {}
        self._core_rga_checks: dict[int, "QCheckBox"] = {}
        self._core_notes_inputs: dict[int, QLineEdit] = {}
        
        if not self._po:
            QMessageBox.critical(self, "Error", "Purchase Order not found.")
            self.reject()
            return
        
        self._build_ui()

    def _build_ui(self) -> None:
        self.setWindowTitle(f"Receive PO: {self._po.get('po_number', '')}")
        self.setMinimumSize(1050, 680)
        self.resize(1220, 760)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setStyleSheet("QFrame { background: #1F2937; padding: 10px 14px; }")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(14, 10, 14, 10)
        title = QLabel(f"Receive PO {self._po.get('po_number', '')}")
        title.setStyleSheet("color: white; font-size: 17px; font-weight: bold;")
        hlay.addWidget(title)
        hlay.addSpacing(16)
        vendor = QLabel(f"Vendor: {self._po.get('vendor_name', '')}")
        vendor.setStyleSheet("color: #D1D5DB; font-size: 12px;")
        hlay.addWidget(vendor)
        status = QLabel(str(self._po.get("status", "")).upper())
        status.setStyleSheet("background: #DBEAFE; color: #1E40AF; font-weight: 700; padding: 4px 10px; border-radius: 4px;")
        hlay.addWidget(status)
        hlay.addStretch()
        layout.addWidget(header)

        body = QVBoxLayout()
        body.setContentsMargins(16, 12, 16, 12)
        body.setSpacing(10)

        metrics = QFrame()
        metrics.setStyleSheet("QFrame { background: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 6px; }")
        mg = QGridLayout(metrics)
        mg.setContentsMargins(12, 10, 12, 10)
        mg.setHorizontalSpacing(18)
        ordered, received, outstanding = self._metric_counts()
        for col, (label, value, color) in enumerate([
            ("Ordered", ordered, "#1F2937"),
            ("Received", received, "#059669"),
            ("Outstanding", outstanding, "#D97706"),
        ]):
            cap = QLabel(label)
            cap.setStyleSheet("font-size: 10px; color: #6B7280; font-weight: 600;")
            val = QLabel(str(value))
            val.setStyleSheet(f"font-size: 20px; color: {color}; font-weight: bold;")
            mg.addWidget(cap, 0, col)
            mg.addWidget(val, 1, col)
        body.addWidget(metrics)

        action_row = QHBoxLayout()
        self._receive_all_btn = QPushButton("Receive All")
        self._receive_all_btn.clicked.connect(self._on_receive_all)
        action_row.addWidget(self._receive_all_btn)
        self._receive_selected_btn = QPushButton("Receive Selected")
        self._receive_selected_btn.clicked.connect(self._on_receive_selected)
        action_row.addWidget(self._receive_selected_btn)
        self._scan_mode_btn = QPushButton("Scan Mode")
        self._scan_mode_btn.setCheckable(True)
        self._scan_mode_btn.toggled.connect(self._on_toggle_scan_mode)
        self._scan_mode_btn.setToolTip("Enable SKU scan entry (format: SKU or SKU*QTY).")
        action_row.addWidget(self._scan_mode_btn)
        action_row.addStretch()
        body.addLayout(action_row)

        self._scan_row = QHBoxLayout()
        self._scan_row.addWidget(QLabel("Scan SKU:"))
        self._scan_input = QLineEdit()
        self._scan_input.setPlaceholderText("Scan SKU, or type SKU*QTY")
        self._scan_input.returnPressed.connect(self._on_scan_entered)
        self._scan_row.addWidget(self._scan_input, 1)
        self._scan_feedback = QLabel("")
        self._scan_feedback.setStyleSheet("color: #6B7280; font-size: 11px;")
        self._scan_row.addWidget(self._scan_feedback)
        body.addLayout(self._scan_row)
        self._set_scan_row_visible(False)

        self._lines_table = QTableWidget(0, 6)
        self._lines_table.setHorizontalHeaderLabels([
            "SKU", "Description", "Ordered", "Received", "Remaining", "Receive Now"
        ])
        self._lines_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._lines_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._lines_table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._lines_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._lines_table.horizontalHeader().setMinimumSectionSize(80)
        self._lines_table.verticalHeader().setVisible(False)
        self._lines_table.verticalHeader().setDefaultSectionSize(44)
        self._lines_table.setAlternatingRowColors(True)
        self._lines_table.setWordWrap(True)
        self._lines_table.setStyleSheet(
            "QTableWidget { border: 1px solid #E5E7EB; border-radius: 6px; font-size: 12px; }"
            "QHeaderView::section { background: #F9FAFB; font-weight: 600; padding: 6px 8px; border: none; border-bottom: 1px solid #E5E7EB; }"
            "QTableWidget::item { padding: 4px 6px; }"
        )
        # Ensure the table never collapses behind the Core Returns panel.
        self._lines_table.setMinimumHeight(220)
        
        self._populate_lines()
        body.addWidget(self._lines_table, 1)

        notes_row = QHBoxLayout()
        notes_row.addWidget(QLabel("Notes:"))
        self._notes = QLineEdit()
        self._notes.setPlaceholderText("Receiving notes (optional)...")
        notes_row.addWidget(self._notes, 1)
        body.addLayout(notes_row)

        # ── Core Returns panel (migration 108) ──
        core_panel = self._build_core_panel()
        if core_panel is not None:
            body.addWidget(core_panel)

        loc_row = QHBoxLayout()
        loc_row.addWidget(QLabel("Receive to Location:"))
        self._location_combo = QComboBox()
        self._location_combo.setMinimumWidth(200)
        self._location_combo.addItem("-- Default (no location) --", None)
        locations = list_locations(active_only=True)
        for loc in locations:
            display = f"{loc.get('code', '')} - {loc.get('name', '')}"
            if loc.get('zone'):
                display += f" ({loc.get('zone')})"
            self._location_combo.addItem(display, loc.get("id"))
        # Pre-select default location from settings
        default_loc = get_setting("default_location_id", "")
        if default_loc:
            idx = self._location_combo.findData(int(default_loc))
            if idx >= 0:
                self._location_combo.setCurrentIndex(idx)
        loc_row.addWidget(self._location_combo)
        loc_row.addStretch()
        body.addLayout(loc_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Post Receipt")
        buttons.accepted.connect(self._on_receive)
        buttons.rejected.connect(self.reject)
        body.addWidget(buttons)

        # Wrap the body in a scroll area so the dialog stays usable on small
        # screens or when the Core Returns panel has many obligations.
        body_widget = QWidget()
        body_widget.setLayout(body)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(body_widget)
        layout.addWidget(scroll, 1)

        self.setLayout(layout)

    def _metric_counts(self) -> tuple[int, int, int]:
        # Exclude accounting-only lines (core deposits, discounts) from metrics.
        lines = [
            l for l in self._po.get("lines", [])
            if str(l.get("line_kind") or "product").lower() not in ("core", "discount")
        ]
        ordered = sum(int(line.get("quantity_ordered", 0) or 0) for line in lines)
        received = sum(int(line.get("quantity_received", 0) or 0) for line in lines)
        return ordered, received, max(ordered - received, 0)

    def _populate_lines(self) -> None:
        """Populate receivable product lines only (core/discount lines excluded)."""
        # Core deposit and discount lines are accounting-only — they must never
        # appear in the receive grid because they have no SKU and would silently
        # bypass inventory adjustments.
        lines = [
            l for l in self._po.get("lines", [])
            if str(l.get("line_kind") or "product").lower() not in ("core", "discount")
        ]
        self._lines_table.setRowCount(len(lines))

        for row_idx, line in enumerate(lines):
            line_id = line.get("id")
            sku = str(line.get("sku", "") or "")
            title = str(line.get("title", "") or line.get("description", "") or "")
            ordered = int(line.get("quantity_ordered", 0) or 0)
            received = int(line.get("quantity_received", 0) or 0)
            remaining = max(ordered - received, 0)

            def item(text: str, bg: QColor = None, align=None) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if bg:
                    it.setBackground(bg)
                if align is not None:
                    it.setTextAlignment(align)
                return it

            # Color code based on status
            if remaining == 0:
                bg = QColor(200, 245, 210)   # green — fully received
            elif received > 0:
                bg = QColor(255, 251, 210)   # amber — partial
            else:
                bg = None

            center = int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            self._lines_table.setItem(row_idx, 0, item(sku, bg))
            self._lines_table.setItem(row_idx, 1, item(title, bg))
            self._lines_table.setItem(row_idx, 2, item(str(ordered), align=center))
            self._lines_table.setItem(row_idx, 3, item(str(received), align=center))
            self._lines_table.setItem(row_idx, 4, item(str(remaining), align=center))

            # Receive Now spinbox — highlighted so it stands out as the action cell
            spin = QSpinBox()
            spin.setRange(0, max(remaining, 0))
            spin.setValue(remaining)
            spin.setEnabled(remaining > 0)
            spin.setStyleSheet(
                "QSpinBox { background: #EFF6FF; border: 1px solid #93C5FD; "
                "border-radius: 3px; padding: 2px 4px; font-size: 13px; font-weight: 600; }"
            )
            self._receive_inputs[line_id] = spin
            self._lines_table.setCellWidget(row_idx, 5, spin)
            if line_id is not None:
                self._row_by_line_id[int(line_id)] = row_idx

        self._lines_table.resizeColumnsToContents()
        # Ensure SKU column readable, Receive Now wide enough for spinbox
        self._lines_table.setColumnWidth(0, max(self._lines_table.columnWidth(0), 120))
        self._lines_table.setColumnWidth(5, max(self._lines_table.columnWidth(5), 90))
        self._lines_table.resizeRowsToContents()

    def _on_receive_all(self) -> None:
        """Set all receivable spinboxes to remaining quantity."""
        lines = [
            l for l in self._po.get("lines", [])
            if str(l.get("line_kind") or "product").lower() not in ("core", "discount")
        ]
        for line in lines:
            line_id = line.get("id")
            ordered = int(line.get("quantity_ordered", 0) or 0)
            received = int(line.get("quantity_received", 0) or 0)
            remaining = max(ordered - received, 0)
            if line_id in self._receive_inputs:
                self._receive_inputs[line_id].setValue(remaining)

    def _on_receive_selected(self) -> None:
        """Set selected rows to full remaining and clear unselected rows."""
        selected_rows = {index.row() for index in self._lines_table.selectionModel().selectedRows()}
        lines = [
            l for l in self._po.get("lines", [])
            if str(l.get("line_kind") or "product").lower() not in ("core", "discount")
        ]
        for row_idx, line in enumerate(lines):
            line_id = line.get("id")
            if line_id not in self._receive_inputs:
                continue
            ordered = int(line.get("quantity_ordered", 0) or 0)
            received = int(line.get("quantity_received", 0) or 0)
            remaining = max(ordered - received, 0)
            self._receive_inputs[line_id].setValue(remaining if row_idx in selected_rows else 0)

    def _set_scan_row_visible(self, visible: bool) -> None:
        for i in range(self._scan_row.count()):
            w = self._scan_row.itemAt(i).widget()
            if w:
                w.setVisible(visible)

    def _on_toggle_scan_mode(self, enabled: bool) -> None:
        self._set_scan_row_visible(enabled)
        if enabled:
            self._scan_feedback.setText("Scan mode enabled")
            self._scan_input.setFocus()
        else:
            self._scan_feedback.setText("")
            self._scan_input.clear()

    def _on_scan_entered(self) -> None:
        raw = self._scan_input.text().strip()
        if not raw:
            return
        qty = 1
        sku = raw
        if "*" in raw:
            sku_part, qty_part = raw.split("*", 1)
            sku = sku_part.strip()
            try:
                qty = max(1, int(qty_part.strip()))
            except ValueError:
                self._scan_feedback.setText("Invalid quantity in scan")
                return
        sku = sku.upper()
        lines = self._po.get("lines", [])
        target_line_id = None
        for line in lines:
            line_sku = str(line.get("sku") or "").upper()
            ordered = int(line.get("quantity_ordered", 0) or 0)
            received = int(line.get("quantity_received", 0) or 0)
            if line_sku == sku and ordered > received:
                target_line_id = int(line.get("id"))
                break
        if target_line_id is None:
            self._scan_feedback.setText(f"No receivable line for {sku}")
            self._scan_input.selectAll()
            return

        spin = self._receive_inputs.get(target_line_id)
        if not spin:
            self._scan_feedback.setText(f"Line control missing for {sku}")
            return
        new_val = min(spin.maximum(), spin.value() + qty)
        spin.setValue(new_val)
        row = self._row_by_line_id.get(target_line_id)
        if row is not None:
            self._lines_table.selectRow(row)
        self._scan_feedback.setText(f"{sku}: +{qty} (now {spin.value()})")
        self._scan_input.clear()

    def _on_receive(self) -> None:
        """Process the receiving."""
        notes = self._notes.text().strip() or None
        location_id = self._location_combo.currentData()  # None if default selected
        
        lines = self._po.get("lines", [])
        any_received = False
        errors = []

        for line in lines:
            line_id = line.get("id")
            if line_id not in self._receive_inputs:
                continue
            
            qty_to_receive = self._receive_inputs[line_id].value()
            if qty_to_receive <= 0:
                continue
            
            result = receive_po_line(
                po_id=self._po_id,
                line_id=line_id,
                quantity_received=qty_to_receive,
                notes=notes,
                location_id=location_id,
            )
            
            if result.get("success"):
                any_received = True
            else:
                errors.append(f"Line {line_id}: {result.get('error')}")

        # ── Apply core-return answers (migration 108) ──
        core_errors = self._apply_core_answers()
        if core_errors:
            errors.extend(core_errors)

        if errors:
            QMessageBox.warning(
                self,
                "Partial Receive",
                f"Some lines failed to receive:\n\n" + "\n".join(errors),
            )
        
        if any_received:
            self.accept()
        elif not errors:
            QMessageBox.information(self, "Nothing to Receive", "No quantities entered.")

    # ------------------------------------------------------------------
    # Core handling overhaul (migration 108) — receiving questions
    # ------------------------------------------------------------------
    _CORE_STATUS_OPTIONS = [
        ("Keep open (still pending)", "pending"),
        ("Old core shipped back with this delivery", "shipped"),
        ("Awaiting customer/shop return", "awaiting_return"),
        ("Vendor received old core (close-loop)", "vendor_received"),
        ("Vendor issued credit (close-loop)", "credited"),
        ("Write off (no return expected)", "written_off"),
    ]

    def _build_core_panel(self) -> QGroupBox | None:
        """Build the Core Returns panel.

        Returns ``None`` when this PO has no open vendor core obligations,
        so the dialog stays compact for orders without cores. When
        obligations exist, the panel exposes the five spec questions for
        each one and the answers are rolled into status transitions on
        ``Post Receipt``.
        """
        try:
            obligations = list_vendor_core_obligations(
                po_id=self._po_id, only_open=True
            )
        except Exception:
            obligations = []
        self._core_obligations = obligations or []
        if not self._core_obligations:
            return None

        box = QGroupBox("Core Returns")
        box.setStyleSheet(
            "QGroupBox { font-weight: 600; border: 1px solid #E5E7EB; "
            "border-radius: 6px; margin-top: 10px; padding: 10px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; "
            "padding: 0 4px; color: #92400E; }"
        )
        outer = QVBoxLayout(box)
        intro = QLabel(
            "Confirm core handling for each refundable deposit on this PO. "
            "Answers update the vendor core ledger automatically."
        )
        intro.setStyleSheet("color: #6B7280; font-size: 11px;")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        for ob in self._core_obligations:
            ob_id = int(ob.get("id") or 0)
            if not ob_id:
                continue
            sku = str(ob.get("product_sku") or ob.get("sku") or "")
            title = str(ob.get("product_title") or ob.get("title") or "")
            qty = int(ob.get("quantity") or 0)
            amt = float(ob.get("core_amount") or 0)
            header = f"{sku or '—'}  ·  {title or 'Product'}  ·  Qty {qty} × ${amt:,.2f} = ${qty * amt:,.2f}"

            sub = QFrame()
            sub.setStyleSheet(
                "QFrame { background: #FFFBEB; border: 1px solid #FDE68A; "
                "border-radius: 4px; padding: 8px; margin-top: 6px; }"
            )
            sub_lay = QVBoxLayout(sub)
            cap = QLabel(header)
            cap.setStyleSheet("font-weight: 600; color: #92400E;")
            sub_lay.addWidget(cap)

            form = QFormLayout()
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(4)

            charged_chk = QCheckBox("Vendor charged core on this shipment")
            charged_chk.setChecked(True)
            charged_chk.setToolTip(
                "Uncheck if the vendor's packing slip / invoice did not "
                "include the refundable core charge. The obligation will be "
                "closed as written-off so it stops showing as outstanding."
            )
            self._core_charged_checks[ob_id] = charged_chk
            form.addRow(charged_chk)

            status_combo = QComboBox()
            for label, value in self._CORE_STATUS_OPTIONS:
                status_combo.addItem(label, value)
            status_combo.setToolTip(
                "Was the old core returned with this delivery? Drives the "
                "vendor core obligation status."
            )
            self._core_status_combos[ob_id] = status_combo
            form.addRow("Old core status:", status_combo)

            outstanding = QLabel("Open" if str(ob.get("status") or "pending").lower() in
                                 ("pending", "awaiting_return", "rga_created", "shipped", "vendor_received")
                                 else str(ob.get("status") or ""))
            outstanding.setStyleSheet("color: #6B7280; font-size: 11px;")
            form.addRow("Currently outstanding:", outstanding)

            credit_input = QLineEdit()
            credit_input.setPlaceholderText(f"Expected credit (default ${qty * amt:,.2f})")
            self._core_credit_inputs[ob_id] = credit_input
            form.addRow("Expected vendor credit:", credit_input)

            rga_chk = QCheckBox("RGA required for this return")
            self._core_rga_checks[ob_id] = rga_chk
            form.addRow(rga_chk)

            notes_input = QLineEdit()
            notes_input.setPlaceholderText("Optional notes (RGA #, tracking, etc.)")
            self._core_notes_inputs[ob_id] = notes_input
            form.addRow("Notes:", notes_input)

            sub_lay.addLayout(form)
            outer.addWidget(sub)

        return box

    def _apply_core_answers(self) -> list[str]:
        """Translate the panel selections into obligation transitions."""
        errors: list[str] = []
        if not self._core_obligations:
            return errors

        for ob in self._core_obligations:
            ob_id = int(ob.get("id") or 0)
            if not ob_id:
                continue
            charged_chk = self._core_charged_checks.get(ob_id)
            status_combo = self._core_status_combos.get(ob_id)
            credit_input = self._core_credit_inputs.get(ob_id)
            rga_chk = self._core_rga_checks.get(ob_id)
            notes_input = self._core_notes_inputs.get(ob_id)
            if status_combo is None:
                continue

            kwargs: dict = {}
            extra_notes_parts: list[str] = []

            # Q1: Did vendor charge core? Unchecked = close as written_off.
            if charged_chk is not None and not charged_chk.isChecked():
                kwargs["status"] = "written_off"
                extra_notes_parts.append(
                    "Vendor did not bill the core charge on this shipment."
                )
            else:
                # Q2 + Q3: Was old core returned? / outstanding?
                target_status = status_combo.currentData() or "pending"
                if target_status and target_status != "pending":
                    kwargs["status"] = target_status

            # Q4: Expected vendor credit (parsed as currency).
            if credit_input is not None:
                raw = credit_input.text().strip().replace("$", "").replace(",", "")
                if raw:
                    try:
                        kwargs["credit_received"] = float(raw)
                    except ValueError:
                        errors.append(
                            f"Core obligation {ob_id}: invalid expected credit '{credit_input.text()}'"
                        )

            # Q5: RGA required → leave a note + (if shipped) advance status.
            if rga_chk is not None and rga_chk.isChecked():
                extra_notes_parts.append("RGA required.")
                # If user picked 'shipped' AND flagged RGA, escalate to rga_created
                # so the downstream RGA flow (mig 107) can attach.
                if kwargs.get("status") == "shipped":
                    kwargs["status"] = "rga_created"

            user_notes = (notes_input.text().strip() if notes_input else "")
            if user_notes:
                extra_notes_parts.append(user_notes)
            if extra_notes_parts:
                kwargs["notes"] = " | ".join(extra_notes_parts)

            if not kwargs:
                continue

            try:
                update_vendor_core_obligation(ob_id, **kwargs)
            except Exception as e:
                errors.append(f"Core obligation {ob_id}: {e}")

        return errors
