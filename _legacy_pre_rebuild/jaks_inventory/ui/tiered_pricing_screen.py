"""Tiered Pricing — Category × Cost-Band → Margin × Customer-Tier discount admin.

This is the v2 UI built on migration 058. Three tabs:

  1. Categories & Bands — pick a category (A..E), edit its cost-band → margin
     table.
  2. Tier × Category Matrix — rows = customer tiers, cols = categories,
     cells = discount % (inline editable).
  3. Vendor Mapping — list every vendor and assign a price category to
     each. (As of migration 065, vendor mapping replaces the legacy
     manufacturer mapping introduced in migration 058.)

The legacy ``pricing_tiers`` table from the prior screen is left dormant.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db import (
    create_customer_tier,
    create_price_category,
    delete_customer_tier,
    delete_price_category,
    get_all_vendors,
    get_tier_discount_grid,
    get_vendor_category,
    list_bands,
    list_customer_tiers,
    list_price_categories,
    set_bands,
    set_tier_category_discount,
    set_vendor_category,
    update_customer_tier,
    update_price_category,
)
from db.database import get_db  # noqa: F401  (kept for future ad-hoc queries)


# ── Helpers ──────────────────────────────────────────────────────────

_BTN_PRIMARY = (
    "QPushButton { background-color: #6B8E23; color: white; border: none; "
    "padding: 8px 16px; border-radius: 4px; font-weight: bold; }"
    "QPushButton:hover { background-color: #556B2F; }"
)
_BTN_DANGER = (
    "QPushButton { background-color: #c0392b; color: white; border: none; "
    "padding: 6px 14px; border-radius: 4px; font-weight: bold; }"
    "QPushButton:hover { background-color: #962d22; }"
)
_TABLE_STYLE = (
    "QTableWidget { gridline-color: #e5e7eb; font-size: 12px; }"
    "QHeaderView::section { background: #f3f4f6; padding: 4px; "
    "border: 1px solid #d1d5db; font-weight: 600; font-size: 11px; }"
)


def _list_active_vendors() -> list[dict]:
    """Return [{id, name, code}, ...] for all active vendors, ordered by name."""
    out: list[dict] = []
    try:
        rows = get_all_vendors(active_only=True) or []
    except Exception:
        rows = []
    for r in rows:
        if not isinstance(r, dict):
            try:
                r = dict(r)
            except Exception:
                continue
        vid = r.get("id")
        name = r.get("name") or r.get("code") or ""
        if vid is None or not name:
            continue
        out.append({"id": int(vid), "name": str(name), "code": str(r.get("code") or "")})
    return out


# ── Category dialog ──────────────────────────────────────────────────

class _CategoryDialog(QDialog):
    def __init__(self, category: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Category" if category else "New Category")
        self.setMinimumWidth(360)
        form = QFormLayout(self)

        self._code = QLineEdit(category.get("code", "") if category else "")
        self._code.setMaxLength(8)
        form.addRow("Code:", self._code)

        self._name = QLineEdit(category.get("name", "") if category else "")
        form.addRow("Name:", self._name)

        self._desc = QTextEdit()
        self._desc.setMaximumHeight(60)
        if category:
            self._desc.setPlainText(category.get("description") or "")
        form.addRow("Description:", self._desc)

        self._sort = QSpinBox()
        self._sort.setRange(0, 999)
        self._sort.setValue(int(category.get("sort_order", 0) or 0) if category else 99)
        form.addRow("Sort order:", self._sort)

        btns = QHBoxLayout()
        save = QPushButton("Save")
        save.setStyleSheet(_BTN_PRIMARY)
        save.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(save)
        btns.addWidget(cancel)
        form.addRow(btns)

    def get_data(self) -> dict:
        return {
            "code": self._code.text().strip().upper(),
            "name": self._name.text().strip(),
            "description": self._desc.toPlainText().strip(),
            "sort_order": self._sort.value(),
        }


# ── Tier dialog ──────────────────────────────────────────────────────

class _TierDialog(QDialog):
    def __init__(self, tier: dict | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Tier" if tier else "New Tier")
        self.setMinimumWidth(360)
        form = QFormLayout(self)

        self._name = QLineEdit(tier.get("name", "") if tier else "")
        form.addRow("Name:", self._name)

        self._desc = QTextEdit()
        self._desc.setMaximumHeight(60)
        if tier:
            self._desc.setPlainText(tier.get("description") or "")
        form.addRow("Description:", self._desc)

        self._sort = QSpinBox()
        self._sort.setRange(0, 999)
        self._sort.setValue(int(tier.get("sort_order", 0) or 0) if tier else 99)
        form.addRow("Sort order:", self._sort)

        btns = QHBoxLayout()
        save = QPushButton("Save")
        save.setStyleSheet(_BTN_PRIMARY)
        save.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(save)
        btns.addWidget(cancel)
        form.addRow(btns)

    def get_data(self) -> dict:
        return {
            "name": self._name.text().strip(),
            "description": self._desc.toPlainText().strip(),
            "sort_order": self._sort.value(),
        }


# ── Tab 1: Categories & Bands ────────────────────────────────────────

class _CategoriesBandsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_category_id: int | None = None
        self._build_ui()
        QTimer.singleShot(50, self._refresh_categories)

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        # Left: category list + buttons
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Price Categories</b>"))
        self._cat_table = QTableWidget()
        self._cat_table.setColumnCount(3)
        self._cat_table.setHorizontalHeaderLabels(["Code", "Name", "Default"])
        self._cat_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._cat_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._cat_table.horizontalHeader().setStretchLastSection(True)
        self._cat_table.setStyleSheet(_TABLE_STYLE)
        self._cat_table.setMinimumWidth(280)
        self._cat_table.itemSelectionChanged.connect(self._on_category_selected)
        left.addWidget(self._cat_table)

        cat_btns = QHBoxLayout()
        add_cat = QPushButton("➕ New")
        add_cat.setStyleSheet(_BTN_PRIMARY)
        add_cat.clicked.connect(self._add_category)
        edit_cat = QPushButton("✏️ Edit")
        edit_cat.clicked.connect(self._edit_category)
        del_cat = QPushButton("🗑️ Delete")
        del_cat.setStyleSheet(_BTN_DANGER)
        del_cat.clicked.connect(self._delete_category)
        default_cat = QPushButton("⭐ Make Default")
        default_cat.clicked.connect(self._make_default)
        cat_btns.addWidget(add_cat)
        cat_btns.addWidget(edit_cat)
        cat_btns.addWidget(default_cat)
        cat_btns.addWidget(del_cat)
        cat_btns.addStretch()
        left.addLayout(cat_btns)

        root.addLayout(left, 1)

        # Right: bands table
        right = QVBoxLayout()
        self._bands_label = QLabel("<b>Cost-Band Margins</b>")
        right.addWidget(self._bands_label)

        info = QLabel(
            "Each row defines a cost range and the margin % to apply.\n"
            "Final list price = cost × (1 + margin %).\n"
            "Leave the last row's <i>Cost Max</i> blank for ∞."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#666; font-size:11px;")
        right.addWidget(info)

        self._bands_table = QTableWidget()
        self._bands_table.setColumnCount(3)
        self._bands_table.setHorizontalHeaderLabels(["Cost Min ($)", "Cost Max ($)", "Margin %"])
        self._bands_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._bands_table.setStyleSheet(_TABLE_STYLE)
        right.addWidget(self._bands_table)

        band_btns = QHBoxLayout()
        add_band = QPushButton("➕ Add Row")
        add_band.clicked.connect(self._add_band_row)
        del_band = QPushButton("➖ Remove Row")
        del_band.clicked.connect(self._remove_band_row)
        save_bands = QPushButton("💾 Save Bands")
        save_bands.setStyleSheet(_BTN_PRIMARY)
        save_bands.clicked.connect(self._save_bands)
        band_btns.addWidget(add_band)
        band_btns.addWidget(del_band)
        band_btns.addStretch()
        band_btns.addWidget(save_bands)
        right.addLayout(band_btns)

        root.addLayout(right, 2)

    def _refresh_categories(self):
        cats = list_price_categories(active_only=False)
        self._cat_table.setRowCount(len(cats))
        for i, c in enumerate(cats):
            id_item = QTableWidgetItem(c.get("code", ""))
            id_item.setData(Qt.ItemDataRole.UserRole, c["id"])
            self._cat_table.setItem(i, 0, id_item)
            self._cat_table.setItem(i, 1, QTableWidgetItem(c.get("name", "")))
            self._cat_table.setItem(i, 2, QTableWidgetItem("⭐" if c.get("is_default") else ""))
        if cats and self._current_category_id is None:
            self._cat_table.selectRow(0)

    def refresh(self):
        self._refresh_categories()

    def _selected_category_id(self) -> int | None:
        sel_model = self._cat_table.selectionModel()
        rows = sel_model.selectedRows() if sel_model else []
        if not rows:
            return None
        item = self._cat_table.item(rows[0].row(), 0)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _on_category_selected(self):
        cid = self._selected_category_id()
        if cid is None:
            return
        self._current_category_id = cid
        cat = next((c for c in list_price_categories(active_only=False) if c["id"] == cid), None)
        if cat:
            self._bands_label.setText(
                f"<b>Cost-Band Margins — {cat.get('code')} · {cat.get('name')}</b>"
            )
        self._load_bands(cid)

    def _add_category(self):
        dlg = _CategoryDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            if not data["code"] or not data["name"]:
                QMessageBox.warning(self, "Missing fields", "Code and Name are required.")
                return
            try:
                create_price_category(
                    data["code"], data["name"],
                    description=data["description"],
                    sort_order=data["sort_order"],
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not create category: {e}")
                return
            self._refresh_categories()

    def _edit_category(self):
        cid = self._selected_category_id()
        if cid is None:
            return
        cat = next((c for c in list_price_categories(active_only=False) if c["id"] == cid), None)
        if not cat:
            return
        dlg = _CategoryDialog(cat, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            try:
                update_price_category(cid, **data)
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not update: {e}")
                return
            self._refresh_categories()

    def _delete_category(self):
        cid = self._selected_category_id()
        if cid is None:
            return
        if QMessageBox.question(
            self, "Delete category",
            "This removes the category, its bands, manufacturer mappings, "
            "and any tier discounts on it. Continue?",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_price_category(cid)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not delete: {e}")
            return
        self._current_category_id = None
        self._refresh_categories()
        self._bands_table.setRowCount(0)

    def _make_default(self):
        cid = self._selected_category_id()
        if cid is None:
            return
        update_price_category(cid, is_default=True)
        self._refresh_categories()

    # — Bands —
    def _load_bands(self, category_id: int):
        bands = list_bands(category_id)
        self._bands_table.setRowCount(len(bands))
        for i, b in enumerate(bands):
            self._bands_table.setItem(i, 0, QTableWidgetItem(f"{float(b.get('cost_min') or 0):.2f}"))
            cmax = b.get("cost_max")
            self._bands_table.setItem(i, 1, QTableWidgetItem("" if cmax is None else f"{float(cmax):.2f}"))
            self._bands_table.setItem(i, 2, QTableWidgetItem(f"{float(b.get('margin_pct') or 0):.2f}"))

    def _add_band_row(self):
        if self._current_category_id is None:
            QMessageBox.information(self, "Pick a category", "Select a category first.")
            return
        r = self._bands_table.rowCount()
        self._bands_table.insertRow(r)
        prev_max = ""
        if r > 0:
            prev_item = self._bands_table.item(r - 1, 1)
            prev_max = prev_item.text() if prev_item else ""
        self._bands_table.setItem(r, 0, QTableWidgetItem(prev_max or "0"))
        self._bands_table.setItem(r, 1, QTableWidgetItem(""))
        self._bands_table.setItem(r, 2, QTableWidgetItem("0"))

    def _remove_band_row(self):
        sel_model = self._bands_table.selectionModel()
        sel = sel_model.selectedRows() if sel_model else []
        if not sel:
            r = self._bands_table.rowCount() - 1
            if r >= 0:
                self._bands_table.removeRow(r)
            return
        for ix in sorted([s.row() for s in sel], reverse=True):
            self._bands_table.removeRow(ix)

    def _save_bands(self):
        if self._current_category_id is None:
            return
        rows: list[tuple] = []
        for i in range(self._bands_table.rowCount()):
            cmin_item = self._bands_table.item(i, 0)
            cmax_item = self._bands_table.item(i, 1)
            margin_item = self._bands_table.item(i, 2)
            try:
                cmin = float((cmin_item.text() if cmin_item else "0").strip() or 0)
            except ValueError:
                QMessageBox.warning(self, "Invalid", f"Row {i+1}: Cost Min must be a number.")
                return
            cmax_text = (cmax_item.text() if cmax_item else "").strip()
            cmax = None
            if cmax_text:
                try:
                    cmax = float(cmax_text)
                except ValueError:
                    QMessageBox.warning(self, "Invalid", f"Row {i+1}: Cost Max must be a number or blank.")
                    return
            try:
                margin = float((margin_item.text() if margin_item else "0").strip() or 0)
            except ValueError:
                QMessageBox.warning(self, "Invalid", f"Row {i+1}: Margin must be a number.")
                return
            rows.append((cmin, cmax, margin))
        try:
            set_bands(self._current_category_id, rows)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Save failed: {e}")
            return
        QMessageBox.information(self, "Saved", "Cost bands saved.")


# ── Tab 2: Tier × Category Matrix ────────────────────────────────────

class _TierMatrixTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._categories: list[dict] = []
        self._tiers: list[dict] = []
        self._build_ui()
        QTimer.singleShot(50, self.refresh)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Customer Tier × Category Discount Matrix</b>"))
        header.addStretch()
        add_tier = QPushButton("➕ New Tier")
        add_tier.setStyleSheet(_BTN_PRIMARY)
        add_tier.clicked.connect(self._add_tier)
        edit_tier = QPushButton("✏️ Edit")
        edit_tier.clicked.connect(self._edit_tier)
        del_tier = QPushButton("🗑️ Delete")
        del_tier.setStyleSheet(_BTN_DANGER)
        del_tier.clicked.connect(self._delete_tier)
        save_btn = QPushButton("💾 Save Matrix")
        save_btn.setStyleSheet(_BTN_PRIMARY)
        save_btn.clicked.connect(self._save_matrix)
        header.addWidget(add_tier)
        header.addWidget(edit_tier)
        header.addWidget(del_tier)
        header.addWidget(save_btn)
        root.addLayout(header)

        info = QLabel(
            "Each cell is the discount % a customer in this tier receives "
            "on products in that category. Net price = list × (1 − discount %)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#666; font-size:11px;")
        root.addWidget(info)

        self._table = QTableWidget()
        self._table.setStyleSheet(_TABLE_STYLE)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        root.addWidget(self._table)

    def refresh(self):
        self._categories = list_price_categories(active_only=False)
        self._tiers = list_customer_tiers(active_only=False)
        grid = get_tier_discount_grid()

        cols = len(self._categories)
        self._table.setRowCount(len(self._tiers))
        self._table.setColumnCount(cols + 1)
        headers = ["Tier"] + [f"{c['code']} — {c['name']}" for c in self._categories]
        self._table.setHorizontalHeaderLabels(headers)

        for r, tier in enumerate(self._tiers):
            name = tier["name"] + (" ⭐" if tier.get("is_default") else "")
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.ItemDataRole.UserRole, tier["id"])
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            font = name_item.font()
            font.setBold(True)
            name_item.setFont(font)
            self._table.setItem(r, 0, name_item)
            for c, cat in enumerate(self._categories):
                val = grid.get(tier["id"], {}).get(cat["id"], 0.0)
                cell = QTableWidgetItem(f"{val:.2f}")
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(r, c + 1, cell)

    def _selected_tier_id(self) -> int | None:
        sel_model = self._table.selectionModel()
        rows = sel_model.selectedRows() if sel_model else []
        if not rows:
            return None
        item = self._table.item(rows[0].row(), 0)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _add_tier(self):
        dlg = _TierDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            d = dlg.get_data()
            if not d["name"]:
                QMessageBox.warning(self, "Missing", "Name required.")
                return
            try:
                create_customer_tier(d["name"], description=d["description"], sort_order=d["sort_order"])
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not create tier: {e}")
                return
            self.refresh()

    def _edit_tier(self):
        tid = self._selected_tier_id()
        if tid is None:
            return
        tier = next((t for t in self._tiers if t["id"] == tid), None)
        if not tier:
            return
        dlg = _TierDialog(tier, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            d = dlg.get_data()
            update_customer_tier(tid, **d)
            self.refresh()

    def _delete_tier(self):
        tid = self._selected_tier_id()
        if tid is None:
            return
        if QMessageBox.question(
            self, "Delete tier",
            "Remove this tier and all its discount entries?",
        ) != QMessageBox.StandardButton.Yes:
            return
        delete_customer_tier(tid)
        self.refresh()

    def _save_matrix(self):
        try:
            for r, tier in enumerate(self._tiers):
                for c, cat in enumerate(self._categories):
                    item = self._table.item(r, c + 1)
                    if item is None:
                        continue
                    txt = (item.text() or "0").strip()
                    try:
                        val = float(txt)
                    except ValueError:
                        QMessageBox.warning(
                            self, "Invalid",
                            f"Tier '{tier['name']}' / Category {cat['code']}: not a number.",
                        )
                        return
                    set_tier_category_discount(tier["id"], cat["id"], val)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Save failed: {e}")
            return
        QMessageBox.information(self, "Saved", "Discount matrix saved.")


# ── Tab 3: Vendor mapping ────────────────────────────────────────────

class _VendorMappingTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._categories: list[dict] = []
        self._vendors: list[dict] = []
        self._build_ui()
        QTimer.singleShot(50, self.refresh)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Vendor → Price Category</b>"))
        header.addStretch()
        save_btn = QPushButton("💾 Save Mappings")
        save_btn.setStyleSheet(_BTN_PRIMARY)
        save_btn.clicked.connect(self._save)
        header.addWidget(save_btn)
        root.addLayout(header)

        info = QLabel(
            "Every active vendor appears below. Pick a category for each. "
            "Vendors without a mapping fall back to the manufacturer mapping "
            "(legacy) and finally the default category. A product's category "
            "override on the product edit dialog wins over this mapping."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color:#666; font-size:11px;")
        root.addWidget(info)

        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["Vendor", "Category"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(36)
        self._table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self._table.setShowGrid(True)
        self._table.setStyleSheet(_TABLE_STYLE)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        root.addWidget(self._table)

    def refresh(self):
        self._categories = list_price_categories(active_only=False)
        self._vendors = _list_active_vendors()
        self._table.setRowCount(len(self._vendors))
        for i, v in enumerate(self._vendors):
            label = v["name"]
            if v.get("code"):
                label = f"{v['name']}  ({v['code']})"
            name_item = QTableWidgetItem(label)
            name_item.setData(Qt.ItemDataRole.UserRole, v["id"])
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            self._table.setItem(i, 0, name_item)

            combo = QComboBox()
            combo.addItem("— inherit default —", None)
            for c in self._categories:
                combo.addItem(f"{c['code']} — {c['name']}", c["id"])
            current = get_vendor_category(v["id"])
            if current is not None:
                ix = combo.findData(current)
                if ix >= 0:
                    combo.setCurrentIndex(ix)

            # Fixed-height combo so every row renders the same way. The
            # previous wrapper-widget approach caused inconsistent row
            # heights on some platforms.
            combo.setFixedHeight(28)
            self._table.setCellWidget(i, 1, combo)
            self._table.setRowHeight(i, 36)

    def _save(self):
        try:
            count = 0
            for i in range(self._table.rowCount()):
                name_item = self._table.item(i, 0)
                widget = self._table.cellWidget(i, 1)
                if not name_item or widget is None:
                    continue
                combo = widget if isinstance(widget, QComboBox) else widget.findChild(QComboBox)
                if combo is None:
                    continue
                vid = name_item.data(Qt.ItemDataRole.UserRole)
                if vid is None:
                    continue
                cat_id = combo.currentData()
                set_vendor_category(int(vid), cat_id)
                count += 1
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Save failed: {e}")
            return
        QMessageBox.information(self, "Saved", f"{count} vendor mappings saved.")


# ── Main screen ──────────────────────────────────────────────────────

class TieredPricingScreen(QWidget):
    """Category-based tiered pricing admin (migration 058)."""

    navigate_to = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        title = QLabel("📊 Tiered Pricing")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #1F2937;")
        layout.addWidget(title)

        sub = QLabel(
            "Category-based pricing engine. Categories own cost-band margins; "
            "customer tiers carry per-category discounts; vendors map to a "
            "category by default. Products may override their vendor's category."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color:#555; font-size:12px;")
        layout.addWidget(sub)

        self._tabs = QTabWidget()
        self._cat_tab = _CategoriesBandsTab()
        self._matrix_tab = _TierMatrixTab()
        self._mfr_tab = _VendorMappingTab()
        self._tabs.addTab(self._cat_tab, "Categories && Bands")
        self._tabs.addTab(self._matrix_tab, "Tier × Category Matrix")
        self._tabs.addTab(self._mfr_tab, "Vendor Mapping")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs)

    def _on_tab_changed(self, index: int):
        widget = self._tabs.widget(index)
        if hasattr(widget, "refresh"):
            widget.refresh()
