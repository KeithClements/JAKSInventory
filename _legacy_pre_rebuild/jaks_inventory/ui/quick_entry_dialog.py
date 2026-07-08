"""Quick Entry Dialog - Ultra-fast keyboard-first product creation.

Tab order: Vendor -> Category -> Subcategory -> Name -> OEM -> Vendor SKU ->
           Cost -> Sell Price -> Margin -> Qty Min -> Qty Max -> Save

Shortcuts:
  ENTER       = next field (except in buttons)
  CTRL+ENTER  = Save & Duplicate
  ALT+ENTER   = Save & New
  ESC         = Cancel
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QKeySequence, QShortcut, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db import (
    create_product,
    create_vendor,
    find_similar_products,
    generate_next_sku,
    get_all_categories,
    get_all_subcategories,
    get_all_vendors,
    get_product_by_sku,
    get_sku_prefix_for_vendor,
    get_subcategories_for_category,
    get_vendor,
)

from .searchable_combo import SearchableComboBox


# =====================================================================
#  Pricing rules: cost -> suggested margin %
# =====================================================================

def _suggested_margin(cost: float) -> float:
    """Return the suggested margin % based on cost tiers."""
    if cost <= 0:
        return 0.0
    if cost < 15:
        return 40.0
    if cost <= 40:
        return 35.0
    if cost <= 75:
        return 32.5
    return 30.0


def _price_from_margin(cost: float, margin_pct: float) -> float:
    """Calculate sell price from cost and margin %."""
    if margin_pct >= 100 or margin_pct < 0 or cost <= 0:
        return 0.0
    return round(cost / (1 - margin_pct / 100), 2)


def _margin_from_price(cost: float, price: float) -> float:
    """Calculate margin % from cost and sell price."""
    if price <= 0 or cost <= 0:
        return 0.0
    return round(((price - cost) / price) * 100, 1)


# =====================================================================
# Lightweight inline "add" dialogs
# =====================================================================

# NOTE: The legacy ``_AddVendorDialog`` was replaced by
# ``VendorDialog(compact=True)`` so Quick Entry creates a vendor with the
# same validation + SKU-prefix support as the full vendor screen.


class _AddCategoryDialog(QDialog):
    """Minimal dialog to add a new category name."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Category")
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name = QLineEdit()
        self._name.setPlaceholderText("Category name")
        form.addRow("Category:", self._name)

        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self.created_name: str | None = None
        self._name.setFocus()

    def _on_save(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Required", "Category name is required.")
            self._name.setFocus()
            return
        self.created_name = name
        self.accept()


class _AddSubCategoryDialog(QDialog):
    """Minimal dialog to add a new sub-category under a parent category."""

    def __init__(self, parent_category: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New Sub Category")
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._parent = QLineEdit(parent_category)
        self._parent.setReadOnly(True)
        self._parent.setStyleSheet("background:#f0f0f0;")
        form.addRow("Parent Category:", self._parent)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Sub-category name")
        form.addRow("Sub Category:", self._name)

        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self.created_name: str | None = None
        self._name.setFocus()

    def _on_save(self) -> None:
        name = self._name.text().strip()
        if not name:
            QMessageBox.warning(self, "Required", "Sub-category name is required.")
            self._name.setFocus()
            return
        self.created_name = name
        self.accept()


# =====================================================================
#  Quick Entry Dialog
# =====================================================================

_ERR_STYLE = "border: 1px solid #c62828;"
_ERR_MSG_STYLE = "color: #c62828; font-size: 8pt; margin: 0; padding: 0;"
_BTN_GREEN = (
    "QPushButton { background-color: #4a6741; color: white; font-weight: bold; "
    "padding: 6px 14px; border-radius: 4px; font-size: 9pt; }"
    "QPushButton:hover { background-color: #5a7a51; }"
)
_BTN_BLUE = (
    "QPushButton { background-color: #1565c0; color: white; font-weight: bold; "
    "padding: 6px 14px; border-radius: 4px; font-size: 9pt; }"
    "QPushButton:hover { background-color: #1976d2; }"
)
_BTN_GRAY = (
    "QPushButton { padding: 6px 14px; border: 1px solid #aaa; "
    "border-radius: 4px; font-size: 9pt; }"
    "QPushButton:hover { background-color: #ececec; }"
)


class QuickEntryDialog(QDialog):
    """Compact dialog for rapid bulk part creation.

    Keyboard-first design optimised for entering 50+ parts quickly.
    """

    # Persisted across instances within the same session
    _last_saved: dict[str, Any] = {}

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick Entry")
        self.setMinimumWidth(700)
        self._duplicate_next = False
        self._new_next = False
        self._updating_prices = False  # guard against recalc loops

        self._build_ui()
        self._set_tab_order()
        self._pre_fill_from_last()
        self._setup_shortcuts()
        self._install_enter_navigation()

        # Auto-focus first field
        QTimer.singleShot(0, lambda: self._vendor_combo.setFocus())

    # ==================================================================
    #  BUILD UI
    # ==================================================================

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        # -- Left: entry form --
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # -- 1. Vendor --
        self._vendor_combo = SearchableComboBox(
            placeholder="Type to search vendors... (None = Pre-Inventory)",
            add_label="+ Vendor",
        )
        vendors = get_all_vendors()
        self._vendor_combo.setItems(
            [("(None)", None)] + [(v["name"], v["id"]) for v in vendors]
        )
        self._vendor_combo.currentChanged.connect(self._on_vendor_changed)
        self._vendor_combo.addRequested.connect(self._on_add_vendor)
        self._vendor_combo._add_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        form.addRow("Vendor:", self._vendor_combo)

        # -- 2. Category --
        self._category_combo = SearchableComboBox(
            placeholder="Type to search categories...",
            add_label="+ Category",
        )
        cats = get_all_categories()
        self._category_combo.setItems([(c, c) for c in cats])
        self._category_combo.currentChanged.connect(self._on_category_changed)
        self._category_combo.addRequested.connect(self._on_add_category)
        self._category_combo._add_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        form.addRow("Category:", self._category_combo)

        # Inline error label for Category
        self._cat_err = QLabel("")
        self._cat_err.setStyleSheet(_ERR_MSG_STYLE)
        self._cat_err.hide()
        form.addRow("", self._cat_err)

        # -- 3. Sub Category --
        self._subcategory_combo = SearchableComboBox(
            placeholder="Select a category first...",
            add_label="+ Sub Cat",
        )
        self._subcategory_combo.setEnabled(False)
        self._subcategory_combo.addRequested.connect(self._on_add_subcategory)
        self._subcategory_combo._add_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        form.addRow("Sub Category:", self._subcategory_combo)

        # -- 4. Product Name --
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Product title (required)")
        form.addRow("Name:", self._name_edit)

        # Inline error label for Name
        self._name_err = QLabel("")
        self._name_err.setStyleSheet(_ERR_MSG_STYLE)
        self._name_err.hide()
        form.addRow("", self._name_err)

        # -- 5. OEM Part Number --
        self._oem_edit = QLineEdit()
        self._oem_edit.setPlaceholderText("OEM / cross-reference part #")
        form.addRow("OEM Part #:", self._oem_edit)

        # -- 6. Vendor SKU --
        self._vendor_sku_edit = QLineEdit()
        self._vendor_sku_edit.setPlaceholderText("Supplier's part number")
        self._vendor_sku_edit.textChanged.connect(lambda _: self._update_sku_preview())
        form.addRow("Vendor SKU:", self._vendor_sku_edit)

        # -- 7. Cost --
        self._cost_spin = QDoubleSpinBox()
        self._cost_spin.setRange(0, 999999.99)
        self._cost_spin.setDecimals(2)
        self._cost_spin.setPrefix("$")
        self._cost_spin.valueChanged.connect(self._on_cost_changed)
        form.addRow("Cost:", self._cost_spin)

        # -- 8. Sell Price --
        self._sell_spin = QDoubleSpinBox()
        self._sell_spin.setRange(0, 999999.99)
        self._sell_spin.setDecimals(2)
        self._sell_spin.setPrefix("$")
        self._sell_spin.valueChanged.connect(self._on_sell_changed)
        form.addRow("Sell Price:", self._sell_spin)

        # -- 9. Margin % --
        margin_row = QHBoxLayout()
        self._margin_spin = QDoubleSpinBox()
        self._margin_spin.setRange(0, 99.9)
        self._margin_spin.setDecimals(1)
        self._margin_spin.setSuffix("%")
        self._margin_spin.valueChanged.connect(self._on_margin_changed)
        margin_row.addWidget(self._margin_spin)
        self._margin_hint = QLabel("")
        self._margin_hint.setStyleSheet("color:#228B22; font-size:8pt;")
        margin_row.addWidget(self._margin_hint)
        margin_row.addStretch()
        form.addRow("Margin:", margin_row)

        # -- Core Charge (optional; for parts with returnable cores) --
        self._core_charge_spin = QDoubleSpinBox()
        self._core_charge_spin.setRange(0, 999999.99)
        self._core_charge_spin.setDecimals(2)
        self._core_charge_spin.setPrefix("$")
        self._core_charge_spin.setToolTip(
            "Core deposit owed to the vendor when an old core is returned.\n"
            "Leave at $0 if this part has no core charge."
        )
        form.addRow("\U0001F504 Core:", self._core_charge_spin)

        # -- 10 & 11. Min / Max Qty --
        qty_row = QHBoxLayout()
        self._min_spin = QSpinBox()
        self._min_spin.setRange(0, 99999)
        qty_row.addWidget(QLabel("Min:"))
        qty_row.addWidget(self._min_spin)
        self._max_spin = QSpinBox()
        self._max_spin.setRange(0, 99999)
        qty_row.addWidget(QLabel("Max:"))
        qty_row.addWidget(self._max_spin)
        qty_row.addStretch()
        form.addRow("Qty:", qty_row)

        # -- Description --
        desc_row = QVBoxLayout()
        desc_hdr = QHBoxLayout()
        desc_hdr.addWidget(QLabel("Description:"))
        desc_hdr.addStretch()
        self._ai_gen_btn = QPushButton("AI Generate")
        self._ai_gen_btn.setStyleSheet(
            "QPushButton { background-color: #7b1fa2; color: white; "
            "padding: 2px 10px; border-radius: 3px; font-size: 8pt; }"
            "QPushButton:hover { background-color: #9c27b0; }"
        )
        self._ai_gen_btn.setToolTip("Generate description using Grok AI")
        self._ai_gen_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._ai_gen_btn.clicked.connect(self._on_ai_generate)
        desc_hdr.addWidget(self._ai_gen_btn)
        desc_row.addLayout(desc_hdr)
        self._desc_edit = QTextEdit()
        self._desc_edit.setPlaceholderText("Product description (HTML). Click AI Generate to auto-create.")
        self._desc_edit.setMaximumHeight(100)
        desc_row.addWidget(self._desc_edit)
        form.addRow("", desc_row)

        # -- SKU (read-only, at bottom) --
        self._sku_label = QLabel("Auto-generated on save")
        self._sku_label.setStyleSheet(
            "color: #888; font-style: italic; padding: 4px 0; font-size: 9pt;"
        )
        form.addRow("SKU:", self._sku_label)

        body.addLayout(form, 3)

        # -- Right: last-used panel --
        panel = QGroupBox("Last Used")
        panel.setStyleSheet("QGroupBox { font-size: 9pt; }")
        pv = QVBoxLayout(panel)
        self._last_info = QLabel("No previous entry yet.")
        self._last_info.setWordWrap(True)
        self._last_info.setStyleSheet("color:#666; font-size:9pt;")
        pv.addWidget(self._last_info)
        self._reuse_btn = QPushButton("Reuse Last Values")
        self._reuse_btn.setToolTip("Populate Vendor, Category, Subcategory, Margin from last save")
        self._reuse_btn.clicked.connect(self._apply_last_values)
        self._reuse_btn.setEnabled(bool(QuickEntryDialog._last_saved))
        self._reuse_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        pv.addWidget(self._reuse_btn)
        pv.addStretch()
        body.addWidget(panel, 1)

        # -- Buttons --
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(_BTN_GRAY)
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cancel_btn.setAutoDefault(False)
        cancel_btn.setDefault(False)
        btn_row.addWidget(cancel_btn)

        self._save_btn = QPushButton("Save && Close")
        self._save_btn.setAutoDefault(False)
        self._save_btn.setDefault(False)
        self._save_btn.setStyleSheet(_BTN_GREEN)
        self._save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(self._save_btn)

        self._dup_btn = QPushButton("Save && Duplicate  (Ctrl+Enter)")
        self._dup_btn.setToolTip("Save and reopen with Vendor/Category/Margin retained")
        self._dup_btn.setStyleSheet(_BTN_BLUE)
        self._dup_btn.setAutoDefault(False)
        self._dup_btn.setDefault(False)
        self._dup_btn.clicked.connect(self._on_save_and_duplicate)
        btn_row.addWidget(self._dup_btn)

        self._new_btn = QPushButton("Save && New  (Alt+Enter)")
        self._new_btn.setToolTip("Save and open a completely blank form")
        self._new_btn.setStyleSheet(_BTN_GRAY)
        self._new_btn.setAutoDefault(False)
        self._new_btn.setDefault(False)
        self._new_btn.clicked.connect(self._on_save_and_new)
        btn_row.addWidget(self._new_btn)

        root.addLayout(btn_row)

    # ==================================================================
    #  TAB ORDER
    # ==================================================================

    def _set_tab_order(self) -> None:
        """Enforce the exact field tab order."""
        chain = [
            self._vendor_combo._combo,
            self._category_combo._combo,
            self._subcategory_combo._combo,
            self._name_edit,
            self._oem_edit,
            self._vendor_sku_edit,
            self._cost_spin,
            self._sell_spin,
            self._margin_spin,
            self._core_charge_spin,
            self._min_spin,
            self._max_spin,
            self._desc_edit,
            self._save_btn,
            self._dup_btn,
            self._new_btn,
        ]
        for i in range(len(chain) - 1):
            QWidget.setTabOrder(chain[i], chain[i + 1])

    # ==================================================================
    #  ENTER KEY = NEXT FIELD (not close dialog)
    # ==================================================================

    def _install_enter_navigation(self) -> None:
        """Make ENTER act like TAB in input fields."""
        self._enter_fields = [
            self._vendor_combo._combo.lineEdit(),
            self._category_combo._combo.lineEdit(),
            self._subcategory_combo._combo.lineEdit(),
            self._name_edit,
            self._oem_edit,
            self._vendor_sku_edit,
            self._cost_spin,
            self._sell_spin,
            self._margin_spin,
            self._core_charge_spin,
            self._min_spin,
            self._max_spin,
        ]
        for w in self._enter_fields:
            w.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            mods = event.modifiers()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                # Ctrl+Enter = Save & Duplicate
                if mods & Qt.KeyboardModifier.ControlModifier:
                    self._on_save_and_duplicate()
                    return True
                # Alt+Enter = Save & New
                if mods & Qt.KeyboardModifier.AltModifier:
                    self._on_save_and_new()
                    return True
                # Plain Enter in input fields = next field
                if obj in self._enter_fields:
                    # If a combo popup is open, let it handle Enter
                    for combo_widget in (self._vendor_combo, self._category_combo, self._subcategory_combo):
                        if obj is combo_widget._combo.lineEdit() and combo_widget._combo.view().isVisible():
                            return False  # let combo handle it
                    self.focusNextChild()
                    return True
        return super().eventFilter(obj, event)

    # ==================================================================
    #  SHORTCUTS
    # ==================================================================

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(
            self._on_save_and_duplicate
        )
        QShortcut(QKeySequence("Ctrl+Enter"), self).activated.connect(
            self._on_save_and_duplicate
        )
        QShortcut(QKeySequence("Alt+Return"), self).activated.connect(
            self._on_save_and_new
        )
        QShortcut(QKeySequence("Alt+Enter"), self).activated.connect(
            self._on_save_and_new
        )

    # ==================================================================
    #  VENDOR / CATEGORY / SUBCATEGORY HANDLERS
    # ==================================================================

    def _on_vendor_changed(self, text: str, vendor_id: Any) -> None:
        self._update_sku_preview()

    def _on_category_changed(self, text: str, _data: Any) -> None:
        """Refresh subcategory options when category changes."""
        cat = text.strip()
        if cat:
            subs = get_subcategories_for_category(cat)
            self._subcategory_combo.setItems([(s, s) for s in subs])
            self._subcategory_combo.setEnabled(True)
            self._subcategory_combo._combo.lineEdit().setPlaceholderText(
                "Type to search sub-categories..."
            )
        else:
            self._subcategory_combo.setItems([])
            self._subcategory_combo.setEnabled(False)
            self._subcategory_combo._combo.lineEdit().setPlaceholderText(
                "Select a category first..."
            )
        # Clear cat error
        self._cat_err.hide()

    # ------------------------------------------------------------------
    # Inline add dialogs
    # ------------------------------------------------------------------

    def _on_add_vendor(self) -> None:
        from .vendor_dialog import VendorDialog
        dlg = VendorDialog(parent=self, compact=True)
        if dlg.exec() and dlg.created_vendor:
            v = dlg.created_vendor
            vendors = get_all_vendors()
            self._vendor_combo.setItems(
                [("(None)", None)] + [(vn["name"], vn["id"]) for vn in vendors]
            )
            self._vendor_combo.setCurrentByData(v["id"])

    def _on_add_category(self) -> None:
        dlg = _AddCategoryDialog(parent=self)
        if dlg.exec() and dlg.created_name:
            name = dlg.created_name
            cats = get_all_categories()
            if name not in cats:
                cats.append(name)
                cats.sort()
            self._category_combo.setItems([(c, c) for c in cats])
            self._category_combo.setCurrentByText(name)
            # Immediately enable subcategory for the new category
            self._subcategory_combo.setItems([])
            self._subcategory_combo.setEnabled(True)
            self._subcategory_combo._combo.lineEdit().setPlaceholderText(
                "Type to search sub-categories..."
            )

    def _on_add_subcategory(self) -> None:
        cat = self._category_combo.currentText().strip()
        if not cat:
            QMessageBox.information(self, "Category Required", "Select a category first.")
            return
        dlg = _AddSubCategoryDialog(parent_category=cat, parent=self)
        if dlg.exec() and dlg.created_name:
            name = dlg.created_name
            subs = get_subcategories_for_category(cat)
            if name not in subs:
                subs.append(name)
                subs.sort()
            self._subcategory_combo.setItems([(s, s) for s in subs])
            self._subcategory_combo.setCurrentByText(name)

    # ==================================================================
    #  PRICING: COST ?" MARGIN ?" SELL PRICE
    # ==================================================================

    def _on_cost_changed(self, cost: float) -> None:
        if self._updating_prices:
            return
        self._updating_prices = True
        margin = self._margin_spin.value()
        if margin <= 0 and cost > 0:
            # Auto-suggest margin from pricing rules
            margin = _suggested_margin(cost)
            self._margin_spin.setValue(margin)
            self._margin_hint.setText(f"(auto: {margin:.0f}%)")
        if cost > 0 and margin > 0:
            price = _price_from_margin(cost, margin)
            self._sell_spin.setValue(price)
        self._update_sku_preview()
        self._updating_prices = False

    def _on_sell_changed(self, price: float) -> None:
        if self._updating_prices:
            return
        self._updating_prices = True
        cost = self._cost_spin.value()
        if cost > 0 and price > 0:
            margin = _margin_from_price(cost, price)
            self._margin_spin.setValue(margin)
            self._margin_hint.setText("")
        self._updating_prices = False

    def _on_margin_changed(self, margin: float) -> None:
        if self._updating_prices:
            return
        self._updating_prices = True
        cost = self._cost_spin.value()
        if cost > 0 and margin > 0:
            price = _price_from_margin(cost, margin)
            self._sell_spin.setValue(price)
            self._margin_hint.setText("")
        self._updating_prices = False

    # ==================================================================
    #  SKU PREVIEW
    # ==================================================================

    def _update_sku_preview(self) -> None:
        vendor_id = self._vendor_combo.currentData()
        vendor_sku = self._vendor_sku_edit.text().strip()
        if vendor_id:
            prefix = get_sku_prefix_for_vendor(vendor_id) or "JAKS"
            if vendor_sku:
                self._sku_label.setText(f"Preview: {prefix}-{vendor_sku.upper()}")
            else:
                self._sku_label.setText(f"Auto: {prefix}-XXXXXX")
        else:
            self._sku_label.setText("Auto: JAKS-NEED-XXXXXX (Pre-Inventory)")

    # ==================================================================
    #  AI DESCRIPTION GENERATION
    # ==================================================================

    def _on_ai_generate(self) -> None:
        """Generate a product description using Grok AI."""
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.information(self, "Name Required", "Enter a product name first.")
            self._name_edit.setFocus()
            return

        vendor_text = self._vendor_combo.currentText().strip()
        if vendor_text == "(None)":
            vendor_text = ""
        cat_text = self._category_combo.currentText().strip()
        oem = self._oem_edit.text().strip()

        self._ai_gen_btn.setText("Generating...")
        self._ai_gen_btn.setEnabled(False)
        QTimer.singleShot(50, lambda: self._run_ai_generation(
            name, vendor_text, cat_text, oem
        ))

    def _run_ai_generation(self, title: str, brand: str, category: str, oem: str) -> None:
        try:
            from phases.description import generate_engaging_description
            desc = generate_engaging_description(
                brand=brand,
                engine="",
                kit_contents=[],
                dimensions="",
                title=title,
                xrefs=[oem] if oem else [],
            )
            self._desc_edit.setPlainText(desc)
        except Exception as e:
            QMessageBox.warning(self, "AI Error", f"Description generation failed: {e}")
        finally:
            self._ai_gen_btn.setText("AI Generate")
            self._ai_gen_btn.setEnabled(True)

    # ==================================================================
    #  PREFILL / REUSE
    # ==================================================================

    def _pre_fill_from_last(self) -> None:
        last = QuickEntryDialog._last_saved
        if not last:
            return
        self._apply_dict(last)
        self._update_last_panel(last)

    def _apply_last_values(self) -> None:
        last = QuickEntryDialog._last_saved
        if last:
            self._apply_dict(last)

    def _apply_dict(self, vals: dict) -> None:
        if vals.get("vendor_id"):
            self._vendor_combo.setCurrentByData(vals["vendor_id"])
        elif vals.get("vendor"):
            self._vendor_combo.setCurrentByText(vals["vendor"])
        if vals.get("category"):
            self._category_combo.setCurrentByText(vals["category"])
            self._on_category_changed(vals["category"], None)
            if vals.get("subcategory"):
                self._subcategory_combo.setCurrentByText(vals["subcategory"])
        if vals.get("margin"):
            self._updating_prices = True
            self._margin_spin.setValue(vals["margin"])
            self._updating_prices = False
        # Clear identity fields
        self._name_edit.clear()
        self._oem_edit.clear()
        self._vendor_sku_edit.clear()
        self._cost_spin.setValue(0)
        self._sell_spin.setValue(0)
        self._core_charge_spin.setValue(0)
        self._update_sku_preview()
        self._name_edit.setFocus()

    def _update_last_panel(self, vals: dict) -> None:
        lines = []
        if vals.get("vendor"):
            lines.append(f"<b>Vendor:</b> {vals['vendor']}")
        if vals.get("category"):
            lines.append(f"<b>Category:</b> {vals['category']}")
        if vals.get("subcategory"):
            lines.append(f"<b>Sub Cat:</b> {vals['subcategory']}")
        if vals.get("margin"):
            lines.append(f"<b>Margin:</b> {vals['margin']:.1f}%")
        if vals.get("last_sku"):
            lines.append(f"<b>Last SKU:</b> {vals['last_sku']}")
        self._last_info.setText("<br>".join(lines) if lines else "No previous entry yet.")
        self._reuse_btn.setEnabled(bool(lines))

    # ==================================================================
    #  INLINE VALIDATION (no popups)
    # ==================================================================

    def _validate(self) -> bool:
        """Validate required fields. Returns True if OK."""
        valid = True

        # Name required
        name = self._name_edit.text().strip()
        if not name:
            self._name_edit.setStyleSheet(_ERR_STYLE)
            self._name_err.setText("Product name is required")
            self._name_err.show()
            if valid:
                self._name_edit.setFocus()
            valid = False
        else:
            self._name_edit.setStyleSheet("")
            self._name_err.hide()

        # Category required
        cat = self._category_combo.currentText().strip()
        if not cat:
            self._cat_err.setText("Category is required")
            self._cat_err.show()
            if valid:
                self._category_combo.setFocus()
            valid = False
        else:
            self._cat_err.hide()

        # Subcategory needs a category
        subcat = self._subcategory_combo.currentText().strip()
        if subcat and not cat:
            self._cat_err.setText("Sub Category requires a Category")
            self._cat_err.show()
            if valid:
                self._category_combo.setFocus()
            valid = False

        return valid

    # ==================================================================
    #  SAVE
    # ==================================================================

    def _on_save_and_duplicate(self) -> None:
        self._duplicate_next = True
        self._new_next = False
        self._on_save()

    def _on_save_and_new(self) -> None:
        self._new_next = True
        self._duplicate_next = False
        self._on_save()

    @property
    def should_duplicate(self) -> bool:
        return self._duplicate_next and self.result() == QDialog.DialogCode.Accepted

    @property
    def should_open_new(self) -> bool:
        return self._new_next and self.result() == QDialog.DialogCode.Accepted

    def _on_save(self) -> None:
        if not self._validate():
            return

        name = self._name_edit.text().strip()
        vendor_text = self._vendor_combo.currentText().strip()
        vendor_id = self._vendor_combo.currentData()

        # Handle "(None)" vendor
        if vendor_text == "(None)" or not vendor_text:
            vendor_text = ""
            vendor_id = None

        cat_text = self._category_combo.currentText().strip()
        subcat_text = self._subcategory_combo.currentText().strip()

        # Determine inventory status
        inventory_status = "pre_inventory" if not vendor_id else "active"

        # Resolve SKU
        prefix = None
        if vendor_id:
            prefix = get_sku_prefix_for_vendor(vendor_id)
        if not prefix:
            prefix = "JAKS-NEED" if not vendor_id else "JAKS"
        vendor_sku = self._vendor_sku_edit.text().strip()
        # If vendor SKU provided and we have a vendor prefix, build deterministic SKU
        if vendor_sku and vendor_id and prefix != "JAKS-NEED":
            sku = f"{prefix}-{vendor_sku}".upper()
            # Check for collision
            existing = get_product_by_sku(sku)
            if existing:
                sku = generate_next_sku(prefix)
        else:
            sku = generate_next_sku(prefix)

        # Duplicate check
        oem = self._oem_edit.text().strip()
        dupes = find_similar_products(sku=sku, title=name, oem_part_number=oem)
        if dupes:
            lines = [
                f"\u2022 {d['sku']}  \u2014  {d['title']}  (match: {d.get('match_reason', 'similar')})"
                for d in dupes[:8]
            ]
            msg = "Possible duplicates:\n\n" + "\n".join(lines) + "\n\nCreate anyway?"
            if (
                QMessageBox.question(self, "Duplicate Warning", msg)
                != QMessageBox.StandardButton.Yes
            ):
                return

        kwargs = dict(
            sku=sku,
            title=name,
            category=cat_text,
            subcategory=subcat_text or None,
            supplier=vendor_text,
            preferred_vendor_id=vendor_id,
            oem_part_number=oem,
            pai_sku=vendor_sku,
            cost=self._cost_spin.value(),
            selling_price=self._sell_spin.value(),
            core_charge=self._core_charge_spin.value(),
            min_qty=self._min_spin.value(),
            max_qty=self._max_spin.value(),
            inventory_status=inventory_status,
        )
        desc_html = self._desc_edit.toPlainText().strip()
        if desc_html:
            kwargs["description_html"] = desc_html

        try:
            result = create_product(**kwargs)
            if not result.get("success"):
                QMessageBox.warning(
                    self, "Error", result.get("error", "Failed to create product")
                )
                return
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Create failed: {e}")
            return

        # Stash last-used values for next dialog
        QuickEntryDialog._last_saved = {
            "vendor": vendor_text,
            "vendor_id": vendor_id,
            "category": cat_text,
            "subcategory": subcat_text,
            "margin": self._margin_spin.value(),
            "cost": self._cost_spin.value(),
            "sell": self._sell_spin.value(),
            "min_qty": self._min_spin.value(),
            "max_qty": self._max_spin.value(),
            "last_sku": sku,
        }

        self.accept()
