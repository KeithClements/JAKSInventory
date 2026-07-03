"""Purchase Order detail \u2014 full workflow screen.

Statuses: DRAFT \u2192 SENT \u2192 PARTIAL \u2192 RECEIVED \u2192 CLOSED
          (CANCELLED at any point)

Actions by status:
  Draft   : Edit header/lines, Save, Send PO, Cancel
  Sent    : Receive Items, Mark Partial, Close
  Partial : Receive Remaining, Close
  Received: Close
  Always  : Open linked Sales Order
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QDate, QDateTime, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCalendarWidget, QCheckBox, QComboBox, QCompleter, QDateTimeEdit, QDialog,
    QDoubleSpinBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from db import (
    get_purchase_order, get_all_vendors, update_purchase_order,
    update_po_status, update_po_line, remove_po_line, add_po_line,
    add_discount_line,
    browse_products, get_sales_order, get_setting,
)
from .discount_line_dialog import DiscountLineDialog
from .workflow_shell import (
    build_bottom_action_bar,
    build_header_bar,
    build_progress_strip,
    create_link_card,
    create_sidebar_container,
    create_sidebar_section_title,
    create_status_badge,
    style_action_button,
)
from .auto_refresh_mixin import AutoRefreshMixin

from .receive_dialog import ReceiveDialog

# ── Constants ────────────────────────────────────────────────
_STATUS_COLORS = {
    "draft":     ("#E5E7EB", "#374151"),
    "sent":      ("#EDE9FE", "#5B21B6"),
    "confirmed": ("#FEF9C3", "#713F12"),
    "ordered":   ("#FEF9C3", "#713F12"),
    "partial":   ("#FFEDD5", "#9A3412"),
    "received":  ("#D1FAE5", "#065F46"),
    "closed":    ("#E5E7EB", "#4B5563"),
    "cancelled": ("#FEE2E2", "#991B1B"),
}
_PROGRESS = ["Draft", "Sent", "Confirmed", "Partial Recv", "Received", "Closed"]
_C_ID, _C_SKU, _C_TITLE, _C_QTY, _C_RECV, _C_COST, _C_CORE, _C_CORE_TOTAL, _C_TOTAL, _C_NOTES = range(10)
_HEADERS = [
    "ID", "Vendor ID", "Description", "Ordered", "Received",
    "Unit Cost", "Core Ea.", "Core Total", "Line Total", "Notes",
]


class PODialog(AutoRefreshMixin, QDialog):
    """Full-screen Purchase Order detail / edit screen."""

    po_changed = Signal()

    def __init__(self, po_id: int = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._po_id = po_id
        self._po: dict | None = None
        self.setWindowTitle("Purchase Order")
        self.setMinimumSize(1100, 680)
        self.resize(1280, 760)
        self._build_ui()
        if po_id:
            self._refresh()
            # Initialize auto-refresh after data is loaded (3 sec interval)
            self._init_auto_refresh(refresh_interval_ms=3000)
        # E5 — warn once if this PO has already been pushed/marked to QBO
        self._qbo_lock_acknowledged = False
        self._qbo_lock_warning_pending = bool(po_id)

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if getattr(self, "_qbo_lock_warning_pending", False):
            self._qbo_lock_warning_pending = False
            try:
                from db import is_po_locked
                if self._po_id and is_po_locked(int(self._po_id)):
                    po_no = (self._po or {}).get(
                        "po_number", f"#{self._po_id}"
                    )
                    resp = QMessageBox.warning(
                        self,
                        "PO locked (QBO export)",
                        f"PO {po_no} has already been exported to QuickBooks "
                        "as a Bill. Editing it now may cause your books to "
                        "fall out of sync with QBO.\n\nEdit anyway?",
                        QMessageBox.StandardButton.Yes
                        | QMessageBox.StandardButton.Cancel,
                        QMessageBox.StandardButton.Cancel,
                    )
                    if resp != QMessageBox.StandardButton.Yes:
                        from PySide6.QtCore import QTimer
                        QTimer.singleShot(0, self.reject)
                        return
                    self._qbo_lock_acknowledged = True
            except Exception:
                pass

    # ==================================================================
    #  UI BUILD
    # ==================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_progress_strip())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)
        splitter.addWidget(self._build_main_panel())
        splitter.addWidget(self._build_sidebar())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([860, 340])
        root.addWidget(splitter, 1)

        root.addWidget(self._build_bottom_bar())

    # ── Header bar ───────────────────────────────────────────
    def _build_header(self) -> QFrame:
        bar, lay = build_header_bar()

        self._title_lbl = QLabel("Purchase Order")
        self._title_lbl.setStyleSheet(
            "color: white; font-size: 16px; font-weight: bold;"
        )
        lay.addWidget(self._title_lbl)

        self._status_badge = create_status_badge("DRAFT")
        lay.addWidget(self._status_badge)

        lay.addSpacing(16)
        self._vendor_lbl = QLabel("")
        self._vendor_lbl.setStyleSheet("color: #D1D5DB; font-size: 12px;")
        lay.addWidget(self._vendor_lbl)

        self._date_lbl = QLabel("")
        self._date_lbl.setStyleSheet("color: #9CA3AF; font-size: 11px;")
        lay.addWidget(self._date_lbl)

        lay.addStretch()

        self._total_lbl = QLabel("$0.00")
        self._total_lbl.setStyleSheet(
            "color: #34D399; font-size: 18px; font-weight: bold;"
        )
        lay.addWidget(self._total_lbl)
        return bar

    # ── Progress strip ───────────────────────────────────────
    def _build_progress_strip(self) -> QFrame:
        strip, labels = build_progress_strip(_PROGRESS)
        self._progress_labels = labels
        return strip

    # ── Main panel (header form + lines table) ───────────────
    def _build_main_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 6, 8)
        lay.setSpacing(8)

        # ── Editable header fields ──
        hdr_frame = QFrame()
        hdr_frame.setStyleSheet(
            "QFrame { background: white; border: 1px solid #E5E7EB; "
            "border-radius: 6px; padding: 8px; }"
        )
        hdr_outer = QVBoxLayout(hdr_frame)
        hdr_outer.setContentsMargins(0, 0, 0, 0)
        hdr_outer.setSpacing(8)
        hdr = QHBoxLayout()
        hdr.setSpacing(12)

        hdr.addWidget(QLabel("Vendor:"))
        self._vendor_combo = QComboBox()
        self._vendor_combo.setMinimumWidth(180)
        vendors = get_all_vendors()
        self._vendor_combo.addItem("-- Select --", None)
        for v in vendors:
            self._vendor_combo.addItem(v.get("name", ""), v.get("id"))
        hdr.addWidget(self._vendor_combo)

        hdr.addWidget(QLabel("Order Date:"))
        self._order_date = QDateTimeEdit()
        self._order_date.setDateTime(QDateTime.currentDateTime())
        self._order_date.setDisplayFormat("M/d/yyyy HH:mm")
        self._order_date.setCalendarPopup(True)
        order_cal = QCalendarWidget(self._order_date)
        order_cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        order_cal.setMinimumSize(310, 240)
        order_cal.setWindowFlag(Qt.WindowType.Popup, True)
        self._order_date.setCalendarWidget(order_cal)
        hdr.addWidget(self._order_date)

        hdr.addWidget(QLabel("Expected:"))
        self._expected_date = QDateTimeEdit()
        self._expected_date.setDateTime(QDateTime.currentDateTime().addDays(7))
        self._expected_date.setDisplayFormat("M/d/yyyy HH:mm")
        self._expected_date.setCalendarPopup(True)
        expected_cal = QCalendarWidget(self._expected_date)
        expected_cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        expected_cal.setMinimumSize(310, 240)
        expected_cal.setWindowFlag(Qt.WindowType.Popup, True)
        self._expected_date.setCalendarWidget(expected_cal)
        hdr.addWidget(self._expected_date)

        hdr.addStretch()
        hdr_outer.addLayout(hdr)

        addr_row = QHBoxLayout()
        addr_row.setSpacing(10)
        addr_row.addWidget(QLabel("Ship To:"))
        self._ship_to_edit = QTextEdit()
        self._ship_to_edit.setPlaceholderText("Shipping destination for this PO")
        self._ship_to_edit.setFixedHeight(72)
        self._ship_to_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._ship_to_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        addr_row.addWidget(self._ship_to_edit, 1)
        self._ship_to_btn = QToolButton()
        self._ship_to_btn.setText("▾")
        self._ship_to_btn.setToolTip("Pick a saved ship-to address")
        self._ship_to_btn.setFixedWidth(26)
        self._ship_to_btn.clicked.connect(self._pick_ship_to_address)
        addr_row.addWidget(self._ship_to_btn)

        addr_row.addWidget(QLabel("Bill To:"))
        self._bill_to_edit = QTextEdit()
        self._bill_to_edit.setPlaceholderText("Billing entity/address for this PO")
        self._bill_to_edit.setFixedHeight(72)
        self._bill_to_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._bill_to_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        addr_row.addWidget(self._bill_to_edit, 1)
        self._bill_to_btn = QToolButton()
        self._bill_to_btn.setText("▾")
        self._bill_to_btn.setToolTip("Pick a saved bill-to address")
        self._bill_to_btn.setFixedWidth(26)
        self._bill_to_btn.clicked.connect(self._pick_bill_to_address)
        addr_row.addWidget(self._bill_to_btn)
        hdr_outer.addLayout(addr_row)

        # ── Drop-ship / No-tags toggles + Vendor Message ──
        opts_row = QHBoxLayout()
        opts_row.setSpacing(12)
        self._drop_ship_chk = QCheckBox("Drop Ship to Customer")
        self._drop_ship_chk.setToolTip(
            "Replace Ship-To with the linked Sales Order's customer address "
            "and notify the vendor to drop ship direct."
        )
        self._drop_ship_chk.toggled.connect(self._on_drop_ship_toggled)
        opts_row.addWidget(self._drop_ship_chk)

        self._no_tags_chk = QCheckBox("Request No Tags / ID Material")
        self._no_tags_chk.setToolTip(
            "Append a request to the vendor message: "
            "'No Tags or Identification Material Please.'"
        )
        opts_row.addWidget(self._no_tags_chk)
        opts_row.addStretch()
        hdr_outer.addLayout(opts_row)

        msg_row = QHBoxLayout()
        msg_lbl = QLabel("Message to Vendor:")
        msg_lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
        msg_row.addWidget(msg_lbl)
        self._vendor_message_edit = QTextEdit()
        self._vendor_message_edit.setPlaceholderText(
            "Optional message printed on the PO and included in the email body "
            "(visible to the vendor)."
        )
        # UAT 2.4-sub — collapse to a single visible row by default to give
        # the line table more space; user can drag-resize taller if needed.
        self._vendor_message_edit.setMinimumHeight(32)
        self._vendor_message_edit.setMaximumHeight(56)
        msg_row.addWidget(self._vendor_message_edit, 1)
        hdr_outer.addLayout(msg_row)

        lay.addWidget(hdr_frame)

        # ── Add line: search picker ──
        self._add_row_frame = QFrame()
        ar_outer = QVBoxLayout(self._add_row_frame)
        ar_outer.setContentsMargins(0, 0, 0, 0)
        ar_outer.setSpacing(4)

        # Search row
        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self._product_search = QLineEdit()
        self._product_search.setPlaceholderText(
            "Search product by SKU, title, or part number\u2026"
        )
        self._product_search.setMinimumWidth(280)
        self._product_search.returnPressed.connect(self._on_search_products)
        search_row.addWidget(self._product_search, 1)

        self._search_btn = QPushButton("\U0001f50d Search")
        self._search_btn.setStyleSheet(
            "QPushButton { background: #1d4ed8; color: white; font-weight: bold; "
            "padding: 4px 12px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #1e3a8a; }"
        )
        self._search_btn.clicked.connect(self._on_search_products)
        search_row.addWidget(self._search_btn)

        new_prod_btn = QPushButton("✚ New Product")
        new_prod_btn.setToolTip("Create a new product and add it to this PO")
        new_prod_btn.setStyleSheet(
            "QPushButton { background: #7c3aed; color: white; font-weight: bold; "
            "padding: 4px 12px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #6d28d9; }"
        )
        new_prod_btn.clicked.connect(self._on_new_product)
        search_row.addWidget(new_prod_btn)

        self._search_count_label = QLabel("")
        self._search_count_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        search_row.addWidget(self._search_count_label)
        search_row.addStretch()
        ar_outer.addLayout(search_row)

        # Results table (hidden until first search)
        self._search_table = QTableWidget(0, 6)
        self._search_table.setHorizontalHeaderLabels(
            ["ID", "SKU", "Title", "Preferred Vendor", "QOH", "Cost"]
        )
        self._search_table.setColumnHidden(0, True)
        self._search_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._search_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._search_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._search_table.setAlternatingRowColors(True)
        self._search_table.verticalHeader().setVisible(False)
        self._search_table.verticalHeader().setDefaultSectionSize(22)
        self._search_table.setMaximumHeight(160)
        self._search_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        self._search_table.doubleClicked.connect(self._on_add_line)
        self._search_table.itemSelectionChanged.connect(self._on_search_row_changed)
        self._search_table.hide()
        self._search_results: list[dict] = []
        ar_outer.addWidget(self._search_table)

        # Action row
        act_row = QHBoxLayout()
        act_row.setSpacing(6)
        act_row.addWidget(QLabel("Qty:"))
        self._qty_spin = QSpinBox()
        self._qty_spin.setRange(1, 9999)
        self._qty_spin.setValue(1)
        act_row.addWidget(self._qty_spin)

        act_row.addWidget(QLabel("Cost:"))
        self._cost_spin = QDoubleSpinBox()
        self._cost_spin.setRange(0, 999999)
        self._cost_spin.setDecimals(2)
        self._cost_spin.setPrefix("$")
        self._cost_spin.setToolTip(
            "Leave at $0.00 to use the product's default cost."
        )
        act_row.addWidget(self._cost_spin)
        act_row.addStretch()

        add_btn = QPushButton("\u2795 Add Line")
        add_btn.setStyleSheet(
            "QPushButton { background: #059669; color: white; font-weight: bold; "
            "padding: 4px 12px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #047857; }"
        )
        add_btn.clicked.connect(self._on_add_line)
        act_row.addWidget(add_btn)

        # ── Discount line button ──
        disc_btn = QPushButton("\u2796 Add Discount Line")
        disc_btn.setStyleSheet(
            "QPushButton { background: #b91c1c; color: white; font-weight: bold; "
            "padding: 4px 12px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #991b1b; }"
        )
        disc_btn.setToolTip(
            "Add a manual discount line (e.g. 5% vendor volume discount). "
            "Stored as a negative-amount line item."
        )
        disc_btn.clicked.connect(self._on_add_discount_line)
        self._disc_btn = disc_btn
        act_row.addWidget(disc_btn)

        self._rm_btn = QPushButton("\U0001f5d1\ufe0f Remove Line")
        self._rm_btn.setStyleSheet(
            "QPushButton { color: #DC2626; padding: 4px 10px; border-radius: 4px; "
            "border: 1px solid #FCA5A5; }"
            "QPushButton:hover { background: #FEE2E2; }"
        )
        self._rm_btn.clicked.connect(self._on_remove_line)
        act_row.addWidget(self._rm_btn)

        ar_outer.addLayout(act_row)
        lay.addWidget(self._add_row_frame)

        # ── Lines table ──
        self._lines_table = QTableWidget(0, 10)
        self._lines_table.setHorizontalHeaderLabels(_HEADERS)
        self._lines_table.setColumnHidden(_C_ID, True)
        # Tooltip on the Core Ea. header explains the deposit semantics so a
        # purchaser hovering on the column understands it's a refundable
        # vendor liability, not an extra fee owed.
        hdr_item = self._lines_table.horizontalHeaderItem(_C_CORE)
        if hdr_item is not None:
            hdr_item.setToolTip("Refundable vendor core deposit per unit.")
        hdr_item = self._lines_table.horizontalHeaderItem(_C_CORE_TOTAL)
        if hdr_item is not None:
            hdr_item.setToolTip(
                "Qty × Core Ea. — total refundable core liability for this line."
            )
        self._lines_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._lines_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._lines_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._lines_table.verticalHeader().setVisible(False)
        self._lines_table.setAlternatingRowColors(True)
        self._lines_table.verticalHeader().setDefaultSectionSize(30)
        self._lines_table.horizontalHeader().setSectionResizeMode(
            _C_TITLE, QHeaderView.ResizeMode.Stretch
        )
        self._lines_table.cellChanged.connect(self._on_cell_changed)
        self._lines_table.setStyleSheet("""
            QTableWidget { border: 1px solid #E5E7EB; border-radius: 6px;
                           gridline-color: #F3F4F6; }
            QHeaderView::section {
                background-color: #F9FAFB; color: #6B7280; font-weight: 600;
                font-size: 11px; padding: 6px 8px; border: none;
                border-bottom: 1px solid #E5E7EB;
            }
            QTableWidget::item { padding: 4px 8px; }
            QTableWidget::item:selected { background-color: #DBEAFE;
                                          color: #1E40AF; }
        """)
        lay.addWidget(self._lines_table, 1)

        # ── Totals row ──
        totals = QHBoxLayout()
        totals.addStretch()
        self._subtotal_lbl = QLabel("Subtotal: $0.00")
        self._subtotal_lbl.setStyleSheet(
            "font-size: 12px; color: #6B7280; font-weight: 600;"
        )
        totals.addWidget(self._subtotal_lbl)
        totals.addSpacing(16)
        self._core_lbl = QLabel("Cores: $0.00")
        self._core_lbl.setStyleSheet(
            "font-size: 12px; color: #7C3AED; font-weight: 600;"
        )
        self._core_lbl.setVisible(False)
        totals.addWidget(self._core_lbl)
        totals.addSpacing(16)
        ship_lbl = QLabel("Shipping:")
        ship_lbl.setStyleSheet("font-size: 12px; color: #6B7280; font-weight: 600;")
        totals.addWidget(ship_lbl)
        self._shipping_spin = QDoubleSpinBox()
        self._shipping_spin.setRange(0, 999999)
        self._shipping_spin.setDecimals(2)
        self._shipping_spin.setPrefix("$")
        self._shipping_spin.setMaximumWidth(110)
        self._shipping_spin.setToolTip(
            "Vendor freight / shipping cost on this PO. Saved with the PO and "
            "included in the total."
        )
        self._shipping_spin.editingFinished.connect(self._on_shipping_changed)
        totals.addWidget(self._shipping_spin)
        totals.addSpacing(24)
        self._grand_lbl = QLabel("Total: $0.00")
        self._grand_lbl.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #059669;"
        )
        totals.addWidget(self._grand_lbl)
        lay.addLayout(totals)
        return panel

    # ── Right sidebar ────────────────────────────────────────
    def _build_sidebar(self) -> QWidget:
        panel, lay = create_sidebar_container(min_width=280, max_width=360)

        # Source SO link
        hdr = create_sidebar_section_title("\U0001f4ce Order Links")
        lay.addWidget(hdr)

        self._so_link_card = create_link_card("Source Sales Order", "\u2014", "Open Sales Order")
        self._so_link_val = self._so_link_card[1]
        self._open_so_btn = self._so_link_card[2]
        self._open_so_btn.clicked.connect(self._open_linked_so)
        lay.addWidget(self._so_link_card[0])

        # Vendor Communication History
        comm_hdr = create_sidebar_section_title("\U0001f4e1 Vendor Communication")
        comm_hdr.setStyleSheet("font-weight: bold; font-size: 12px; color: #1F2937; margin-top: 8px;")
        lay.addWidget(comm_hdr)
        self._comm_history_lbl = QLabel("Not yet sent.")
        self._comm_history_lbl.setWordWrap(True)
        self._comm_history_lbl.setStyleSheet(
            "font-size: 11px; color: #374151; background: white; "
            "border: 1px solid #E5E7EB; border-radius: 4px; padding: 8px;"
        )
        lay.addWidget(self._comm_history_lbl)

        # Vendor confirm # field
        confirm_row = QHBoxLayout()
        confirm_row.addWidget(QLabel("Confirm #:"))
        self._vendor_confirm_edit = QLineEdit()
        self._vendor_confirm_edit.setPlaceholderText("Vendor confirmation / PO #")
        self._vendor_confirm_edit.setStyleSheet(
            "QLineEdit { font-size: 11px; border: 1px solid #D1D5DB; border-radius: 3px; padding: 3px; }"
        )
        self._vendor_confirm_edit.editingFinished.connect(self._on_save_confirm_number)
        confirm_row.addWidget(self._vendor_confirm_edit, 1)
        lay.addLayout(confirm_row)

        # Tracking # field
        tracking_row = QHBoxLayout()
        tracking_row.addWidget(QLabel("Tracking #:"))
        self._tracking_edit = QLineEdit()
        self._tracking_edit.setPlaceholderText("Carrier tracking number")
        self._tracking_edit.setStyleSheet(
            "QLineEdit { font-size: 11px; border: 1px solid #D1D5DB; border-radius: 3px; padding: 3px; }"
        )
        self._tracking_edit.editingFinished.connect(self._on_save_tracking_number)
        tracking_row.addWidget(self._tracking_edit, 1)
        lay.addLayout(tracking_row)

        # Notes
        notes_hdr = create_sidebar_section_title("\U0001f4dd Notes")
        notes_hdr.setStyleSheet("font-weight: bold; font-size: 12px; color: #1F2937; margin-top: 8px;")
        lay.addWidget(notes_hdr)
        self._notes_edit = QTextEdit()
        self._notes_edit.setMaximumHeight(120)
        self._notes_edit.setStyleSheet(
            "QTextEdit { background: white; border: 1px solid #E5E7EB; "
            "border-radius: 4px; font-size: 11px; padding: 4px; }"
        )
        lay.addWidget(self._notes_edit)

        # Receive summary
        recv_hdr = create_sidebar_section_title("\U0001f4e6 Receiving Summary")
        recv_hdr.setStyleSheet("font-weight: bold; font-size: 12px; color: #1F2937; margin-top: 8px;")
        lay.addWidget(recv_hdr)
        self._recv_summary = QLabel("")
        self._recv_summary.setWordWrap(True)
        self._recv_summary.setStyleSheet(
            "font-size: 11px; color: #374151; background: white; "
            "border: 1px solid #E5E7EB; border-radius: 4px; padding: 8px;"
        )
        lay.addWidget(self._recv_summary)

        # ── Core Summary panel ────────────────────────────────
        # Surface refundable vendor-core liability up-front so a buyer
        # can never miss the deposit owed back. Auto-hides when the PO
        # has no core-bearing lines.
        self._core_summary_hdr = create_sidebar_section_title("\U0001f504 Core Summary")
        self._core_summary_hdr.setStyleSheet(
            "font-weight: bold; font-size: 12px; color: #1F2937; margin-top: 8px;"
        )
        lay.addWidget(self._core_summary_hdr)
        self._core_summary_lbl = QLabel("")
        self._core_summary_lbl.setWordWrap(True)
        self._core_summary_lbl.setStyleSheet(
            "font-size: 11px; color: #374151; background: #F5F3FF; "
            "border: 1px solid #DDD6FE; border-radius: 4px; padding: 8px;"
        )
        lay.addWidget(self._core_summary_lbl)
        self._core_refund_note = QLabel(
            "Refundable upon return of rebuildable core."
        )
        self._core_refund_note.setWordWrap(True)
        self._core_refund_note.setStyleSheet(
            "font-size: 10px; font-style: italic; color: #6D28D9; "
            "padding: 2px 4px;"
        )
        lay.addWidget(self._core_refund_note)
        # Hidden by default; _refresh shows them when the PO has cores.
        self._core_summary_hdr.setVisible(False)
        self._core_summary_lbl.setVisible(False)
        self._core_refund_note.setVisible(False)

        lay.addStretch()
        return panel

    # ── Bottom action bar ────────────────────────────────────
    def _build_bottom_bar(self) -> QFrame:
        bar, lay = build_bottom_action_bar()

        self._save_btn = QPushButton("\U0001f4be Save Draft")
        self._save_btn.setStyleSheet(style_action_button("primary"))
        self._save_btn.clicked.connect(self._on_save)
        lay.addWidget(self._save_btn)

        self._send_btn = QPushButton("\U0001f4e8 Send PO")
        self._send_btn.setStyleSheet(style_action_button("purple"))
        self._send_btn.clicked.connect(self._on_send)
        lay.addWidget(self._send_btn)

        self._confirm_po_btn = QPushButton("\u2705 Vendor Confirmed")
        self._confirm_po_btn.setStyleSheet(
            "QPushButton { background: #D97706; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #B45309; }"
        )
        self._confirm_po_btn.clicked.connect(self._on_confirm_po)
        lay.addWidget(self._confirm_po_btn)

        self._resend_btn = QPushButton("\U0001f504 Resend PO")
        self._resend_btn.setStyleSheet(style_action_button("purple"))
        self._resend_btn.clicked.connect(self._on_resend)
        lay.addWidget(self._resend_btn)

        self._print_btn = QPushButton("\U0001f5a8\ufe0f Print PO")
        self._print_btn.setStyleSheet(style_action_button("teal"))
        self._print_btn.clicked.connect(self._on_print)
        lay.addWidget(self._print_btn)

        self._receive_btn = QPushButton("\U0001f4e6 Receive Items")
        self._receive_btn.setStyleSheet(style_action_button("success"))
        self._receive_btn.clicked.connect(self._on_receive)
        lay.addWidget(self._receive_btn)

        # Core handling overhaul (migration 108): jump to the vendor core
        # obligations ledger for this PO. Only enabled once there are any
        # obligations on the PO so it doesn't clutter cores-free POs.
        self._cores_btn = QPushButton("\U0001f50d View Cores")
        self._cores_btn.setStyleSheet(style_action_button("purple"))
        self._cores_btn.setToolTip(
            "Open the vendor core obligations ledger for this PO."
        )
        self._cores_btn.clicked.connect(self._on_view_cores)
        lay.addWidget(self._cores_btn)

        self._close_po_btn = QPushButton("\u2705 Close PO")
        self._close_po_btn.setStyleSheet(
            "QPushButton { background: #6B7280; color: white; font-weight: bold; "
            "padding: 6px 14px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #4B5563; }"
        )
        self._close_po_btn.clicked.connect(self._on_close_po)
        lay.addWidget(self._close_po_btn)

        lay.addStretch()

        self._cancel_po_btn = QPushButton("Cancel PO")
        self._cancel_po_btn.setStyleSheet(style_action_button("danger"))
        self._cancel_po_btn.clicked.connect(self._on_cancel_po)
        lay.addWidget(self._cancel_po_btn)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(style_action_button("neutral"))
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)
        return bar

    # ==================================================================
    #  STATE TRACKING FOR AUTO-REFRESH
    # ==================================================================
    def _get_state_snapshot(self) -> dict:
        """Snapshot of PO state for change detection."""
        if not self._po_id:
            return {}
        po = get_purchase_order(self._po_id)
        if not po:
            return {}
        return {
            "po_status": po.get("status"),
            "po_updated": po.get("updated_at"),
            "total": po.get("total"),
        }

    # ==================================================================
    #  REFRESH / POPULATE
    # ==================================================================
    def _refresh(self) -> None:
        self._lines_table.blockSignals(True)
        po = get_purchase_order(self._po_id)
        if not po:
            self._lines_table.blockSignals(False)
            return
        self._po = po
        status = (po.get("status") or "draft").lower()

        # Header
        self._title_lbl.setText(f"PO {po.get('po_number', '')}")
        self._vendor_lbl.setText(f"Vendor: {po.get('vendor_name') or '\u2014'}")
        ordered = self._format_display_dt(po.get("order_date"))
        expected = self._format_display_dt(po.get("expected_date"))
        self._date_lbl.setText(f"Ordered: {ordered}  \u00b7  Expected: {expected}")
        total = float(po.get("total") or 0)
        self._total_lbl.setText(f"${total:,.2f}")

        # Status badge
        bg, fg = _STATUS_COLORS.get(status, ("#E5E7EB", "#374151"))
        self._status_badge.setText(status.upper())
        self._status_badge.setStyleSheet(
            f"font-weight: 600; font-size: 12px; padding: 4px 12px; "
            f"border-radius: 4px; background: {bg}; color: {fg};"
        )

        # Progress strip
        stage_map = {
            "draft": 0, "sent": 1,
            "confirmed": 2, "ordered": 2,
            "partial": 3,
            "received": 4, "closed": 5, "cancelled": -1,
        }
        active = stage_map.get(status, 0)
        for i, lbl in enumerate(self._progress_labels):
            if i < active:
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

        # Vendor combo
        vendor_id = po.get("vendor_id")
        for i in range(self._vendor_combo.count()):
            if self._vendor_combo.itemData(i) == vendor_id:
                self._vendor_combo.setCurrentIndex(i)
                break
        od = po.get("order_date")
        if od:
            self._order_date.setDateTime(self._parse_db_dt(od))
        ed = po.get("expected_date")
        if ed:
            self._expected_date.setDateTime(self._parse_db_dt(ed))
        self._notes_edit.setPlainText(str(po.get("notes") or ""))
        so_for_defaults = None

        # Lines table
        lines = po.get("lines", [])
        self._lines_table.setRowCount(len(lines))
        for i, ln in enumerate(lines):
            def _ro(text):
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            kind = (ln.get("line_kind") or "product").lower()
            is_discount = kind == "discount"
            is_core = kind == "core"

            self._lines_table.setItem(i, _C_ID, _ro(ln.get("id")))
            if is_discount:
                sku_item = _ro("\u2014")  # em dash
                title_item = _ro(ln.get("description") or ln.get("title") or "Discount")
                # Italic + red for discount rows.
                disc_color = QColor("#b91c1c")
                for it in (sku_item, title_item):
                    f = it.font(); f.setItalic(True); it.setFont(f)
                    it.setForeground(disc_color)
                self._lines_table.setItem(i, _C_SKU, sku_item)
                self._lines_table.setItem(i, _C_TITLE, title_item)
            elif is_core:
                # Child "Refundable Core Deposit" row — indented italic purple.
                sku_item = _ro("\u21b3")  # downwards arrow with tip rightwards
                desc = (
                    ln.get("description")
                    or "Core Deposit \u2013 refundable on accepted core return"
                )
                title_item = _ro("    " + desc)
                core_color = QColor("#7C3AED")
                for it in (sku_item, title_item):
                    f = it.font(); f.setItalic(True); it.setFont(f)
                    it.setForeground(core_color)
                title_item.setToolTip(
                    "Auto-generated refundable core deposit line.\n"
                    "Quantity follows the parent product line.\n"
                    "Tracked in vendor_core_obligations for RGA/credit lifecycle."
                )
                self._lines_table.setItem(i, _C_SKU, sku_item)
                self._lines_table.setItem(i, _C_TITLE, title_item)
            else:
                self._lines_table.setItem(i, _C_SKU, _ro(self._vendor_part_for_line(ln)))
                self._lines_table.setItem(i, _C_TITLE, _ro(ln.get("title")))

            qty = int(ln.get("quantity_ordered") or 0)
            recv = int(ln.get("quantity_received") or 0)

            if status == "draft" and not is_discount and not is_core:
                qty_item = QTableWidgetItem(str(qty))
                qty_item.setFlags(qty_item.flags() | Qt.ItemFlag.ItemIsEditable)
            else:
                qty_item = _ro("" if is_discount else qty)
            self._lines_table.setItem(i, _C_QTY, qty_item)

            recv_item = _ro("" if is_discount else recv)
            if not is_discount and not is_core:
                if recv >= qty and qty > 0:
                    recv_item.setForeground(QColor("#059669"))
                elif recv > 0:
                    recv_item.setForeground(QColor("#D97706"))
            elif is_core and recv > 0:
                recv_item.setForeground(QColor("#7C3AED"))
            self._lines_table.setItem(i, _C_RECV, recv_item)

            cost = float(ln.get("unit_cost") or 0)
            if status == "draft" and not is_discount and not is_core:
                cost_item = QTableWidgetItem(f"{cost:.2f}")
                cost_item.setFlags(cost_item.flags() | Qt.ItemFlag.ItemIsEditable)
            elif is_core:
                # For core child rows, "Unit Cost" shows the per-unit deposit.
                cost_item = _ro(f"${cost:,.2f}")
                cost_item.setForeground(QColor("#7C3AED"))
                f = cost_item.font(); f.setItalic(True); cost_item.setFont(f)
            else:
                cost_item = _ro("" if is_discount else f"${cost:,.2f}")
            self._lines_table.setItem(i, _C_COST, cost_item)

            core_chg = float(ln.get("core_charge") or 0)
            if is_core:
                # The child row IS the core; per-unit cell stays blank.
                core_item = _ro("")
            elif status == "draft" and not is_discount:
                # Editable cell stores plain number; renderer below
                # decorates with "$x.xx ea" on read-only rows.
                core_item = QTableWidgetItem(f"{core_chg:.2f}")
                core_item.setFlags(core_item.flags() | Qt.ItemFlag.ItemIsEditable)
            else:
                core_item = _ro("" if is_discount else (f"${core_chg:,.2f} ea" if core_chg else ""))
            if core_chg > 0 and not is_discount and not is_core:
                core_item.setForeground(QColor("#7C3AED"))  # purple → core
            if not is_core:
                core_item.setToolTip(
                    "Refundable vendor core deposit per unit.\n"
                    "A separate 'Core Deposit' child line is auto-generated below "
                    "when the vendor uses separate-line core handling."
                )
            self._lines_table.setItem(i, _C_CORE, core_item)

            # ── Core Total column (qty × core_each) ─────────────
            qty_for_core = int(ln.get("quantity_ordered") or 0)
            core_total_val = float(
                ln.get("core_total") or (qty_for_core * core_chg)
            )
            if is_discount or is_core or core_total_val <= 0:
                core_total_item = _ro("")
            else:
                core_total_item = _ro(f"${core_total_val:,.2f}")
                core_total_item.setForeground(QColor("#7C3AED"))
            core_total_item.setToolTip(
                "Total refundable core liability for this line (Qty × Core Ea.)."
            )
            self._lines_table.setItem(i, _C_CORE_TOTAL, core_total_item)

            lt = float(ln.get("line_total") or 0)
            if is_discount:
                # Show as negative in parentheses (accounting style), red italic.
                total_item = _ro(f"(${abs(lt):,.2f})")
                f = total_item.font(); f.setItalic(True); total_item.setFont(f)
                total_item.setForeground(QColor("#b91c1c"))
            elif is_core:
                total_item = _ro(f"${lt:,.2f}")
                total_item.setForeground(QColor("#7C3AED"))
                f = total_item.font(); f.setItalic(True); total_item.setFont(f)
            else:
                # Line Total now reads the product subtotal only. Cores have
                # their own dedicated column so we no longer roll them into
                # the line total (kept legacy fallback for old data where
                # ``line_total`` already contained the combined amount).
                total_item = _ro(f"${lt:,.2f}")
            self._lines_table.setItem(i, _C_TOTAL, total_item)

            notes = ln.get("notes") or ""
            notes_item = QTableWidgetItem(notes)
            if is_core:
                notes_item.setFlags(notes_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                f = notes_item.font(); f.setItalic(True); notes_item.setFont(f)
                notes_item.setForeground(QColor("#7C3AED"))
            else:
                notes_item.setFlags(notes_item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._lines_table.setItem(i, _C_NOTES, notes_item)

            # Subtle row tint for child core rows so the parent/child
            # hierarchy is unmistakable at a glance.
            if is_core:
                tint = QColor("#F5F3FF")  # very pale lavender
                for col in range(self._lines_table.columnCount()):
                    it = self._lines_table.item(i, col)
                    if it is not None:
                        it.setBackground(tint)

        self._lines_table.resizeColumnsToContents()
        self._lines_table.horizontalHeader().setSectionResizeMode(
            _C_TITLE, QHeaderView.ResizeMode.Stretch
        )

        # Subtotal / core / total
        subtotal = float(po.get("subtotal") or 0)
        core_subtotal = float(po.get("core_subtotal") or 0)
        self._subtotal_lbl.setText(f"Subtotal: ${subtotal:,.2f}")
        if hasattr(self, "_core_lbl"):
            self._core_lbl.setText(f"Cores: ${core_subtotal:,.2f}")
            self._core_lbl.setVisible(core_subtotal > 0)
        if hasattr(self, "_shipping_spin"):
            self._shipping_spin.blockSignals(True)
            self._shipping_spin.setValue(float(po.get("shipping") or 0))
            # Always editable — user explicitly asked to be able to revise
            # shipping costs even after the PO has been sent (the change is
            # logged via update_purchase_order activity).
            self._shipping_spin.setEnabled(True)
            self._shipping_spin.blockSignals(False)
        self._grand_lbl.setText(f"Total: ${total:,.2f}")

        # Receiving summary
        total_ordered = sum(int(l.get("quantity_ordered") or 0) for l in lines)
        total_recv = sum(int(l.get("quantity_received") or 0) for l in lines)
        fully = sum(
            1 for l in lines
            if int(l.get("quantity_received") or 0) >= int(l.get("quantity_ordered") or 0)
            and int(l.get("quantity_ordered") or 0) > 0
        )
        self._recv_summary.setText(
            f"Lines: {len(lines)}\n"
            f"Total ordered: {total_ordered}\n"
            f"Total received: {total_recv}\n"
            f"Outstanding: {max(total_ordered - total_recv, 0)}\n"
            f"Fully received: {fully} / {len(lines)}"
        )

        # ── Core Summary panel (vendor-side refundable cores) ──
        # Aggregate per-line core_charge × qty across product rows. We
        # deliberately exclude ``line_kind='core'`` sibling rows here so
        # we don't double-count (those mirror the same liability).
        core_lines = [
            l for l in lines
            if str(l.get("line_kind") or "product").lower() != "core"
            and float(l.get("core_charge") or 0) > 0
        ]
        core_items = len(core_lines)
        core_qty_owed = sum(int(l.get("quantity_ordered") or 0) for l in core_lines)
        core_liability = sum(
            float(l.get("core_total") or
                  (int(l.get("quantity_ordered") or 0) *
                   float(l.get("core_charge") or 0)))
            for l in core_lines
        )
        has_cores = core_items > 0
        self._core_summary_hdr.setVisible(has_cores)
        self._core_summary_lbl.setVisible(has_cores)
        self._core_refund_note.setVisible(has_cores)
        if has_cores:
            self._core_summary_lbl.setText(
                f"<b>Core Items:</b> {core_items}<br>"
                f"<b>Core Qty Owed:</b> {core_qty_owed}<br>"
                f"<b>Vendor Core Liability:</b> "
                f"<span style='color:#7C3AED;font-weight:bold;'>"
                f"${core_liability:,.2f}</span><br>"
                f"<b>Vendor Return Expected:</b> "
                f"<span style='color:#059669;font-weight:bold;'>YES</span>"
            )

        # SO link
        notes_text = str(po.get("notes") or "")
        so_ref = ""
        if "From SO #" in notes_text:
            try:
                so_ref = notes_text.split("From SO #")[1].split()[0].strip()
            except (IndexError, ValueError):
                pass
        if so_ref:
            so = get_sales_order(int(so_ref))
            so_for_defaults = so
            so_number = (so or {}).get("so_number", f"SO #{so_ref}")
            customer = (so or {}).get("customer_company") or (so or {}).get("customer_name") or "Unknown customer"
            so_status = ((so or {}).get("status") or "draft").replace("_", " ").title()
            self._so_link_val.setText(f"{so_number} \u2014 {customer}\nStatus: {so_status}")
            self._open_so_btn.setEnabled(True)
            self._open_so_btn.setProperty("so_id", int(so_ref))
        else:
            self._so_link_val.setText("Not linked to an SO")
            self._open_so_btn.setEnabled(False)
            self._open_so_btn.setProperty("so_id", None)

        ship_to = str(po.get("ship_to") or "").strip()
        bill_to = str(po.get("bill_to") or "").strip()
        # Drop-ship + no-tags toggles (block signals so we don't recurse into ship-to)
        drop_ship = bool(po.get("drop_ship"))
        no_tags = bool(po.get("no_tags"))
        self._drop_ship_chk.blockSignals(True)
        self._drop_ship_chk.setChecked(drop_ship)
        self._drop_ship_chk.blockSignals(False)
        self._no_tags_chk.setChecked(no_tags)
        self._vendor_message_edit.setPlainText(str(po.get("vendor_message") or ""))
        self._so_for_defaults = so_for_defaults
        if not ship_to:
            ship_to = self._default_ship_to(so_for_defaults, drop_ship=drop_ship)
        if not bill_to:
            bill_to = self._default_bill_to()
        self._ship_to_edit.setPlainText(ship_to)
        self._bill_to_edit.setPlainText(bill_to)

        # Button visibility based on status
        is_draft = status == "draft"
        is_active = status in ("sent", "confirmed", "ordered", "partial")
        is_terminal = status in ("received", "closed", "cancelled")

        self._add_row_frame.setVisible(is_draft)
        self._rm_btn.setVisible(is_draft)
        self._vendor_combo.setEnabled(is_draft)
        self._order_date.setEnabled(is_draft)
        self._expected_date.setEnabled(is_draft or status == "confirmed")
        self._notes_edit.setReadOnly(not is_draft)
        self._ship_to_edit.setReadOnly(not is_draft)
        self._bill_to_edit.setReadOnly(not is_draft)
        self._ship_to_btn.setEnabled(is_draft)
        self._bill_to_btn.setEnabled(is_draft)
        self._vendor_message_edit.setReadOnly(not is_draft)
        self._drop_ship_chk.setEnabled(is_draft)
        self._no_tags_chk.setEnabled(is_draft)

        self._save_btn.setVisible(is_draft)
        self._send_btn.setVisible(is_draft)
        self._confirm_po_btn.setVisible(status == "sent")
        self._resend_btn.setVisible(status in ("sent", "confirmed", "ordered"))
        self._print_btn.setVisible(status != "cancelled")
        self._receive_btn.setVisible(is_active)
        self._close_po_btn.setVisible(
            status in ("sent", "confirmed", "ordered", "partial", "received")
        )
        self._cancel_po_btn.setVisible(not is_terminal)

        # Update communication history
        self._refresh_comm_history(po)


        self._lines_table.blockSignals(False)

    # ==================================================================
    #  LINE EDITING (Draft only)
    # ==================================================================
    def _on_cell_changed(self, row: int, col: int) -> None:
        if col not in (_C_QTY, _C_COST, _C_CORE, _C_NOTES):
            return
        id_item = self._lines_table.item(row, _C_ID)
        if not id_item:
            return
        line_id = int(id_item.text())

        if col == _C_QTY:
            raw = self._lines_table.item(row, _C_QTY).text().strip()
            try:
                new_qty = int(raw)
            except ValueError:
                self._refresh()
                return
            if new_qty < 1:
                self._refresh()
                return
            update_po_line(line_id, quantity_ordered=new_qty)

        elif col == _C_COST:
            raw = (
                self._lines_table.item(row, _C_COST)
                .text()
                .replace("$", "")
                .replace(",", "")
                .strip()
            )
            try:
                new_cost = float(raw)
            except ValueError:
                self._refresh()
                return
            if new_cost < 0:
                self._refresh()
                return
            update_po_line(line_id, unit_cost=new_cost)

        elif col == _C_CORE:
            raw = (
                self._lines_table.item(row, _C_CORE)
                .text()
                .replace("$", "")
                .replace(",", "")
                .strip()
            )
            try:
                new_core = float(raw) if raw else 0.0
            except ValueError:
                self._refresh()
                return
            if new_core < 0:
                self._refresh()
                return
            update_po_line(line_id, core_charge=new_core)

        elif col == _C_NOTES:
            text = self._lines_table.item(row, _C_NOTES).text().strip()
            update_po_line(line_id, notes=text or None)
            return  # no full refresh needed for notes

        self._lines_table.blockSignals(True)
        self._refresh()
        self._lines_table.blockSignals(False)

    def _current_po_vendor_id(self) -> int | None:
        """Return the vendor_id currently selected on this PO (combo first, then DB)."""
        try:
            cd = self._vendor_combo.currentData()
            if cd:
                return int(cd)
        except (TypeError, ValueError, AttributeError):
            pass
        if self._po:
            try:
                vid = self._po.get("vendor_id")
                return int(vid) if vid else None
            except (TypeError, ValueError):
                return None
        return None

    def _on_new_product(self) -> None:
        """Open the Edit Product dialog to create a new product, then add it as a PO line."""
        try:
            from .edit_product_dialog import EditProductDialog
        except ImportError:
            QMessageBox.warning(self, "Unavailable", "Product editor not available.")
            return

        dlg = EditProductDialog(create_mode=True, parent=self)
        if dlg.exec() != EditProductDialog.DialogCode.Accepted:
            return

        # Retrieve the new product id the dialog exposes
        new_id = (
            getattr(dlg, "_product_id", None)
            or getattr(dlg, "product_id", None)
            or getattr(dlg, "saved_product_id", None)
        )
        if not new_id:
            # Fall back: re-search by the SKU if the dialog exposes it
            new_sku = getattr(dlg, "sku", None)
            if new_sku:
                try:
                    results = browse_products(search=new_sku, limit=5)
                    if results:
                        new_id = results[0].get("id")
                except Exception:
                    pass

        if not new_id:
            QMessageBox.information(
                self, "Product Created",
                "Product saved. Use the search bar to find and add it to this PO."
            )
            return

        try:
            results = browse_products(search=str(new_id), limit=5)
            prod = next((r for r in results if str(r.get("id")) == str(new_id)), None)
            if not prod and results:
                prod = results[0]
        except Exception:
            prod = None

        if not prod:
            QMessageBox.information(
                self, "Product Created",
                "Product saved. Use the search bar to find and add it to this PO."
            )
            return

        # Inject into search results and trigger _on_add_line
        self._search_results = [prod]
        self._search_table.hide()
        self._qty_spin.setValue(1)
        cost = float(prod.get("cost") or prod.get("pai_cost") or 0)
        self._cost_spin.setValue(cost)
        self._on_add_line()

    def _on_search_products(self) -> None:
        """Search products and populate the picker results table."""
        query = self._product_search.text().strip()
        if not query:
            self._search_table.hide()
            self._search_count_label.setText("")
            return
        try:
            results = browse_products(search=query, limit=50)
        except Exception as exc:
            QMessageBox.warning(self, "Search failed", str(exc))
            return
        self._search_results = results

        # Vendor name lookup (for the Preferred Vendor column + mismatch tooltip)
        vendor_names: dict[int, str] = {}
        try:
            for v in get_all_vendors():
                vid = v.get("id")
                if vid is not None:
                    vendor_names[int(vid)] = v.get("name") or ""
        except Exception:
            pass

        current_vendor_id = self._current_po_vendor_id()

        self._search_table.setRowCount(len(results))
        for i, p in enumerate(results):
            def mk(text, align_right: bool = False) -> QTableWidgetItem:
                it = QTableWidgetItem("" if text is None else str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if align_right:
                    it.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                return it

            pid = p.get("id") or p.get("product_id") or ""
            self._search_table.setItem(i, 0, mk(pid))
            self._search_table.setItem(i, 1, mk(p.get("sku", "")))
            self._search_table.setItem(i, 2, mk(p.get("title", "")))

            try:
                pref_vid = int(p.get("preferred_vendor_id") or 0) or None
            except (TypeError, ValueError):
                pref_vid = None
            pref_name = vendor_names.get(pref_vid, "") if pref_vid else ""
            vendor_item = mk(pref_name or "\u2014")
            mismatch = (
                current_vendor_id is not None
                and pref_vid is not None
                and pref_vid != current_vendor_id
            )
            if mismatch:
                vendor_item.setForeground(QColor("#92400e"))
                vendor_item.setBackground(QColor("#fef3c7"))
                tooltip = (
                    f"Preferred vendor: {pref_name or 'Vendor #' + str(pref_vid)}.\n"
                    "Different from this PO's vendor — adding will prompt confirmation."
                )
                vendor_item.setToolTip(tooltip)
            self._search_table.setItem(i, 3, vendor_item)

            qoh = int(p.get("qty_on_hand") or 0)
            qoh_item = mk(str(qoh), align_right=True)
            if qoh <= 0:
                qoh_item.setForeground(QColor("#c62828"))
            self._search_table.setItem(i, 4, qoh_item)

            cost = float(p.get("cost") or p.get("pai_cost") or 0)
            self._search_table.setItem(i, 5, mk(f"${cost:,.2f}", align_right=True))

        self._search_count_label.setText(
            f"Found {len(results)} result{'s' if len(results) != 1 else ''}"
        )
        self._search_table.show()
        if results:
            self._search_table.selectRow(0)

    def _on_search_row_changed(self) -> None:
        """Auto-fill cost spin from the selected product when user hasn't typed one."""
        row = self._search_table.currentRow()
        if row < 0 or row >= len(self._search_results):
            return
        if self._cost_spin.value() == 0:
            p = self._search_results[row]
            try:
                self._cost_spin.setValue(
                    float(p.get("cost") or p.get("pai_cost") or 0)
                )
            except (TypeError, ValueError):
                pass

    def _on_add_line(self) -> None:
        if not self._po_id:
            # Try to auto-create a new PO if vendor is selected
            vendor_id = self._vendor_combo.currentData()
            if not vendor_id:
                QMessageBox.warning(self, "Validation", "Please select a vendor before adding items.")
                return
            from db import create_purchase_order
            # Use current UI values for order_date/expected_date if available
            order_date = self._order_date.dateTime().toString("yyyy-MM-dd HH:mm:ss") if hasattr(self, "_order_date") else None
            expected_date = self._expected_date.dateTime().toString("yyyy-MM-dd HH:mm:ss") if hasattr(self, "_expected_date") else None
            ship_to = self._ship_to_edit.toPlainText().strip() if hasattr(self, "_ship_to_edit") else None
            bill_to = self._bill_to_edit.toPlainText().strip() if hasattr(self, "_bill_to_edit") else None
            notes = self._notes_edit.toPlainText().strip() if hasattr(self, "_notes_edit") else None
            result = create_purchase_order(
                vendor_id=vendor_id,
                order_date=order_date,
                expected_date=expected_date,
                ship_to=ship_to,
                bill_to=bill_to,
                notes=notes,
            )
            if not result.get("success"):
                QMessageBox.warning(self, "Error", f"Could not create PO: {result.get('error','Unknown error')}")
                return
            self._po_id = result["po_id"]
            self._refresh()

        # 1) Prefer the row selected in the search-results table.
        product: dict | None = None
        row = self._search_table.currentRow()
        # Auto-pick the first result when nothing is explicitly selected
        if row < 0 and self._search_table.isVisible() and self._search_results:
            row = 0
            self._search_table.selectRow(0)
        if (
            self._search_table.isVisible()
            and 0 <= row < len(self._search_results)
        ):
            product = self._search_results[row]
        else:
            # 2) Fallback: typed text → exact SKU match (preserve muscle memory).
            search_text = self._product_search.text().strip()
            if not search_text:
                QMessageBox.information(
                    self, "Pick a product",
                    "Type a search and click Search, then select a row before adding.",
                )
                return
            try:
                matches = browse_products(search=search_text, limit=20)
            except Exception as exc:
                QMessageBox.warning(self, "Search failed", str(exc))
                return
            sku_part = search_text.split(" - ")[0].strip().lower()
            for p in matches:
                if (p.get("sku") or "").lower() == sku_part:
                    product = p
                    break
            if not product:
                # Show results so the user can pick.
                self._on_search_products()
                QMessageBox.information(
                    self, "Pick a product",
                    "More than one match — select a row in the results and click Add Line.",
                )
                return

        if not product:
            return

        # Preferred-vendor mismatch soft-confirm
        try:
            pref_vid = int(product.get("preferred_vendor_id") or 0) or None
        except (TypeError, ValueError):
            pref_vid = None
        current_vid = self._current_po_vendor_id()
        if pref_vid and current_vid and pref_vid != current_vid:
            pref_name = ""
            try:
                for v in get_all_vendors():
                    if int(v.get("id") or 0) == pref_vid:
                        pref_name = v.get("name") or ""
                        break
            except Exception:
                pass
            prompt = (
                f"'{product.get('sku', '')}' has a preferred vendor of "
                f"{pref_name or '#' + str(pref_vid)}, which differs from this PO's vendor."
                "\n\nAdd it to this PO anyway?"
            )
            reply = QMessageBox.question(
                self, "Preferred vendor mismatch", prompt,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        qty = self._qty_spin.value()
        cost = self._cost_spin.value()
        if cost == 0:
            cost = float(product.get("cost") or product.get("pai_cost") or 0)
        result = add_po_line(self._po_id, product["id"], qty, cost)
        if not result.get("success"):
            QMessageBox.warning(self, "Add Line Failed", f"Could not add line:\n{result.get('error', 'Unknown error')}")
            return

        # Reset for next entry
        self._product_search.clear()
        self._search_table.hide()
        self._search_table.setRowCount(0)
        self._search_results = []
        self._search_count_label.setText("")
        self._qty_spin.setValue(1)
        self._cost_spin.setValue(0)
        self._product_search.setFocus()
        self._refresh()

    def _on_remove_line(self) -> None:
        row = self._lines_table.currentRow()
        if row < 0:
            return
        id_item = self._lines_table.item(row, _C_ID)
        if not id_item:
            return
        remove_po_line(int(id_item.text()))
        self._refresh()

    def _on_add_discount_line(self) -> None:
        """Open the shared DiscountLineDialog and append a negative line."""
        if not self._po_id:
            # Try to auto-create a new PO if vendor is selected
            vendor_id = self._vendor_combo.currentData()
            if not vendor_id:
                QMessageBox.warning(self, "Validation", "Please select a vendor before adding a discount.")
                return
            from db import create_purchase_order
            order_date = self._order_date.dateTime().toString("yyyy-MM-dd HH:mm:ss") if hasattr(self, "_order_date") else None
            expected_date = self._expected_date.dateTime().toString("yyyy-MM-dd HH:mm:ss") if hasattr(self, "_expected_date") else None
            ship_to = self._ship_to_edit.toPlainText().strip() if hasattr(self, "_ship_to_edit") else None
            bill_to = self._bill_to_edit.toPlainText().strip() if hasattr(self, "_bill_to_edit") else None
            notes = self._notes_edit.toPlainText().strip() if hasattr(self, "_notes_edit") else None
            result = create_purchase_order(
                vendor_id=vendor_id,
                order_date=order_date,
                expected_date=expected_date,
                ship_to=ship_to,
                bill_to=bill_to,
                notes=notes,
            )
            if not result.get("success"):
                QMessageBox.warning(self, "Error", f"Could not create PO: {result.get('error','Unknown error')}")
                return
            self._po_id = result["po_id"]
            self._refresh()
        # Compute current product subtotal for % preview (exclude existing discount rows).
        subtotal = 0.0
        for r in range(self._lines_table.rowCount()):
            it = self._lines_table.item(r, _C_TOTAL)
            if not it:
                continue
            txt = it.text().strip()
            if txt.startswith("(") and txt.endswith(")"):
                continue  # discount row already
            # Strip $, , and spaces
            cleaned = txt.replace("$", "").replace(",", "").strip()
            try:
                subtotal += float(cleaned)
            except ValueError:
                pass
        dlg = DiscountLineDialog(
            self,
            doc_type="po",
            product_subtotal=subtotal,
            default_description="Vendor volume discount",
        )
        if dlg.exec() != dlg.DialogCode.Accepted or not dlg.result_payload:
            return
        payload = dlg.result_payload
        result = add_discount_line(
            "po",
            self._po_id,
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
        self._refresh()

    # ==================================================================
    #  ACTIONS
    # ==================================================================
    def _on_save(self) -> None:
        if not self._po_id:
            return
        vendor_id = self._vendor_combo.currentData()
        if not vendor_id:
            QMessageBox.warning(self, "Validation", "Please select a vendor.")
            return
        update_purchase_order(
            self._po_id,
            vendor_id=vendor_id,
            order_date=self._order_date.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
            expected_date=self._expected_date.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
            ship_to=self._ship_to_edit.toPlainText().strip() or None,
            bill_to=self._bill_to_edit.toPlainText().strip() or None,
            notes=self._notes_edit.toPlainText().strip() or None,
            vendor_message=self._build_auto_vendor_message() or None,
            drop_ship=1 if self._drop_ship_chk.isChecked() else 0,
            no_tags=1 if self._no_tags_chk.isChecked() else 0,
            shipping=float(self._shipping_spin.value()) if hasattr(self, "_shipping_spin") else 0,
        )
        self._refresh()
        self.po_changed.emit()

    def _on_shipping_changed(self) -> None:
        if not self._po_id:
            return
        try:
            update_purchase_order(
                self._po_id,
                shipping=float(self._shipping_spin.value()),
            )
            self._refresh()
            self.po_changed.emit()
        except Exception:
            pass

    def _on_send(self) -> None:
        if not self._po_id:
            return
        po = self._po
        lines = po.get("lines", []) if po else []
        if not lines:
            QMessageBox.warning(self, "No Lines", "Add line items before sending.")
            return
        vendor_id = self._vendor_combo.currentData()
        if not vendor_id:
            QMessageBox.warning(self, "Validation", "Select a vendor first.")
            return

        # Look up vendor email so we can confirm it before sending.
        from db import get_vendor
        from jaks_inventory.utils.notification_service import send_po_to_vendor, email_configured
        vendor_row = get_vendor(vendor_id) or {}
        vendor_email = (vendor_row.get("contact_email") or "").strip()

        send_email = email_configured() and bool(vendor_email)
        # User can pick whether to actually email the vendor or just flip
        # the status to SENT (some vendors prefer phone/portal handoff).
        prompt = (
            f"Send PO {po.get('po_number', '')}?\n\n"
            f"Vendor: {po.get('vendor_name', '')}\n"
            f"Lines: {len(lines)}\n"
            f"Total: ${float(po.get('total') or 0):,.2f}\n\n"
        )
        if send_email:
            prompt += (
                f"Email PDF to {vendor_email} and mark as SENT,\n"
                "or mark as SENT without sending email?"
            )
        elif not email_configured():
            prompt += "SMTP is not configured — PO will be marked SENT only."
        else:
            prompt += "Vendor has no contact email — PO will be marked SENT only."

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Send Purchase Order")
        msg_box.setText(prompt)
        msg_box.setIcon(QMessageBox.Icon.Question)
        if send_email:
            email_btn = msg_box.addButton("Email && Mark Sent",
                                          QMessageBox.ButtonRole.AcceptRole)
            mark_btn = msg_box.addButton("Mark Sent Only",
                                         QMessageBox.ButtonRole.ActionRole)
        else:
            email_btn = None
            mark_btn = msg_box.addButton("Mark Sent",
                                         QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg_box.addButton(QMessageBox.StandardButton.Cancel)
        msg_box.exec()
        clicked = msg_box.clickedButton()
        if clicked is cancel_btn or clicked is None:
            return
        do_email = (clicked is email_btn)
        # Persist header + tags before sending
        save_result = update_purchase_order(
            self._po_id,
            vendor_id=vendor_id,
            order_date=self._order_date.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
            expected_date=self._expected_date.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
            ship_to=self._ship_to_edit.toPlainText().strip() or None,
            bill_to=self._bill_to_edit.toPlainText().strip() or None,
            notes=self._notes_edit.toPlainText().strip() or None,
            vendor_message=self._build_auto_vendor_message() or None,
            drop_ship=1 if self._drop_ship_chk.isChecked() else 0,
            no_tags=1 if self._no_tags_chk.isChecked() else 0,
            shipping=float(self._shipping_spin.value()) if hasattr(self, "_shipping_spin") else 0,
        )
        if not save_result.get("success"):
            QMessageBox.warning(self, "Error", save_result.get("error", "Failed to save PO."))
            return
        from datetime import datetime as _dt
        update_purchase_order(
            self._po_id,
            sent_at=_dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            sent_method="email" if do_email else "manual",
        )
        update_po_status(self._po_id, "sent")

        # Send email with PDF attachment (best-effort)
        if do_email:
            try:
                result = send_po_to_vendor(self._po_id)
                if result.get("success"):
                    QMessageBox.information(
                        self,
                        "PO Sent",
                        f"Purchase Order emailed to {vendor_email}.",
                    )
                else:
                    QMessageBox.warning(
                        self,
                        "Email Failed",
                        f"PO marked as sent, but email delivery failed:\n\n{result.get('error', 'Unknown error')}",
                    )
            except Exception as exc:
                QMessageBox.warning(
                    self,
                    "Email Failed",
                    f"PO marked as sent, but email delivery failed:\n\n{exc}",
                )
        else:
            QMessageBox.information(
                self,
                "PO Sent",
                f"Purchase Order {po.get('po_number', '')} marked as SENT.",
            )

        self._refresh()
        self.po_changed.emit()

    def _on_confirm_po(self) -> None:
        """Mark PO as Confirmed — vendor acknowledged the order."""
        if not self._po_id:
            return
        from datetime import datetime as _dt
        reply = QMessageBox.question(
            self,
            "Confirm Purchase Order",
            "Mark this PO as CONFIRMED?\n\nRecord that the vendor has acknowledged this order.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        update_purchase_order(
            self._po_id,
            confirmed_at=_dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        update_po_status(self._po_id, "confirmed")
        self._refresh()
        self.po_changed.emit()

    def _on_resend(self) -> None:
        """Resend PO — increments resend_count and records timestamp."""
        if not self._po_id or not self._po:
            return
        po = self._po
        lines = po.get("lines", [])
        from db import get_vendor
        from jaks_inventory.utils.notification_service import send_po_to_vendor, email_configured
        vendor_row = get_vendor(po.get("vendor_id")) if po.get("vendor_id") else {}
        vendor_email = ((vendor_row or {}).get("contact_email") or "").strip()
        send_email = email_configured() and bool(vendor_email)

        prompt = (
            f"Resend PO {po.get('po_number', '')} to vendor?\n\n"
            f"Vendor: {po.get('vendor_name', '')}\n"
            f"Lines: {len(lines)}\n"
            f"Total: ${float(po.get('total') or 0):,.2f}\n\n"
        )
        if send_email:
            prompt += f"An email with the PO PDF will be sent to:\n  {vendor_email}"
        else:
            prompt += "(No email will be sent — SMTP or vendor email is missing.)"
        reply = QMessageBox.question(
            self, "Resend Purchase Order", prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from datetime import datetime as _dt
        resend_count = int(po.get("resend_count") or 0) + 1
        update_purchase_order(
            self._po_id,
            last_resent_at=_dt.now().strftime("%Y-%m-%d %H:%M:%S"),
            resend_count=resend_count,
        )
        if send_email:
            try:
                result = send_po_to_vendor(self._po_id)
                if not result.get("success"):
                    QMessageBox.warning(
                        self, "Email Failed",
                        f"Resend recorded, but email delivery failed:\n\n{result.get('error', 'Unknown error')}",
                    )
            except Exception as exc:
                QMessageBox.warning(self, "Email Failed", str(exc))
        self._refresh()
        self.po_changed.emit()

    def _refresh_comm_history(self, po: dict) -> None:
        """Update the communication history label in the sidebar."""
        if not hasattr(self, "_comm_history_lbl"):
            return
        lines = []
        sent_at = po.get("sent_at")
        sent_by = po.get("sent_by") or ""
        sent_method = po.get("sent_method") or "manual"
        if sent_at:
            sent_str = str(sent_at)[:16]
            lines.append(f"Sent: {sent_str}")
            if sent_by:
                lines.append(f"By: {sent_by}")
            lines.append(f"Method: {sent_method.title()}")
        else:
            lines.append("Not yet sent.")

        confirmed_at = po.get("confirmed_at")
        if confirmed_at:
            lines.append(f"Confirmed: {str(confirmed_at)[:16]}")

        resend_count = int(po.get("resend_count") or 0)
        if resend_count:
            last_resent = po.get("last_resent_at") or ""
            lines.append(f"Resent: {resend_count}x" + (f" (last: {str(last_resent)[:16]})" if last_resent else ""))

        vendor_confirm = po.get("vendor_confirm_number") or ""
        if vendor_confirm:
            lines.append(f"Vendor Confirm #: {vendor_confirm}")

        tracking = po.get("tracking_number") or ""
        if tracking:
            lines.append(f"Tracking: {tracking}")

        self._comm_history_lbl.setText("\n".join(lines) if lines else "No communication history.")

        # Populate editable fields
        if hasattr(self, "_vendor_confirm_edit"):
            self._vendor_confirm_edit.blockSignals(True)
            self._vendor_confirm_edit.setText(str(vendor_confirm))
            self._vendor_confirm_edit.blockSignals(False)
        if hasattr(self, "_tracking_edit"):
            self._tracking_edit.blockSignals(True)
            self._tracking_edit.setText(str(tracking))
            self._tracking_edit.blockSignals(False)

    def _on_save_confirm_number(self) -> None:
        if not self._po_id:
            return
        val = self._vendor_confirm_edit.text().strip()
        update_purchase_order(self._po_id, vendor_confirm_number=val or None)

    def _on_save_tracking_number(self) -> None:
        if not self._po_id:
            return
        val = self._tracking_edit.text().strip()
        update_purchase_order(self._po_id, tracking_number=val or None)


    def _on_receive(self) -> None:
        if not self._po_id:
            return
        dlg = ReceiveDialog(po_id=self._po_id, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh()
            self.po_changed.emit()

    def _on_view_cores(self) -> None:
        """Open a read-only ledger of vendor core obligations for this PO."""
        if not self._po_id:
            return
        try:
            from db import list_vendor_core_obligations
            obligations = list_vendor_core_obligations(po_id=self._po_id)
        except Exception as e:
            QMessageBox.warning(self, "Core Obligations", f"Could not load: {e}")
            return
        if not obligations:
            QMessageBox.information(
                self,
                "Core Obligations",
                "No vendor core obligations on this PO.\n\n"
                "Cores only appear here when a product with core handling is "
                "ordered from a vendor that bills cores separately.",
            )
            return

        from PySide6.QtWidgets import QDialog as _QDialog, QVBoxLayout, QTableWidget, \
            QTableWidgetItem, QHeaderView, QDialogButtonBox
        dlg = _QDialog(self)
        dlg.setWindowTitle(f"Vendor Core Obligations — PO {self._po.get('po_number','')}")
        dlg.resize(1180, 460)
        lay = QVBoxLayout(dlg)
        intro = QLabel(
            "Open obligations are deposits the vendor still owes you for "
            "returned rebuildable cores. Use the Receive Items dialog to "
            "advance their status."
        )
        intro.setStyleSheet("color: #6B7280; font-size: 11px;")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        cols = [
            "Status", "SKU", "Product", "Qty Ordered", "Core / Unit",
            "Total Liability", "Expected Refund", "Vendor RGA",
            "Vendor Credit #", "Credit Recv'd", "Notes",
        ]
        table = QTableWidget(len(obligations), len(cols))
        table.setHorizontalHeaderLabels(cols)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.horizontalHeader().setSectionResizeMode(len(cols) - 1, QHeaderView.ResizeMode.Stretch)

        OPEN_STATUSES = {"pending", "awaiting_return", "rga_created", "shipped", "vendor_received"}
        for r, ob in enumerate(obligations):
            status = str(ob.get("status") or "pending").lower()
            qty = int(ob.get("quantity") or 0)
            qty_ordered = int(ob.get("po_qty_ordered") or qty)
            amt = float(ob.get("core_amount") or 0)
            credit = float(ob.get("credit_received") or 0)
            total_liability = qty * amt
            # Until the vendor credits us, the expected refund equals
            # the open balance (liability minus any credit applied so
            # far). When fully credited, expected refund is $0.
            expected_refund = max(total_liability - credit, 0)
            cells = [
                status.replace("_", " ").title(),
                str(ob.get("product_sku") or ""),
                str(ob.get("product_title") or ""),
                str(qty_ordered),
                f"${amt:,.2f}",
                f"${total_liability:,.2f}",
                f"${expected_refund:,.2f}",
                str(ob.get("rga_number") or ""),
                str(ob.get("vendor_credit_number") or ""),
                f"${credit:,.2f}",
                str(ob.get("notes") or ""),
            ]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(val)
                if status in OPEN_STATUSES and expected_refund > 0:
                    it.setBackground(QColor(255, 251, 235))  # amber tint
                elif status in ("credited",):
                    it.setBackground(QColor(236, 253, 245))  # green tint
                elif status in ("written_off", "rejected"):
                    it.setBackground(QColor(243, 244, 246))  # gray tint
                table.setItem(r, c, it)

        lay.addWidget(table, 1)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(dlg.reject)
        btns.accepted.connect(dlg.accept)
        lay.addWidget(btns)
        dlg.exec()

    def _on_close_po(self) -> None:
        if not self._po_id or not self._po:
            return
        po = self._po
        # Only count physical product lines toward outstanding qty. Core deposit
        # and discount lines are accounting-only and have no receivable inventory.
        lines = [
            l for l in po.get("lines", [])
            if str(l.get("line_kind") or "product").lower() not in ("core", "discount")
        ]
        total_ordered = sum(int(l.get("quantity_ordered") or 0) for l in lines)
        total_recv = sum(int(l.get("quantity_received") or 0) for l in lines)
        outstanding = max(total_ordered - total_recv, 0)

        if outstanding > 0:
            # Must pick a close reason when there is unreceived qty
            from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout
            reason_dlg = QDialog(self)
            reason_dlg.setWindowTitle("Close PO with Outstanding Items")
            reason_dlg.setMinimumWidth(400)
            rl = QFormLayout(reason_dlg)
            rl.addRow(QLabel(
                f"<b>{outstanding} item(s) have not been received.</b><br>"
                "Select a reason to close with outstanding qty:"
            ))
            reason_combo = QComboBox()
            reason_combo.addItem("Select a reason...", "")
            reason_combo.addItem("Cancelled by vendor", "cancelled_by_vendor")
            reason_combo.addItem("Customer cancelled order", "customer_cancelled")
            reason_combo.addItem("Short-shipped / written off", "short_shipped")
            reason_combo.addItem("Converted to return / RGA", "converted_rga")
            reason_combo.addItem("Other", "other")
            rl.addRow("Reason:", reason_combo)
            btns = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            btns.accepted.connect(reason_dlg.accept)
            btns.rejected.connect(reason_dlg.reject)
            rl.addRow(btns)
            if reason_dlg.exec() != QDialog.DialogCode.Accepted:
                return
            reason = reason_combo.currentData()
            if not reason:
                QMessageBox.warning(self, "Required", "Please select a close reason.")
                return
            update_purchase_order(self._po_id, close_reason=reason)
        else:
            reply = QMessageBox.question(
                self,
                "Close PO",
                "Close this Purchase Order? All items received — mark as complete.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        update_po_status(self._po_id, "closed")
        self._refresh()
        self.po_changed.emit()

    def _on_cancel_po(self) -> None:
        if not self._po_id:
            return
        reply = QMessageBox.question(
            self,
            "Cancel PO",
            "Cancel this Purchase Order? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        update_po_status(self._po_id, "cancelled")
        self._refresh()
        self.po_changed.emit()

    def _open_linked_so(self) -> None:
        so_id = self._open_so_btn.property("so_id")
        if not so_id:
            return
        try:
            from jaks_inventory.ui.so_detail_dialog import open_so_detail

            open_so_detail(int(so_id), parent=self)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _on_print(self) -> None:
        if not self._po:
            return
        # Persist current form edits (vendor message, no-tags, drop-ship,
        # ship-to/bill-to, dates, notes) so the PDF reflects what the user sees.
        if self._po_id and self._vendor_combo.currentData():
            try:
                update_purchase_order(
                    self._po_id,
                    vendor_id=self._vendor_combo.currentData(),
                    order_date=self._order_date.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
                    expected_date=self._expected_date.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
                    ship_to=self._ship_to_edit.toPlainText().strip() or None,
                    bill_to=self._bill_to_edit.toPlainText().strip() or None,
                    notes=self._notes_edit.toPlainText().strip() or None,
                    vendor_message=self._build_auto_vendor_message() or None,
                    drop_ship=1 if self._drop_ship_chk.isChecked() else 0,
                    no_tags=1 if self._no_tags_chk.isChecked() else 0,
                    shipping=float(self._shipping_spin.value()) if hasattr(self, "_shipping_spin") else 0,
                )
                self._refresh()
            except Exception:
                pass
        try:
            pdf_path = self._build_po_pdf()
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf_path)))
        except Exception as e:
            QMessageBox.warning(self, "Print PO", f"Could not generate PO PDF:\n{e}")

    @staticmethod
    def _parse_db_dt(value) -> QDateTime:
        raw = str(value or "").strip()
        if not raw:
            return QDateTime.currentDateTime()
        for fmt in ("yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd HH:mm", "yyyy-MM-dd"):
            parsed = QDateTime.fromString(raw[:19], fmt)
            if parsed.isValid():
                return parsed
        date_only = QDate.fromString(raw[:10], "yyyy-MM-dd")
        if date_only.isValid():
            return QDateTime(date_only, QDateTime.currentDateTime().time())
        return QDateTime.currentDateTime()

    @classmethod
    def _format_display_dt(cls, value) -> str:
        if not value:
            return "\u2014"
        return cls._parse_db_dt(value).toString("M/d/yyyy HH:mm")

    def _build_po_pdf(self) -> Path:
        po = self._po or get_purchase_order(self._po_id)
        if not po:
            raise RuntimeError("Purchase order not found")

        from jaks_inventory.utils.pdf_purchase_order import generate_purchase_order_pdf

        return Path(generate_purchase_order_pdf(po))

    @staticmethod
    def _vendor_part_for_line(line: dict) -> str:
        return str(
            line.get("supplier_part_number")
            or line.get("pai_sku")
            or line.get("oem_part_number")
            or line.get("sku")
            or ""
        )

    @staticmethod
    def _default_bill_to() -> str:
        # Prefer explicit override from PO Settings.
        explicit = (get_setting("po_default_bill_to", "") or "").strip()
        if explicit:
            return explicit
        name = get_setting("company_name", "JAKS Inventory")
        address = get_setting("company_address", "")
        city = get_setting("company_city", "")
        state = get_setting("company_state", "")
        zipcode = get_setting("company_zip", "")
        lines = [name]
        if address:
            lines.append(address)
        city_state = ", ".join([x for x in [city, state] if x]).strip(", ")
        if zipcode:
            city_state = f"{city_state} {zipcode}".strip()
        if city_state:
            lines.append(city_state)
        return "\n".join(lines)

    def _pick_ship_to_address(self) -> None:
        """Show a menu of saved ship-to addresses for quick selection."""
        self._pick_saved_address("po_saved_ship_to_addresses", self._ship_to_edit)

    def _pick_bill_to_address(self) -> None:
        """Show a menu of saved bill-to addresses for quick selection."""
        self._pick_saved_address("po_saved_bill_to_addresses", self._bill_to_edit)

    def _pick_saved_address(self, setting_key: str, target_edit: QTextEdit) -> None:
        import json
        raw = get_setting(setting_key, "[]") or "[]"
        try:
            addresses = json.loads(raw)
        except Exception:
            addresses = []
        if not addresses:
            QMessageBox.information(
                self, "No Saved Addresses",
                "No saved addresses found.\n\nAdd them in Settings → Purchase Orders.",
            )
            return
        menu = QMenu(self)
        for entry in addresses:
            name = entry.get("name") or entry.get("address", "")[:40]
            action = menu.addAction(name)
            action.setData(entry.get("address", ""))
        btn = self._ship_to_btn if target_edit is self._ship_to_edit else self._bill_to_btn
        chosen = menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        if chosen:
            target_edit.setPlainText(chosen.data())

    def _default_ship_to(self, so: dict | None, drop_ship: bool = False) -> str:
        """Build the default ship-to text.

        Normal POs always default to the company's PO Settings ship-to address
        (or the company billing address as a fallback).  Only when ``drop_ship``
        is True do we pull the linked SO's customer ship-to address.
        """
        if drop_ship and so:
            customer_ship = self._customer_ship_to_for_so(so)
            if customer_ship:
                return customer_ship

        explicit = (get_setting("po_default_ship_to", "") or "").strip()
        if explicit:
            return explicit
        return self._default_bill_to()

    @staticmethod
    def _customer_ship_to_for_so(so: dict) -> str:
        """Return the SO customer's ship-to block (or empty if missing)."""
        from db import get_customer
        cust_id = so.get("customer_id")
        cust = get_customer(cust_id) if cust_id else None
        if not cust:
            return ""
        name = cust.get("company") or cust.get("name") or ""
        addr = cust.get("ship_to_address") or cust.get("address") or ""
        city = cust.get("ship_to_city") or cust.get("city") or ""
        state = cust.get("ship_to_state") or cust.get("state") or ""
        zipc = cust.get("ship_to_zip") or cust.get("zip") or ""
        lines = []
        if name:
            lines.append(str(name))
        if addr:
            lines.append(str(addr))
        city_state = ", ".join([x for x in [city, state] if x]).strip(", ")
        if zipc:
            city_state = f"{city_state} {zipc}".strip()
        if city_state:
            lines.append(city_state)
        return "\n".join(lines)

    def _on_drop_ship_toggled(self, checked: bool) -> None:
        """When drop-ship is toggled, swap ship-to between SO customer and default."""
        so = getattr(self, "_so_for_defaults", None)
        if checked and not so:
            QMessageBox.information(
                self,
                "No Linked Sales Order",
                "This PO is not linked to a Sales Order, so the customer ship-to "
                "address can't be auto-filled. The address will fall back to the "
                "default ship-to.",
            )
        new_text = self._default_ship_to(so, drop_ship=checked)
        self._ship_to_edit.setPlainText(new_text)

    def _build_auto_vendor_message(self) -> str:
        """Return ONLY the user-entered vendor message text.

        The drop-ship and no-tags toggles are persisted as their own
        ``drop_ship`` / ``no_tags`` columns and rendered separately on the
        printed PO. Appending them here caused the message to grow with
        every save (the field round-trips through the database, so each
        load-then-save would duplicate the auto-generated tag lines).
        """
        return self._vendor_message_edit.toPlainText().strip()
