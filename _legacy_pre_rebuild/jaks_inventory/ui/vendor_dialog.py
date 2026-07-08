"""Vendor edit/add dialog (grouped layout, validation, multi-purpose).

Supports two modes:

* Full (default) — Identification, Contact, Procurement, Notes groups.
* Compact (``compact=True``) — Only Identification + Contact, for quick
  inline creation from other flows (Quick Entry, etc.).

After ``accept()`` callers may read:

* ``self.created_vendor`` — the created vendor dict (None on update).
* ``self.vendor_id`` — id of the (created or edited) vendor.
* ``self._save_and_new`` — True if user pressed "Save & Add Another"; the
  caller is expected to immediately re-open another instance.
"""
from __future__ import annotations

import re

from PySide6.QtCore import QTime, Qt, QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from db import (
    create_vendor,
    create_sku_prefix,
    get_all_vendors,
    get_sku_prefix_for_vendor,
    get_vendor,
    get_vendor_avg_transit_days,
    update_sku_prefix,
    update_vendor,
)


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _format_phone(raw: str) -> str:
    """Normalize a phone string to ``(XXX) XXX-XXXX`` if it has 10 digits."""
    digits = re.sub(r"\D+", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"
    return (raw or "").strip()


class VendorDialog(QDialog):
    """Dialog for creating or editing a vendor."""

    def __init__(
        self,
        vendor_id: int | None = None,
        parent=None,
        *,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self._vendor_id = vendor_id
        self._vendor: dict | None = None
        self._compact = compact

        # Public attributes set after accept().
        self.created_vendor: dict | None = None
        self.vendor_id: int | None = vendor_id
        self._save_and_new: bool = False

        if vendor_id:
            v = get_vendor(vendor_id)
            self._vendor = dict(v) if v else None

        self._build_ui()
        self._populate()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setWindowTitle("Edit Vendor" if self._vendor_id else "New Vendor")
        self.setMinimumWidth(420 if self._compact else 580)
        # Cap dialog height so it never exceeds typical screen heights;
        # interior content scrolls when needed.
        self.resize(self.minimumWidth(), 720)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Scrollable inner content
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        root = QVBoxLayout(inner)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        root.addWidget(self._build_identification_group())
        root.addWidget(self._build_contact_group())
        if not self._compact:
            root.addWidget(self._build_procurement_group())
            root.addWidget(self._build_returns_address_group())
            root.addWidget(self._build_notes_group())
            if self._vendor_id:
                root.addWidget(self._build_core_obligations_group())
        root.addStretch(1)

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # Buttons row (kept outside the scroll area so they're always visible).
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(12, 8, 12, 12)
        btn_row.addStretch(1)

        if not self._vendor_id:
            self._save_new_btn = QPushButton("\U0001F4BE  Save && Add Another")
            self._save_new_btn.setToolTip("Save this vendor and immediately open a fresh dialog.")
            self._save_new_btn.clicked.connect(self._on_save_and_new)
            btn_row.addWidget(self._save_new_btn)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._on_save)
        self._buttons.rejected.connect(self.reject)
        btn_row.addWidget(self._buttons)

        outer.addLayout(btn_row)

    def _build_identification_group(self) -> QGroupBox:
        box = QGroupBox("Identification")
        form = QFormLayout(box)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Vendor name (required)")
        form.addRow("Name:", self._name)

        self._code = QLineEdit()
        self._code.setPlaceholderText("Short code / SKU prefix (e.g. PAI, IPD)")
        self._code.setMaximumWidth(140)
        self._code.textChanged.connect(self._on_code_changed)
        form.addRow("Code / SKU Prefix:", self._code)

        # Hidden widget kept so _save_sku_prefix() still works unchanged.
        self._sku_prefix = QLineEdit()
        self._sku_prefix.setVisible(False)

        if not self._compact:
            self._website = QLineEdit()
            self._website.setPlaceholderText("https://example.com")
            form.addRow("Website:", self._website)

            self._account_number = QLineEdit()
            self._account_number.setPlaceholderText("Our account # with this vendor")
            form.addRow("Account #:", self._account_number)

            self._qbo_vendor_id = QLineEdit()
            self._qbo_vendor_id.setPlaceholderText("QuickBooks Vendor ID (optional)")
            self._qbo_vendor_id.setMaximumWidth(160)
            form.addRow("QBO ID:", self._qbo_vendor_id)

            tax_row = QHBoxLayout()
            self._tax_id = QLineEdit()
            self._tax_id.setPlaceholderText("EIN / SSN for 1099")
            self._tax_id.setMaximumWidth(160)
            tax_row.addWidget(self._tax_id)
            self._is_1099 = QCheckBox("1099 vendor")
            tax_row.addWidget(self._is_1099)
            tax_row.addStretch(1)
            tax_wrap = QWidget()
            tax_wrap.setLayout(tax_row)
            form.addRow("Tax ID:", tax_wrap)

        self._is_active = QCheckBox("Active")
        self._is_active.setChecked(True)
        form.addRow("", self._is_active)

        return box

    def _build_contact_group(self) -> QGroupBox:
        box = QGroupBox("Primary Contact")
        form = QFormLayout(box)

        self._contact_name = QLineEdit()
        form.addRow("Contact:", self._contact_name)

        self._contact_email = QLineEdit()
        self._contact_email.setPlaceholderText("email@vendor.com")
        # Live validator (visual; final check on save).
        rx = QRegularExpression(r"^[^@\s]*@?[^@\s]*\.?[^@\s]*$")
        self._contact_email.setValidator(QRegularExpressionValidator(rx, self))
        form.addRow("Email:", self._contact_email)

        self._contact_phone = QLineEdit()
        self._contact_phone.setPlaceholderText("(555) 123-4567")
        self._contact_phone.editingFinished.connect(self._on_phone_finished)
        form.addRow("Phone:", self._contact_phone)

        self._address = QTextEdit()
        self._address.setPlaceholderText("Bill-to / mailing address…")
        self._address.setMaximumHeight(60)
        form.addRow("Bill-to:", self._address)

        if not self._compact:
            self._ship_from_address = QTextEdit()
            self._ship_from_address.setPlaceholderText("Ship-from address (if different)…")
            self._ship_from_address.setMaximumHeight(60)
            form.addRow("Ship-from:", self._ship_from_address)

        return box

    def _build_procurement_group(self) -> QGroupBox:
        box = QGroupBox("Procurement")
        form = QFormLayout(box)

        self._payment_terms = QLineEdit()
        self._payment_terms.setPlaceholderText("Net 30, COD, etc.")
        form.addRow("Payment Terms:", self._payment_terms)

        self._lead_time = QSpinBox()
        self._lead_time.setRange(0, 365)
        self._lead_time.setValue(14)
        self._lead_time.setSuffix(" days")
        self._lead_time.setToolTip("Vendor's typical lead time (days from order to ship).")
        form.addRow("Lead Time:", self._lead_time)

        # Avg Transit row: spinbox + Refresh button.
        transit_row = QHBoxLayout()
        self._avg_transit = QSpinBox()
        self._avg_transit.setRange(0, 365)
        self._avg_transit.setSuffix(" days")
        self._avg_transit.setReadOnly(True)
        self._avg_transit.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._avg_transit.setStyleSheet(
            "QSpinBox { background-color: #f0f0f0; color: #555; }"
        )
        self._avg_transit.setToolTip(
            "Auto-calculated from received POs (avg of order_date → received_date). Read-only."
        )
        transit_row.addWidget(self._avg_transit)
        self._refresh_transit_btn = QPushButton("\u21BB Refresh")
        self._refresh_transit_btn.setToolTip(
            "Recompute avg transit from this vendor's PO history."
        )
        self._refresh_transit_btn.clicked.connect(self._on_refresh_transit)
        self._refresh_transit_btn.setEnabled(bool(self._vendor_id))
        transit_row.addWidget(self._refresh_transit_btn)
        transit_row.addStretch(1)
        transit_wrap = QWidget()
        transit_wrap.setLayout(transit_row)
        form.addRow("Avg Transit:", transit_wrap)

        # Cutoff time row — checkbox + time + tz on one logical line.
        cutoff_row = QHBoxLayout()
        self._cutoff_enable = QCheckBox("Enabled")
        self._cutoff_enable.setToolTip(
            "When enabled, the cutoff time drives PO expected-date calc and "
            "the PO dialog will warn if the cutoff has passed."
        )
        cutoff_row.addWidget(self._cutoff_enable)
        self._cutoff_time = QTimeEdit()
        self._cutoff_time.setDisplayFormat("hh:mm AP")
        self._cutoff_time.setTime(QTime(14, 0))
        cutoff_row.addWidget(self._cutoff_time)
        self._cutoff_tz = QComboBox()
        self._cutoff_tz.setEditable(True)
        for tz in (
            "America/Denver", "America/Chicago", "America/New_York",
            "America/Los_Angeles", "America/Phoenix", "UTC",
        ):
            self._cutoff_tz.addItem(tz)
        self._cutoff_tz.setCurrentText("America/Denver")
        cutoff_row.addWidget(self._cutoff_tz, 1)
        cutoff_wrap = QWidget()
        cutoff_wrap.setLayout(cutoff_row)
        form.addRow("Cutoff Time:", cutoff_wrap)

        # Wire enable→disable on time/tz.
        def _toggle_cutoff(enabled: bool) -> None:
            self._cutoff_time.setEnabled(enabled)
            self._cutoff_tz.setEnabled(enabled)
        self._cutoff_enable.toggled.connect(_toggle_cutoff)
        _toggle_cutoff(False)

        self._cutoff_notes = QLineEdit()
        self._cutoff_notes.setPlaceholderText(
            "Optional notes (holidays, exceptions, blackout days…)"
        )
        form.addRow("Cutoff Notes:", self._cutoff_notes)

        # Min order / freight thresholds.
        self._min_order = QDoubleSpinBox()
        self._min_order.setRange(0, 999_999)
        self._min_order.setPrefix("$ ")
        self._min_order.setDecimals(2)
        self._min_order.setToolTip("Vendor's minimum order amount (informational).")
        form.addRow("Min Order:", self._min_order)

        self._freight_paid = QDoubleSpinBox()
        self._freight_paid.setRange(0, 999_999)
        self._freight_paid.setPrefix("$ ")
        self._freight_paid.setDecimals(2)
        self._freight_paid.setToolTip(
            "Order total at which vendor pays freight (informational)."
        )
        form.addRow("Freight Paid Over:", self._freight_paid)

        return box

    def _build_notes_group(self) -> QGroupBox:
        box = QGroupBox("Notes & Policy")
        form = QFormLayout(box)

        # Core handling overhaul (migration 108): vendor-level override of
        # the product's default ``core_type``. DEPRECATED in the May 2026
        # Core Handling Revitalization — the engine now uses a single rule
        # (``products.has_core`` + ``products.core_charge``) regardless of
        # vendor. The widget is kept hidden so the existing save handler
        # can still round-trip the column without crashing.
        self._core_handling_combo = QComboBox()
        _vch_options = [
            (None, "Use product default"),
            ("separate_line", "Always bill cores as a separate line"),
            ("included_in_price", "Cores included in unit price"),
            ("conditional_exchange", "Conditional exchange (charge only if no return)"),
        ]
        for value, label in _vch_options:
            self._core_handling_combo.addItem(label, value)
        self._core_handling_combo.setVisible(False)

        self._return_policy = QTextEdit()
        self._return_policy.setPlaceholderText("Return / RMA policy notes…")
        self._return_policy.setMaximumHeight(50)
        form.addRow("Return Policy:", self._return_policy)

        self._tracking_url_template = QLineEdit()
        self._tracking_url_template.setPlaceholderText(
            "Tracking URL (use {tracking} as the placeholder)"
        )
        form.addRow("Tracking URL:", self._tracking_url_template)

        self._notes = QTextEdit()
        self._notes.setPlaceholderText("Internal notes…")
        self._notes.setMaximumHeight(60)
        form.addRow("Notes:", self._notes)

        return box

    def _build_returns_address_group(self) -> QGroupBox:
        """Vendor Returns / RGA shipping address + contact (Phase G).

        These columns are used by the core packing slip + freight label
        PDFs as the "Ship To" destination. When all five address rows
        are blank, the PDF builder falls back to the regular vendor
        ``address`` blob, so this group is optional.
        """
        box = QGroupBox("Returns / RGA Address")
        form = QFormLayout(box)

        self._returns_line1 = QLineEdit()
        self._returns_line1.setPlaceholderText("Street address")
        form.addRow("Address 1:", self._returns_line1)

        self._returns_line2 = QLineEdit()
        self._returns_line2.setPlaceholderText("Suite / Dock # (optional)")
        form.addRow("Address 2:", self._returns_line2)

        city_row = QHBoxLayout()
        self._returns_city = QLineEdit()
        self._returns_city.setPlaceholderText("City")
        self._returns_state = QLineEdit()
        self._returns_state.setPlaceholderText("State")
        self._returns_state.setMaximumWidth(60)
        self._returns_zip = QLineEdit()
        self._returns_zip.setPlaceholderText("ZIP")
        self._returns_zip.setMaximumWidth(90)
        city_row.addWidget(self._returns_city, 3)
        city_row.addWidget(self._returns_state, 1)
        city_row.addWidget(self._returns_zip, 1)
        wrap = QWidget(); wrap.setLayout(city_row)
        form.addRow("City / ST / ZIP:", wrap)

        self._returns_contact_name = QLineEdit()
        self._returns_contact_name.setPlaceholderText("Returns dept. contact (optional)")
        form.addRow("Attn:", self._returns_contact_name)

        self._returns_contact_phone = QLineEdit()
        self._returns_contact_phone.setPlaceholderText("Returns dept. phone (optional)")
        form.addRow("Phone:", self._returns_contact_phone)

        self._returns_contact_email = QLineEdit()
        self._returns_contact_email.setPlaceholderText("Returns dept. email (optional)")
        form.addRow("Email:", self._returns_contact_email)

        return box

    def _build_core_obligations_group(self) -> QGroupBox:
        """Outstanding vendor core obligations for this vendor (read-only)."""
        from db.core_handling import (
            get_outstanding_vendor_core_amount,
            list_vendor_core_obligations,
        )

        try:
            outstanding = float(
                get_outstanding_vendor_core_amount(self._vendor_id) or 0
            )
        except Exception:
            outstanding = 0.0
        try:
            obligations = list_vendor_core_obligations(
                vendor_id=self._vendor_id, only_open=True
            )
        except Exception:
            obligations = []

        box = QGroupBox(
            f"Core Obligations  ·  Outstanding to this vendor: ${outstanding:,.2f}"
        )
        v = QVBoxLayout(box)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        # Status chip row — new 5-state lifecycle (May 2026 plan).
        # Legacy status names are folded into the matching new bucket so the
        # chip counts stay accurate during the migration window.
        counts: dict[str, int] = {}
        for o in obligations:
            s = str(o.get("status") or "").lower()
            counts[s] = counts.get(s, 0) + 1
        chips = QHBoxLayout()
        chips.setSpacing(10)
        for label, keys in (
            ("On Shelf",          ("on_shelf",)),
            ("Awaiting Customer", ("awaiting_customer_core", "awaiting_return")),
            ("Ready to Ship",     ("ready_to_ship", "pending")),
            ("Shipped",           ("shipped", "rga_created", "vendor_received")),
            ("Closed",            ("closed", "credited")),
        ):
            n = sum(counts.get(k, 0) for k in keys)
            chip = QLabel(f"<b>{n}</b> {label}")
            chip.setStyleSheet(
                "padding:4px 8px; border:1px solid #ccc; border-radius:8px;"
                " background:#f4f4f4;"
            )
            chips.addWidget(chip)
        chips.addStretch(1)
        v.addLayout(chips)

        if not obligations:
            empty = QLabel(
                "<i>No open core obligations to this vendor.</i>"
            )
            empty.setStyleSheet("color:#666;")
            v.addWidget(empty)
            return box

        # Mini-table of top 10 open obligations
        headers = [
            "Core #", "Product / SKU", "Source Customer",
            "Source Invoice", "Amount", "Status",
        ]
        top = obligations[:10]
        table = QTableWidget(len(top), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(False)
        try:
            table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Stretch
            )
        except Exception:
            pass

        # Pull customer/invoice/source-PO info via a one-shot query.
        from db.database import get_db
        ids = [int(o.get("id") or 0) for o in top]
        lineage: dict[int, dict] = {}
        if ids:
            placeholders = ",".join(["?"] * len(ids))
            try:
                with get_db() as conn:
                    rows = conn.execute(
                        f"""
                        SELECT vco.id AS obl_id,
                               cr.id  AS customer_core_id,
                               COALESCE(c.name, c.company) AS customer_name,
                               inv.invoice_number AS invoice_number
                          FROM vendor_core_obligations vco
                          LEFT JOIN core_returns cr ON cr.id = vco.customer_core_id
                          LEFT JOIN customers c ON c.id = cr.customer_id
                          LEFT JOIN invoices inv ON inv.id = cr.invoice_id
                         WHERE vco.id IN ({placeholders})
                        """,
                        ids,
                    ).fetchall()
                for r in rows:
                    d = dict(r)
                    lineage[int(d["obl_id"])] = d
            except Exception:
                pass

        for i, o in enumerate(top):
            oid = int(o.get("id") or 0)
            lin = lineage.get(oid, {})
            qty = int(o.get("quantity") or 1)
            amt = float(o.get("core_amount") or 0) * qty
            cells = [
                str(oid),
                f"{o.get('product_sku') or ''}  {o.get('product_title') or ''}".strip(),
                lin.get("customer_name") or "—",
                lin.get("invoice_number") or "—",
                f"${amt:,.2f}",
                str(o.get("status") or ""),
            ]
            for c, val in enumerate(cells):
                table.setItem(i, c, QTableWidgetItem(str(val)))
        table.resizeColumnsToContents()
        try:
            table.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Stretch
            )
        except Exception:
            pass
        v.addWidget(table)

        if len(obligations) > len(top):
            more = QLabel(
                f"<i>Showing {len(top)} of {len(obligations)} open obligations.</i>"
            )
            more.setStyleSheet("color:#666;")
            v.addWidget(more)

        # "View all on RGA screen" button
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        view_btn = QPushButton("View all on RGA screen →")
        view_btn.clicked.connect(self._open_rga_for_vendor)
        btn_row.addWidget(view_btn)
        v.addLayout(btn_row)

        return box

    def _open_rga_for_vendor(self) -> None:
        """Best-effort: ask the parent main window to switch to Vendor
        Returns and pre-filter to this vendor."""
        if not self._vendor_id:
            return
        # Walk up looking for a main window with a switch helper.
        target = self.parent()
        while target is not None:
            for attr in ("open_vendor_returns_for_vendor",
                         "show_vendor_returns_screen"):
                fn = getattr(target, attr, None)
                if callable(fn):
                    try:
                        fn(self._vendor_id)
                    except TypeError:
                        try:
                            fn()
                        except Exception:
                            pass
                    self.accept()
                    return
            target = target.parent() if hasattr(target, "parent") else None
        # Fallback: just close the dialog so the user can navigate manually.
        self.accept()

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------
    def _populate(self) -> None:
        if not self._vendor:
            return
        v = self._vendor
        self._name.setText(str(v.get("name") or ""))
        self._code.setText(str(v.get("code") or ""))
        self._contact_name.setText(str(v.get("contact_name") or ""))
        self._contact_email.setText(str(v.get("contact_email") or ""))
        self._contact_phone.setText(str(v.get("contact_phone") or ""))
        self._address.setPlainText(str(v.get("address") or ""))
        self._is_active.setChecked(bool(v.get("is_active", True)))

        if not self._compact:
            self._payment_terms.setText(str(v.get("payment_terms") or ""))
            self._notes.setPlainText(str(v.get("notes") or ""))
            self._lead_time.setValue(v.get("lead_time_days", 14) or 14)

            self._website.setText(str(v.get("website") or ""))
            self._account_number.setText(str(v.get("account_number") or ""))
            self._qbo_vendor_id.setText(str(v.get("qbo_vendor_id") or ""))
            self._tax_id.setText(str(v.get("tax_id") or ""))
            self._is_1099.setChecked(bool(v.get("is_1099")))
            self._ship_from_address.setPlainText(str(v.get("ship_from_address") or ""))
            self._min_order.setValue(float(v.get("min_order_amount") or 0))
            self._freight_paid.setValue(float(v.get("freight_paid_threshold") or 0))
            self._return_policy.setPlainText(str(v.get("return_policy") or ""))
            self._tracking_url_template.setText(str(v.get("tracking_url_template") or ""))
            self._refresh_avg_transit_display()

            # Phase G — vendor returns / RGA address.
            self._returns_line1.setText(str(v.get("returns_address_line1") or ""))
            self._returns_line2.setText(str(v.get("returns_address_line2") or ""))
            self._returns_city.setText(str(v.get("returns_address_city") or ""))
            self._returns_state.setText(str(v.get("returns_address_state") or ""))
            self._returns_zip.setText(str(v.get("returns_address_zip") or ""))
            self._returns_contact_name.setText(str(v.get("returns_contact_name") or ""))
            self._returns_contact_phone.setText(str(v.get("returns_contact_phone") or ""))
            self._returns_contact_email.setText(str(v.get("returns_contact_email") or ""))

            # Core handling override (migration 108)
            _ch = v.get("core_handling")
            _ch_idx = self._core_handling_combo.findData(_ch)
            if _ch_idx < 0:
                _ch_idx = self._core_handling_combo.findData(None)
            self._core_handling_combo.setCurrentIndex(max(_ch_idx, 0))

            cutoff_local = (v.get("cutoff_time_local") or "").strip()
            if cutoff_local:
                self._cutoff_enable.setChecked(True)
                t = QTime.fromString(cutoff_local[:5], "HH:mm")
                if t.isValid():
                    self._cutoff_time.setTime(t)
            self._cutoff_tz.setCurrentText(
                (v.get("cutoff_timezone") or "America/Denver").strip()
            )
            self._cutoff_notes.setText(str(v.get("cutoff_notes") or ""))

        # SKU prefix — load into _sku_prefix (hidden); if code is blank, also
        # pre-fill code from the stored prefix.
        if self._vendor_id:
            try:
                pfx = get_sku_prefix_for_vendor(self._vendor_id)
                if pfx:
                    self._sku_prefix.setText(pfx["prefix"])
                    if not self._code.text().strip():
                        self._code.setText(pfx["prefix"])
            except Exception:
                pass

    def _refresh_avg_transit_display(self) -> None:
        if not self._vendor_id or self._compact:
            return
        try:
            computed = get_vendor_avg_transit_days(self._vendor_id)
        except Exception:
            computed = None
        if computed is None:
            stored = (self._vendor or {}).get("avg_transit_days") or 0
            self._avg_transit.setValue(int(stored))
            self._avg_transit.setToolTip(
                "No PO history yet — showing stored value."
            )
        else:
            self._avg_transit.setValue(int(computed))
            self._avg_transit.setToolTip(
                "Auto-calculated from this vendor's recent received POs."
            )

    def _on_refresh_transit(self) -> None:
        self._refresh_avg_transit_display()

    # ------------------------------------------------------------------
    # Live formatting / validation helpers
    # ------------------------------------------------------------------
    def _on_code_changed(self, text: str) -> None:
        """Keep the hidden _sku_prefix widget in sync with the Code field."""
        self._sku_prefix.setText(text.strip())

    def _on_phone_finished(self) -> None:
        formatted = _format_phone(self._contact_phone.text())
        if formatted and formatted != self._contact_phone.text():
            self._contact_phone.setText(formatted)

    def _validate(self) -> tuple[bool, str | None]:
        """Return (valid, error_message_if_any)."""
        name = self._name.text().strip()
        if not name:
            self._name.setFocus()
            return False, "Vendor name is required."

        email = self._contact_email.text().strip()
        if email and not _EMAIL_RE.match(email):
            self._contact_email.setFocus()
            return False, f"'{email}' doesn't look like a valid email."

        # Code uniqueness.
        code = self._code.text().strip()
        if code:
            try:
                others = get_all_vendors(active_only=False)
            except Exception:
                others = []
            for v in others:
                if (v.get("id") != self._vendor_id
                        and (v.get("code") or "").strip().lower() == code.lower()):
                    return False, (
                        f"Code '{code}' is already used by '{v.get('name')}'. "
                        "Pick a different code."
                    )

        # Duplicate-name warning (case-insensitive). Soft confirm for new vendors.
        if not self._vendor_id:
            try:
                others = get_all_vendors(active_only=False)
            except Exception:
                others = []
            dup = next(
                (v for v in others
                 if (v.get("name") or "").strip().lower() == name.lower()),
                None,
            )
            if dup is not None:
                ans = QMessageBox.question(
                    self, "Duplicate Vendor",
                    f"A vendor named '{dup.get('name')}' already exists. "
                    "Create another with the same name?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if ans != QMessageBox.Yes:
                    return False, None

        return True, None

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def _gather_payload(self) -> dict:
        payload = {
            "name": self._name.text().strip(),
            "code": self._code.text().strip() or None,
            "contact_name": self._contact_name.text().strip() or None,
            "contact_email": self._contact_email.text().strip() or None,
            "contact_phone": self._contact_phone.text().strip() or None,
            "address": self._address.toPlainText().strip() or None,
        }
        if not self._compact:
            payload.update({
                "payment_terms": self._payment_terms.text().strip() or None,
                "notes": self._notes.toPlainText().strip() or None,
                "lead_time_days": self._lead_time.value(),
                "avg_transit_days": self._avg_transit.value(),
                "cutoff_time_local": (
                    self._cutoff_time.time().toString("HH:mm")
                    if self._cutoff_enable.isChecked() else None
                ),
                "cutoff_timezone": self._cutoff_tz.currentText().strip() or "America/Denver",
                "cutoff_notes": self._cutoff_notes.text().strip() or None,
                "website": self._website.text().strip() or None,
                "account_number": self._account_number.text().strip() or None,
                "tax_id": self._tax_id.text().strip() or None,
                "is_1099": self._is_1099.isChecked(),
                "ship_from_address": self._ship_from_address.toPlainText().strip() or None,
                "min_order_amount": float(self._min_order.value()),
                "freight_paid_threshold": float(self._freight_paid.value()),
                "return_policy": self._return_policy.toPlainText().strip() or None,
                "tracking_url_template": self._tracking_url_template.text().strip() or None,
                "qbo_vendor_id": self._qbo_vendor_id.text().strip() or None,
                # Migration 108 — None means "use product default".
                "core_handling": self._core_handling_combo.currentData(),
                # Phase G — vendor returns / RGA address.
                "returns_address_line1": self._returns_line1.text().strip() or None,
                "returns_address_line2": self._returns_line2.text().strip() or None,
                "returns_address_city":  self._returns_city.text().strip()  or None,
                "returns_address_state": self._returns_state.text().strip() or None,
                "returns_address_zip":   self._returns_zip.text().strip()   or None,
                "returns_contact_name":  self._returns_contact_name.text().strip()  or None,
                "returns_contact_phone": self._returns_contact_phone.text().strip() or None,
                "returns_contact_email": self._returns_contact_email.text().strip() or None,
            })
        return payload

    def _save_sku_prefix(self, vid: int, name: str) -> None:
        sku_pfx = self._sku_prefix.text().strip()
        if not sku_pfx:
            return
        try:
            existing = get_sku_prefix_for_vendor(vid)
            if existing:
                update_sku_prefix(existing["id"], prefix=sku_pfx)
            else:
                create_sku_prefix(sku_pfx, name, vendor_id=vid)
        except Exception:
            # Non-fatal; surface only via console.
            pass

    def _on_save(self) -> None:
        ok, err = self._validate()
        if not ok:
            if err:
                QMessageBox.warning(self, "Validation", err)
            return

        payload = self._gather_payload()

        if self._vendor_id:
            payload["is_active"] = self._is_active.isChecked()
            result = update_vendor(self._vendor_id, **payload)
            if result.get("success"):
                self._save_sku_prefix(self._vendor_id, payload["name"])
                self.vendor_id = self._vendor_id
                self.accept()
            else:
                QMessageBox.critical(
                    self, "Error",
                    f"Failed to save vendor:\n{result.get('error')}",
                )
            return

        result = create_vendor(**payload)
        if not result.get("success"):
            QMessageBox.critical(
                self, "Error",
                f"Failed to save vendor:\n{result.get('error')}",
            )
            return

        new_id = result.get("vendor_id") or (result.get("vendor") or {}).get("id")
        if new_id:
            self._save_sku_prefix(int(new_id), payload["name"])
        self.created_vendor = result.get("vendor")
        self.vendor_id = new_id
        self.accept()

    def _on_save_and_new(self) -> None:
        """Save the current vendor; caller will reopen a fresh dialog."""
        self._save_and_new = True
        self._on_save()
        # If validation aborted, _on_save did not call accept; reset the flag.
        if self.result() != QDialog.DialogCode.Accepted:
            self._save_and_new = False
