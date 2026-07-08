"""Pre-Inventory Review screen — staging area before formal inventory.

Items in pre-inventory have a light red background and cannot be
quoted, sold, or invoiced until approved here.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QInputDialog,
    QApplication,
)


_COL_HEADERS = [
    "ID", "Source", "ESN", "OEM Part #", "Description",
    "Kit #", "PAI #", "Cost", "Price", "Status", "Created", "",
]

_PENDING_BG = QColor("#FEE2E2")     # Light red
_APPROVED_BG = QColor("#D1FAE5")    # Light green
_REJECTED_BG = QColor("#F3F4F6")    # Light gray

_BTN_PRIMARY = (
    "QPushButton { background-color: #4a6741; color: white; "
    "font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 9pt; }"
    "QPushButton:hover { background-color: #5a7a51; }"
)
_BTN_DANGER = (
    "QPushButton { background-color: #DC2626; color: white; "
    "font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 9pt; }"
    "QPushButton:hover { background-color: #B91C1C; }"
)
_BTN_ACCENT = (
    "QPushButton { background-color: #2563EB; color: white; "
    "font-weight: bold; padding: 4px 10px; border-radius: 4px; font-size: 9pt; }"
    "QPushButton:hover { background-color: #1D4ED8; }"
)


class PreInventoryScreen(QWidget):
    """Pre-Inventory Review — approve or reject staged parts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[dict] = []
        self._build_ui()
        QTimer.singleShot(100, self._load_items)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)

        # ── Header ──
        header = QHBoxLayout()
        title = QLabel("📦 Pre-Inventory Review")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #1F2937;")
        header.addWidget(title)
        header.addStretch()

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #6B7280; font-size: 12px;")
        header.addWidget(self._count_label)
        root.addLayout(header)

        # ── Filter bar ──
        filter_row = QHBoxLayout()

        self._status_combo = QComboBox()
        self._status_combo.addItems(["All", "Pending", "Approved", "Rejected"])
        self._status_combo.currentIndexChanged.connect(self._load_items)
        filter_row.addWidget(QLabel("Status:"))
        filter_row.addWidget(self._status_combo)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter by part #, description, ESN…")
        self._search_edit.textChanged.connect(self._on_filter)
        filter_row.addWidget(self._search_edit, 1)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setStyleSheet(_BTN_PRIMARY)
        refresh_btn.clicked.connect(self._load_items)
        filter_row.addWidget(refresh_btn)

        # Bulk actions
        self._approve_all_btn = QPushButton("✅ Approve All Pending")
        self._approve_all_btn.setStyleSheet(_BTN_PRIMARY)
        self._approve_all_btn.clicked.connect(self._approve_all)
        filter_row.addWidget(self._approve_all_btn)

        root.addLayout(filter_row)

        # ── Table ──
        self._table = QTableWidget(0, len(_COL_HEADERS))
        self._table.setHorizontalHeaderLabels(_COL_HEADERS)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)  # Description
        self._table.setColumnWidth(0, 40)   # ID
        self._table.setColumnWidth(1, 80)   # Source
        self._table.setColumnWidth(2, 100)  # ESN
        self._table.setColumnWidth(3, 120)  # OEM Part #
        self._table.setColumnWidth(5, 100)  # Kit #
        self._table.setColumnWidth(6, 100)  # PAI #
        self._table.setColumnWidth(7, 70)   # Cost
        self._table.setColumnWidth(8, 70)   # Price
        self._table.setColumnWidth(9, 80)   # Status
        self._table.setColumnWidth(10, 130) # Created
        self._table.setColumnWidth(11, 300) # Actions
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(False)
        root.addWidget(self._table, 1)

        # ── Status bar ──
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #6B7280; font-size: 11px;")
        root.addWidget(self._status_label)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------
    def _load_items(self):
        from jaks_inventory.services.pre_inventory_service import list_pre_inventory

        status_filter = self._status_combo.currentText().lower()
        if status_filter == "all":
            status_filter = None

        self._items = list_pre_inventory(status_filter)
        self._apply_filter()

    def _on_filter(self):
        self._apply_filter()

    def _apply_filter(self):
        query = self._search_edit.text().strip().lower()
        filtered = self._items
        if query:
            filtered = [
                it for it in self._items
                if query in (it.get("oem_part_number", "") or "").lower()
                or query in (it.get("description", "") or "").lower()
                or query in (it.get("esn", "") or "").lower()
                or query in (it.get("kit_number", "") or "").lower()
                or query in (it.get("pai_part_number", "") or "").lower()
            ]
        self._populate_table(filtered)

    def _populate_table(self, items: list[dict]):
        self._table.setRowCount(len(items))
        pending_count = sum(1 for it in items if it.get("status") == "pending")

        for row, it in enumerate(items):
            status = it.get("status", "pending")

            # Determine row background
            if status == "pending":
                bg = _PENDING_BG
            elif status == "approved":
                bg = _APPROVED_BG
            else:
                bg = _REJECTED_BG

            brush = QBrush(bg)

            def _cell(text, col):
                item = QTableWidgetItem(str(text) if text else "")
                item.setBackground(brush)
                if status == "rejected":
                    f = item.font()
                    f.setStrikeOut(True)
                    item.setFont(f)
                self._table.setItem(row, col, item)

            _cell(it.get("id", ""), 0)
            _cell(it.get("source", ""), 1)
            _cell(it.get("esn", ""), 2)
            _cell(it.get("oem_part_number", ""), 3)
            _cell(it.get("description", "") or it.get("kit_description", ""), 4)
            _cell(it.get("kit_number", ""), 5)
            _cell(it.get("pai_part_number", ""), 6)
            _cell(f"${it.get('cost', 0):.2f}" if it.get("cost") else "", 7)
            _cell(f"${it.get('selling_price', 0):.2f}" if it.get("selling_price") else "", 8)

            # Status badge
            badge = QTableWidgetItem(status.upper())
            badge.setBackground(brush)
            badge.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if status == "pending":
                badge.setForeground(QBrush(QColor("#DC2626")))
            elif status == "approved":
                badge.setForeground(QBrush(QColor("#059669")))
            else:
                badge.setForeground(QBrush(QColor("#6B7280")))
            f = badge.font()
            f.setBold(True)
            badge.setFont(f)
            self._table.setItem(row, 9, badge)

            _cell(it.get("created_at", ""), 10)

            # Action buttons
            actions = QWidget()
            actions_lay = QHBoxLayout(actions)
            actions_lay.setContentsMargins(2, 2, 2, 2)
            actions_lay.setSpacing(4)

            item_id = it.get("id")

            if status == "pending":
                approve_btn = QPushButton("✅ Approve")
                approve_btn.setStyleSheet(_BTN_PRIMARY)
                approve_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                approve_btn.clicked.connect(lambda _, iid=item_id: self._approve_item(iid))
                actions_lay.addWidget(approve_btn)

                reject_btn = QPushButton("❌ Reject")
                reject_btn.setStyleSheet(_BTN_DANGER)
                reject_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                reject_btn.clicked.connect(lambda _, iid=item_id: self._reject_item(iid))
                actions_lay.addWidget(reject_btn)

                pai_btn = QPushButton("PAI")
                pai_btn.setStyleSheet(_BTN_ACCENT)
                pai_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                pai_btn.setToolTip("Enrich with PAI data")
                pai_btn.clicked.connect(lambda _, iid=item_id: self._enrich_pai(iid))
                actions_lay.addWidget(pai_btn)

                edit_btn = QPushButton("✏️ Edit")
                edit_btn.setStyleSheet(
                    "QPushButton { padding: 4px 8px; border: 1px solid #D1D5DB; "
                    "border-radius: 4px; font-size: 9pt; }"
                    "QPushButton:hover { background: #F3F4F6; }"
                )
                edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                edit_btn.clicked.connect(lambda _, iid=item_id: self._edit_item(iid))
                actions_lay.addWidget(edit_btn)

            elif status == "approved":
                pid = it.get("product_id")
                if pid:
                    open_btn = QPushButton("📄 Open Product")
                    open_btn.setStyleSheet(_BTN_PRIMARY)
                    open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                    open_btn.clicked.connect(lambda _, pid_=pid: self._open_product(pid_))
                    actions_lay.addWidget(open_btn)

            self._table.setCellWidget(row, 11, actions)
            self._table.setRowHeight(row, 38)

        self._count_label.setText(
            f"{len(items)} item(s) shown — {pending_count} pending review"
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _approve_item(self, item_id: int):
        from jaks_inventory.services.pre_inventory_service import approve_item

        result = approve_item(item_id)
        if result.get("success"):
            sku = result.get("sku", "")
            QMessageBox.information(
                self, "Approved",
                f"Part approved and added to inventory as {sku}.\n"
                f"Product ID: {result.get('product_id')}"
            )
            self._load_items()
        else:
            QMessageBox.warning(self, "Error", result.get("error", "Unknown error"))

    def _reject_item(self, item_id: int):
        reason, ok = QInputDialog.getText(
            self, "Reject Item", "Reason for rejection (optional):"
        )
        if not ok:
            return

        from jaks_inventory.services.pre_inventory_service import reject_item
        reject_item(item_id, reason=reason)
        self._load_items()
        self._status_label.setText(f"Item {item_id} rejected.")

    def _approve_all(self):
        pending = [it for it in self._items if it.get("status") == "pending"]
        if not pending:
            QMessageBox.information(self, "Nothing to Approve", "No pending items.")
            return

        reply = QMessageBox.question(
            self, "Approve All",
            f"Approve all {len(pending)} pending item(s) and add to inventory?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from jaks_inventory.services.pre_inventory_service import approve_item
        success = 0
        errors = []
        for it in pending:
            r = approve_item(it["id"])
            if r.get("success"):
                success += 1
            else:
                errors.append(f"ID {it['id']}: {r.get('error', 'Unknown')}")

        self._load_items()
        msg = f"✅ {success}/{len(pending)} items approved."
        if errors:
            msg += f"\n\nErrors:\n" + "\n".join(errors[:5])
        QMessageBox.information(self, "Bulk Approve", msg)

    def _enrich_pai(self, item_id: int):
        self._status_label.setText(f"Enriching item {item_id} with PAI…")
        QApplication.processEvents()

        from jaks_inventory.services.pre_inventory_service import enrich_pai
        result = enrich_pai(item_id)
        if result.get("success"):
            pai_result = result.get("pai_result", {})
            self._status_label.setText(
                f"✅ PAI enriched: {pai_result.get('pai_sku', 'N/A')} — "
                f"Cost: ${pai_result.get('cost', 0):.2f}"
            )
            self._load_items()
        else:
            self._status_label.setText(f"PAI enrichment failed: {result.get('error', '')}")

    def _edit_item(self, item_id: int):
        """Open a simple edit dialog for the pre-inventory item."""
        from jaks_inventory.services.pre_inventory_service import (
            get_pre_inventory_item,
            update_pre_inventory,
        )

        item = get_pre_inventory_item(item_id)
        if not item:
            return

        from PySide6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox, QDoubleSpinBox

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Edit Pre-Inventory #{item_id}")
        dlg.setMinimumWidth(500)
        form = QFormLayout(dlg)

        desc_edit = QLineEdit(item.get("description", ""))
        form.addRow("Description:", desc_edit)

        oem_edit = QLineEdit(item.get("oem_part_number", ""))
        form.addRow("OEM Part #:", oem_edit)

        pai_edit = QLineEdit(item.get("pai_part_number", ""))
        form.addRow("PAI Part #:", pai_edit)

        cost_spin = QDoubleSpinBox()
        cost_spin.setMaximum(99999)
        cost_spin.setDecimals(2)
        cost_spin.setPrefix("$")
        cost_spin.setValue(item.get("cost", 0) or 0)
        form.addRow("Cost:", cost_spin)

        price_spin = QDoubleSpinBox()
        price_spin.setMaximum(99999)
        price_spin.setDecimals(2)
        price_spin.setPrefix("$")
        price_spin.setValue(item.get("selling_price", 0) or 0)
        form.addRow("Price:", price_spin)

        notes_edit = QLineEdit(item.get("notes", ""))
        form.addRow("Notes:", notes_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            update_pre_inventory(
                item_id,
                description=desc_edit.text().strip(),
                oem_part_number=oem_edit.text().strip(),
                pai_part_number=pai_edit.text().strip(),
                cost=cost_spin.value(),
                selling_price=price_spin.value(),
                notes=notes_edit.text().strip(),
            )
            self._load_items()

    def _open_product(self, product_id: int):
        """Open the product detail dialog for an approved item."""
        try:
            from jaks_inventory.ui.product_detail_dialog import ProductDetailDialog
            dlg = ProductDetailDialog(product_id, parent=self)
            dlg.exec()
        except Exception as exc:
            QMessageBox.warning(self, "Error", str(exc))
