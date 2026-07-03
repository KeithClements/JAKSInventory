"""Document Settings dialog — modal popup for tweaking how quotes,
sales orders, and invoices are displayed and exported.

Hosted from the quote dialog (⚙ Document Settings button) so estimators
can tweak presentation without navigating to the global Settings screen.
Reads/writes the same ``app_settings`` keys as
``ui/settings_screen.py``'s "Quote / SO / Invoice Display" group, so
the two stay in sync.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QSpinBox, QVBoxLayout,
)

from db import get_setting, set_setting


class DocumentSettingsDialog(QDialog):
    """Modal dialog exposing the per-document display/export defaults."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Document Settings")
        self.setModal(True)
        self.resize(520, 560)

        root = QVBoxLayout(self)

        intro = QLabel(
            "These settings control how quotes, sales orders, and invoices "
            "are presented to customers. Changes apply to the next document "
            "you generate."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #555; font-size: 9pt;")
        root.addWidget(intro)

        # ── Display group ──
        display_group = QGroupBox("Display")
        df = QFormLayout(display_group)

        self._show_sku = QCheckBox("Show SKU column")
        df.addRow(self._show_sku)

        self._show_warranty = QCheckBox("Show product warranty under description")
        df.addRow(self._show_warranty)

        self._show_core = QCheckBox("Show Core Deposit column")
        df.addRow(self._show_core)

        self._show_notes = QCheckBox("Show line notes / ETA")
        df.addRow(self._show_notes)

        self._show_freight = QCheckBox("Show per-line freight")
        df.addRow(self._show_freight)

        self._show_supplier = QCheckBox("Show supplier under each line")
        df.addRow(self._show_supplier)

        self._show_company_address = QCheckBox("Show our company address in PDF header")
        df.addRow(self._show_company_address)

        root.addWidget(display_group)

        # ── Branding group ──
        brand_group = QGroupBox("Branding")
        bf = QFormLayout(brand_group)

        self._logo_size_pct = QSpinBox()
        self._logo_size_pct.setRange(50, 150)
        self._logo_size_pct.setSingleStep(5)
        self._logo_size_pct.setSuffix(" %")
        self._logo_size_pct.setToolTip(
            "Scale the PDF header logo. 100% = 2.6\" wide × 1.1\" tall.")
        bf.addRow("Logo size:", self._logo_size_pct)

        root.addWidget(brand_group)

        # ── Defaults group ──
        defaults_group = QGroupBox("New-quote Defaults")
        dfm = QFormLayout(defaults_group)

        self._default_terms = QLineEdit()
        self._default_terms.setPlaceholderText("e.g. Net 30")
        dfm.addRow("Terms:", self._default_terms)

        self._default_expires_days = QSpinBox()
        self._default_expires_days.setRange(0, 365)
        dfm.addRow("Quote validity (days):", self._default_expires_days)

        root.addWidget(defaults_group)

        # ── Repair-package names ──
        pkg_group = QGroupBox("Repair Package Names")
        pkg_group.setToolTip(
            "Customer-facing names used on multi-option quotes (Option A/B/C).")
        pf = QFormLayout(pkg_group)
        self._pkg_a = QLineEdit()
        self._pkg_a.setPlaceholderText("Essential Repair Package")
        pf.addRow("Option A:", self._pkg_a)
        self._pkg_b = QLineEdit()
        self._pkg_b.setPlaceholderText("Recommended Repair Package")
        pf.addRow("Option B (Recommended ⭐):", self._pkg_b)
        self._pkg_c = QLineEdit()
        self._pkg_c.setPlaceholderText("Maximum Protection Package")
        pf.addRow("Option C:", self._pkg_c)
        root.addWidget(pkg_group)

        # ── Buttons ──
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._load()

    # ── Persistence ──

    def _load(self) -> None:
        self._show_sku.setChecked(get_setting("quote_show_sku", "0") == "1")
        self._show_warranty.setChecked(get_setting("quote_show_warranty", "1") == "1")
        self._show_core.setChecked(get_setting("quote_show_core", "1") == "1")
        self._show_notes.setChecked(get_setting("quote_show_notes", "1") == "1")
        self._show_freight.setChecked(get_setting("quote_show_freight", "1") == "1")
        self._show_supplier.setChecked(get_setting("quote_show_supplier", "0") == "1")
        self._show_company_address.setChecked(
            get_setting("quote_show_company_address", "1") == "1")
        try:
            self._logo_size_pct.setValue(int(get_setting("quote_logo_size_pct", "100")))
        except Exception:
            self._logo_size_pct.setValue(100)
        self._default_terms.setText(get_setting("quote_default_terms", ""))
        try:
            self._default_expires_days.setValue(
                int(get_setting("quote_default_expires_days", "30")))
        except Exception:
            self._default_expires_days.setValue(30)
        self._pkg_a.setText(get_setting("package_name_a", "Essential Repair Package"))
        self._pkg_b.setText(get_setting("package_name_b", "Recommended Repair Package"))
        self._pkg_c.setText(get_setting("package_name_c", "Maximum Protection Package"))

    def _save(self) -> None:
        set_setting("quote_show_sku", "1" if self._show_sku.isChecked() else "0")
        set_setting("quote_show_warranty",
                    "1" if self._show_warranty.isChecked() else "0")
        set_setting("quote_show_core",
                    "1" if self._show_core.isChecked() else "0")
        set_setting("quote_show_notes",
                    "1" if self._show_notes.isChecked() else "0")
        set_setting("quote_show_freight",
                    "1" if self._show_freight.isChecked() else "0")
        set_setting("quote_show_supplier",
                    "1" if self._show_supplier.isChecked() else "0")
        set_setting("quote_show_company_address",
                    "1" if self._show_company_address.isChecked() else "0")
        set_setting("quote_logo_size_pct", str(self._logo_size_pct.value()))
        set_setting("quote_default_terms", self._default_terms.text().strip())
        set_setting("quote_default_expires_days",
                    str(self._default_expires_days.value()))
        set_setting("package_name_a",
                    self._pkg_a.text().strip() or "Essential Repair Package")
        set_setting("package_name_b",
                    self._pkg_b.text().strip() or "Recommended Repair Package")
        set_setting("package_name_c",
                    self._pkg_c.text().strip() or "Maximum Protection Package")
        self.accept()
