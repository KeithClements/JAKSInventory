"""Invoice creation/editing dialog."""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QFrame,
    QLineEdit,
    QTextEdit,
    QComboBox,
    QDateEdit,
    QCalendarWidget,
    QPushButton,
    QMessageBox,
    QGroupBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QDoubleSpinBox,
    QSpinBox,
    QLabel,
    QCompleter,
    QSplitter,
    QWidget,
)
from PySide6.QtGui import QColor


def _make_calendar() -> QCalendarWidget:
    """Build a QCalendarWidget that won't be clipped in the popup."""
    cal = QCalendarWidget()
    cal.setGridVisible(True)
    cal.setHorizontalHeaderFormat(
        QCalendarWidget.HorizontalHeaderFormat.SingleLetterDayNames
    )
    cal.setVerticalHeaderFormat(
        QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
    )
    cal.setMinimumSize(300, 230)
    cal.setStyleSheet(
        "QCalendarWidget QWidget { background-color: white; }"
        "QCalendarWidget QAbstractItemView:enabled { color: #1F2937; }"
        "QCalendarWidget QToolButton { color: #1F2937; background: #F9FAFB; padding: 4px 8px; }"
    )
    return cal

from db import (
    get_all_customers,
    get_customer,
    create_invoice,
    get_invoice,
    add_invoice_line,
    add_discount_line,
    remove_invoice_line,
    update_invoice_line_price,
    update_invoice_status,
    update_invoice,
    browse_products,
    get_product_by_sku,
    get_setting,
    estimate_order_shipping,
    get_db,
)
from .discount_line_dialog import DiscountLineDialog
from .workflow_shell import (
    build_bottom_action_bar,
    build_header_bar,
    build_progress_strip,
    build_summary_cards,
    create_activity_text_area,
    create_link_card,
    create_sidebar_container,
    create_sidebar_section_title,
    create_status_badge,
    style_action_button,
)
from .auto_refresh_mixin import AutoRefreshMixin


_INV_BADGE_COLORS = {
    "draft": ("#E5E7EB", "#374151"),
    "sent": ("#DBEAFE", "#1E40AF"),
    "partial": ("#FEF3C7", "#92400E"),
    "paid": ("#D1FAE5", "#065F46"),
    "void": ("#FEE2E2", "#991B1B"),
}
_INV_PROGRESS = ["Quote", "Sales Order", "Purchasing", "Receiving", "Picking", "Shipping", "Invoicing"]


