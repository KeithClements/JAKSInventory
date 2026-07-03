"""Pricing Maintenance – Bulk price adjustment for multiple products."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
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
)

from db.database import get_db


class PricingMaintenanceScreen(QWidget):
    """Allows user to adjust prices for many products at once."""

    navigate_to = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        QTimer.singleShot(200, self._refresh)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        title = QLabel("🔧 Pricing Maintenance")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1F2937;")
        layout.addWidget(title)

        # ── Filter / Scope ──
        scope_box = QGroupBox("Scope")
        sg = QGridLayout(scope_box)

        sg.addWidget(QLabel("Category:"), 0, 0)
        self._cat_combo = QComboBox()
        self._cat_combo.addItem("All Categories")
        self._cat_combo.currentIndexChanged.connect(self._refresh)
        sg.addWidget(self._cat_combo, 0, 1)

        sg.addWidget(QLabel("Manufacturer:"), 0, 2)
        self._mfr_combo = QComboBox()
        self._mfr_combo.addItem("All Manufacturers")
        self._mfr_combo.currentIndexChanged.connect(self._refresh)
        sg.addWidget(self._mfr_combo, 0, 3)

        layout.addWidget(scope_box)

        # ── Bulk Action Bar ──
        bulk = QGroupBox("Bulk Action")
        bg = QHBoxLayout(bulk)

        bg.addWidget(QLabel("Adjust:"))
        self._action_combo = QComboBox()
        self._action_combo.addItems([
            "Set margin %", "Set markup %", "Increase price %", "Decrease price %",
            "Set fixed price",
        ])
        bg.addWidget(self._action_combo)

        self._value_edit = QLineEdit()
        self._value_edit.setValidator(QDoubleValidator(0, 999999, 2))
        self._value_edit.setPlaceholderText("Value")
        self._value_edit.setFixedWidth(100)
        bg.addWidget(self._value_edit)

        apply_btn = QPushButton("✅ Apply to Selected")
        apply_btn.setStyleSheet(
            "QPushButton { background-color: #6B8E23; color: white; border: none; "
            "padding: 8px 16px; border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background-color: #556B2F; }"
        )
        apply_btn.clicked.connect(self._apply_bulk)
        bg.addWidget(apply_btn)

        select_all_btn = QPushButton("☑ Select All")
        select_all_btn.clicked.connect(self._select_all)
        bg.addWidget(select_all_btn)

        bg.addStretch()
        layout.addWidget(bulk)

        # ── Table ──
        cols = ["", "SKU", "Title", "Cost", "Current Price", "Margin %", "New Price"]
        self._table = QTableWidget()
        self._table.setColumnCount(len(cols))
        self._table.setHorizontalHeaderLabels(cols)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.setColumnWidth(0, 30)
        self._table.setStyleSheet(
            "QTableWidget { gridline-color: #e5e7eb; font-size: 12px; }"
            "QHeaderView::section { background: #f3f4f6; padding: 4px; "
            "border: 1px solid #d1d5db; font-weight: 600; font-size: 11px; }"
        )
        layout.addWidget(self._table)

    # ------------------------------------------------------------------
    def _refresh(self) -> None:
        try:
            with get_db() as conn:
                # Populate filter combos on first load
                if self._cat_combo.count() == 1:
                    cats = conn.execute(
                        "SELECT DISTINCT category FROM products WHERE category IS NOT NULL ORDER BY category"
                    ).fetchall()
                    for r in cats:
                        c = r["category"]
                        if c:
                            self._cat_combo.addItem(c)
                    mfrs = conn.execute(
                        "SELECT DISTINCT manufacturer FROM products WHERE manufacturer IS NOT NULL ORDER BY manufacturer"
                    ).fetchall()
                    for r in mfrs:
                        m = r["manufacturer"]
                        if m:
                            self._mfr_combo.addItem(m)

                where, params = [], []
                cat = self._cat_combo.currentText()
                if cat != "All Categories":
                    where.append("category = %s")
                    params.append(cat)
                mfr = self._mfr_combo.currentText()
                if mfr != "All Manufacturers":
                    where.append("manufacturer = %s")
                    params.append(mfr)

                sql = "SELECT id, sku, title, pai_cost, selling_price FROM products"
                if where:
                    sql += " WHERE " + " AND ".join(where)
                sql += " ORDER BY sku"
                rows = conn.execute(sql, tuple(params)).fetchall()
        except Exception:
            rows = []

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            pid = row["id"]
            cost = float(row["pai_cost"] or 0)
            price = float(row["selling_price"] or 0)
            margin = ((price - cost) / price * 100) if price else 0.0

            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            chk.setData(Qt.ItemDataRole.UserRole, pid)
            self._table.setItem(i, 0, chk)

            self._table.setItem(i, 1, QTableWidgetItem(row["sku"] or ""))
            ti = QTableWidgetItem(row["title"] or "")
            ti.setFlags(ti.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(i, 2, ti)

            for col, val, fmt in [(3, cost, "$"), (4, price, "$"), (5, margin, "%")]:
                item = QTableWidgetItem(
                    f"${val:,.2f}" if fmt == "$" else f"{val:.1f}%"
                )
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(i, col, item)

            self._table.setItem(i, 6, QTableWidgetItem(""))  # New price (calculated)

        self._table.setSortingEnabled(True)

    # ------------------------------------------------------------------
    def _select_all(self) -> None:
        for r in range(self._table.rowCount()):
            item = self._table.item(r, 0)
            if item:
                item.setCheckState(Qt.CheckState.Checked)

    # ------------------------------------------------------------------
    def _apply_bulk(self) -> None:
        try:
            val = float(self._value_edit.text())
        except ValueError:
            QMessageBox.warning(self, "Input Error", "Enter a valid number.")
            return

        action = self._action_combo.currentText()
        selected_ids = []
        updates = []

        for r in range(self._table.rowCount()):
            chk = self._table.item(r, 0)
            if not chk or chk.checkState() != Qt.CheckState.Checked:
                continue
            pid = chk.data(Qt.ItemDataRole.UserRole)
            cost_text = self._table.item(r, 3).text().replace("$", "").replace(",", "")
            price_text = self._table.item(r, 4).text().replace("$", "").replace(",", "")
            cost = float(cost_text) if cost_text else 0
            old_price = float(price_text) if price_text else 0

            if action == "Set margin %":
                new_price = cost / (1 - val / 100) if val < 100 else old_price
            elif action == "Set markup %":
                new_price = cost * (1 + val / 100)
            elif action == "Increase price %":
                new_price = old_price * (1 + val / 100)
            elif action == "Decrease price %":
                new_price = old_price * (1 - val / 100)
            elif action == "Set fixed price":
                new_price = val
            else:
                new_price = old_price

            new_price = round(new_price, 2)
            selected_ids.append(pid)
            updates.append((new_price, pid))
            self._table.item(r, 6).setText(f"${new_price:,.2f}")

        if not selected_ids:
            QMessageBox.information(self, "No Selection", "Check the rows you want to update.")
            return

        ans = QMessageBox.question(
            self, "Confirm",
            f"Update selling price for {len(selected_ids)} product(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        try:
            with get_db() as conn:
                for new_price, pid in updates:
                    conn.execute(
                        "UPDATE products SET selling_price = %s WHERE id = %s",
                        (new_price, pid),
                    )
                conn.commit()
            QMessageBox.information(self, "Done", f"Updated {len(updates)} product(s).")
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
