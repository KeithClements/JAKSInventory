"""Sales order picking workflow dialog."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from db import get_db, get_sales_order, get_so_lines, log_so_activity, update_sales_order

_COL_LINE_ID = 0
_COL_SKU = 1
_COL_DESC = 2
_COL_ORDERED = 3
_COL_ALLOC = 4
_COL_PICKED = 5
_COL_REMAIN = 6
_COL_PICK_NOW = 7
_COL_LOCATION = 8
_COL_STATUS = 9


class PickDialog(QDialog):
    """Picking workflow for an SO."""

    pick_saved = Signal(bool)

    def __init__(self, so_id: int, parent=None):
        super().__init__(parent)
        self._so_id = so_id
        self._so = get_sales_order(so_id)
        self._lines = get_so_lines(so_id)
        self._scan_mode = False
        self.create_invoice_requested = False
        self._row_by_line_id: dict[int, int] = {}
        self._spin_by_line_id: dict[int, QSpinBox] = {}
        if not self._so:
            QMessageBox.critical(self, "Error", "Sales Order not found.")
            self.reject()
            return
        self._build_ui()
        self._populate_grid()

    def _build_ui(self) -> None:
        self.setWindowTitle("Picking Workflow")
        self.setMinimumSize(1120, 700)
        self.resize(1300, 820)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QVBoxLayout()
        body.setContentsMargins(14, 12, 14, 12)
        body.setSpacing(10)
        content = QHBoxLayout()
        content.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(10)
        left.addLayout(self._build_actions())

        self._scan_row = QHBoxLayout()
        self._scan_row.addWidget(QLabel("Scan SKU:"))
        self._scan_input = QLineEdit()
        self._scan_input.setPlaceholderText("SKU or SKU*QTY")
        self._scan_input.returnPressed.connect(self._on_scan_entered)
        self._scan_row.addWidget(self._scan_input, 1)
        self._scan_feedback = QLabel("")
        self._scan_feedback.setStyleSheet("font-size: 11px; color: #6B7280;")
        self._scan_row.addWidget(self._scan_feedback)
        left.addLayout(self._scan_row)
        self._set_scan_visible(False)

        self._grid = QTableWidget(0, 10)
        self._grid.setHorizontalHeaderLabels(
            ["Line ID", "SKU", "Description", "Ordered", "Allocated", "Picked", "Remaining", "Pick Now", "Location", "Status"]
        )
        self._grid.setColumnHidden(_COL_LINE_ID, True)
        self._grid.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._grid.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._grid.verticalHeader().setVisible(False)
        self._grid.setAlternatingRowColors(True)
        self._grid.setStyleSheet(
            "QTableWidget { border: 1px solid #E5E7EB; border-radius: 6px; }"
            "QHeaderView::section { background: #F9FAFB; font-weight: 600; padding: 6px; border: none; border-bottom: 1px solid #E5E7EB; }"
        )
        left.addWidget(self._grid, 1)

        self._notes = QLineEdit()
        self._notes.setPlaceholderText("Picking notes / staging instructions (optional)")
        left.addWidget(self._notes)

        content.addLayout(left, 1)
        content.addWidget(self._build_right_panel())
        body.addLayout(content, 1)

        footer = QDialogButtonBox()
        self._confirm_btn = QPushButton("Confirm Pick")
        self._confirm_btn.clicked.connect(self._on_confirm_pick)
        self._save_btn = QPushButton("Save Draft Pick")
        self._save_btn.clicked.connect(self._on_save_draft)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        footer.addButton(self._confirm_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        footer.addButton(self._save_btn, QDialogButtonBox.ButtonRole.ActionRole)
        footer.addButton(self._cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)
        body.addWidget(footer)

        root.addLayout(body, 1)

    def _build_header(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet("QFrame { background: #1F2937; padding: 10px 14px; }")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)

        so_number = self._so.get("so_number", f"SO #{self._so_id}")
        customer = self._so.get("customer_company") or self._so.get("customer_name") or "Unknown customer"
        status = (self._so.get("status") or "confirmed").replace("_", " ").upper()

        title = QLabel(f"Pick Order {so_number}")
        title.setStyleSheet("color: white; font-size: 17px; font-weight: bold;")
        lay.addWidget(title)
        lay.addSpacing(16)

        info = QLabel(f"{customer}   |   {status}")
        info.setStyleSheet("color: #D1D5DB; font-size: 12px;")
        lay.addWidget(info)
        lay.addStretch()

        allocated, picked, ready = self._summary_counts(self._lines)
        for label, val, color in (
            ("Allocated", allocated, "#93C5FD"),
            ("Picked", picked, "#34D399"),
            ("Ready Lines", ready, "#FBBF24"),
        ):
            cap = QLabel(f"{label}: {val}")
            cap.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 700;")
            lay.addWidget(cap)
            lay.addSpacing(10)
        return frame

    def _build_actions(self) -> QHBoxLayout:
        lay = QHBoxLayout()
        self._pick_all_btn = QPushButton("Pick All")
        self._pick_all_btn.clicked.connect(self._on_pick_all)
        lay.addWidget(self._pick_all_btn)

        self._pick_selected_btn = QPushButton("Pick Selected")
        self._pick_selected_btn.clicked.connect(self._on_pick_selected)
        lay.addWidget(self._pick_selected_btn)

        self._mark_missing_btn = QPushButton("Mark Missing")
        self._mark_missing_btn.clicked.connect(self._on_mark_missing_selected)
        lay.addWidget(self._mark_missing_btn)

        self._scan_mode_btn = QPushButton("Scan Mode")
        self._scan_mode_btn.setCheckable(True)
        self._scan_mode_btn.toggled.connect(self._on_toggle_scan_mode)
        lay.addWidget(self._scan_mode_btn)

        lay.addStretch()
        return lay

    @staticmethod
    def _line_status(line: dict) -> str:
        ordered = int(line.get("quantity_ordered", 0) or 0)
        alloc = int(line.get("allocated_qty", 0) or 0)
        picked = int(line.get("quantity_picked", 0) or 0)
        if picked >= ordered and ordered > 0:
            return "Picked"
        if picked > 0:
            return "Partial"
        if alloc > 0:
            return "Ready"
        return "Waiting"

    @staticmethod
    def _remaining_for_pick(line: dict) -> int:
        alloc = int(line.get("allocated_qty", 0) or 0)
        picked = int(line.get("quantity_picked", 0) or 0)
        return max(alloc - picked, 0)

    @staticmethod
    def _summary_counts(lines: list[dict]) -> tuple[int, int, int]:
        allocated = sum(int(l.get("allocated_qty", 0) or 0) for l in lines)
        picked = sum(int(l.get("quantity_picked", 0) or 0) for l in lines)
        ready = sum(1 for l in lines if PickDialog._line_status(l) == "Ready")
        return allocated, picked, ready

    def _build_right_panel(self) -> QFrame:
        panel = QFrame()
        panel.setMaximumWidth(340)
        panel.setMinimumWidth(280)
        panel.setStyleSheet(
            "QFrame { background: #FAFAFA; border: 1px solid #E5E7EB; border-radius: 6px; }"
        )
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        title = QLabel("Pick Summary")
        title.setStyleSheet("font-weight: 700; color: #1F2937;")
        lay.addWidget(title)

        self._panel_summary = QLabel("")
        self._panel_summary.setStyleSheet(
            "font-size: 11px; color: #374151; background: white; "
            "border: 1px solid #E5E7EB; border-radius: 4px; padding: 8px;"
        )
        self._panel_summary.setWordWrap(True)
        lay.addWidget(self._panel_summary)

        links = QLabel("Order Links")
        links.setStyleSheet("font-weight: 700; color: #1F2937; margin-top: 4px;")
        lay.addWidget(links)

        self._panel_links = QLabel("")
        self._panel_links.setStyleSheet(
            "font-size: 11px; color: #374151; background: white; "
            "border: 1px solid #E5E7EB; border-radius: 4px; padding: 8px;"
        )
        self._panel_links.setWordWrap(True)
        lay.addWidget(self._panel_links)

        self._panel_po_btns = QVBoxLayout()
        self._panel_po_btns.setSpacing(4)
        lay.addLayout(self._panel_po_btns)

        notes = QLabel("Notes Summary")
        notes.setStyleSheet("font-weight: 700; color: #1F2937; margin-top: 4px;")
        lay.addWidget(notes)

        self._panel_notes = QPlainTextEdit()
        self._panel_notes.setReadOnly(True)
        self._panel_notes.setMaximumHeight(230)
        self._panel_notes.setStyleSheet(
            "QPlainTextEdit { font-size: 11px; color: #374151; background: white; "
            "border: 1px solid #E5E7EB; border-radius: 4px; padding: 6px; }"
        )
        lay.addWidget(self._panel_notes, 1)
        return panel

    def _refresh_right_panel(self) -> None:
        lines = self._lines
        alloc = sum(int(l.get("allocated_qty", 0) or 0) for l in lines)
        picked = sum(int(l.get("quantity_picked", 0) or 0) for l in lines)
        remaining = sum(self._remaining_for_pick(l) for l in lines)
        self._panel_summary.setText(
            f"Lines: {len(lines)}\n"
            f"Allocated: {alloc}\n"
            f"Picked: {picked}\n"
            f"Remaining: {remaining}"
        )

        so_status = (self._so.get("status") or "confirmed").replace("_", " ").title()
        po_summary = self._linked_po_status_text()
        self._panel_links.setText(
            f"SO: {self._so.get('so_number', f'SO #{self._so_id}')}\n"
            f"SO Status: {so_status}\n"
            f"PO Status: {po_summary}"
        )
        self._refresh_po_buttons()

        so_notes = str(self._so.get("notes") or "").strip()
        internal = str(self._so.get("internal_notes") or "").strip()
        line_notes = [str(l.get("notes") or "").strip() for l in lines if str(l.get("notes") or "").strip()]
        chunks = []
        if so_notes:
            chunks.append(f"SO Notes:\n{so_notes}")
        if internal:
            chunks.append(f"Internal Notes:\n{internal}")
        if line_notes:
            chunks.append("Line Notes:\n" + "\n".join(f"- {n}" for n in line_notes[:10]))
        self._panel_notes.setPlainText("\n\n".join(chunks) if chunks else "No notes.")

    def _linked_po_status_text(self) -> str:
        rows = self._get_linked_pos()
        if not rows:
            return "None"
        parts = []
        for row in rows:
            status = str(row.get("status") or "").upper()
            po_number = str(row.get("po_number") or f"PO #{row.get('id')}")
            parts.append(f"{po_number} ({status})")
        return ", ".join(parts)

    def _get_linked_pos(self) -> list[dict]:
        product_ids = [l.get("product_id") for l in self._lines if l.get("product_id")]
        if not product_ids:
            return []
        with get_db() as conn:
            placeholders = ",".join("?" for _ in product_ids)
            rows = conn.execute(
                f"""
                SELECT DISTINCT po.id, po.po_number, po.status
                FROM purchase_order_lines pol
                JOIN purchase_orders po ON po.id = pol.po_id
                WHERE pol.product_id IN ({placeholders})
                  AND po.status <> 'cancelled'
                ORDER BY po.created_at DESC
                LIMIT 5
                """,
                tuple(product_ids),
            ).fetchall()
        return [dict(r) if hasattr(r, "keys") else {"id": r[0], "po_number": r[1], "status": r[2]} for r in rows]

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _refresh_po_buttons(self) -> None:
        self._clear_layout(self._panel_po_btns)
        linked = self._get_linked_pos()
        if not linked:
            return
        for po in linked:
            po_id = int(po.get("id") or 0)
            po_num = str(po.get("po_number") or f"PO #{po_id}")
            status = str(po.get("status") or "").upper()
            btn = QPushButton(f"Open {po_num}  [{status}]")
            btn.setStyleSheet(
                "QPushButton { text-align: left; font-size: 10px; color: #1E40AF; "
                "background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 4px; padding: 4px 6px; }"
                "QPushButton:hover { background: #DBEAFE; }"
            )
            btn.clicked.connect(lambda checked=False, pid=po_id: self._open_linked_po(pid))
            self._panel_po_btns.addWidget(btn)

    def _open_linked_po(self, po_id: int) -> None:
        if not po_id:
            return
        try:
            from jaks_inventory.ui.po_dialog import PODialog
            dlg = PODialog(po_id=po_id, parent=self)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "Open PO", str(e))

    def _populate_grid(self) -> None:
        self._row_by_line_id.clear()
        self._spin_by_line_id.clear()
        self._grid.setRowCount(len(self._lines))

        for row, line in enumerate(self._lines):
            line_id = int(line.get("id") or 0)
            ordered = int(line.get("quantity_ordered", 0) or 0)
            alloc = int(line.get("allocated_qty", 0) or 0)
            picked = int(line.get("quantity_picked", 0) or 0)
            remain = self._remaining_for_pick(line)

            def _item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            self._grid.setItem(row, _COL_LINE_ID, _item(str(line_id)))
            self._grid.setItem(row, _COL_SKU, _item(str(line.get("sku") or "")))
            self._grid.setItem(row, _COL_DESC, _item(str(line.get("description") or "")))
            self._grid.setItem(row, _COL_ORDERED, _item(str(ordered)))
            self._grid.setItem(row, _COL_ALLOC, _item(str(alloc)))
            self._grid.setItem(row, _COL_PICKED, _item(str(picked)))
            self._grid.setItem(row, _COL_REMAIN, _item(str(remain)))

            spin = QSpinBox()
            spin.setRange(0, max(ordered, alloc))
            spin.setValue(remain)
            spin.setEnabled(alloc > 0)
            self._grid.setCellWidget(row, _COL_PICK_NOW, spin)

            self._grid.setItem(row, _COL_LOCATION, _item(str(line.get("location_code") or "")))
            status_text = self._line_status(line)
            st = _item(status_text)
            st.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            if status_text == "Ready":
                st.setBackground(QColor("#DBEAFE"))
                st.setForeground(QColor("#1E40AF"))
            elif status_text == "Partial":
                st.setBackground(QColor("#FEF3C7"))
                st.setForeground(QColor("#92400E"))
            elif status_text == "Picked":
                st.setBackground(QColor("#D1FAE5"))
                st.setForeground(QColor("#065F46"))
            else:
                st.setBackground(QColor("#F3F4F6"))
                st.setForeground(QColor("#6B7280"))
            self._grid.setItem(row, _COL_STATUS, st)

            self._row_by_line_id[line_id] = row
            self._spin_by_line_id[line_id] = spin

        self._grid.resizeColumnsToContents()
        self._grid.horizontalHeader().setStretchLastSection(False)
        self._refresh_right_panel()

    def _on_pick_all(self) -> None:
        for line in self._lines:
            line_id = int(line.get("id") or 0)
            spin = self._spin_by_line_id.get(line_id)
            if not spin:
                continue
            spin.setValue(self._remaining_for_pick(line))

    def _on_pick_selected(self) -> None:
        selected_rows = {idx.row() for idx in self._grid.selectionModel().selectedRows()}
        for row, line in enumerate(self._lines):
            line_id = int(line.get("id") or 0)
            spin = self._spin_by_line_id.get(line_id)
            if not spin:
                continue
            spin.setValue(self._remaining_for_pick(line) if row in selected_rows else 0)

    def _on_mark_missing_selected(self) -> None:
        selected_rows = {idx.row() for idx in self._grid.selectionModel().selectedRows()}
        if not selected_rows:
            QMessageBox.information(self, "Mark Missing", "Select one or more rows first.")
            return

        targets = []
        for row in sorted(selected_rows):
            if row < 0 or row >= len(self._lines):
                continue
            line = self._lines[row]
            missing_qty = self._remaining_for_pick(line)
            if missing_qty > 0:
                targets.append((line, missing_qty))

        if not targets:
            QMessageBox.information(self, "Mark Missing", "No selected rows have remaining allocated quantity.")
            return

        qty_map = self._prompt_missing_quantities(targets)
        if qty_map is None:
            return
        targets = [(line, qty_map.get(int(line.get("id") or 0), 0)) for line, _ in targets]
        targets = [(line, qty) for line, qty in targets if qty > 0]
        if not targets:
            QMessageBox.information(self, "Mark Missing", "No missing quantities entered.")
            return

        total_missing = sum(q for _, q in targets)
        reply = QMessageBox.question(
            self,
            "Mark Missing",
            f"Mark {len(targets)} line(s) as missing and send {total_missing} unit(s) back to purchasing?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        ts = datetime.now().strftime("%Y-%m-%d")
        with get_db() as conn:
            for line, missing_qty in targets:
                line_id = int(line.get("id") or 0)
                alloc = int(line.get("allocated_qty", 0) or 0)
                backordered = int(line.get("quantity_backordered", 0) or 0)
                notes = str(line.get("notes") or "").strip()
                tag = f"[MISSING {missing_qty} on {ts}]"
                merged_notes = f"{tag}\n{notes}" if notes else tag
                conn.execute(
                    """
                    UPDATE sales_order_lines
                    SET allocated_qty = ?,
                        quantity_backordered = ?,
                        notes = ?
                    WHERE id = ?
                    """,
                    (max(alloc - missing_qty, 0), backordered + missing_qty, merged_notes, line_id),
                )
            conn.commit()

        log_so_activity(self._so_id, "items_missing", f"{len(targets)} line(s), {total_missing} unit(s) flagged for purchasing")
        so = get_sales_order(self._so_id)
        if so and (so.get("status") or "").lower() not in ("invoiced", "cancelled"):
            update_sales_order(self._so_id, status="confirmed")
        self._lines = get_so_lines(self._so_id)
        self._so = get_sales_order(self._so_id) or self._so
        self._populate_grid()
        QMessageBox.information(self, "Marked Missing", "Selected lines were moved back to purchasing.")

    def _prompt_missing_quantities(self, targets: list[tuple[dict, int]]) -> dict[int, int] | None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Mark Missing Quantities")
        dlg.setMinimumWidth(560)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Enter missing quantity per selected line:"))

        row_spins: list[tuple[int, QSpinBox]] = []
        for line, max_missing in targets:
            row = QHBoxLayout()
            sku = str(line.get("sku") or "?")
            desc = str(line.get("description") or "")[:48]
            row.addWidget(QLabel(f"{sku} — {desc}"), 1)
            row.addWidget(QLabel(f"Max {max_missing}"))
            spin = QSpinBox()
            spin.setRange(0, max_missing)
            spin.setValue(max_missing)
            row.addWidget(spin)
            lay.addLayout(row)
            row_spins.append((int(line.get("id") or 0), spin))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return {line_id: int(sp.value() or 0) for line_id, sp in row_spins}

    def _set_scan_visible(self, visible: bool) -> None:
        for i in range(self._scan_row.count()):
            w = self._scan_row.itemAt(i).widget()
            if w:
                w.setVisible(visible)

    def _on_toggle_scan_mode(self, enabled: bool) -> None:
        self._scan_mode = enabled
        self._set_scan_visible(enabled)
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
        sku = raw
        qty = 1
        if "*" in raw:
            left, right = raw.split("*", 1)
            sku = left.strip()
            try:
                qty = max(1, int(right.strip()))
            except ValueError:
                self._scan_feedback.setText("Invalid quantity")
                return

        sku = sku.upper()
        target_line = None
        for line in self._lines:
            line_sku = str(line.get("sku") or "").upper()
            if line_sku == sku and self._remaining_for_pick(line) > 0:
                target_line = line
                break

        if not target_line:
            self._scan_feedback.setText(f"No ready line for {sku}")
            self._scan_input.selectAll()
            return

        line_id = int(target_line.get("id") or 0)
        spin = self._spin_by_line_id.get(line_id)
        if not spin:
            return
        spin.setValue(min(spin.maximum(), spin.value() + qty))
        row = self._row_by_line_id.get(line_id)
        if row is not None:
            self._grid.selectRow(row)
        self._scan_feedback.setText(f"{sku}: +{qty}")
        self._scan_input.clear()

    def _collect_picks(self) -> tuple[list[tuple[dict, int]], bool]:
        picks: list[tuple[dict, int]] = []
        allow_overpick = False
        for line in self._lines:
            line_id = int(line.get("id") or 0)
            spin = self._spin_by_line_id.get(line_id)
            if not spin:
                continue
            pick_now = int(spin.value() or 0)
            if pick_now <= 0:
                continue
            remaining = self._remaining_for_pick(line)
            if pick_now > remaining:
                allow_overpick = True
            picks.append((line, pick_now))
        return picks, allow_overpick

    def _apply_picks(self, final_confirm: bool) -> None:
        self.create_invoice_requested = False
        picks, has_overpick = self._collect_picks()
        if not picks:
            QMessageBox.information(self, "No Picks", "No picking quantities entered.")
            return

        if has_overpick:
            reply = QMessageBox.question(
                self,
                "Over-pick Confirmation",
                "Some Pick Now values exceed Remaining. Continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        updated_lines = 0
        total_units = 0
        with get_db() as conn:
            for line, pick_now in picks:
                line_id = int(line.get("id") or 0)
                ordered = int(line.get("quantity_ordered", 0) or 0)
                alloc = int(line.get("allocated_qty", 0) or 0)
                picked = int(line.get("quantity_picked", 0) or 0)

                new_picked = min(picked + pick_now, ordered)
                delta_picked = max(new_picked - picked, 0)
                if delta_picked <= 0:
                    continue
                new_alloc = max(alloc - delta_picked, 0)
                conn.execute(
                    "UPDATE sales_order_lines SET quantity_picked = ?, allocated_qty = ? WHERE id = ?",
                    (new_picked, new_alloc, line_id),
                )
                updated_lines += 1
                total_units += delta_picked
            conn.commit()

        fresh = get_so_lines(self._so_id)
        all_picked = all(
            int(l.get("quantity_picked", 0) or 0) >= int(l.get("quantity_ordered", 0) or 0)
            for l in fresh
            if int(l.get("quantity_ordered", 0) or 0) > 0
        )

        if updated_lines > 0:
            if all_picked and final_confirm:
                update_sales_order(self._so_id, status="ready")
            elif final_confirm:
                update_sales_order(self._so_id, status="picking")
            note = self._notes.text().strip()
            detail = f"{updated_lines} lines, {total_units} units"
            if note:
                detail = f"{detail}; {note}"
            log_so_activity(self._so_id, "items_picked", detail)

        self._lines = get_so_lines(self._so_id)
        self._so = get_sales_order(self._so_id) or self._so
        self._refresh_right_panel()

        if final_confirm:
            total_remaining = sum(self._remaining_for_pick(l) for l in self._lines)
            msg = QMessageBox(self)
            msg.setWindowTitle("Pick Summary")
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setText(
                f"Picked {total_units} unit(s) across {updated_lines} line(s).\n"
                f"Remaining to pick: {total_remaining}"
            )
            if all_picked:
                msg.setInformativeText("All lines are fully picked.")
                invoice_btn = msg.addButton("Create Invoice", QMessageBox.ButtonRole.AcceptRole)
                back_btn = msg.addButton("Back to SO", QMessageBox.ButtonRole.RejectRole)
                msg.exec()
                self.create_invoice_requested = msg.clickedButton() is invoice_btn
                _ = back_btn
            else:
                msg.setInformativeText("Partial pick saved.")
                msg.addButton("Back to SO", QMessageBox.ButtonRole.AcceptRole)
                msg.exec()

        self.pick_saved.emit(all_picked)
        self.accept()

    def _on_confirm_pick(self) -> None:
        self._apply_picks(final_confirm=True)

    def _on_save_draft(self) -> None:
        self._apply_picks(final_confirm=False)
