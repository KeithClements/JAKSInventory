"""New Product Wizard - 5-step guided flow for creating new products."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QFrame,
    QWidget,
    QStackedWidget,
    QCompleter,
    QCheckBox,
    QRadioButton,
    QMessageBox,
    QPlainTextEdit,
    QFormLayout,
)

from db import (
    get_all_vendors,
    get_all_categories,
    get_all_manufacturers,
    get_all_engine_manufacturers,
    get_all_engine_models,
    get_setting,
    create_product,
    find_similar_products,
    get_product_by_sku,
    get_sku_prefix_for_vendor,
    generate_next_sku,
)

# Reuse constants from the edit dialog
_DEFAULT_ENGINE_MFRS = [
    "Caterpillar", "Cummins", "Detroit Diesel", "International / Navistar",
    "Mack", "PACCAR", "Volvo",
]
_DEFAULT_ENGINE_MODELS = [
    "ISX 15", "ISX 12", "ISB 6.7", "ISC 8.3", "ISL 8.9", "ISM 11",
    "N14", "M11", "L10", "855",
    "C15", "C13", "C12", "C9", "C7", "3406E", "3406B", "3126",
    "DD15", "DD13", "DD16", "Series 60 14L", "Series 60 12.7",
    "DT466", "DT530", "MaxxForce 13", "A26",
    "MX-13", "MX-11", "D13", "D11",
]
_DEFAULT_TRUCK_MFRS = [
    "Kenworth", "Peterbilt", "Freightliner", "Western Star",
    "Volvo", "Mack", "International / Navistar",
]
_DEFAULT_TRUCK_SYSTEMS = [
    "Suspension", "Brakes", "Exhaust / Aftertreatment", "Cooling",
    "Electrical", "Fuel System", "Air System", "Steering",
    "Drivetrain", "HVAC", "Body / Cab",
]

_ENGINE_MFR_ABBREV: dict[str, str] = {
    "Caterpillar": "CAT", "Cummins": "CUM", "Detroit Diesel": "DD",
    "International / Navistar": "INT", "International": "INT",
    "Navistar": "NAV", "Mack": "MACK", "PACCAR": "PAC", "Volvo": "VOL",
}

STEP_NAMES = ["Basic Info", "Platform", "Supplier", "Pricing", "Review"]


class NewProductWizard(QDialog):
    """5-step wizard for creating a new product."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Product Wizard")
        self.setMinimumSize(800, 600)
        self._vendors = []
        self._created_sku: str | None = None
        self._build_ui()

    @property
    def created_sku(self) -> str | None:
        return self._created_sku

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header bar ──
        header = QFrame()
        header.setStyleSheet(
            "QFrame#wiz_header { background-color: #3d4a5c; }"
            "QLabel { color: white; background: transparent; }"
        )
        header.setObjectName("wiz_header")
        header.setMinimumHeight(50)
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(16, 8, 16, 8)
        title = QLabel("New Product Wizard")
        title.setStyleSheet("font-size: 15pt; font-weight: bold; color: white; background: transparent;")
        h_lay.addWidget(title)
        h_lay.addStretch()
        outer.addWidget(header)

        # ── Step indicator bar ──
        self._step_bar = QFrame()
        self._step_bar.setStyleSheet(
            "QFrame { background-color: #eee; border-bottom: 1px solid #ccc; }"
        )
        self._step_bar.setMinimumHeight(44)
        step_lay = QHBoxLayout(self._step_bar)
        step_lay.setContentsMargins(16, 6, 16, 6)
        step_lay.setSpacing(8)

        self._step_labels: list[QLabel] = []
        for i, name in enumerate(STEP_NAMES):
            lbl = QLabel(f"  {i+1}. {name}  ")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(
                "background: #ddd; color: #666; font-weight: bold; font-size: 9pt; "
                "border-radius: 12px; padding: 4px 12px;"
            )
            step_lay.addWidget(lbl)
            self._step_labels.append(lbl)
        step_lay.addStretch()
        outer.addWidget(self._step_bar)

        # ── Stacked pages ──
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_step_basic())
        self._stack.addWidget(self._build_step_platform())
        self._stack.addWidget(self._build_step_supplier())
        self._stack.addWidget(self._build_step_pricing())
        self._stack.addWidget(self._build_step_review())
        outer.addWidget(self._stack, 1)

        # ── Bottom nav bar ──
        nav_bar = QFrame()
        nav_bar.setStyleSheet("QFrame { background: #f5f5f0; border-top: 1px solid #ccc; }")
        nav_bar.setMinimumHeight(50)
        nav_lay = QHBoxLayout(nav_bar)
        nav_lay.setContentsMargins(16, 8, 16, 8)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setStyleSheet(
            "QPushButton { background: #6b7280; color: white; font-weight: bold; "
            "padding: 8px 20px; border-radius: 4px; font-size: 10pt; }"
            "QPushButton:hover { background: #7b8290; }"
        )
        self._back_btn.clicked.connect(self._go_back)
        nav_lay.addWidget(self._back_btn)
        nav_lay.addStretch()

        self._skip_btn = QPushButton("Skip →")
        self._skip_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #6a1b9a; font-weight: bold; "
            "padding: 8px 16px; border: 1px solid #6a1b9a; border-radius: 4px; font-size: 9pt; }"
            "QPushButton:hover { background: #f3e5f5; }"
        )
        self._skip_btn.clicked.connect(self._go_next)
        nav_lay.addWidget(self._skip_btn)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setStyleSheet(
            "QPushButton { background: #6a1b9a; color: white; font-weight: bold; "
            "padding: 8px 24px; border-radius: 4px; font-size: 10pt; }"
            "QPushButton:hover { background: #7b1fa2; }"
        )
        self._next_btn.clicked.connect(self._go_next)
        nav_lay.addWidget(self._next_btn)

        self._create_btn = QPushButton("✅ Create Product")
        self._create_btn.setStyleSheet(
            "QPushButton { background: #2e7d32; color: white; font-weight: bold; "
            "padding: 8px 24px; border-radius: 4px; font-size: 10pt; }"
            "QPushButton:hover { background: #388e3c; }"
        )
        self._create_btn.clicked.connect(self._do_create)
        self._create_btn.hide()
        nav_lay.addWidget(self._create_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #666; "
            "padding: 8px 16px; border: 1px solid #aaa; border-radius: 4px; font-size: 9pt; }"
            "QPushButton:hover { background: #eee; }"
        )
        cancel_btn.clicked.connect(self.reject)
        nav_lay.addWidget(cancel_btn)

        outer.addWidget(nav_bar)

        # Keyboard shortcuts
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._go_next)

        self._current_step = 0
        self._update_step_ui()

    # ------------------------------------------------------------------
    # STEP PAGES
    # ------------------------------------------------------------------

    def _build_step_basic(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 24, 40, 24)

        lbl = QLabel("Step 1: Basic Product Information")
        lbl.setStyleSheet("font-size: 13pt; font-weight: bold; color: #333;")
        lay.addWidget(lbl)
        lay.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        # SKU
        sku_row = QHBoxLayout()
        self._w_sku = QLineEdit(get_setting("sku_prefix") or "")
        self._w_sku.setPlaceholderText("JAKS-PART-NUMBER")
        sku_row.addWidget(self._w_sku, 1)
        auto_btn = QPushButton("Auto-SKU")
        auto_btn.setStyleSheet(
            "QPushButton { padding: 4px 10px; border: 1px solid #aaa; border-radius: 3px; font-size: 9pt; }"
            "QPushButton:hover { background: #eee; }"
        )
        auto_btn.clicked.connect(self._on_wizard_auto_sku)
        sku_row.addWidget(auto_btn)
        form.addRow("SKU:", sku_row)

        # Product Name
        self._w_title = QLineEdit()
        self._w_title.setPlaceholderText("e.g. Cummins ISX Water Pump")
        form.addRow("Product Name:", self._w_title)

        # Category
        self._w_category = QComboBox()
        self._w_category.setEditable(True)
        self._w_category.addItem("")
        try:
            for cat in get_all_categories():
                self._w_category.addItem(cat)
        except Exception:
            pass
        form.addRow("Category:", self._w_category)

        # Condition
        self._w_condition = QComboBox()
        self._w_condition.addItems(["New", "Remanufactured", "Used", "Core"])
        form.addRow("Condition:", self._w_condition)

        # OEM Part #
        self._w_oem = QLineEdit()
        self._w_oem.setPlaceholderText("OEM part number")
        form.addRow("OEM Part #:", self._w_oem)

        # Brief Description
        self._w_brief = QLineEdit()
        self._w_brief.setPlaceholderText("e.g. Water Pump, Turbocharger")
        form.addRow("Brief Description:", self._w_brief)

        lay.addLayout(form)
        lay.addStretch()
        return page

    def _build_step_platform(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 24, 40, 24)

        lbl = QLabel("Step 2: Platform — Engine / Truck")
        lbl.setStyleSheet("font-size: 13pt; font-weight: bold; color: #333;")
        lay.addWidget(lbl)
        lay.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        # Manufacturer
        self._w_manufacturer = QComboBox()
        self._w_manufacturer.setEditable(True)
        self._w_manufacturer.addItem("")
        try:
            for m in sorted(set(get_all_manufacturers())):
                self._w_manufacturer.addItem(m)
        except Exception:
            pass
        form.addRow("Manufacturer:", self._w_manufacturer)

        # Type toggle
        toggle_row = QHBoxLayout()
        self._w_engine_radio = QRadioButton("Engine")
        self._w_truck_radio = QRadioButton("Truck")
        self._w_engine_radio.setChecked(True)
        self._w_engine_radio.toggled.connect(self._on_wizard_platform_toggle)
        toggle_row.addWidget(self._w_engine_radio)
        toggle_row.addWidget(self._w_truck_radio)
        toggle_row.addStretch()
        form.addRow("Type:", toggle_row)

        # Engine fields
        self._w_eng_mfr = QComboBox()
        self._w_eng_mfr.setEditable(True)
        self._w_eng_mfr.addItem("")
        try:
            db_eng = get_all_engine_manufacturers()
        except Exception:
            db_eng = []
        for em in sorted(set(_DEFAULT_ENGINE_MFRS + db_eng)):
            self._w_eng_mfr.addItem(em)
        self._w_eng_mfr_label = QLabel("Engine Manufacturer:")
        form.addRow(self._w_eng_mfr_label, self._w_eng_mfr)

        self._w_eng_model = QComboBox()
        self._w_eng_model.setEditable(True)
        self._w_eng_model.addItem("")
        try:
            db_models = get_all_engine_models()
        except Exception:
            db_models = []
        for em in sorted(set(_DEFAULT_ENGINE_MODELS + db_models)):
            self._w_eng_model.addItem(em)
        self._w_eng_model_label = QLabel("Engine Model:")
        form.addRow(self._w_eng_model_label, self._w_eng_model)

        # Truck fields (hidden initially)
        self._w_truck_mfr = QComboBox()
        self._w_truck_mfr.setEditable(True)
        self._w_truck_mfr.addItem("")
        for tm in _DEFAULT_TRUCK_MFRS:
            self._w_truck_mfr.addItem(tm)
        self._w_truck_mfr_label = QLabel("Truck Manufacturer:")
        form.addRow(self._w_truck_mfr_label, self._w_truck_mfr)

        self._w_truck_model = QLineEdit()
        self._w_truck_model.setPlaceholderText("e.g. T680, 579")
        self._w_truck_model_label = QLabel("Truck Model:")
        form.addRow(self._w_truck_model_label, self._w_truck_model)

        self._w_truck_system = QComboBox()
        self._w_truck_system.setEditable(True)
        self._w_truck_system.addItem("")
        for ts in _DEFAULT_TRUCK_SYSTEMS:
            self._w_truck_system.addItem(ts)
        self._w_truck_system_label = QLabel("Truck System:")
        form.addRow(self._w_truck_system_label, self._w_truck_system)

        # Initial visibility
        self._on_wizard_platform_toggle(True)

        lay.addLayout(form)
        lay.addStretch()
        return page

    def _build_step_supplier(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 24, 40, 24)

        lbl = QLabel("Step 3: Supplier / Vendor")
        lbl.setStyleSheet("font-size: 13pt; font-weight: bold; color: #333;")
        lay.addWidget(lbl)
        lay.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        # Supplier
        self._w_supplier = QComboBox()
        self._w_supplier.addItem("-- Select Vendor --", None)
        try:
            self._vendors = get_all_vendors()
            for v in self._vendors:
                self._w_supplier.addItem(v.get("name", ""), v.get("id"))
        except Exception:
            pass
        form.addRow("Supplier (required):", self._w_supplier)

        # Vendor SKU
        self._w_vendor_sku = QLineEdit()
        self._w_vendor_sku.setPlaceholderText("Vendor's part number")
        form.addRow("Vendor SKU:", self._w_vendor_sku)

        # Vendor Description
        self._w_vendor_desc = QLineEdit()
        self._w_vendor_desc.setPlaceholderText("Vendor's description")
        form.addRow("Vendor Description:", self._w_vendor_desc)

        # Vendor Link
        self._w_vendor_link = QLineEdit()
        self._w_vendor_link.setPlaceholderText("URL to vendor listing")
        form.addRow("Vendor Link:", self._w_vendor_link)

        lay.addLayout(form)
        lay.addStretch()
        return page

    def _build_step_pricing(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 24, 40, 24)

        lbl = QLabel("Step 4: Pricing")
        lbl.setStyleSheet("font-size: 13pt; font-weight: bold; color: #333;")
        lay.addWidget(lbl)
        lay.addSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        # Selling Price
        self._w_selling = QDoubleSpinBox()
        self._w_selling.setRange(0, 999999.99)
        self._w_selling.setDecimals(2)
        self._w_selling.setPrefix("$")
        form.addRow("Selling Price:", self._w_selling)

        # Cost
        self._w_cost = QDoubleSpinBox()
        self._w_cost.setRange(0, 999999.99)
        self._w_cost.setDecimals(2)
        self._w_cost.setPrefix("$")
        form.addRow("Cost (Landed):", self._w_cost)

        # Vendor Cost
        self._w_vendor_cost = QDoubleSpinBox()
        self._w_vendor_cost.setRange(0, 999999.99)
        self._w_vendor_cost.setDecimals(2)
        self._w_vendor_cost.setPrefix("$")
        form.addRow("Vendor Cost:", self._w_vendor_cost)

        # Compare At Price
        self._w_compare = QDoubleSpinBox()
        self._w_compare.setRange(0, 999999.99)
        self._w_compare.setDecimals(2)
        self._w_compare.setPrefix("$")
        form.addRow("Compare At Price:", self._w_compare)

        # Core Charge
        self._w_core = QDoubleSpinBox()
        self._w_core.setRange(0, 999999.99)
        self._w_core.setDecimals(2)
        self._w_core.setPrefix("$")
        form.addRow("Core Charge:", self._w_core)

        # Margin display
        self._w_margin_label = QLabel("")
        self._w_margin_label.setStyleSheet("color: #228B22; font-weight: bold; font-size: 10pt;")
        form.addRow("", self._w_margin_label)

        self._w_selling.valueChanged.connect(self._update_wizard_margin)
        self._w_cost.valueChanged.connect(self._update_wizard_margin)

        # Vendor-driven auto pricing (mirrors edit dialog)
        self._wiz_selling_user_override = False

        def _on_selling_edited(_v: float) -> None:
            if getattr(self, "_wiz_auto_pricing", False):
                return
            self._wiz_selling_user_override = True

        def _resolve_wizard_category():
            try:
                from db import (
                    get_vendor_category, get_price_category,
                    get_default_price_category,
                )
            except Exception:
                return None
            vid = self._w_supplier.currentData() if hasattr(self, "_w_supplier") else None
            if vid:
                cid = get_vendor_category(int(vid))
                if cid:
                    cat = get_price_category(int(cid))
                    if cat:
                        return cat
            return get_default_price_category()

        def _apply_wizard_auto_price(*, force: bool = False) -> None:
            if self._wiz_selling_user_override and not force:
                return
            cat = _resolve_wizard_category()
            if not cat:
                return
            cost = float(self._w_cost.value() or 0) or float(self._w_vendor_cost.value() or 0)
            if cost <= 0:
                return
            try:
                from db import find_band_for_cost
                band = find_band_for_cost(int(cat["id"]), cost)
            except Exception:
                band = None
            if not band:
                return
            margin_pct = float(band.get("margin_pct") or 0)
            if margin_pct >= 100:
                return
            new_price = round(cost / (1 - margin_pct / 100.0), 2)
            if abs(new_price - float(self._w_selling.value() or 0)) < 0.005:
                return
            self._wiz_auto_pricing = True
            self._w_selling.blockSignals(True)
            self._w_selling.setValue(new_price)
            self._w_selling.blockSignals(False)
            self._wiz_auto_pricing = False
            self._update_wizard_margin()

        self._apply_wizard_auto_price = _apply_wizard_auto_price
        self._w_selling.valueChanged.connect(_on_selling_edited)
        self._w_supplier.currentIndexChanged.connect(lambda *_: _apply_wizard_auto_price())
        self._w_cost.valueChanged.connect(lambda *_: _apply_wizard_auto_price())
        self._w_vendor_cost.valueChanged.connect(lambda *_: _apply_wizard_auto_price())

        lay.addLayout(form)
        lay.addStretch()
        return page

    def _build_step_review(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(40, 24, 40, 24)

        lbl = QLabel("Step 5: Review & Create")
        lbl.setStyleSheet("font-size: 13pt; font-weight: bold; color: #333;")
        lay.addWidget(lbl)
        lay.addSpacing(8)

        self._review_text = QPlainTextEdit()
        self._review_text.setReadOnly(True)
        self._review_text.setStyleSheet(
            "font-family: 'Segoe UI', sans-serif; font-size: 10pt; "
            "background: #fafafa; border: 1px solid #ddd; border-radius: 4px; padding: 8px;"
        )
        lay.addWidget(self._review_text, 1)

        hint = QLabel("Click 'Create Product' to save, or go back to make changes.")
        hint.setStyleSheet("color: #888; font-size: 9pt; margin-top: 8px;")
        lay.addWidget(hint)

        # Open in full editor checkbox
        self._open_editor_cb = QCheckBox("Open in full editor after creating")
        self._open_editor_cb.setChecked(True)
        lay.addWidget(self._open_editor_cb)

        return page

    # ------------------------------------------------------------------
    # STEP NAVIGATION
    # ------------------------------------------------------------------

    def _update_step_ui(self):
        self._stack.setCurrentIndex(self._current_step)
        total = len(STEP_NAMES)

        # Update step indicator bar
        for i, lbl in enumerate(self._step_labels):
            if i < self._current_step:
                lbl.setStyleSheet(
                    "background: #2e7d32; color: white; font-weight: bold; font-size: 9pt; "
                    "border-radius: 12px; padding: 4px 12px;"
                )
                lbl.setText(f"  ✓ {STEP_NAMES[i]}  ")
            elif i == self._current_step:
                lbl.setStyleSheet(
                    "background: #6a1b9a; color: white; font-weight: bold; font-size: 9pt; "
                    "border-radius: 12px; padding: 4px 12px;"
                )
                lbl.setText(f"  {i+1}. {STEP_NAMES[i]}  ")
            else:
                lbl.setStyleSheet(
                    "background: #ddd; color: #666; font-weight: bold; font-size: 9pt; "
                    "border-radius: 12px; padding: 4px 12px;"
                )
                lbl.setText(f"  {i+1}. {STEP_NAMES[i]}  ")

        # Button visibility
        self._back_btn.setVisible(self._current_step > 0)
        is_last = self._current_step == total - 1
        self._next_btn.setVisible(not is_last)
        self._skip_btn.setVisible(not is_last and self._current_step > 0)
        self._create_btn.setVisible(is_last)

        # Populate review on last step
        if is_last:
            self._populate_review()

    def _go_next(self):
        if self._current_step == 0:
            # Validate basic info
            if not self._w_sku.text().strip():
                QMessageBox.warning(self, "Required", "SKU is required.")
                return
            if not self._w_title.text().strip():
                QMessageBox.warning(self, "Required", "Product Name is required.")
                return

        if self._current_step < len(STEP_NAMES) - 1:
            self._current_step += 1
            self._update_step_ui()

    def _go_back(self):
        if self._current_step > 0:
            self._current_step -= 1
            self._update_step_ui()

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    def _on_wizard_platform_toggle(self, engine_checked: bool):
        show_engine = engine_checked
        for w in (self._w_eng_mfr_label, self._w_eng_mfr,
                  self._w_eng_model_label, self._w_eng_model):
            w.setVisible(show_engine)
        for w in (self._w_truck_mfr_label, self._w_truck_mfr,
                  self._w_truck_model_label, self._w_truck_model,
                  self._w_truck_system_label, self._w_truck_system):
            w.setVisible(not show_engine)

    def _on_wizard_auto_sku(self):
        vendor_id = self._w_supplier.currentData()
        vendor_sku = self._w_vendor_sku.text().strip()

        if vendor_id:
            vendor_code = ""
            for v in self._vendors:
                if v.get("id") == vendor_id:
                    vendor_code = (v.get("code") or "").strip()
                    break
            part = vendor_sku or self._w_oem.text().strip()
            if vendor_code and part:
                self._w_sku.setText(f"JAKS-{vendor_code}-{part}")
                return

        if not vendor_sku:
            QMessageBox.information(
                self, "Need Info",
                "Select a supplier and enter a Vendor SKU first,\n"
                "or enter the SKU manually."
            )

    def _update_wizard_margin(self):
        sell = self._w_selling.value()
        cost = self._w_cost.value()
        if sell > 0 and cost > 0:
            margin = ((sell - cost) / sell) * 100
            color = "#228B22" if margin > 0 else "#dc3545"
            self._w_margin_label.setStyleSheet(
                f"color: {color}; font-weight: bold; font-size: 10pt;"
            )
            self._w_margin_label.setText(f"Margin: ${sell - cost:,.2f} ({margin:.1f}%)")
        else:
            self._w_margin_label.setText("")

    def _populate_review(self):
        lines = []
        lines.append(f"SKU:              {self._w_sku.text().strip()}")
        lines.append(f"Product Name:     {self._w_title.text().strip()}")
        lines.append(f"Category:         {self._w_category.currentText().strip()}")
        lines.append(f"Condition:        {self._w_condition.currentText()}")
        lines.append(f"OEM Part #:       {self._w_oem.text().strip()}")
        lines.append(f"Brief Desc:       {self._w_brief.text().strip()}")
        lines.append("")
        lines.append(f"Manufacturer:     {self._w_manufacturer.currentText().strip()}")
        if self._w_engine_radio.isChecked():
            lines.append(f"Engine Mfr:       {self._w_eng_mfr.currentText().strip()}")
            lines.append(f"Engine Model:     {self._w_eng_model.currentText().strip()}")
        else:
            lines.append(f"Truck Mfr:        {self._w_truck_mfr.currentText().strip()}")
            lines.append(f"Truck Model:      {self._w_truck_model.text().strip()}")
            lines.append(f"Truck System:     {self._w_truck_system.currentText().strip()}")
        lines.append("")
        supplier = self._w_supplier.currentText()
        if supplier == "-- Select Vendor --":
            supplier = "(none)"
        lines.append(f"Supplier:         {supplier}")
        lines.append(f"Vendor SKU:       {self._w_vendor_sku.text().strip()}")
        lines.append("")
        sell = self._w_selling.value()
        cost = self._w_cost.value()
        lines.append(f"Selling Price:    ${sell:,.2f}")
        lines.append(f"Cost (Landed):    ${cost:,.2f}")
        if sell > 0 and cost > 0:
            margin = ((sell - cost) / sell) * 100
            lines.append(f"Margin:           {margin:.1f}%")
        vcost = self._w_vendor_cost.value()
        if vcost > 0:
            lines.append(f"Vendor Cost:      ${vcost:,.2f}")
        compare = self._w_compare.value()
        if compare > 0:
            lines.append(f"Compare At:       ${compare:,.2f}")
        core = self._w_core.value()
        if core > 0:
            lines.append(f"Core Charge:      ${core:,.2f}")

        self._review_text.setPlainText("\n".join(lines))

    # ------------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------------

    def _do_create(self):
        sku = self._w_sku.text().strip()
        title = self._w_title.text().strip()
        if not sku or not title:
            QMessageBox.warning(self, "Error", "SKU and Product Name are required.")
            return

        supplier = self._w_supplier.currentText()
        if supplier == "-- Select Vendor --":
            supplier = ""
        vendor_id = self._w_supplier.currentData()

        kwargs = dict(
            sku=sku,
            title=title,
            category=self._w_category.currentText().strip(),
            condition=self._w_condition.currentText(),
            oem_part_number=self._w_oem.text().strip(),
            brief_description=self._w_brief.text().strip(),
            manufacturer=self._w_manufacturer.currentText().strip(),
            oem_manufacturer=self._w_eng_mfr.currentText().strip() if self._w_engine_radio.isChecked() else "",
            engine=self._w_eng_model.currentText().strip() if self._w_engine_radio.isChecked() else "",
            truck_manufacturer=self._w_truck_mfr.currentText().strip() if not self._w_engine_radio.isChecked() else "",
            truck_model=self._w_truck_model.text().strip() if not self._w_engine_radio.isChecked() else "",
            truck_system=self._w_truck_system.currentText().strip() if not self._w_engine_radio.isChecked() else "",
            supplier=supplier,
            preferred_vendor_id=vendor_id,
            pai_sku=self._w_vendor_sku.text().strip(),
            vendor_description=self._w_vendor_desc.text().strip(),
            pai_link=self._w_vendor_link.text().strip(),
            selling_price=self._w_selling.value(),
            cost=self._w_cost.value(),
            pai_cost=self._w_vendor_cost.value(),
            compare_at_price=self._w_compare.value(),
            core_charge=self._w_core.value(),
        )

        try:
            # Check for duplicates
            dupes = find_similar_products(
                sku=sku, title=title,
                oem_part_number=self._w_oem.text().strip(),
            )
            if dupes:
                lines = [f"• {d['sku']} — {d['title']} ({d.get('match_reason', 'similar')})" for d in dupes[:5]]
                msg = "Potential duplicates found:\n\n" + "\n".join(lines) + "\n\nCreate anyway?"
                if QMessageBox.question(self, "Duplicate Warning", msg) != QMessageBox.StandardButton.Yes:
                    return

            result = create_product(**kwargs)
            if result.get("success"):
                self._created_sku = sku
                QMessageBox.information(self, "Created", f"Product '{sku}' created successfully.")
                self.accept()
            else:
                QMessageBox.warning(self, "Error", result.get("error", "Failed to create product"))
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Create failed: {e}")