class InvoiceDialog(AutoRefreshMixin, QDialog):
    """Dialog for creating or editing an invoice."""

    def __init__(self, invoice_id: int = None, customer_id: int = None, parent=None):
        super().__init__(parent)
        self._invoice_id = invoice_id
        self._invoice = None
        self._is_new = invoice_id is None
        self._preset_customer_id = customer_id
        
        if invoice_id:
            inv = get_invoice(invoice_id)
            # Convert to dict to release any DB cursor
            self._invoice = dict(inv) if inv else None
            self.setWindowTitle(f"Invoice: {self._invoice.get('invoice_number', '') if self._invoice else ''}")
        else:
            self.setWindowTitle("New Invoice")

        self.setMinimumSize(1180, 760)
        self.resize(1360, 860)
        try:
            from PySide6.QtCore import Qt
            self.setWindowFlag(Qt.WindowType.Window, True)
            self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
            self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        except Exception:
            pass
        self._build_ui()
        self._load_data()
        # Initialize auto-refresh after UI and data are loaded (3 sec interval)
        self._init_auto_refresh(refresh_interval_ms=3000)
        # E5 — warn once if this invoice has already been pushed/marked to QBO
        self._qbo_lock_acknowledged = False
        self._qbo_lock_warning_pending = bool(invoice_id)

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if getattr(self, "_qbo_lock_warning_pending", False):
            self._qbo_lock_warning_pending = False
            try:
                from db import is_invoice_locked
                if self._invoice_id and is_invoice_locked(int(self._invoice_id)):
                    inv_no = (self._invoice or {}).get(
                        "invoice_number", f"#{self._invoice_id}"
                    )
                    resp = QMessageBox.warning(
                        self,
                        "Invoice locked (QBO export)",
                        f"Invoice {inv_no} has already been exported to "
                        "QuickBooks. Editing it now may cause your books to "
                        "fall out of sync with QBO.\n\n"
                        "Open in read-only mode is recommended. Edit anyway?",
                        QMessageBox.StandardButton.Yes
                        | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Cancel,
                    )
                    if resp != QMessageBox.StandardButton.Yes:
                        # Defer reject so showEvent finishes cleanly.
                        from PySide6.QtCore import QTimer
                        QTimer.singleShot(0, self.reject)
                        return
                    self._qbo_lock_acknowledged = True
            except Exception:
                # Never let the lock check itself break the dialog.
                pass

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_progress_strip())
        root.addWidget(self._build_summary_cards())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)
        splitter.addWidget(self._build_main_panel())
        splitter.addWidget(self._build_sidebar())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([930, 360])
        root.addWidget(splitter, 1)

        root.addWidget(self._build_bottom_bar())

    def _build_header(self) -> QFrame:
        bar, lay = build_header_bar()

        self._title_label = QLabel("New Invoice")
        self._title_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        lay.addWidget(self._title_label)

        self._status_badge = create_status_badge("DRAFT")
        lay.addWidget(self._status_badge)

        lay.addSpacing(16)
        self._header_customer = QLabel("Customer: —")
        self._header_customer.setStyleSheet("color: #D1D5DB; font-size: 12px;")
        lay.addWidget(self._header_customer)

        self._header_source = QLabel("Source SO: —")
        self._header_source.setStyleSheet("color: #93C5FD; font-size: 12px;")
        lay.addWidget(self._header_source)

        self._header_dates = QLabel("")
        self._header_dates.setStyleSheet("color: #9CA3AF; font-size: 11px;")
        lay.addWidget(self._header_dates)

        lay.addStretch()

        self._header_total = QLabel("$0.00")
        self._header_total.setStyleSheet("color: #34D399; font-size: 18px; font-weight: bold;")
        lay.addWidget(self._header_total)
        return bar

    def _build_progress_strip(self) -> QFrame:
        strip, labels = build_progress_strip(_INV_PROGRESS)
        self._progress_labels = labels
        return strip

    def _build_summary_cards(self) -> QFrame:
        frame, cards = build_summary_cards(
            [
                ("Lines", "0", "#6B7280"),
                ("Qty", "0", "#2563EB"),
                ("Subtotal", "$0.00", "#059669"),
                ("Tax", "$0.00", "#D97706"),
                ("Shipping", "$0.00", "#0891B2"),
                ("Balance Due", "$0.00", "#DC2626"),
            ],
            min_width=110,
        )
        (
            self._card_lines,
            self._card_qty,
            self._card_subtotal,
            self._card_tax,
            self._card_shipping,
            self._card_balance,
        ) = cards
        return frame

    @staticmethod
    def _make_card(title, value, color):
        # Retained for compatibility in case older call sites reference this helper.
        from .workflow_shell import create_metric_card
        return create_metric_card(title, value, color, min_width=110)

    def _build_main_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 6, 8)
        lay.setSpacing(8)

        hdr_frame = QFrame()
        hdr_frame.setStyleSheet(
            "QFrame { background: white; border: 1px solid #E5E7EB; border-radius: 6px; padding: 8px; }"
        )
        hdr = QHBoxLayout(hdr_frame)
        hdr.setSpacing(12)

        hdr.addWidget(QLabel("Customer:"))
        self._customer_combo = QComboBox()
        self._customer_combo.setMinimumWidth(260)
        self._load_customers()
        self._customer_combo.currentIndexChanged.connect(self._on_customer_changed)
        hdr.addWidget(self._customer_combo)

        self._customer_info = QLabel("")
        self._customer_info.setStyleSheet("color: #6B7280; font-size: 11px;")
        hdr.addWidget(self._customer_info, 1)

        hdr.addWidget(QLabel("Invoice Date:"))
        self._invoice_date = QDateEdit()
        self._invoice_date.setCalendarPopup(True)
        self._invoice_date.setDisplayFormat("M/d/yyyy")
        self._invoice_date.setCalendarWidget(_make_calendar())
        self._invoice_date.setDate(date.today())
        hdr.addWidget(self._invoice_date)

        hdr.addWidget(QLabel("Due Date:"))
        self._due_date = QDateEdit()
        self._due_date.setCalendarPopup(True)
        self._due_date.setDisplayFormat("M/d/yyyy")
        self._due_date.setCalendarWidget(_make_calendar())
        terms = get_setting("payment_terms", "Net 30")
        term_days = int(terms.split()[-1]) if terms.startswith("Net") else 0
        self._due_date.setDate(date.today() + timedelta(days=term_days))
        hdr.addWidget(self._due_date)
        lay.addWidget(hdr_frame)

        # Equipment / ESN row (second header row)
        eq_frame = QFrame()
        eq_frame.setStyleSheet(
            "QFrame { background: #FFF7ED; border: 1px solid #FED7AA; "
            "border-radius: 6px; padding: 6px; }"
        )
        eq_lay = QHBoxLayout(eq_frame)
        eq_lay.setSpacing(8)
        eq_lay.addWidget(QLabel("Applies to:"))
        self._equipment_type_combo = QComboBox()
        self._equipment_type_combo.addItem("— none —", "")
        self._equipment_type_combo.addItem("Engine", "engine")
        self._equipment_type_combo.addItem("Truck", "truck")
        eq_lay.addWidget(self._equipment_type_combo)

        eq_lay.addWidget(QLabel("Make:"))
        self._equipment_make_combo = QComboBox()
        self._equipment_make_combo.setEditable(True)
        self._equipment_make_combo.addItems([
            "", "Cummins", "Caterpillar", "Detroit Diesel",
            "Navistar / International", "PACCAR", "Volvo / Mack",
            "John Deere", "Perkins", "Deutz", "Other",
        ])
        self._equipment_make_combo.setMinimumWidth(140)
        eq_lay.addWidget(self._equipment_make_combo)

        eq_lay.addWidget(QLabel("Model:"))
        self._equipment_model_edit = QLineEdit()
        self._equipment_model_edit.setPlaceholderText("e.g. DT466")
        self._equipment_model_edit.setMaximumWidth(130)
        eq_lay.addWidget(self._equipment_model_edit)

        eq_lay.addWidget(QLabel("ESN:"))
        self._esn_edit = QLineEdit()
        self._esn_edit.setPlaceholderText("Engine S/N")
        self._esn_edit.setMaximumWidth(140)
        eq_lay.addWidget(self._esn_edit)

        eq_lay.addWidget(QLabel("VIN:"))
        self._vin_edit = QLineEdit()
        self._vin_edit.setPlaceholderText("VIN")
        self._vin_edit.setMaximumWidth(170)
        eq_lay.addWidget(self._vin_edit)
        eq_lay.addStretch()
        lay.addWidget(eq_frame)

        add_frame = QFrame()
        add_frame.setStyleSheet("QFrame { background: transparent; }")
        add_outer = QVBoxLayout(add_frame)
        add_outer.setContentsMargins(0, 0, 0, 0)
        add_outer.setSpacing(6)

        add_row1 = QHBoxLayout()
        self._sku_edit = QLineEdit()
        self._sku_edit.setPlaceholderText("SKU or search…")
        self._sku_edit.setMinimumWidth(170)
        self._setup_sku_completer()
        self._sku_edit.textChanged.connect(self._on_sku_changed)
        add_row1.addWidget(self._sku_edit)

        self._find_product_btn = QPushButton("🔍 Find Product")
        self._find_product_btn.setToolTip("Open product search to look up SKU, OEM, description…")
        self._find_product_btn.clicked.connect(self._on_find_product)
        self._find_product_btn.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; padding: 4px 10px; "
            "border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        add_row1.addWidget(self._find_product_btn)

        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText("Description")
        add_row1.addWidget(self._desc_edit, 1)
        add_outer.addLayout(add_row1)

        add_row2 = QHBoxLayout()
        self._serial_label = QLabel("Serial:")
        self._serial_combo = QComboBox()
        self._serial_combo.setMinimumWidth(150)
        self._serial_combo.setPlaceholderText("Select serial…")
        self._serial_combo.currentIndexChanged.connect(self._on_serial_selected)
        self._serial_label.setVisible(False)
        self._serial_combo.setVisible(False)
        add_row2.addWidget(self._serial_label)
        add_row2.addWidget(self._serial_combo)

        add_row2.addWidget(QLabel("Qty:"))
        self._qty_spin = QSpinBox()
        self._qty_spin.setRange(1, 9999)
        self._qty_spin.setValue(1)
        self._qty_spin.setMinimumWidth(60)
        add_row2.addWidget(self._qty_spin)

        add_row2.addWidget(QLabel("Price:"))
        self._price_spin = QDoubleSpinBox()
        self._price_spin.setRange(0, 999999)
        self._price_spin.setDecimals(2)
        self._price_spin.setPrefix("$")
        self._price_spin.setMinimumWidth(100)
        add_row2.addWidget(self._price_spin)

        add_row2.addStretch()
        self._add_line_btn = QPushButton("➕ Add Line")
        self._add_line_btn.clicked.connect(self._on_add_line)
        self._add_line_btn.setStyleSheet(
            "QPushButton { background: #059669; color: white; font-weight: bold; "
            "padding: 4px 12px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #047857; }"
        )
        add_row2.addWidget(self._add_line_btn)
        self._add_discount_btn = QPushButton("➖ Add Discount")
        self._add_discount_btn.setToolTip(
            "Add a manual discount line (% of subtotal or fixed dollar amount)."
        )
        self._add_discount_btn.clicked.connect(self._on_add_discount_line)
        self._add_discount_btn.setStyleSheet(
            "QPushButton { background: #b91c1c; color: white; font-weight: bold; "
            "padding: 4px 12px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #991b1b; }"
        )
        add_row2.addWidget(self._add_discount_btn)
        self._remove_line_btn = QPushButton("🗑️ Remove Selected")
        self._remove_line_btn.clicked.connect(self._on_remove_line)
        self._remove_line_btn.setStyleSheet(
            "QPushButton { color: #DC2626; padding: 4px 10px; border-radius: 4px; "
            "border: 1px solid #FCA5A5; }"
            "QPushButton:hover { background: #FEE2E2; }"
        )
        add_row2.addWidget(self._remove_line_btn)
        add_outer.addLayout(add_row2)
        lay.addWidget(add_frame)

        self._lines_table = QTableWidget(0, 8)
        self._lines_table.setHorizontalHeaderLabels([
            "ID", "Description", "Serial", "Qty", "Unit Price", "Core", "Warranty", "Total"
        ])
        self._lines_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._lines_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._lines_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._lines_table.setColumnHidden(0, True)
        self._lines_table.verticalHeader().setVisible(False)
        self._lines_table.setAlternatingRowColors(True)
        self._lines_table.verticalHeader().setDefaultSectionSize(30)
        self._lines_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._lines_table.setColumnWidth(2, 120)
        self._lines_table.setColumnWidth(5, 90)
        self._lines_table.setColumnWidth(6, 130)
        self._lines_table.cellChanged.connect(self._on_line_price_changed)
        self._lines_table.cellDoubleClicked.connect(self._on_line_cell_double_clicked)
        self._lines_table.cellClicked.connect(self._on_line_cell_clicked)
        # Right-click on a line row \u2192 Process Core / Print Slip / View Product / Copy SKU.
        self._lines_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._lines_table.customContextMenuRequested.connect(self._on_line_context_menu)
        self._lines_table.setStyleSheet(
            "QTableWidget { border: 1px solid #E5E7EB; border-radius: 6px; gridline-color: #F3F4F6; }"
            "QHeaderView::section {"
            " background-color: #F9FAFB; color: #6B7280; font-weight: 600;"
            " font-size: 11px; padding: 6px 8px; border: none; border-bottom: 1px solid #E5E7EB;"
            "}"
            "QTableWidget::item { padding: 4px 8px; }"
            "QTableWidget::item:selected { background-color: #DBEAFE; color: #1E40AF; }"
        )
        lay.addWidget(self._lines_table, 1)

        controls = QFrame()
        controls.setStyleSheet(
            "QFrame { background: white; border: 1px solid #E5E7EB; border-radius: 6px; padding: 8px; }"
        )
        c_lay = QVBoxLayout(controls)
        c_lay.setContentsMargins(10, 8, 10, 8)
        c_lay.setSpacing(8)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Tax Rate:"))
        self._tax_spin = QDoubleSpinBox()
        self._tax_spin.setRange(0, 100)
        self._tax_spin.setSuffix("%")
        self._tax_spin.setValue(float(get_setting("default_tax_rate", "0")))
        self._tax_spin.valueChanged.connect(self._update_totals)
        self._tax_spin.editingFinished.connect(self._persist_tax_rate)
        row1.addWidget(self._tax_spin)
        row1.addSpacing(12)

        row1.addWidget(QLabel("Shipping:"))
        self._shipping_spin = QDoubleSpinBox()
        self._shipping_spin.setRange(0, 99999)
        self._shipping_spin.setDecimals(2)
        self._shipping_spin.setPrefix("$")
        self._shipping_spin.setFixedWidth(120)
        self._shipping_spin.valueChanged.connect(self._update_totals)
        self._shipping_spin.editingFinished.connect(self._persist_shipping)
        row1.addWidget(self._shipping_spin)

        self._est_ship_btn = QPushButton("Estimate")
        self._est_ship_btn.setFixedWidth(80)
        self._est_ship_btn.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; font-size: 11px; "
            "padding: 2px 6px; border-radius: 3px; border: none; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self._est_ship_btn.clicked.connect(self._estimate_shipping)
        row1.addWidget(self._est_ship_btn)

        self._ship_est_label = QLabel("")
        self._ship_est_label.setStyleSheet("color: #6B7280; font-size: 11px;")
        row1.addWidget(self._ship_est_label)
        row1.addSpacing(12)

        from PySide6.QtWidgets import QCheckBox as _QCheckBox
        self._cc_fee_check = _QCheckBox("\U0001F4B3 Add 3% CC fee")
        self._cc_fee_check.setToolTip(
            "Apply a 3% credit-card convenience fee to this invoice.\n"
            "Customer can avoid by paying with cash, check, or ACH."
        )
        self._cc_fee_check.toggled.connect(self._on_cc_fee_toggled)
        row1.addWidget(self._cc_fee_check)

        row1.addStretch()
        row1.addWidget(QLabel("Total:"))
        self._subtotal_label = QLabel("$0.00")
        self._tax_label = QLabel("$0.00")
        self._total_label = QLabel("$0.00")
        self._total_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #059669;")
        row1.addWidget(self._total_label)
        c_lay.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Customer Notes:"))
        self._notes_edit = QLineEdit()
        self._notes_edit.setPlaceholderText("Notes visible to customer")
        if self._is_new:
            self._notes_edit.setText(get_setting("invoice_notes", ""))
        row2.addWidget(self._notes_edit, 1)
        c_lay.addLayout(row2)
        lay.addWidget(controls)
        return panel

    def _build_sidebar(self) -> QWidget:
        panel, lay = create_sidebar_container(min_width=300, max_width=380)

        self._source_card = self._make_link_card("Source Sales Order", "Not linked", "Open Sales Order")
        self._source_card[2].clicked.connect(self._open_source_sales_order)
        lay.addWidget(self._source_card[0])

        self._shipment_card = QFrame()
        self._shipment_card.setStyleSheet(
            "QFrame { background: white; border: 1px solid #E5E7EB; border-radius: 4px; padding: 6px; }"
        )
        shipment_lay = QVBoxLayout(self._shipment_card)
        shipment_lay.setContentsMargins(8, 4, 8, 4)
        shipment_lay.setSpacing(6)
        ship_title = QLabel("Shipment / Tracking")
        ship_title.setStyleSheet("font-size: 10px; color: #6B7280; font-weight: 600;")
        shipment_lay.addWidget(ship_title)
        self._shipment_summary = QLabel("No shipment linked yet")
        self._shipment_summary.setWordWrap(True)
        self._shipment_summary.setStyleSheet("font-size: 11px; color: #1F2937; font-weight: 500;")
        shipment_lay.addWidget(self._shipment_summary)
        ship_form = QFormLayout()
        ship_form.setContentsMargins(0, 0, 0, 0)
        ship_form.setSpacing(6)
        self._tracking_edit = QLineEdit()
        self._tracking_edit.setPlaceholderText("Tracking number")
        ship_form.addRow("Tracking #:", self._tracking_edit)
        self._ship_date_edit = QDateEdit()
        self._ship_date_edit.setCalendarPopup(True)
        self._ship_date_edit.setDisplayFormat("M/d/yyyy")
        self._ship_date_edit.setCalendarWidget(_make_calendar())
        self._ship_date_edit.setSpecialValueText(" ")
        self._ship_date_edit.setDate(self._ship_date_edit.minimumDate())
        ship_form.addRow("Ship Date:", self._ship_date_edit)
        shipment_lay.addLayout(ship_form)
        self._shipment_hint = QLabel("Future shipping workflow will own these fields. Invoice uses shipment data when available.")
        self._shipment_hint.setWordWrap(True)
        self._shipment_hint.setStyleSheet("font-size: 10px; color: #6B7280;")
        shipment_lay.addWidget(self._shipment_hint)
        lay.addWidget(self._shipment_card)

        # Payment Status card with Record Payment button
        self._payment_card = QFrame()
        self._payment_card.setStyleSheet(
            "QFrame { background: white; border: 1px solid #E5E7EB; border-radius: 4px; padding: 6px; }"
        )
        payment_lay = QVBoxLayout(self._payment_card)
        payment_lay.setContentsMargins(8, 4, 8, 4)
        payment_lay.setSpacing(6)

        pay_title = QLabel("Payment Status")
        pay_title.setStyleSheet("font-size: 10px; color: #6B7280; font-weight: 600;")
        payment_lay.addWidget(pay_title)

        self._payment_status = QLabel("Draft · $0.00 paid · $0.00 due")
        self._payment_status.setStyleSheet("font-size: 11px; color: #1F2937; font-weight: 500;")
        self._payment_status.setWordWrap(True)
        payment_lay.addWidget(self._payment_status)

        pay_btn_row = QHBoxLayout()
        pay_btn_row.setContentsMargins(0, 0, 0, 0)
        pay_btn_row.setSpacing(6)

        self._record_payment_btn = QPushButton("💰 Record Payment")
        self._record_payment_btn.setFixedHeight(26)
        self._record_payment_btn.setStyleSheet(style_action_button("teal"))
        self._record_payment_btn.clicked.connect(self._on_record_payment)
        pay_btn_row.addWidget(self._record_payment_btn)

        refresh_totals_btn = QPushButton("Refresh")
        refresh_totals_btn.setFixedHeight(26)
        refresh_totals_btn.setStyleSheet(style_action_button("subtle"))
        refresh_totals_btn.clicked.connect(self._reload_invoice)
        pay_btn_row.addWidget(refresh_totals_btn)

        payment_lay.addLayout(pay_btn_row)
        lay.addWidget(self._payment_card)

        # QBO Sync card — surfaces the audit log row for this invoice.
        self._qbo_sync_card = QFrame()
        self._qbo_sync_card.setStyleSheet(
            "QFrame { background: white; border: 1px solid #E5E7EB; border-radius: 4px; padding: 6px; }"
        )
        qbo_lay = QVBoxLayout(self._qbo_sync_card)
        qbo_lay.setContentsMargins(8, 4, 8, 4)
        qbo_lay.setSpacing(6)

        qbo_title = QLabel("QBO Sync")
        qbo_title.setStyleSheet("font-size: 10px; color: #6B7280; font-weight: 600;")
        qbo_lay.addWidget(qbo_title)

        self._qbo_sync_status = QLabel("Not yet pushed to QBO")
        self._qbo_sync_status.setStyleSheet("font-size: 11px; color: #1F2937; font-weight: 500;")
        self._qbo_sync_status.setWordWrap(True)
        qbo_lay.addWidget(self._qbo_sync_status)

        qbo_btn_row = QHBoxLayout()
        qbo_btn_row.setContentsMargins(0, 0, 0, 0)
        qbo_btn_row.setSpacing(6)
        self._view_qbo_payload_btn = QPushButton("View QBO Payload…")
        self._view_qbo_payload_btn.setFixedHeight(26)
        self._view_qbo_payload_btn.setStyleSheet(style_action_button("subtle"))
        self._view_qbo_payload_btn.clicked.connect(self._on_view_qbo_payload)
        qbo_btn_row.addWidget(self._view_qbo_payload_btn)
        qbo_lay.addLayout(qbo_btn_row)

        lay.addWidget(self._qbo_sync_card)

        # Core return card — Receive / Issue Credit without leaving the invoice.
        self._core_card = QFrame()
        self._core_card.setStyleSheet(
            "QFrame { background: white; border: 1px solid #E5E7EB; border-radius: 4px; padding: 6px; }"
        )
        core_lay = QVBoxLayout(self._core_card)
        core_lay.setContentsMargins(8, 4, 8, 4)
        core_lay.setSpacing(6)

        core_title = QLabel("Core Returns")
        core_title.setStyleSheet("font-size: 10px; color: #6B7280; font-weight: 600;")
        core_lay.addWidget(core_title)

        self._core_status_label = QLabel("No cores linked")
        self._core_status_label.setStyleSheet("font-size: 11px; color: #1F2937; font-weight: 500;")
        self._core_status_label.setWordWrap(True)
        core_lay.addWidget(self._core_status_label)

        core_btn_row = QHBoxLayout()
        core_btn_row.setContentsMargins(0, 0, 0, 0)
        core_btn_row.setSpacing(6)

        self._process_core_btn = QPushButton("↩ Process Core Return")
        self._process_core_btn.setFixedHeight(26)
        self._process_core_btn.setStyleSheet(style_action_button("teal"))
        self._process_core_btn.setToolTip(
            "Receive a returned core and issue credit. "
            "Available when this invoice has pending or received cores."
        )
        self._process_core_btn.clicked.connect(self._on_process_core_return)
        core_btn_row.addWidget(self._process_core_btn)

        self._print_core_btn = QPushButton("🖨 Print Receipt")
        self._print_core_btn.setFixedHeight(26)
        self._print_core_btn.setStyleSheet(style_action_button("subtle"))
        self._print_core_btn.setToolTip(
            "Print the customer-signable Core Return Receipt for this invoice."
        )
        self._print_core_btn.clicked.connect(self._on_print_core_receipt)
        core_btn_row.addWidget(self._print_core_btn)

        core_lay.addLayout(core_btn_row)
        lay.addWidget(self._core_card)

        self._customer_card = self._make_link_card("Customer Summary", "No customer selected", "Open Customer")
        self._customer_card[2].clicked.connect(self._open_customer)
        lay.addWidget(self._customer_card[0])

        notes_hdr = create_sidebar_section_title("Activity / Notes")
        lay.addWidget(notes_hdr)
        self._activity_notes = create_activity_text_area(read_only=True)
        lay.addWidget(self._activity_notes, 1)
        return panel

    @staticmethod
    def _make_link_card(title, value, btn_text):
        return create_link_card(title, value, btn_text)

    def _build_bottom_bar(self) -> QFrame:
        bar, lay = build_bottom_action_bar()

        self._save_btn = QPushButton("💾 Save as Draft")
        self._save_btn.clicked.connect(self._on_save)
        self._save_btn.setStyleSheet(style_action_button("neutral"))
        self._save_btn.setToolTip("Save the invoice in Draft state without deducting inventory.")
        lay.addWidget(self._save_btn)

        self._finalize_btn = QPushButton("📤 Finalize Invoice")
        self._finalize_btn.setDefault(True)
        self._finalize_btn.clicked.connect(self._on_finalize)
        self._finalize_btn.setStyleSheet(style_action_button("success"))
        self._finalize_btn.setToolTip(
            "Finalize this invoice.\n"
            "Inventory, serials, cores, and warranties will be committed."
        )
        lay.addWidget(self._finalize_btn)

        self._revert_btn = QPushButton("↩ Revert to Draft")
        self._revert_btn.clicked.connect(self._on_revert_to_draft)
        self._revert_btn.setStyleSheet(style_action_button("warning"))
        self._revert_btn.setToolTip(
            "Reverse this invoice back to draft so lines can be edited.\n"
            "Restores inventory, voids unreceived core returns, and frees serials."
        )
        lay.addWidget(self._revert_btn)

        self._return_btn = QPushButton("↪ Return / RMA")
        self._return_btn.clicked.connect(self._on_return)
        self._return_btn.setStyleSheet(style_action_button("purple"))
        self._return_btn.setToolTip(
            "Process a customer return: restock inventory, free serials,\n"
            "and credit the invoice for the returned amount."
        )
        lay.addWidget(self._return_btn)

        self._print_btn = QPushButton("🖨 Print")
        self._print_btn.clicked.connect(self._on_print)
        lay.addWidget(self._print_btn)

        self._email_btn = QPushButton("📧 Send Email")
        self._email_btn.clicked.connect(self._on_send_email)
        lay.addWidget(self._email_btn)

        # Bottom-bar mirrors of the sidebar Core Return actions — customer is
        # often standing at the counter; surfacing these here means staff don't
        # have to hunt the right rail.
        self._bottom_process_core_btn = QPushButton("↩ Process Core Return")
        self._bottom_process_core_btn.setStyleSheet(style_action_button("teal"))
        self._bottom_process_core_btn.setToolTip(
            "Receive a returned core from the customer and issue credit."
        )
        self._bottom_process_core_btn.clicked.connect(self._on_process_core_return)
        self._bottom_process_core_btn.setVisible(False)
        lay.addWidget(self._bottom_process_core_btn)

        self._bottom_print_core_btn = QPushButton("🖨 Print Core Slip")
        self._bottom_print_core_btn.setStyleSheet(style_action_button("subtle"))
        self._bottom_print_core_btn.setToolTip(
            "Print the customer-signable Core Return Receipt for this invoice."
        )
        self._bottom_print_core_btn.clicked.connect(self._on_print_core_receipt)
        self._bottom_print_core_btn.setVisible(False)
        lay.addWidget(self._bottom_print_core_btn)

        self._bottom_print_warranty_btn = QPushButton("🛡 Print Warranty")
        self._bottom_print_warranty_btn.setStyleSheet(style_action_button("subtle"))
        self._bottom_print_warranty_btn.setToolTip(
            "Print warranty certificate(s) for any warrantied lines on this invoice."
        )
        self._bottom_print_warranty_btn.clicked.connect(self._on_print_warranty)
        self._bottom_print_warranty_btn.setVisible(False)
        lay.addWidget(self._bottom_print_warranty_btn)

        lay.addStretch()

        self._save_close_btn = QPushButton("💾 Save && Close")
        self._save_close_btn.clicked.connect(self._on_save_and_close)
        self._save_close_btn.setStyleSheet(style_action_button("success"))
        self._save_close_btn.setToolTip(
            "Save the invoice fields that are currently editable, then close this window."
        )
        lay.addWidget(self._save_close_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        lay.addWidget(self._cancel_btn)
        return bar

    def _apply_progress_strip(self) -> None:
        source_so = self._get_linked_sales_order()
        active = len(_INV_PROGRESS) - 1
        completed_until = active - 1 if source_so else active - 1
        for i, lbl in enumerate(self._progress_labels):
            if i < completed_until:
                lbl.setStyleSheet(
                    "font-size: 11px; padding: 4px 12px; border-radius: 3px; "
                    "color: #059669; background: #D1FAE5; font-weight: 600;"
                )
            elif i == active:
                lbl.setStyleSheet(
                    "font-size: 11px; padding: 4px 12px; border-radius: 3px; "
                    "color: white; background: #2563EB; font-weight: 700;"
                )
            else:
                lbl.setStyleSheet(
                    "font-size: 11px; padding: 4px 12px; border-radius: 3px; "
                    "color: #9CA3AF; background: transparent;"
                )

    # ==================================================================
    #  STATE TRACKING FOR AUTO-REFRESH
    # ==================================================================
    def _get_state_snapshot(self) -> dict:
        """Snapshot of invoice state for change detection."""
        if not self._invoice_id:
            return {}
        inv = get_invoice(self._invoice_id)
        if not inv:
            return {}
        return {
            "inv_status": inv.get("status"),
            "inv_updated": inv.get("updated_at"),
            "finalized_at": inv.get("finalized_at"),
            "total": inv.get("total"),
            "amount_paid": inv.get("amount_paid"),
            "balance_due": inv.get("balance_due"),
        }

    def _refresh(self) -> None:
        """Auto-refresh callback when invoice state changes elsewhere."""
        self._reload_invoice()

    def _refresh_chrome(self) -> None:
        inv = self._invoice or {}
        status = str(inv.get("status") or "draft").lower()
        invoice_number = inv.get("invoice_number") or "New Invoice"
        self._title_label.setText(f"Invoice {invoice_number}" if self._invoice_id else "New Invoice")

        finalized_at = inv.get("finalized_at")
        finalized = bool(finalized_at)
        needs_finalize = not finalized and status != "void"

        if needs_finalize:
            bg, fg = "#FEF3C7", "#92400E"
            self._status_badge.setText(f"{status.upper()} - NEEDS FINALIZE")
            self._status_badge.setToolTip(
                "Inventory, serials, cores, and warranties are not committed yet."
            )
        else:
            bg, fg = _INV_BADGE_COLORS.get(status, ("#E5E7EB", "#374151"))
            self._status_badge.setText(status.upper())
            if finalized_at:
                self._status_badge.setToolTip(
                    f"Finalized at {self._format_timestamp(finalized_at) or finalized_at}"
                )
            else:
                self._status_badge.setToolTip("")
        self._status_badge.setStyleSheet(
            f"font-weight: 600; font-size: 12px; padding: 4px 12px; border-radius: 4px; background: {bg}; color: {fg};"
        )

        customer_name = inv.get("customer_company") or inv.get("customer_name") or self._customer_combo.currentText() or "—"
        self._header_customer.setText(f"Customer: {customer_name}")

        source_so = self._get_linked_sales_order()
        self._header_source.setText(
            f"Source SO: {source_so.get('so_number', '—')}" if source_so else "Source SO: —"
        )

        created_text = self._format_timestamp(inv.get("created_at")) or self._format_date(inv.get("invoice_date"))
        due_text = self._format_date(inv.get("due_date"))
        self._header_dates.setText(f"Invoice: {created_text or '—'}  ·  Due: {due_text or '—'}")

        total = float(inv.get("total") or 0)
        self._header_total.setText(f"${total:,.2f}")

        lines = inv.get("lines", [])
        qty = sum(int(line.get("quantity") or 0) for line in lines)
        subtotal = float(inv.get("subtotal") or 0)
        tax = float(inv.get("tax_amount") or 0)
        shipping = float(inv.get("shipping") or 0)
        balance_due = float(inv.get("balance_due") or max(total - float(inv.get("amount_paid") or 0), 0))
        self._card_lines[2].setText(str(len(lines)))
        self._card_qty[2].setText(str(qty))
        self._card_subtotal[2].setText(f"${subtotal:,.2f}")
        self._card_tax[2].setText(f"${tax:,.2f}")
        self._card_shipping[2].setText(f"${shipping:,.2f}")
        self._card_balance[2].setText(f"${balance_due:,.2f}")

        self._source_card[1].setText(
            f"{source_so.get('so_number')} · {str(source_so.get('status') or '').upper()}" if source_so else "Not linked"
        )
        self._source_card[2].setEnabled(bool(source_so))

        shipment = self._get_linked_shipment(source_so.get("id") if source_so else None)
        tracking = shipment.get("tracking_number") if shipment else (inv.get("tracking_number") or "")
        ship_date = shipment.get("ship_date") if shipment else inv.get("ship_date")
        ship_summary = []
        if shipment:
            ship_summary.append(f"{shipment.get('carrier') or 'Carrier TBD'} · {str(shipment.get('status') or '').upper()}")
            if shipment.get("service_level"):
                ship_summary.append(str(shipment.get("service_level")))
            if shipment.get("freight_charge"):
                ship_summary.append(f"Freight Charge ${float(shipment.get('freight_charge') or 0):,.2f}")
        else:
            ship_summary.append("Using invoice fallback fields")
        if tracking:
            ship_summary.append(f"Tracking: {tracking}")
        if ship_date:
            ship_summary.append(f"Ship Date: {self._format_date(ship_date)}")
        self._shipment_summary.setText("\n".join(ship_summary))

        final_text = "Finalized" if finalized else "Needs Finalize"
        self._payment_status.setText(
            f"{status.upper()} · {final_text} · "
            f"Paid ${float(inv.get('amount_paid') or 0):,.2f} · Due ${balance_due:,.2f}"
        )

        # Core return card status
        try:
            from db import get_core_returns_for_invoice
            cores_on_inv = (
                get_core_returns_for_invoice(int(self._invoice_id)) or []
                if self._invoice_id else []
            )
        except Exception:
            cores_on_inv = []

        # Detect "potential cores" on a draft invoice — i.e. lines whose
        # product carries a core charge but whose actual core_returns rows
        # don't exist yet (created on finalize). This lets us show the
        # core card *before* finalize so the user sees core paperwork is
        # coming and can preview the slip.
        potential_core_units = 0
        if not cores_on_inv:
            try:
                from db import get_product_by_id
                for ln in (inv.get("lines") or []):
                    pid = ln.get("product_id")
                    if not pid:
                        continue
                    sku = (ln.get("sku") or "").upper()
                    if sku.startswith("CORE-") or sku.startswith("RTN-"):
                        continue
                    prod = get_product_by_id(pid) or {}
                    cc = float(prod.get("core_charge") or 0)
                    csp = float(prod.get("core_sell_price") or 0)
                    if cc > 0 or csp > 0:
                        potential_core_units += int(ln.get("quantity") or 0)
            except Exception:
                potential_core_units = 0

        if cores_on_inv:
            counts = {"pending": 0, "received": 0, "credited": 0, "voided": 0}
            for c in cores_on_inv:
                key = str(c.get("status") or "pending").lower()
                counts[key] = counts.get(key, 0) + 1
            parts = []
            if counts.get("pending"):
                parts.append(f"{counts['pending']} pending")
            if counts.get("received"):
                parts.append(f"{counts['received']} received")
            if counts.get("credited"):
                parts.append(f"{counts['credited']} credited")
            if counts.get("voided"):
                parts.append(f"{counts['voided']} voided")
            self._core_status_label.setText(
                f"{len(cores_on_inv)} core{'s' if len(cores_on_inv) != 1 else ''} \u2014 "
                + ", ".join(parts)
            )
            actionable = counts.get("pending", 0) + counts.get("received", 0) > 0
            self._process_core_btn.setEnabled(actionable)
            self._print_core_btn.setEnabled(True)
            self._core_card.setVisible(True)
        elif potential_core_units > 0:
            # Draft invoice with core-bearing lines \u2014 show the card with a
            # heads-up; actions are disabled until the cores actually exist.
            self._core_status_label.setText(
                f"{potential_core_units} core{'s' if potential_core_units != 1 else ''} "
                "will be created on finalize"
            )
            self._process_core_btn.setEnabled(False)
            self._process_core_btn.setToolTip(
                "Available after the invoice is finalized."
            )
            self._print_core_btn.setEnabled(False)
            self._print_core_btn.setToolTip(
                "Available after the invoice is finalized."
            )
            self._core_card.setVisible(True)
        else:
            self._core_status_label.setText("No cores linked")
            self._process_core_btn.setEnabled(False)
            self._print_core_btn.setEnabled(False)
            # Hide entirely so the sidebar isn't cluttered for non-core invoices.
            self._core_card.setVisible(False)

        # Bottom-bar button visibility by finalization state.
        is_paid = status in ("paid", "partial")
        # ``finalized_at`` is the dedicated source of truth for "inventory
        # has been committed" — payment-driven status changes no longer
        # mask that. An invoice can be ``partial`` (early payment) yet
        # still need finalization.
        # Save as Draft: draft only.
        if hasattr(self, "_save_btn"):
            self._save_btn.setVisible(not finalized and status != "void")
        # Finalize: show whenever the invoice has not been finalized yet,
        # even if a partial payment landed first.
        if hasattr(self, "_finalize_btn"):
            self._finalize_btn.setVisible(needs_finalize)
            self._finalize_btn.setEnabled(bool(self._invoice_id) and needs_finalize)
            self._finalize_btn.setText("📤 Finalize Invoice")
            self._finalize_btn.setToolTip(
                "Finalize this invoice: deduct inventory, commit serials, "
                "create core returns, and issue warranty certificates."
            )
        # Revert to Draft only after finalize, and only when not paid.
        if hasattr(self, "_revert_btn"):
            self._revert_btn.setVisible(finalized)
            self._revert_btn.setEnabled(finalized and not is_paid)
            if is_paid:
                self._revert_btn.setToolTip(
                    "Cannot revert a paid invoice. Refund or void payments first."
                )
        # Return / RMA only useful after invoice is finalized.
        if hasattr(self, "_return_btn"):
            self._return_btn.setVisible(finalized)

        # Mirror sidebar core actions on the bottom bar so counter staff
        # can act without scrolling the right rail.
        if hasattr(self, "_bottom_process_core_btn"):
            has_cores = bool(cores_on_inv)
            has_any_core_intent = has_cores or potential_core_units > 0
            self._bottom_process_core_btn.setVisible(has_any_core_intent)
            self._bottom_print_core_btn.setVisible(has_any_core_intent)
            if has_cores:
                # Mirror the sidebar enablement rule.
                self._bottom_process_core_btn.setEnabled(
                    self._process_core_btn.isEnabled()
                )
                self._bottom_print_core_btn.setEnabled(True)
                self._bottom_print_core_btn.setToolTip(
                    "Print the customer-signable Core Return Receipt for "
                    "this invoice."
                )
            elif potential_core_units > 0:
                # Print preview is OK pre-finalize; processing is not.
                self._bottom_process_core_btn.setEnabled(False)
                self._bottom_process_core_btn.setToolTip(
                    "Available after the invoice is finalized."
                )
                self._bottom_print_core_btn.setEnabled(True)
                self._bottom_print_core_btn.setToolTip(
                    "Print a preview core-return slip from the current "
                    "invoice lines. Final receipt prints after finalize."
                )

        # Warranty: surface the Print Warranty button only when the invoice
        # has any line carrying warranty coverage. Available regardless of
        # finalize state — pre-finalize it produces a "preview" cert based
        # on the live line data; post-finalize it pulls from the
        # certificates materialized on the customer_warranties ledger.
        if hasattr(self, "_bottom_print_warranty_btn"):
            try:
                has_warranty = any(
                    int(ln.get("warranty_months") or 0) > 0
                    for ln in inv.get("lines", [])
                )
            except Exception:
                has_warranty = False
            self._bottom_print_warranty_btn.setVisible(has_warranty)
            if has_warranty:
                # Always enabled — pre-finalize prints the brochure as a
                # fallback so the customer leaves with the policy in hand.
                self._bottom_print_warranty_btn.setEnabled(True)
                if finalized:
                    self._bottom_print_warranty_btn.setToolTip(
                        "Print warranty certificate(s) for this invoice."
                    )
                else:
                    self._bottom_print_warranty_btn.setToolTip(
                        "Prints JAK's warranty brochure now; per-line "
                        "certificates print after the invoice is finalized."
                    )

        customer_lines = [customer_name]
        if inv.get("customer_phone"):
            customer_lines.append(str(inv.get("customer_phone")))
        if inv.get("customer_email"):
            customer_lines.append(str(inv.get("customer_email")))
        if self._customer_info.text().strip():
            customer_lines.append(self._customer_info.text().strip())
        address_bits = [inv.get("customer_address"), inv.get("customer_city"), inv.get("customer_state"), inv.get("customer_zip")]
        address_bits = [str(bit) for bit in address_bits if bit]
        if address_bits:
            customer_lines.append(", ".join(address_bits))
        self._customer_card[1].setText("\n".join(customer_lines) if customer_lines else "No customer selected")
        self._customer_card[2].setEnabled(bool(inv.get("customer_id") or self._customer_combo.currentData()))

        notes_text = self._notes_edit.text().strip() or str(inv.get("notes") or "").strip()
        activity_lines = []
        if inv.get("created_at"):
            activity_lines.append(f"Created: {self._format_timestamp(inv.get('created_at'))}")
        if inv.get("updated_at"):
            activity_lines.append(f"Updated: {self._format_timestamp(inv.get('updated_at'))}")
        if source_so:
            activity_lines.append(f"Linked Sales Order: {source_so.get('so_number')}")
        if notes_text:
            activity_lines.append("")
            activity_lines.append("Notes:")
            activity_lines.append(notes_text)
        self._activity_notes.setPlainText("\n".join(activity_lines) if activity_lines else "No activity yet.")

        has_invoice = self._invoice_id is not None
        can_edit = has_invoice and not finalized and status != "void"
        can_finalize = has_invoice and not finalized and status != "void"
        self._set_editing_enabled(can_edit)
        self._save_btn.setEnabled(can_edit)
        self._save_btn.setVisible(can_edit)
        self._finalize_btn.setVisible(can_finalize)
        self._finalize_btn.setEnabled(can_finalize)
        self._print_btn.setEnabled(has_invoice)
        self._email_btn.setEnabled(has_invoice and bool(inv.get("customer_email")))
        # Revert button only makes sense for finalized-but-unpaid invoices.
        amount_paid = float(inv.get("amount_paid") or 0)
        revertable = has_invoice and finalized and status != "void" and amount_paid <= 0
        self._revert_btn.setVisible(has_invoice and finalized)
        self._revert_btn.setEnabled(revertable)
        if has_invoice and finalized and not revertable:
            self._revert_btn.setToolTip(
                "Cannot revert: invoice has payments recorded or is voided."
            )
        # Return is only available on finalized invoices.
        returnable = has_invoice and finalized and status in ("sent", "paid", "partial")
        self._return_btn.setVisible(returnable)
        self._return_btn.setEnabled(returnable)
        self._apply_progress_strip()

    @staticmethod
    def _format_date(value) -> str:
        if not value:
            return ""
        return str(value)[:10]

    @staticmethod
    def _format_timestamp(value) -> str:
        if not value:
            return ""
        return str(value).replace("T", " ")[:16]

    def _get_linked_sales_order(self) -> dict | None:
        if not self._invoice_id:
            return None
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, so_number, status, ship_method, tracking_number, promised_date, created_at "
                "FROM sales_orders WHERE invoice_id = %s ORDER BY id DESC LIMIT 1",
                (self._invoice_id,),
            ).fetchone()
        return dict(row) if row and hasattr(row, "keys") else ({
            "id": row[0], "so_number": row[1], "status": row[2], "ship_method": row[3],
            "tracking_number": row[4], "promised_date": row[5], "created_at": row[6],
        } if row else None)

    def _get_linked_shipment(self, so_id: int | None) -> dict | None:
        if not so_id:
            return None
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, carrier, service_level, tracking_number, ship_date, estimated_arrival, "
                "actual_arrival, freight_cost, freight_charge, status "
                "FROM shipments WHERE so_id = %s ORDER BY COALESCE(ship_date, created_at) DESC, id DESC LIMIT 1",
                (so_id,),
            ).fetchone()
        return dict(row) if row and hasattr(row, "keys") else ({
            "id": row[0], "carrier": row[1], "service_level": row[2], "tracking_number": row[3],
            "ship_date": row[4], "estimated_arrival": row[5], "actual_arrival": row[6],
            "freight_cost": row[7], "freight_charge": row[8], "status": row[9],
        } if row else None)

    def _reload_invoice(self) -> None:
        if self._invoice_id:
            inv = get_invoice(self._invoice_id)
            self._invoice = dict(inv) if inv else None
        self._refresh_chrome()
        # Lines/totals can move when payments / cores / returns change —
        # re-render so the bottom totals row stays in sync with the badge.
        try:
            self._refresh_lines()
        except Exception:
            pass
        try:
            self._refresh_qbo_sync_card()
        except Exception:
            pass

    # ─── QBO Sync card ───────────────────────────────────────────────
    def _refresh_qbo_sync_card(self) -> None:
        if not hasattr(self, "_qbo_sync_status"):
            return
        if not self._invoice_id:
            self._qbo_sync_status.setText("Save the invoice before pushing to QBO.")
            self._view_qbo_payload_btn.setEnabled(False)
            return
        try:
            from qbo import sync_log
            row = sync_log.latest_for("invoice", int(self._invoice_id))
        except Exception:
            row = None
        if not row:
            self._qbo_sync_status.setText("● Not yet pushed to QBO")
            self._qbo_sync_status.setStyleSheet(
                "font-size: 11px; color: #6B7280; font-weight: 500;"
            )
            self._view_qbo_payload_btn.setEnabled(False)
            return
        d = dict(row) if hasattr(row, "keys") else row
        status = d.get("status") or ""
        env = d.get("environment") or ""
        qid = d.get("qbo_object_id") or d.get("qbo_id") or "(no id)"
        when = str(d.get("created_at") or "")[:19]
        if status == "success":
            dot, color = "●", "#059669"
        elif status == "failed":
            dot, color = "●", "#DC2626"
        else:
            dot, color = "●", "#6B7280"
        self._qbo_sync_status.setText(
            f"{dot} {status or 'pending'} · env={env} · QBO Id={qid}\n"
            f"last sync: {when}"
        )
        self._qbo_sync_status.setStyleSheet(
            f"font-size: 11px; color: {color}; font-weight: 500;"
        )
        self._view_qbo_payload_btn.setEnabled(True)

    def _on_view_qbo_payload(self) -> None:
        if not self._invoice_id:
            QMessageBox.information(self, "QBO Payload",
                                    "Save the invoice first.")
            return
        try:
            from qbo import sync_log
            row = sync_log.latest_for("invoice", int(self._invoice_id))
        except Exception as exc:
            QMessageBox.warning(self, "QBO Payload",
                                f"Failed to load audit log: {exc}")
            return
        if not row:
            QMessageBox.information(self, "QBO Payload",
                                    "No QBO sync attempts logged for this invoice yet.")
            return
        d = dict(row) if hasattr(row, "keys") else row
        try:
            from jaks_inventory.ui.qbo_audit_entry_dialog import QBOAuditEntryDialog
            dlg = QBOAuditEntryDialog(int(d.get("id")), parent=self)
            dlg.exec()
        except Exception as exc:
            QMessageBox.warning(self, "QBO Payload",
                                f"Failed to open audit dialog: {exc}")
        self._refresh_qbo_sync_card()

    def _open_source_sales_order(self) -> None:
        source_so = self._get_linked_sales_order()
        if not source_so:
            return
        from jaks_inventory.ui.so_detail_dialog import open_so_detail
        open_so_detail(int(source_so.get("id")), parent=self)

    def _open_customer(self) -> None:
        customer_id = self._invoice.get("customer_id") if self._invoice else self._customer_combo.currentData()
        if not customer_id:
            return
        try:
            from jaks_inventory.ui.customer_detail_dialog import CustomerDetailDialog
            dlg = CustomerDetailDialog(int(customer_id), parent=self)
            dlg.exec()
        except Exception as e:
            QMessageBox.warning(self, "Customer", f"Could not open customer:\n{e}")

    def _load_customers(self) -> None:
        """Load customers into combo box."""
        self._customer_combo.clear()
        self._customer_combo.addItem("-- Select Customer --", None)
        
        customers = get_all_customers()
        for c in customers:
            display = c.get("name", "")
            if c.get("company"):
                display = f"{display} ({c.get('company')})"
            self._customer_combo.addItem(display, c.get("id"))

    def _setup_sku_completer(self) -> None:
        """Setup autocomplete for SKU field."""
        products = browse_products(limit=500)
        skus = [p.get("sku", "") for p in products if p.get("sku")]
        completer = QCompleter(skus)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._sku_edit.setCompleter(completer)
        self._sku_edit.editingFinished.connect(self._on_sku_entered)

    def _price_for(self, prod: dict) -> float:
        """Resolve unit price via compute_price (tiered pricing v2).

        Falls back to ``selling_price`` if computation fails or returns
        zero. F2 simple-first: any customer tier discount / category band
        flows through transparently.
        """
        try:
            pid = prod.get("id") or prod.get("product_id")
            if not pid:
                return float(prod.get("selling_price") or 0)
            cid = self._customer_combo.currentData() if hasattr(self, "_customer_combo") else None
            from db import compute_price
            r = compute_price(int(pid), customer_id=int(cid) if cid else None)
            net = r.get("net_price")
            if net is not None and float(net) > 0:
                return float(net)
        except Exception:
            pass
        return float(prod.get("selling_price") or 0)

    def _on_sku_entered(self) -> None:
        """Auto-fill description and price when SKU is entered."""
        sku = self._sku_edit.text().strip()
        if not sku:
            return
        
        product = get_product_by_sku(sku)
        if product:
            self._desc_edit.setText(product.get("title", "")[:100])
            self._price_spin.setValue(self._price_for(product))

    def _on_find_product(self) -> None:
        """Open the product picker modal and apply the chosen product to the line inputs."""
        from .product_picker_dialog import ProductPickerDialog
        picked = ProductPickerDialog.pick(parent=self, initial_search=self._sku_edit.text().strip())
        if not picked:
            return
        sku = picked.get("sku") or ""
        self._sku_edit.setText(sku)
        # Mirror _on_sku_entered behaviour
        self._desc_edit.setText((picked.get("title") or "")[:100])
        try:
            self._price_spin.setValue(self._price_for(picked))
        except (TypeError, ValueError):
            pass
        self._qty_spin.setFocus()

    def _apply_payment_terms_to_due_date(self, terms: str | None) -> None:
        """Set the due date based on a 'Net N' / 'COD' / 'Due on receipt' string."""
        if not terms:
            return
        t = str(terms).strip().lower()
        days = 30  # sensible default
        if t in ("cod", "due on receipt", "due-on-receipt", "doc"):
            days = 0
        elif t.startswith("net"):
            try:
                days = int("".join(ch for ch in t if ch.isdigit()) or "30")
            except ValueError:
                days = 30
        self._due_date.setDate(date.today() + timedelta(days=days))

    def _on_customer_changed(self, index: int) -> None:
        """Update customer info display and apply/zero tax based on exempt status."""
        customer_id = self._customer_combo.currentData()
        if not customer_id:
            self._customer_info.setText("")
            return
        
        customer = get_customer(customer_id)
        if customer:
            parts = []
            if customer.get("email"):
                parts.append(customer.get("email"))
            if customer.get("phone"):
                parts.append(customer.get("phone"))
            if customer.get("payment_terms"):
                parts.append(customer.get("payment_terms"))
            self._customer_info.setText(" | ".join(parts))

            # Apply customer-specific payment terms to the due date (only on a new
            # invoice — don't overwrite an explicit due date on an existing one).
            if self._is_new:
                self._apply_payment_terms_to_due_date(customer.get("payment_terms"))

            # Auto-apply or zero tax based on tax_exempt + ship-to address
            if self._tax_spin.isEnabled():
                if customer.get("tax_exempt"):
                    self._tax_spin.setValue(0.0)
                    self._tax_spin.setEnabled(False)
                    self._tax_spin.setToolTip("Tax exempt customer — tax locked to 0%")
                else:
                    self._tax_spin.setEnabled(True)
                    self._tax_spin.setToolTip("")
                    # Resolve ship-to address: prefer invoice's saved ship_to,
                    # fall back to customer billing address.
                    ship_state = ""
                    ship_zip = ""
                    if self._invoice:
                        ship_state = (self._invoice.get("ship_to_state") or "").strip()
                        ship_zip = (self._invoice.get("ship_to_zip") or "").strip()
                    if not ship_state:
                        ship_state = (customer.get("state") or "").strip()
                    if not ship_zip:
                        ship_zip = (customer.get("zip") or customer.get("zip_code") or "").strip()

                    # Honor destination-sourced toggle: when disabled, only
                    # in-state shipments get the home-state rate.
                    destination_sourced = get_setting("tax_destination_sourced", "1") == "1"
                    home_state = (get_setting("tax_home_state", "") or "").strip().upper()

                    rate = 0.0
                    try:
                        from db import resolve_tax_rate
                        if destination_sourced:
                            rate = resolve_tax_rate(ship_state, ship_zip) * 100
                        else:
                            if ship_state.upper() == home_state and home_state:
                                rate = resolve_tax_rate(home_state, "") * 100
                    except Exception:
                        # Fall back to default_tax_rate if lookup unavailable
                        rate = float(get_setting("default_tax_rate", "0") or "0")

                    self._tax_spin.setValue(rate)
                    if rate > 0:
                        self._tax_spin.setToolTip(
                            f"Auto-applied {rate:.3f}% based on ship-to "
                            f"{ship_state} {ship_zip}".strip()
                        )
                    else:
                        self._tax_spin.setToolTip(
                            "No tax rate on file for this ship-to state — "
                            "no nexus or rate not configured in Settings → Invoice → Sales Tax"
                        )

    def _load_data(self) -> None:
        """Load existing invoice data."""
        # Set preset customer if provided
        if self._preset_customer_id:
            idx = self._customer_combo.findData(self._preset_customer_id)
            if idx >= 0:
                self._customer_combo.setCurrentIndex(idx)

        if not self._invoice:
            self._refresh_chrome()
            return

        # Set customer
        customer_id = self._invoice.get("customer_id")
        if customer_id:
            idx = self._customer_combo.findData(customer_id)
            if idx >= 0:
                self._customer_combo.setCurrentIndex(idx)
        
        # Set dates
        if self._invoice.get("invoice_date"):
            self._invoice_date.setDate(date.fromisoformat(str(self._invoice["invoice_date"])[:10]))
        if self._invoice.get("due_date"):
            self._due_date.setDate(date.fromisoformat(str(self._invoice["due_date"])[:10]))
        
        # Set tax rate
        tax_rate = float(self._invoice.get("tax_rate") or 0) * 100
        self._tax_spin.setValue(tax_rate)

        # Set CC convenience-fee toggle
        self._cc_fee_check.blockSignals(True)
        self._cc_fee_check.setChecked(bool(int(self._invoice.get("cc_fee_enabled") or 0)))
        self._cc_fee_check.blockSignals(False)
        
        # Set notes
        self._notes_edit.setText(self._invoice.get("notes", "") or "")

        # Set tracking / shipping
        tracking = self._invoice.get("tracking_number", "") or ""
        self._tracking_edit.setText(tracking)
        if self._invoice.get("ship_date"):
            self._ship_date_edit.setDate(date.fromisoformat(str(self._invoice["ship_date"])[:10]))

        # Load saved shipping cost
        saved_shipping = float(self._invoice.get("shipping", 0) or 0)
        self._shipping_spin.setValue(saved_shipping)

        # Equipment / ESN / VIN
        eq_type = (self._invoice.get("equipment_type") or "").lower()
        eq_idx = self._equipment_type_combo.findData(eq_type)
        if eq_idx >= 0:
            self._equipment_type_combo.setCurrentIndex(eq_idx)
        eq_make = self._invoice.get("equipment_make") or ""
        mk_idx = self._equipment_make_combo.findText(eq_make)
        if mk_idx >= 0:
            self._equipment_make_combo.setCurrentIndex(mk_idx)
        else:
            self._equipment_make_combo.setCurrentText(eq_make)
        self._equipment_model_edit.setText(self._invoice.get("equipment_model") or "")
        self._esn_edit.setText(self._invoice.get("esn") or "")
        self._vin_edit.setText(self._invoice.get("vin") or "")

        # Load lines
        self._refresh_lines()
        
        # Enable/disable editing widgets based on finalization state. Status
        # can change for payment/customer communication, but finalized_at is
        # the inventory commitment gate.
        finalized = bool(self._invoice.get("finalized_at"))
        self._set_editing_enabled(not finalized and self._invoice.get("status") != "void")

        self._refresh_chrome()

    def _set_editing_enabled(self, enabled: bool) -> None:
        """Lock/unlock editing widgets based on invoice finalization."""
        self._customer_combo.setEnabled(enabled)
        self._invoice_date.setEnabled(enabled)
        self._due_date.setEnabled(enabled)
        self._sku_edit.setEnabled(enabled)
        self._desc_edit.setEnabled(enabled)
        self._qty_spin.setEnabled(enabled)
        self._price_spin.setEnabled(enabled)
        self._add_line_btn.setEnabled(enabled)
        self._remove_line_btn.setEnabled(enabled)
        self._tax_spin.setEnabled(enabled)
        self._shipping_spin.setEnabled(enabled)
        self._tracking_edit.setEnabled(enabled)
        self._ship_date_edit.setEnabled(enabled)
        self._notes_edit.setEnabled(enabled)
        self._est_ship_btn.setEnabled(enabled)

    def _refresh_lines(self) -> None:
        """Refresh the lines table."""
        self._lines_table.blockSignals(True)
        self._lines_table.setRowCount(0)
        
        if not self._invoice:
            self._lines_table.blockSignals(False)
            self._update_totals()
            return
        
        lines = self._invoice.get("lines", [])
        # Hide legacy CORE-* paired rows. Cores are now tracked inline on
        # the parent line via ``invoice_lines.core_charge``; older invoices
        # may still carry a separate CORE-* row, but we no longer render it.
        visible_lines = [
            ln for ln in lines
            if not str(ln.get("sku") or "").upper().startswith("CORE-")
        ]
        self._lines_table.setRowCount(len(visible_lines))
        
        for row_idx, line in enumerate(visible_lines):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            is_discount = (line.get("line_kind") or "product") == "discount"
            if is_discount:
                # Render as a single red italic row with parenthesized negative total.
                lt = float(line.get("line_total") or 0)
                desc_item = item(line.get("description") or "Discount")
                f = desc_item.font(); f.setItalic(True); desc_item.setFont(f)
                disc_color = QColor("#b91c1c")
                desc_item.setForeground(disc_color)
                total_item = item(f"(${abs(lt):,.2f})")
                f = total_item.font(); f.setItalic(True); total_item.setFont(f)
                total_item.setForeground(disc_color)
                self._lines_table.setItem(row_idx, 0, item(line.get("id")))
                self._lines_table.setItem(row_idx, 1, desc_item)
                self._lines_table.setItem(row_idx, 2, item(""))   # serial
                self._lines_table.setItem(row_idx, 3, item(""))   # qty
                self._lines_table.setItem(row_idx, 4, item(""))   # unit price
                self._lines_table.setItem(row_idx, 5, item(""))   # core
                self._lines_table.setItem(row_idx, 6, item("\u2014"))  # warranty
                self._lines_table.setItem(row_idx, 7, total_item)
                continue

            # Core charge column \u2014 sourced from the line itself (inline
            # ``core_charge`` per migration 036). Falls back to the product
            # default for legacy lines that haven't been touched. Editable
            # while the invoice is in draft.
            line_core = float(line.get("core_charge") or 0)
            core_charge = line_core
            product_id = line.get("product_id")
            if core_charge <= 0 and product_id:
                from db import get_product_by_id
                product = get_product_by_id(product_id)
                if product:
                    core_charge = float(
                        product.get("core_sell_price")
                        or product.get("core_charge")
                        or 0
                    )
            inv_status = str((self._invoice or {}).get("status") or "draft").lower()
            can_edit_lines = (
                not bool((self._invoice or {}).get("finalized_at"))
                and inv_status != "void"
            )
            core_text = f"${core_charge:.2f}" if core_charge > 0 else (
                "$0.00" if can_edit_lines else "-"
            )
            core_item = QTableWidgetItem(core_text)
            core_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            if can_edit_lines and product_id:
                # Editable before finalize so users can adjust per-line core.
                core_item.setFlags(core_item.flags() | Qt.ItemFlag.ItemIsEditable)
            else:
                core_item.setFlags(core_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if core_charge > 0:
                core_item.setForeground(QColor(0, 128, 0))
                core_item.setData(Qt.ItemDataRole.UserRole, line.get("id"))
                core_item.setToolTip(
                    "Per-unit core deposit on this line.\n"
                    "Editable before the invoice is finalized.\n"
                    "Click Process Core Return after finalizing."
                )
            
            # Get serial number if serial_id set
            serial_display = ""
            serial_id = line.get("serial_id")
            if serial_id:
                from db import get_serial
                serial = get_serial(serial_id)
                if serial:
                    serial_display = serial.get("serial_number", "")
            
            self._lines_table.setItem(row_idx, 0, item(line.get("id")))
            self._lines_table.setItem(row_idx, 1, item(line.get("description")))
            self._lines_table.setItem(row_idx, 2, item(serial_display))
            qty_item = item(line.get("quantity"))
            qty_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._lines_table.setItem(row_idx, 3, qty_item)
            # Unit Price column is editable
            price_val = float(line.get('unit_price') or 0)
            price_item = QTableWidgetItem(f"${price_val:.2f}")
            if can_edit_lines:
                price_item.setFlags(price_item.flags() | Qt.ItemFlag.ItemIsEditable)
            else:
                price_item.setFlags(price_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._lines_table.setItem(row_idx, 4, price_item)
            
            # Core charge column (built above; just install it).
            self._lines_table.setItem(row_idx, 5, core_item)

            # Warranty column
            w_months = int(line.get("warranty_months") or 0)
            w_type = line.get("warranty_type") or "parts_only"
            w_mileage = line.get("warranty_mileage_limit")
            if w_months > 0:
                from engine.warranty import format_warranty_short
                w_item = item(format_warranty_short(w_months, w_type, w_mileage))
                w_item.setForeground(QColor("#1565c0"))
            else:
                w_item = item("—")
            w_item.setToolTip("Double-click to edit warranty")
            self._lines_table.setItem(row_idx, 6, w_item)

            total_item = item(f"${float(line.get('line_total') or 0):.2f}")
            total_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._lines_table.setItem(row_idx, 7, total_item)

            # Visually distinguish auto-generated core lines.
            sku = (line.get("sku") or "")
            if sku.upper().startswith("CORE-"):
                tint = QColor(232, 245, 233)  # light green
                for col in range(self._lines_table.columnCount()):
                    cell = self._lines_table.item(row_idx, col)
                    if cell is not None:
                        cell.setBackground(tint)
            elif sku.upper().startswith("RTN-"):
                tint = QColor(254, 226, 226)  # light red — return/credit
                for col in range(self._lines_table.columnCount()):
                    cell = self._lines_table.item(row_idx, col)
                    if cell is not None:
                        cell.setBackground(tint)
        
        self._lines_table.blockSignals(False)
        self._update_totals()

    def _on_line_price_changed(self, row: int, col: int) -> None:
        """Handle inline price/core edit on the lines table."""
        if col not in (4, 5):  # Unit Price (4) or Core (5)
            return
        id_item = self._lines_table.item(row, 0)
        cell_item = self._lines_table.item(row, col)
        if not id_item or not cell_item:
            return
        line_id = int(id_item.text())
        raw = cell_item.text().replace("$", "").replace(",", "").strip()
        if raw in ("", "-"):
            self._refresh_lines()
            return
        try:
            new_value = float(raw)
        except ValueError:
            self._refresh_lines()
            return
        if new_value < 0:
            self._refresh_lines()
            return
        if col == 4:
            update_invoice_line_price(line_id, new_value)
        else:
            from db import update_invoice_line_core_charge
            update_invoice_line_core_charge(line_id, new_value)
        # Reload invoice data and refresh
        inv = get_invoice(self._invoice_id)
        self._invoice = dict(inv) if inv else None
        self._refresh_lines()

    def _on_line_cell_double_clicked(self, row: int, col: int) -> None:
        if col != 6:  # Warranty column
            return
        id_item = self._lines_table.item(row, 0)
        if not id_item:
            return
        line_id = int(id_item.text())
        # Find the line dict by id
        line = None
        for ln in (self._invoice.get("lines", []) if self._invoice else []):
            if int(ln.get("id", 0)) == line_id:
                line = ln
                break
        if not line:
            return
        self._edit_line_warranty(line)

    # ------------------------------------------------------------------
    # Single click on the green Core charge cell \u2192 start that line's
    # core return workflow. Per UAT decision, single-click + confirm.
    # ------------------------------------------------------------------
    def _on_line_cell_clicked(self, row: int, col: int) -> None:
        if col != 5:  # Core charge column
            return
        cell = self._lines_table.item(row, 5)
        if cell is None:
            return
        line_id = cell.data(Qt.ItemDataRole.UserRole)
        if not line_id:
            return  # row has no core charge
        if not self._invoice_id:
            QMessageBox.information(
                self, "Save First",
                "Please save the invoice before processing cores.",
            )
            return
        finalized = bool((self._invoice or {}).get("finalized_at"))
        if not finalized:
            QMessageBox.information(
                self, "Finalize First",
                "Cores are created when the invoice is finalized.\n"
                "Click Finalize Invoice first, then come back to process the core.",
            )
            return
        ans = QMessageBox.question(
            self, "Process Core Return",
            "Open the core return workflow for this line?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        from jaks_inventory.ui.core_return_flow import process_core_return_for_invoice
        if process_core_return_for_invoice(
            int(self._invoice_id), parent=self,
            invoice_line_id=int(line_id),
        ):
            self._reload_invoice()

    # ------------------------------------------------------------------
    # Right-click on a line row \u2192 quick actions.
    # ------------------------------------------------------------------
    def _on_line_context_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        idx = self._lines_table.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        id_item = self._lines_table.item(row, 0)
        if not id_item:
            return
        try:
            line_id = int(id_item.text())
        except (TypeError, ValueError):
            return
        line = None
        for ln in (self._invoice.get("lines", []) if self._invoice else []):
            if int(ln.get("id", 0)) == line_id:
                line = ln
                break
        if not line:
            return

        core_cell = self._lines_table.item(row, 5)
        line_has_core = bool(
            core_cell and core_cell.data(Qt.ItemDataRole.UserRole)
        )
        finalized = bool((self._invoice or {}).get("finalized_at"))

        menu = QMenu(self)
        act_process = menu.addAction("\u21a9 Process Core Return")
        act_process.setEnabled(
            line_has_core and finalized and bool(self._invoice_id)
        )

        act_print_core = menu.addAction("\ud83d\udda8 Print Core Slip")
        act_print_core.setEnabled(
            line_has_core and finalized and bool(self._invoice_id)
        )

        menu.addSeparator()
        act_view = menu.addAction("\ud83d\udd0d View Product")
        act_view.setEnabled(bool(line.get("product_id")))
        act_copy = menu.addAction("\ud83d\udccb Copy SKU")
        act_copy.setEnabled(bool(line.get("sku")))

        chosen = menu.exec(self._lines_table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_process:
            from jaks_inventory.ui.core_return_flow import process_core_return_for_invoice
            if process_core_return_for_invoice(
                int(self._invoice_id), parent=self,
                invoice_line_id=int(line_id),
            ):
                self._reload_invoice()
        elif chosen is act_print_core:
            self._on_print_core_receipt()
        elif chosen is act_view:
            try:
                from jaks_inventory.ui.product_detail_dialog import ProductDetailDialog
                dlg = ProductDetailDialog(int(line.get("product_id")), parent=self)
                dlg.exec()
            except Exception as exc:
                QMessageBox.warning(
                    self, "View Product", f"Could not open product:\n{exc}",
                )
        elif chosen is act_copy:
            try:
                from PySide6.QtWidgets import QApplication
                QApplication.clipboard().setText(str(line.get("sku") or ""))
            except Exception:
                pass

    def _edit_line_warranty(self, line: dict) -> None:
        from PySide6.QtWidgets import (
            QDialog, QFormLayout, QSpinBox, QComboBox, QCheckBox,
            QDialogButtonBox, QHBoxLayout, QWidget,
        )
        from db.inventory import update_invoice_line

        line_id = int(line.get("id"))
        current_months = int(line.get("warranty_months") or 0)
        current_type = (line.get("warranty_type") or "parts_only").lower()
        current_mileage = line.get("warranty_mileage_limit")

        dlg = QDialog(self)
        dlg.setWindowTitle("Warranty for Line")
        form = QFormLayout(dlg)

        months_spin = QSpinBox()
        months_spin.setRange(0, 120)
        months_spin.setSuffix(" months")
        months_spin.setValue(current_months)
        form.addRow("Duration:", months_spin)

        type_combo = QComboBox()
        type_combo.addItem("Parts Only", "parts_only")
        type_combo.addItem("Parts & Labor", "parts_labor")
        type_combo.setCurrentIndex(1 if current_type == "parts_labor" else 0)
        form.addRow("Coverage:", type_combo)

        mi_row = QWidget()
        mi_lay = QHBoxLayout(mi_row)
        mi_lay.setContentsMargins(0, 0, 0, 0)
        mi_spin = QSpinBox()
        mi_spin.setRange(0, 1_000_000)
        mi_spin.setSingleStep(1000)
        mi_spin.setSuffix(" mi")
        mi_spin.setValue(int(current_mileage or 0))
        unlimited_cb = QCheckBox("Unlimited")
        unlimited_cb.setChecked(current_mileage in (None, 0))

        def _sync(_state):
            mi_spin.setEnabled(not unlimited_cb.isChecked())
        unlimited_cb.stateChanged.connect(_sync)
        _sync(unlimited_cb.checkState())
        mi_lay.addWidget(mi_spin)
        mi_lay.addWidget(unlimited_cb)
        form.addRow("Mileage limit:", mi_row)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        months = int(months_spin.value())
        w_type = type_combo.currentData() or "parts_only"
        mileage = None if unlimited_cb.isChecked() else int(mi_spin.value())
        if mileage == 0:
            mileage = None
        update_invoice_line(
            line_id,
            warranty_months=months,
            warranty_type=w_type,
            warranty_mileage_limit=mileage,
        )
        inv = get_invoice(self._invoice_id)
        self._invoice = dict(inv) if inv else None
        self._refresh_lines()

    def _persist_tax_rate(self) -> None:
        """Persist the current tax-spinner value (percent → decimal)."""
        if not self._invoice_id:
            return
        try:
            update_invoice(self._invoice_id, tax_rate=self._tax_spin.value() / 100.0)
            inv = get_invoice(self._invoice_id)
            if inv:
                self._invoice = dict(inv)
                self._refresh_chrome()
        except Exception:
            pass

    def _persist_shipping(self) -> None:
        """Persist the current shipping-spinner value."""
        if not self._invoice_id:
            return
        try:
            update_invoice(self._invoice_id, shipping=self._shipping_spin.value())
            inv = get_invoice(self._invoice_id)
            if inv:
                self._invoice = dict(inv)
                self._refresh_chrome()
        except Exception:
            pass

    def _on_cc_fee_toggled(self, checked: bool) -> None:
        """Persist the 3% CC convenience-fee toggle and refresh totals."""
        if not self._invoice_id:
            return
        try:
            update_invoice(self._invoice_id, cc_fee_enabled=1 if checked else 0)
            inv = get_invoice(self._invoice_id)
            if inv:
                self._invoice = dict(inv)
                self._update_totals()
                self._refresh_chrome()
        except Exception:
            pass

    def _update_totals(self) -> None:
        """Recalculate and display totals."""
        def _money_from_item(row: int, col: int) -> float:
            cell = self._lines_table.item(row, col)
            if not cell:
                return 0.0
            raw = cell.text().replace("$", "").replace(",", "").strip()
            if raw in ("", "-"):
                return 0.0
            # Accounting-style negative: "($300.00)"
            negative = False
            if raw.startswith("(") and raw.endswith(")"):
                raw = raw[1:-1]
                negative = True
            try:
                v = float(raw)
                return -v if negative else v
            except ValueError:
                return 0.0

        qty = 0
        subtotal = 0.0
        for row in range(self._lines_table.rowCount()):
            qty_item = self._lines_table.item(row, 3)
            row_qty = 0
            if qty_item:
                try:
                    row_qty = int(qty_item.text())
                    qty += row_qty
                except ValueError:
                    pass
            subtotal += _money_from_item(row, 7)  # Column 7 is parts line total
            subtotal += _money_from_item(row, 5) * row_qty  # Column 5 is per-unit core
        
        tax_rate = self._tax_spin.value() / 100
        tax_amount = subtotal * tax_rate
        shipping = self._shipping_spin.value()
        base = subtotal + tax_amount + shipping
        cc_fee = round(base * 0.03, 2) if (
            self._invoice and int(self._invoice.get("cc_fee_enabled") or 0)
        ) else 0.0
        total = base + cc_fee
        
        self._subtotal_label.setText(f"${subtotal:,.2f}")
        self._tax_label.setText(f"${tax_amount:,.2f}")
        self._total_label.setText(f"${total:,.2f}")
        self._header_total.setText(f"${total:,.2f}")
        self._card_lines[2].setText(str(self._lines_table.rowCount()))
        self._card_qty[2].setText(str(qty))
        self._card_subtotal[2].setText(f"${subtotal:,.2f}")
        self._card_tax[2].setText(f"${tax_amount:,.2f}")
        self._card_shipping[2].setText(f"${shipping:,.2f}")
        balance_due = float(self._invoice.get("balance_due") or total) if self._invoice else total
        self._card_balance[2].setText(f"${balance_due:,.2f}")
        if self._invoice:
            self._refresh_chrome()

    def _update_source_link(self) -> None:
        """Legacy hook retained for compatibility with older call sites."""
        self._refresh_chrome()

    def _on_source_link_clicked(self, link: str) -> None:
        """Handle clicks on the source document back-link."""
        if link.startswith("quote:"):
            q_num = link.split(":", 1)[1]
            try:
                from db import get_all_quotes
                quotes = get_all_quotes()
                for q in quotes:
                    if q.get("quote_number") == q_num:
                        from jaks_inventory.ui.quote_dialog import QuoteDialog
                        dlg = QuoteDialog(quote_id=q["id"], parent=self)
                        dlg.exec()
                        return
            except Exception:
                pass
        elif link.startswith("so:"):
            so_num = link.split(":", 1)[1]
            try:
                from db import get_db
                with get_db() as conn:
                    row = conn.execute(
                        "SELECT id FROM sales_orders WHERE so_number = ?", (so_num,)
                    ).fetchone()
                if row:
                    from jaks_inventory.ui.so_detail_dialog import open_so_detail
                    so_id = row[0] if not hasattr(row, 'keys') else row["id"]
                    open_so_detail(int(so_id), parent=self)
            except Exception:
                pass

    def _generate_invoice_pdf(self):
        if not self._invoice_id:
            return None
        inv = get_invoice(self._invoice_id)
        if not inv:
            return None
        from jaks_inventory.utils.pdf_invoice import generate_invoice_pdf
        return generate_invoice_pdf(inv)

    def _on_print(self) -> None:
        if not self._invoice_id:
            if not self._create_invoice():
                return
        inv = get_invoice(self._invoice_id)
        if not inv:
            return
        from jaks_inventory.utils.pdf_invoice import generate_invoice_pdf
        from jaks_inventory.utils.pdf_helpers import safe_generate_pdf
        safe_generate_pdf(generate_invoice_pdf, dict(inv), parent=self)

        # UAT 1.7b — first-print bundle: when the invoice carries cores
        # AND has never been printed before, also generate the Core
        # Return slip so the customer leaves with both pages. Subsequent
        # prints stay invoice-only; the "Print Core Slip" button on this
        # dialog handles reprints.
        try:
            from db import (
                invoice_has_cores,
                mark_invoice_first_printed,
                get_core_returns_for_invoice,
            )
            first_print = mark_invoice_first_printed(int(self._invoice_id))
            if first_print and invoice_has_cores(int(self._invoice_id)):
                from jaks_inventory.utils.pdf_core_receipt import (
                    generate_core_receipt_pdf,
                    generate_core_slip_for_invoice_preview,
                )
                cores = get_core_returns_for_invoice(int(self._invoice_id)) or []
                if cores:
                    safe_generate_pdf(
                        generate_core_receipt_pdf,
                        int(self._invoice_id), parent=self,
                    )
                else:
                    safe_generate_pdf(
                        generate_core_slip_for_invoice_preview,
                        int(self._invoice_id), parent=self,
                    )
        except Exception:
            # Best-effort — never block invoice printing on core-slip
            # generation. The user can always reprint via the toolbar.
            pass

    def _on_record_payment(self) -> None:
        """Open payment recording dialog."""
        if not self._invoice_id:
            QMessageBox.information(self, "Save First", "Please save the invoice before recording payment.")
            return

        from jaks_inventory.ui.payment_dialog import PaymentDialog
        try:
            inv = get_invoice(self._invoice_id)
            if not inv:
                QMessageBox.warning(self, "Error", "Could not load invoice.")
                return

            # Block payment until the invoice is finalized — finalization
            # is what commits inventory, cores, and serials. Offer to do it
            # now so counter staff don't get stuck mid-checkout.
            if not inv.get("finalized_at"):
                ans = QMessageBox.question(
                    self,
                    "Finalize First?",
                    "This invoice has not been finalized yet, so inventory "
                    "and cores have not been committed.\n\n"
                    "Finalize now and continue to payment?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if ans != QMessageBox.StandardButton.Yes:
                    return
                from db import finalize_invoice as _finalize
                result = _finalize(int(self._invoice_id))
                if not result.get("success"):
                    QMessageBox.warning(
                        self, "Finalize Failed",
                        result.get("error") or "Could not finalize invoice.",
                    )
                    return
                try:
                    if result.get("cores_created"):
                        from .core_events import core_events
                        core_events.cores_changed.emit()
                except Exception:
                    pass
                inv = get_invoice(self._invoice_id) or inv
                self._invoice = dict(inv)
                self._refresh_chrome()
                self._refresh_lines()

            payment_dlg = PaymentDialog(invoice_id=self._invoice_id, current_balance=float(inv.get("balance_due", 0)), parent=self)
            if payment_dlg.exec():
                # Reload invoice to show updated payment status
                self._reload_invoice()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not open payment dialog:\n{e}")

    def _on_process_core_return(self) -> None:
        """Receive returned cores and issue credit, all from this invoice view."""
        if not self._invoice_id:
            QMessageBox.information(self, "Save First", "Please save the invoice before processing cores.")
            return
        inv = self._invoice or get_invoice(self._invoice_id) or {}
        if not inv.get("finalized_at"):
            QMessageBox.information(
                self, "Finalize First",
                "Cores are materialized when the invoice is finalized.\n"
                "Click Finalize Invoice first, then come back to process the core.",
            )
            return
        from jaks_inventory.ui.core_return_flow import process_core_return_for_invoice
        if process_core_return_for_invoice(int(self._invoice_id), parent=self):
            self._reload_invoice()

    def _on_print_core_receipt(self) -> None:
        """Print the customer-signable Core Return Receipt PDF for this invoice.

        Pre-finalize: synthesizes a preview slip from the invoice lines so
        the customer can leave the counter with paperwork.
        Post-finalize: prints the official receipt off the materialized
        ``core_returns`` rows.
        """
        if not self._invoice_id:
            QMessageBox.information(self, "Save First", "Please save the invoice first.")
            return
        try:
            from db import get_core_returns_for_invoice
            cores = get_core_returns_for_invoice(int(self._invoice_id)) or []
        except Exception:
            cores = []
        from jaks_inventory.utils.pdf_helpers import safe_generate_pdf
        from jaks_inventory.utils.pdf_core_receipt import (
            generate_core_receipt_pdf,
            generate_core_slip_for_invoice_preview,
        )
        try:
            if cores:
                safe_generate_pdf(
                    generate_core_receipt_pdf, int(self._invoice_id), parent=self,
                )
            else:
                # Draft / unfinalized — fall back to a preview slip from the
                # current invoice lines. Raises if no core-bearing lines.
                safe_generate_pdf(
                    generate_core_slip_for_invoice_preview,
                    int(self._invoice_id), parent=self,
                )
        except ValueError as exc:
            QMessageBox.information(self, "No Cores", str(exc))
        except Exception as exc:
            QMessageBox.warning(self, "Core Receipt Failed", str(exc))

    def _on_print_warranty(self) -> None:
        """Print combined warranty certificate PDF for this invoice."""
        if not self._invoice_id:
            QMessageBox.information(self, "Save First", "Please save the invoice first.")
            return
        try:
            from db import list_warranties_for_invoice
            certs = list_warranties_for_invoice(int(self._invoice_id)) or []
        except Exception:
            certs = []
        if not certs:
            # Pre-finalize fallback: warranty certificates aren't materialized
            # until the invoice is finalized. Print the company-wide warranty
            # brochure so the customer leaves the counter with the policy in
            # hand; the personalized certificate prints once finalized.
            from jaks_inventory.utils.pdf_helpers import safe_generate_pdf
            from jaks_inventory.utils.pdf_warranty_brochure import (
                generate_warranty_brochure,
            )
            try:
                safe_generate_pdf(generate_warranty_brochure, parent=self)
                QMessageBox.information(
                    self, "Warranty Brochure",
                    "This invoice is still a Draft, so personalized warranty "
                    "certificates have not been issued yet. Printed JAK's "
                    "warranty brochure instead. Finalize the invoice to "
                    "generate per-line certificates.",
                )
            except Exception as exc:
                QMessageBox.warning(self, "Warranty Print Failed", str(exc))
            return
        from jaks_inventory.utils.pdf_helpers import safe_generate_pdf
        from jaks_inventory.utils.pdf_warranty_certificate import (
            generate_warranty_certificates_for_invoice,
        )
        try:
            safe_generate_pdf(
                generate_warranty_certificates_for_invoice,
                int(self._invoice_id), parent=self,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Warranty Print Failed", str(exc))

    def _on_send_email(self) -> None:
        if not self._invoice_id:
            if not self._create_invoice():
                return
        inv = get_invoice(self._invoice_id)
        if not inv:
            return
        try:
            from jaks_inventory.utils.notification_service import (
                send_invoice_email, get_customer_contact, email_configured,
            )
            if not email_configured():
                QMessageBox.warning(self, "Email Not Configured", "Configure SMTP settings first.")
                return
            contact = get_customer_contact(inv.get("customer_id"))
            if not contact.get("email"):
                QMessageBox.warning(self, "No Email", "This customer has no email address on file.")
                return
            from jaks_inventory.utils.email_worker import EmailWorker
            self._email_btn.setEnabled(False)
            self._email_btn.setText("⏳ Sending…")
            self._email_worker = EmailWorker(
                lambda: send_invoice_email(inv, contact["email"]), parent=self
            )

            def _on_result(result: dict) -> None:
                self._email_btn.setText("📧 Send Email")
                self._email_btn.setEnabled(True)
                if result.get("success"):
                    QMessageBox.information(self, "Email Sent", f"Invoice emailed to {contact['email']}.")
                else:
                    QMessageBox.warning(self, "Email Failed", result.get("error", "Unknown error"))

            self._email_worker.finished_result.connect(_on_result)
            self._email_worker.start()
        except Exception as e:
            self._email_btn.setText("📧 Send Email")
            self._email_btn.setEnabled(True)
            QMessageBox.warning(self, "Email Failed", str(e))

    def _estimate_shipping(self) -> None:
        """Estimate shipping from line item weights."""
        if not self._invoice_id:
            self._ship_est_label.setText("Save invoice first")
            return
        from db import get_invoice
        inv = get_invoice(self._invoice_id)
        if not inv:
            return
        lines = inv.get("lines") or []
        if not lines:
            # Try getting lines from the table
            items = []
            for row in range(self._lines_table.rowCount()):
                sku_item = self._lines_table.item(row, 1)
                qty_item = self._lines_table.item(row, 3)
                if sku_item:
                    prod = get_product_by_sku(sku_item.text())
                    if prod:
                        items.append({"product_id": prod["id"],
                                      "quantity": int(qty_item.text()) if qty_item else 1})
        else:
            items = [{"product_id": l.get("product_id"), "quantity": l.get("quantity", 1)} for l in lines]

        if not items:
            self._ship_est_label.setText("No items")
            return

        result = estimate_order_shipping(items, "UPS", "Ground")
        est = result.get("estimated_cost", 0)
        weight = result.get("total_weight", 0)
        no_wt = result.get("items_without_weight", 0)
        if weight <= 0:
            self._ship_est_label.setText("No weights on file")
        else:
            note = f" ({no_wt} missing wt)" if no_wt else ""
            self._ship_est_label.setText(f"{weight:.1f} lbs{note}")
            self._shipping_spin.setValue(est)

    def _on_sku_changed(self, text: str) -> None:
        """Handle SKU text change - update serial selector if product has serials."""
        self._serial_combo.clear()
        self._serial_label.setVisible(False)
        self._serial_combo.setVisible(False)
        self._selected_serial_id = None
        
        if not text.strip():
            return
        
        # Check if this SKU has available serials
        product = get_product_by_sku(text.strip())
        if product:
            product_id = product.get("id")
            from db import get_available_serials
            serials = get_available_serials(product_id)
            
            if serials:
                # Product is serialized - show serial selector
                self._serial_label.setVisible(True)
                self._serial_combo.setVisible(True)
                self._serial_combo.addItem("-- Select Serial --", None)
                for s in serials:
                    display = s.get("serial_number", "")
                    if s.get("lot_number"):
                        display += f" (Lot: {s.get('lot_number')})"
                    self._serial_combo.addItem(display, s.get("id"))
                
                # Force qty to 1 for serialized items
                self._qty_spin.setValue(1)
                self._qty_spin.setEnabled(False)
            else:
                self._qty_spin.setEnabled(True)
            
            # Auto-fill description and price (routes through customer tier).
            self._desc_edit.setText(product.get("title", ""))
            self._price_spin.setValue(self._price_for(product))
    
    def _on_serial_selected(self, index: int) -> None:
        """Handle serial selection."""
        self._selected_serial_id = self._serial_combo.currentData()

    def _on_add_line(self) -> None:
        """Add a line item."""
        if not self._invoice_id:
            # Need to create invoice first
            if not self._create_invoice():
                return
        
        sku = self._sku_edit.text().strip()
        desc = self._desc_edit.text().strip()
        qty = self._qty_spin.value()
        price = self._price_spin.value()
        
        if not desc:
            QMessageBox.warning(self, "Validation", "Description is required.")
            return
        
        # Get product_id if SKU matches
        product_id = None
        serial_id = getattr(self, "_selected_serial_id", None)
        
        if sku:
            product = get_product_by_sku(sku)
            if product:
                product_id = product.get("id")
        
        # F4: MAP warn-and-confirm. If the product has a non-zero
        # map_price and the user is selling below it, ask before posting.
        if product_id:
            try:
                from db import get_product_map_price
                map_price = get_product_map_price(int(product_id))
            except Exception:
                map_price = 0.0
            if map_price > 0 and float(price) + 0.005 < map_price:
                reply = QMessageBox.warning(
                    self, "Below MAP",
                    f"Unit price ${float(price):,.2f} is below the "
                    f"manufacturer's Minimum Advertised Price of "
                    f"${map_price:,.2f}.\n\n"
                    "Sell below MAP anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
        
        # Overbook guard — for direct invoices (no parent SO), warn if the
        # requested qty exceeds what's available after existing
        # reservations. Adding the line will still clamp inside
        # _reserve_for_invoice_line, but the user should know they're
        # claiming stock that's promised elsewhere (open SOs / drafts).
        if product_id and int(qty) > 0:
            try:
                from db import get_stock_state
                _stock = get_stock_state(int(product_id))
            except Exception:
                _stock = None
            if _stock and int(_stock.get("available") or 0) < int(qty):
                _avail = int(_stock.get("available") or 0)
                _bits = []
                for _r in (_stock.get("reservations") or [])[:5]:
                    _bits.append(
                        f"  • {(_r.get('doc_type') or '').upper()} "
                        f"{_r.get('doc_number') or ''}: {_r.get('quantity') or 0}"
                    )
                _detail = "\n".join(_bits) if _bits else "  (no open reservations)"
                reply = QMessageBox.warning(
                    self, "Overbook Warning",
                    f"Only {_avail} of {sku or 'this part'} is available "
                    f"(On Hand {int(_stock.get('on_hand') or 0)}, "
                    f"Reserved {int(_stock.get('reserved') or 0)}).\n\n"
                    f"You are about to invoice {int(qty)}. "
                    f"That stock is promised to:\n{_detail}\n\n"
                    "Add the line anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        # Validate serial selection if serials visible
        if self._serial_combo.isVisible() and not serial_id:
            reply = QMessageBox.question(
                self, "No Serial Selected",
                "This product has available serial numbers but none selected.\n\n"
                "Add line without a serial number?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        
        add_invoice_line(
            invoice_id=self._invoice_id,
            product_id=product_id,
            serial_id=serial_id,
            sku=sku,
            description=desc,
            quantity=qty,
            unit_price=price,
        )
        
        # Reload invoice data (convert to dict to release cursor)
        inv = get_invoice(self._invoice_id)
        self._invoice = dict(inv) if inv else None
        self._refresh_lines()
        
        # Clear inputs
        self._sku_edit.clear()
        self._desc_edit.clear()
        self._qty_spin.setValue(1)
        self._qty_spin.setEnabled(True)
        self._price_spin.setValue(0)
        self._serial_combo.clear()
        self._serial_label.setVisible(False)
        self._serial_combo.setVisible(False)
        self._selected_serial_id = None
        self._sku_edit.setFocus()

        # ── Suggested sells popup ──
        if product_id:
            self._show_suggested_sells(product_id)

    def _show_suggested_sells(self, product_id: int) -> None:
        """Show the suggested sells popup, adding checked items as invoice lines."""
        from .suggested_sells_popup import SuggestedSellsPopup

        dlg = SuggestedSellsPopup(product_id, parent=self)
        if not dlg.has_suggestions:
            return
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for item in dlg.selected_items():
            add_invoice_line(
                invoice_id=self._invoice_id,
                product_id=item["product_id"],
                serial_id=None,
                sku=item["sku"],
                description=item["title"],
                quantity=item["qty"],
                unit_price=item["selling_price"],
            )
        inv = get_invoice(self._invoice_id)
        self._invoice = dict(inv) if inv else None
        self._refresh_lines()

    def _on_remove_line(self) -> None:
        """Remove selected line."""
        selected = self._lines_table.selectedItems()
        if not selected:
            return
        
        row = selected[0].row()
        line_id_item = self._lines_table.item(row, 0)
        if not line_id_item:
            return
        
        line_id = int(line_id_item.text())
        remove_invoice_line(line_id)
        
        # Reload invoice data (convert to dict to release cursor)
        inv = get_invoice(self._invoice_id)
        self._invoice = dict(inv) if inv else None
        self._refresh_lines()

    def _on_add_discount_line(self) -> None:
        """Open the shared DiscountLineDialog and append a negative line."""
        if not self._invoice_id:
            # Create header first so the discount has somewhere to live.
            if not self._create_invoice():
                return
        # Compute current non-discount subtotal for % preview.
        subtotal = 0.0
        for ln in (self._invoice.get("lines") or []) if self._invoice else []:
            if (ln.get("line_kind") or "product") == "discount":
                continue
            subtotal += float(ln.get("line_total") or 0)
        dlg = DiscountLineDialog(
            self,
            doc_type="invoice",
            product_subtotal=subtotal,
            default_description="Discount",
        )
        if dlg.exec() != dlg.DialogCode.Accepted or not dlg.result_payload:
            return
        payload = dlg.result_payload
        result = add_discount_line(
            "invoice",
            self._invoice_id,
            description=payload["description"],
            discount_amount=payload.get("discount_amount"),
            discount_pct=payload.get("discount_pct"),
        )
        if not result.get("success"):
            QMessageBox.warning(
                self,
                "Discount Line",
                f"Could not add discount line: {result.get('error', 'unknown error')}",
            )
            return
        inv = get_invoice(self._invoice_id)
        self._invoice = dict(inv) if inv else None
        self._refresh_lines()

    def _create_invoice(self) -> bool:
        """Create the invoice if it doesn't exist yet."""
        customer_id = self._customer_combo.currentData()
        if not customer_id:
            QMessageBox.warning(self, "Validation", "Please select a customer.")
            return False
        
        try:
            result = create_invoice(
                customer_id=customer_id,
                invoice_date=self._invoice_date.date().toString("yyyy-MM-dd"),
                due_date=self._due_date.date().toString("yyyy-MM-dd"),
                notes=self._notes_edit.text().strip() or None,
                equipment_type=self._equipment_type_combo.currentData() or None,
                equipment_make=self._equipment_make_combo.currentText().strip() or None,
                equipment_model=self._equipment_model_edit.text().strip() or None,
                esn=self._esn_edit.text().strip() or None,
                vin=self._vin_edit.text().strip() or None,
            )
            self._invoice_id = result["id"]
            # Convert to dict to release cursor
            inv = get_invoice(self._invoice_id)
            self._invoice = dict(inv) if inv else None
            self.setWindowTitle(f"Invoice: {result['invoice_number']}")
            self._refresh_chrome()
            return True
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create invoice:\n{e}")
            return False

    def _save_invoice_fields(self) -> bool:
        """Persist invoice header/sidebar fields without changing finalization."""
        if not self._invoice_id:
            if not self._create_invoice():
                return False

        # Persist tracking / ship date / notes / shipping cost / equipment
        kwargs = {}
        tracking = self._tracking_edit.text().strip()
        if tracking:
            kwargs["tracking_number"] = tracking
        ship_dt = self._ship_date_edit.date()
        if ship_dt > self._ship_date_edit.minimumDate():
            kwargs["ship_date"] = ship_dt.toString("yyyy-MM-dd")
        notes = self._notes_edit.text().strip()
        kwargs["notes"] = notes or None
        kwargs["shipping"] = self._shipping_spin.value()
        kwargs["tax_rate"] = self._tax_spin.value() / 100.0
        kwargs["equipment_type"] = self._equipment_type_combo.currentData() or None
        kwargs["equipment_make"] = self._equipment_make_combo.currentText().strip() or None
        kwargs["equipment_model"] = self._equipment_model_edit.text().strip() or None
        kwargs["esn"] = self._esn_edit.text().strip() or None
        kwargs["vin"] = self._vin_edit.text().strip() or None

        if kwargs:
            update_invoice(self._invoice_id, **kwargs)

        inv = get_invoice(self._invoice_id)
        self._invoice = dict(inv) if inv else None
        self._refresh_chrome()
        return True

    def _on_save(self) -> None:
        """Save the invoice and close."""
        if self._save_invoice_fields():
            self.accept()

    def _on_save_and_close(self) -> None:
        """Save the invoice and close."""
        if self._save_invoice_fields():
            self.accept()

    def _on_finalize(self) -> None:
        """Finalize the invoice and deduct inventory."""
        if not self._invoice_id:
            QMessageBox.warning(self, "Error", "Save the invoice first.")
            return

        # Save any pending changes first
        kwargs = {}
        # Safety net: if customer is tax_exempt, force tax to zero before finalizing
        customer_id = self._customer_combo.currentData()
        if customer_id:
            cust = get_customer(customer_id)
            if cust and cust.get("tax_exempt") and self._tax_spin.value() != 0.0:
                import logging
                logging.getLogger(__name__).warning(
                    "Finalize: forcing tax to 0 for tax-exempt customer %s", customer_id
                )
                self._tax_spin.setValue(0.0)
                update_invoice(self._invoice_id, tax_rate=0.0, tax_amount=0.0)
        tracking = self._tracking_edit.text().strip()
        if tracking:
            kwargs["tracking_number"] = tracking
        ship_dt = self._ship_date_edit.date()
        if ship_dt > self._ship_date_edit.minimumDate():
            kwargs["ship_date"] = ship_dt.toString("yyyy-MM-dd")
        notes = self._notes_edit.text().strip()
        kwargs["notes"] = notes or None
        kwargs["shipping"] = self._shipping_spin.value()
        kwargs["tax_rate"] = self._tax_spin.value() / 100.0
        kwargs["equipment_type"] = self._equipment_type_combo.currentData() or None
        kwargs["equipment_make"] = self._equipment_make_combo.currentText().strip() or None
        kwargs["equipment_model"] = self._equipment_model_edit.text().strip() or None
        kwargs["esn"] = self._esn_edit.text().strip() or None
        kwargs["vin"] = self._vin_edit.text().strip() or None
        if kwargs:
            update_invoice(self._invoice_id, **kwargs)

        reply = QMessageBox.question(
            self, "Send Invoice",
            "Send this invoice to the customer?\n\n"
            "Inventory for these items will be removed from stock.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        from db import finalize_invoice
        result = finalize_invoice(self._invoice_id, deduct_inventory=True)
        
        if result.get("success"):
            # Notify the Cores screen (and any open Customer Detail
            # dialog) so newly-created core returns appear immediately
            # without requiring a manual tab switch / refresh.
            try:
                if result.get("cores_created"):
                    from .core_events import core_events
                    core_events.cores_changed.emit()
            except Exception:
                pass
            # Generate and open PDF
            self._print_and_prompt()
            self.accept()
        else:
            QMessageBox.critical(
                self, "Error",
                f"Failed to finalize:\n{result.get('error')}"
            )

    def _on_revert_to_draft(self) -> None:
        """Revert a finalized invoice back to draft for editing."""
        if not self._invoice_id:
            return
        reply = QMessageBox.question(
            self, "Revert to Draft",
            "Revert this invoice back to draft?\n\n"
            "This will:\n"
            "• Restore inventory quantities\n"
            "• Free any reserved serial numbers\n"
            "• Void unreceived core returns\n"
            "• Re-enable editing of all fields\n\n"
            "Refunds (if any) must be issued separately.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        from db import revert_invoice_to_draft
        result = revert_invoice_to_draft(self._invoice_id)
        if not result.get("success"):
            QMessageBox.critical(
                self, "Cannot Revert",
                result.get("error") or "Unknown error.",
            )
            return

        restored = len(result.get("restored", []))
        cores = len(result.get("cores_voided", []))
        serials = len(result.get("serials_returned", []))
        QMessageBox.information(
            self, "Reverted",
            f"Invoice reverted to draft.\n\n"
            f"Inventory restored: {restored} line(s)\n"
            f"Serials freed: {serials}\n"
            f"Core returns voided: {cores}",
        )
        self._reload_invoice()
        self._set_editing_enabled(True)
        self._refresh_lines()

    def _on_return(self) -> None:
        """Open the customer-return dialog for this invoice."""
        if not self._invoice_id:
            return
        from jaks_inventory.ui.invoice_return_dialog import InvoiceReturnDialog
        dlg = InvoiceReturnDialog(self._invoice_id, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._reload_invoice()
            self._refresh_lines()

    def _print_and_prompt(self) -> None:
        """Generate PDF, open it, then show email/SMS/print action prompt."""
        if not self._invoice_id:
            return
        inv = get_invoice(self._invoice_id)
        if not inv:
            return

        # Generate and open PDF (with friendly retry/copy on lock)
        from jaks_inventory.utils.pdf_invoice import generate_invoice_pdf
        from jaks_inventory.utils.pdf_helpers import safe_generate_pdf
        safe_generate_pdf(generate_invoice_pdf, dict(inv), parent=self)

        # If this invoice generated any core returns, also produce the
        # signable Core Return Receipt so the customer has one row per
        # physical core they owe back.
        cores_for_invoice = []
        try:
            from db import get_core_returns_for_invoice
            cores_for_invoice = get_core_returns_for_invoice(int(self._invoice_id)) or []
        except Exception:
            cores_for_invoice = []

        # Post-finalize action prompt
        from PySide6.QtWidgets import QCheckBox
        dlg = QDialog(self)
        dlg.setWindowTitle("Invoice Finalized")
        dlg.setFixedWidth(340)
        lay = QVBoxLayout(dlg)

        inv_num = inv.get("invoice_number", "")
        lay.addWidget(QLabel(f"Invoice {inv_num} finalized successfully.\nSelect actions:"))
        lay.addSpacing(8)

        email_cb = QCheckBox("📧 Email to customer")
        email_cb.setChecked(True)
        lay.addWidget(email_cb)

        sms_cb = QCheckBox("📱 SMS to customer")
        sms_cb.setChecked(True)
        lay.addWidget(sms_cb)

        core_cb = None
        if cores_for_invoice:
            core_cb = QCheckBox(
                f"🖨 Print Core Return Receipt ({len(cores_for_invoice)} core"
                f"{'s' if len(cores_for_invoice) != 1 else ''})"
            )
            core_cb.setChecked(True)
            lay.addWidget(core_cb)

        lay.addSpacing(8)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        go_btn = QPushButton("Go")
        go_btn.setStyleSheet(
            "QPushButton { background: #2e7d32; color: white; font-weight: bold; "
            "padding: 6px 20px; border-radius: 3px; }"
            "QPushButton:hover { background: #388e3c; }"
        )
        go_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(go_btn)
        skip_btn = QPushButton("Skip")
        skip_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(skip_btn)
        lay.addLayout(btn_row)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Print core receipt first (independent of customer contact info).
            if core_cb is not None and core_cb.isChecked():
                try:
                    from jaks_inventory.utils.pdf_core_receipt import generate_core_receipt_pdf
                    safe_generate_pdf(generate_core_receipt_pdf, int(self._invoice_id), parent=self)
                except Exception as e:
                    QMessageBox.warning(self, "Core Receipt Failed", str(e))

            customer_id = inv.get("customer_id")
            if not customer_id:
                return

            from jaks_inventory.utils.notification_service import (
                send_invoice_email, send_invoice_sms,
                get_customer_contact, email_configured,
            )
            from jaks_inventory.utils.sms_service import is_configured as sms_is_configured
            contact = get_customer_contact(customer_id)

            if email_cb.isChecked() and contact.get("email"):
                if email_configured():
                    from jaks_inventory.utils.email_worker import EmailWorker
                    self._email_worker = EmailWorker(
                        lambda: send_invoice_email(inv, contact["email"]), parent=self
                    )
                    def _on_email(result):
                        if result.get("success"):
                            QMessageBox.information(self, "Email Sent",
                                f"Invoice emailed to {contact['email']}.")
                        else:
                            QMessageBox.warning(self, "Email Failed", result.get("error", ""))
                    self._email_worker.finished_result.connect(_on_email)
                    self._email_worker.start()

            if sms_cb.isChecked() and contact.get("phone"):
                if sms_is_configured():
                    sms_result = send_invoice_sms(inv, contact["phone"])
                    if not sms_result.get("success"):
                        QMessageBox.warning(self, "SMS Failed", sms_result.get("error", ""))

    def get_invoice_id(self) -> int:
        """Return the invoice ID."""
        return self._invoice_id
