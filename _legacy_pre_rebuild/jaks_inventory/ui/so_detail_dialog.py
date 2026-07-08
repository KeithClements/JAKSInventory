"""Sales Order detail — guided-workflow redesign."""
from __future__ import annotations

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListWidget, QMenu, QMessageBox, QPushButton, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from db import (
    get_sales_order, get_so_lines, add_so_line, remove_so_line,
    update_sales_order, convert_so_to_invoice, get_product_by_sku,
    create_po_from_so, estimate_order_shipping, update_so_line_price,
    get_purchase_order, get_invoice, get_quote,
    log_so_activity, get_so_activity, derive_so_line_status, derive_so_status,
    find_open_purchase_orders_for_vendor, get_setting, add_discount_line,
)
from db.inventory import convert_so_to_invoice_partial
from .discount_line_dialog import DiscountLineDialog
from .workflow_shell import (
    build_bottom_action_bar,
    build_header_bar,
    build_progress_strip,
    build_summary_cards,
    create_link_card,
    create_sidebar_container,
    create_sidebar_section_title,
    create_status_badge,
    style_action_button,
)
from .auto_refresh_mixin import AutoRefreshMixin

_BADGE_COLORS = {
    "OPEN": ("#E5E7EB", "#374151"), "ALLOCATED": ("#D1FAE5", "#065F46"),
    "RECEIVED": ("#DCFCE7", "#166534"), "NEEDS_PO": ("#FEF3C7", "#92400E"),
    "ON_PO": ("#DBEAFE", "#1E40AF"), "PARTIAL": ("#EDE9FE", "#5B21B6"),
    "PICKED": ("#FFEDD5", "#9A3412"), "READY_TO_INVOICE": ("#CCFBF1", "#0F766E"),
    "INVOICED": ("#F3F4F6", "#6B7280"), "DRAFT": ("#E5E7EB", "#374151"),
    "CONFIRMED": ("#DBEAFE", "#1E40AF"), "PURCHASING": ("#FEF3C7", "#92400E"),
    "READY_TO_PICK": ("#D1FAE5", "#065F46"), "CLOSED": ("#F3F4F6", "#6B7280"),
    "CANCELLED": ("#FEE2E2", "#991B1B"),
}
_BADGE_ICONS = {
    "ALLOCATED": "\U0001f7e2", "RECEIVED": "\U0001f7e9", "NEEDS_PO": "\U0001f7e1", "ON_PO": "\U0001f535",
    "PARTIAL": "\U0001f7e3", "PICKED": "\U0001f7e0", "READY_TO_INVOICE": "\U0001f7e2",
    "INVOICED": "\u26ab", "OPEN": "\u26aa",
}
_PROGRESS_STAGES = ["Quote", "Sales Order", "Purchasing", "Receiving", "Picking", "Invoicing"]
_C_ID, _C_SKU, _C_DESC, _C_ORD, _C_ALLOC = 0, 1, 2, 3, 4
_C_PICKED, _C_ONPO, _C_RECV, _C_PRICE, _C_TOTAL, _C_STATUS = 5, 6, 7, 8, 9, 10
_C_COUNT = 11
_LINE_HEADERS = [
    "ID", "SKU", "Description", "Ordered", "Allocated",
    "Picked", "On PO", "Received", "Price", "Total", "Status",
]


def _manual_po_mode_enabled() -> bool:
    """Return True when Sales Order PO creation should be strictly manual."""
    return get_setting("so_manual_po_mode", "1") == "1"


class SODetailDialog(AutoRefreshMixin, QDialog):
    """Full-screen guided-workflow Sales Order detail screen."""

    so_changed = Signal()

    def __init__(self, so_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._so_id = so_id
        self._linked_po_ids: list[int] = []
        self.setWindowTitle("Sales Order Details")
        self.setMinimumSize(1200, 750)
        self.resize(1400, 820)
        # Non-modal: user may keep the SO dialog open while navigating
        # other menu items or opening additional sales orders. Caller side
        # uses ``open_so_detail()`` to focus an existing instance.
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        try:
            self.setWindowFlag(Qt.WindowType.Window, True)
            self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
            self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        except Exception:
            pass
        self._build_ui()
        self._refresh()
        # Initialize auto-refresh after UI and data are loaded (3 sec interval)
        self._init_auto_refresh(refresh_interval_ms=3000)

    # ==================================================================
    #  UI BUILD
    # ==================================================================
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_progress_strip())
        root.addWidget(self._build_summary_cards())
        root.addWidget(self._build_next_action())
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)
        splitter.addWidget(self._build_lines_panel())
        splitter.addWidget(self._build_sidebar())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([900, 340])
        root.addWidget(splitter, 1)
        root.addWidget(self._build_bottom_bar())

    def _build_header(self) -> QFrame:
        bar, lay = build_header_bar()
        self._title_label = QLabel("SO #\u2014")
        self._title_label.setStyleSheet("color: white; font-size: 16px; font-weight: bold;")
        lay.addWidget(self._title_label)
        self._header_status = create_status_badge("DRAFT")
        lay.addWidget(self._header_status)
        lay.addSpacing(20)
        self._customer_label = QLabel("")
        self._customer_label.setStyleSheet("color: #D1D5DB; font-size: 12px;")
        lay.addWidget(self._customer_label)
        self._priority_label = QLabel("")
        self._priority_label.setStyleSheet("color: #FCD34D; font-size: 12px; font-weight: 600;")
        lay.addWidget(self._priority_label)
        self._date_label = QLabel("")
        self._date_label.setStyleSheet("color: #9CA3AF; font-size: 11px;")
        lay.addWidget(self._date_label)
        lay.addStretch()
        self._total_label = QLabel("$0.00")
        self._total_label.setStyleSheet("color: #34D399; font-size: 18px; font-weight: bold;")
        lay.addWidget(self._total_label)
        return bar

    def _build_progress_strip(self) -> QFrame:
        strip, labels = build_progress_strip(_PROGRESS_STAGES)
        self._progress_labels = labels
        return strip

    def _build_summary_cards(self) -> QFrame:
        frame, cards = build_summary_cards(
            [
                ("Lines", "0", "#6B7280"),
                ("Allocated", "0", "#059669"),
                ("Needs PO", "0", "#D97706"),
                ("On PO", "0", "#2563EB"),
                ("Picked", "0", "#EA580C"),
                ("Ready to Invoice", "0", "#0D9488"),
            ],
            min_width=100,
        )
        (
            self._card_lines,
            self._card_allocated,
            self._card_needs_po,
            self._card_on_po,
            self._card_picked,
            self._card_ready,
        ) = cards
        return frame

    @staticmethod
    def _make_card(title, value, color):
        from .workflow_shell import create_metric_card
        return create_metric_card(title, value, color, min_width=100)

    def _build_next_action(self) -> QFrame:
        self._next_action_frame = QFrame()
        self._next_action_frame.setStyleSheet(
            "QFrame { background: #EFF6FF; border-bottom: 2px solid #93C5FD; }"
        )
        lay = QHBoxLayout(self._next_action_frame)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(12)
        self._next_icon = QLabel("\U0001f4cb")
        self._next_icon.setStyleSheet("font-size: 20px;")
        lay.addWidget(self._next_icon)
        info = QVBoxLayout()
        info.setSpacing(2)
        self._next_title = QLabel("Next Step")
        self._next_title.setStyleSheet("font-weight: bold; font-size: 12px; color: #1E40AF;")
        info.addWidget(self._next_title)
        self._next_detail = QLabel("")
        self._next_detail.setStyleSheet("font-size: 11px; color: #3B82F6;")
        info.addWidget(self._next_detail)
        lay.addLayout(info)
        lay.addStretch()
        self._next_btn1 = QPushButton("")
        self._next_btn1.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; font-weight: bold; "
            "padding: 6px 16px; border-radius: 4px; border: none; font-size: 11px; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self._next_btn1.clicked.connect(self._run_next_btn1_action)
        self._next_btn1.hide()
        lay.addWidget(self._next_btn1)
        self._next_btn2 = QPushButton("")
        self._next_btn2.setStyleSheet(
            "QPushButton { background: white; color: #2563EB; font-weight: 600; "
            "padding: 6px 16px; border-radius: 4px; border: 1px solid #93C5FD; font-size: 11px; }"
            "QPushButton:hover { background: #EFF6FF; }"
        )
        self._next_btn2.clicked.connect(self._run_next_btn2_action)
        self._next_btn2.hide()
        lay.addWidget(self._next_btn2)
        self._next_btn1_action = None
        self._next_btn2_action = None
        return self._next_action_frame

    def _build_lines_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 8, 4, 8)
        lay.setSpacing(6)
        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        self._sku_input = QLineEdit()
        self._sku_input.setPlaceholderText("Add SKU\u2026")
        self._sku_input.setMaximumWidth(200)
        self._sku_input.returnPressed.connect(self._add_line)
        add_row.addWidget(self._sku_input)
        add_row.addWidget(QLabel("Qty:"))
        self._qty_input = QSpinBox()
        self._qty_input.setRange(1, 9999)
        self._qty_input.setValue(1)
        add_row.addWidget(self._qty_input)
        add_btn = QPushButton("\u2795 Add Line")
        add_btn.setStyleSheet(
            "QPushButton { background: #059669; color: white; font-weight: bold; "
            "padding: 4px 12px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #047857; }"
        )
        add_btn.clicked.connect(self._add_line)
        add_row.addWidget(add_btn)

        disc_btn = QPushButton("➖ Discount")
        disc_btn.setToolTip("Add a manual discount line (% or fixed $)")
        disc_btn.setStyleSheet(
            "QPushButton { background: #b91c1c; color: white; font-weight: bold; "
            "padding: 4px 12px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #991b1b; }"
        )
        disc_btn.clicked.connect(self._add_discount_line)
        add_row.addWidget(disc_btn)
        add_row.addStretch()
        rm_btn = QPushButton("\U0001f5d1\ufe0f Remove")
        rm_btn.setStyleSheet(
            "QPushButton { color: #DC2626; padding: 4px 10px; border-radius: 4px; "
            "border: 1px solid #FCA5A5; }"
            "QPushButton:hover { background: #FEE2E2; }"
        )
        rm_btn.clicked.connect(self._remove_line)
        add_row.addWidget(rm_btn)
        lay.addLayout(add_row)

        self._lines_table = QTableWidget(0, _C_COUNT)
        self._lines_table.setHorizontalHeaderLabels(_LINE_HEADERS)
        self._lines_table.setColumnHidden(_C_ID, True)
        self._lines_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._lines_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._lines_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._lines_table.verticalHeader().setVisible(False)
        self._lines_table.setAlternatingRowColors(True)
        self._lines_table.verticalHeader().setDefaultSectionSize(30)
        self._lines_table.horizontalHeader().setSectionResizeMode(_C_DESC, QHeaderView.ResizeMode.Stretch)
        self._lines_table.cellChanged.connect(self._on_line_price_changed)
        self._lines_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._lines_table.customContextMenuRequested.connect(self._on_lines_context_menu)
        self._lines_table.setStyleSheet("""
            QTableWidget { border: 1px solid #E5E7EB; border-radius: 6px; gridline-color: #F3F4F6; }
            QHeaderView::section {
                background-color: #F9FAFB; color: #6B7280; font-weight: 600;
                font-size: 11px; padding: 6px 8px; border: none; border-bottom: 1px solid #E5E7EB;
            }
            QTableWidget::item { padding: 4px 8px; }
            QTableWidget::item:selected { background-color: #DBEAFE; color: #1E40AF; }
        """)
        lay.addWidget(self._lines_table, 1)

        # Tracking # + customer-facing notes (#9). Both render on the
        # printed/emailed Sales Order and roll forward to the invoice.
        track_row = QHBoxLayout()
        track_row.setSpacing(8)
        track_row.addWidget(QLabel("Tracking #:"))
        self._tracking_edit = QLineEdit()
        self._tracking_edit.setPlaceholderText("e.g. 1Z999AA10123456784")
        self._tracking_edit.setFixedWidth(220)
        self._tracking_edit.editingFinished.connect(self._on_tracking_changed)
        track_row.addWidget(self._tracking_edit)
        track_row.addWidget(QLabel("Customer Notes (printed on PDF):"))
        track_row.addStretch()
        lay.addLayout(track_row)
        self._customer_notes_edit = QTextEdit()
        self._customer_notes_edit.setPlaceholderText(
            "Notes the customer will see on the printed Sales Order and Invoice"
        )
        self._customer_notes_edit.setMaximumHeight(60)
        self._customer_notes_edit.installEventFilter(self)
        lay.addWidget(self._customer_notes_edit)

        ship_row = QHBoxLayout()
        ship_row.setSpacing(8)
        ship_row.addWidget(QLabel("Carrier:"))
        self._carrier_combo = QComboBox()
        # Local Delivery is the default for in-area drops; the cost field
        # becomes "Fuel Surcharge" instead of "Shipping".
        self._carrier_combo.addItems(["Local Delivery", "UPS", "FedEx", "Freight"])
        self._carrier_combo.setFixedWidth(120)
        self._carrier_combo.currentTextChanged.connect(self._on_carrier_changed)
        ship_row.addWidget(self._carrier_combo)
        est_btn = QPushButton("Estimate Shipping")
        est_btn.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; padding: 4px 10px; "
            "border-radius: 4px; border: none; font-size: 10px; }"
        )
        est_btn.clicked.connect(self._estimate_shipping)
        ship_row.addWidget(est_btn)
        self._shipping_est_label = QLabel("")
        self._shipping_est_label.setStyleSheet("color: #6B7280; font-size: 11px;")
        ship_row.addWidget(self._shipping_est_label)
        ship_row.addStretch()
        self._ship_cost_label = QLabel("Fuel Surcharge $:")
        ship_row.addWidget(self._ship_cost_label)
        self._shipping_spin = QDoubleSpinBox()
        self._shipping_spin.setRange(0, 99999)
        self._shipping_spin.setDecimals(2)
        self._shipping_spin.setPrefix("$")
        self._shipping_spin.setFixedWidth(120)
        self._shipping_spin.valueChanged.connect(self._on_shipping_changed)
        ship_row.addWidget(self._shipping_spin)
        self._cc_fee_check = QCheckBox("\U0001F4B3 3% CC fee")
        self._cc_fee_check.setToolTip(
            "Apply a 3% credit-card convenience fee.\n"
            "Customer can avoid by paying with cash, check, or ACH."
        )
        self._cc_fee_check.toggled.connect(self._on_cc_fee_toggled)
        ship_row.addWidget(self._cc_fee_check)
        self._grand_total_label = QLabel("$0.00")
        self._grand_total_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #059669;")
        ship_row.addWidget(self._grand_total_label)
        lay.addLayout(ship_row)
        return panel

    def _build_sidebar(self) -> QWidget:
        panel, lay = create_sidebar_container(min_width=280, max_width=360)
        hdr = create_sidebar_section_title("\U0001f4ce Order Links")
        lay.addWidget(hdr)
        self._link_quote_frame = self._make_link_card("Source Quote", "\u2014", "Open Quote")
        self._link_quote_frame[2].clicked.connect(self._open_linked_quote)
        lay.addWidget(self._link_quote_frame[0])

        # PO section — dynamic container of clickable PO cards
        po_hdr = QLabel("\U0001f4e6 Purchase Orders")
        po_hdr.setStyleSheet("font-weight: bold; font-size: 11px; color: #1F2937; margin-top: 4px;")
        lay.addWidget(po_hdr)
        self._po_cards_container = QVBoxLayout()
        self._po_cards_container.setSpacing(4)
        lay.addLayout(self._po_cards_container)

        self._link_inv_frame = self._make_link_card("Invoice", "None", "Create Invoice")
        self._link_inv_frame[2].clicked.connect(self._on_invoice_action)
        lay.addWidget(self._link_inv_frame[0])
        act_hdr = create_sidebar_section_title("\U0001f4cb Activity")
        act_hdr.setStyleSheet("font-weight: bold; font-size: 12px; color: #1F2937; margin-top: 8px;")
        lay.addWidget(act_hdr)
        self._activity_list = QListWidget()
        self._activity_list.setStyleSheet(
            "QListWidget { background: white; border: 1px solid #E5E7EB; "
            "border-radius: 4px; font-size: 10px; }"
            "QListWidget::item { padding: 4px 6px; border-bottom: 1px solid #F3F4F6; }"
        )
        lay.addWidget(self._activity_list, 1)
        return panel

    @staticmethod
    def _make_link_card(title, value, btn_text):
        return create_link_card(title, value, btn_text)

    def _build_bottom_bar(self) -> QFrame:
        bar, lay = build_bottom_action_bar()
        self._po_btn = QPushButton("\U0001f4e6 Create / View PO")
        self._po_btn.setStyleSheet(style_action_button("warning"))
        self._po_btn.clicked.connect(self._open_linked_po)
        lay.addWidget(self._po_btn)
        self._pick_btn = QPushButton("\U0001f4cb Pick")
        self._pick_btn.setStyleSheet(style_action_button("warning"))
        self._pick_btn.clicked.connect(self._on_pick_items)
        lay.addWidget(self._pick_btn)
        self._invoice_btn = QPushButton("\U0001f4c4 Invoice")
        self._invoice_btn.setStyleSheet(style_action_button("success"))
        self._invoice_btn.clicked.connect(self._convert_to_invoice)
        lay.addWidget(self._invoice_btn)
        self._print_so_btn = QPushButton("\U0001f5a8 Print SO")
        self._print_so_btn.setStyleSheet(style_action_button("neutral"))
        self._print_so_btn.clicked.connect(self._print_sales_order)
        lay.addWidget(self._print_so_btn)
        # Print a core return slip when the SO has core-bearing lines. This
        # is the customer's preliminary paperwork for the cores they owe
        # back; it is replaced by the formal receipt at invoice finalize.
        self._print_core_slip_btn = QPushButton("\U0001f5a8 Print Core Slip")
        self._print_core_slip_btn.setStyleSheet(style_action_button("neutral"))
        self._print_core_slip_btn.setToolTip(
            "Print a core return slip for the customer to take with their order."
        )
        self._print_core_slip_btn.clicked.connect(self._print_core_slip)
        self._print_core_slip_btn.setVisible(False)
        lay.addWidget(self._print_core_slip_btn)
        self._email_cust_btn = QPushButton("\u2709 Email Customer")
        self._email_cust_btn.setStyleSheet(style_action_button("primary"))
        self._email_cust_btn.clicked.connect(self._email_customer_so)
        lay.addWidget(self._email_cust_btn)
        lay.addStretch()
        self._confirm_btn = QPushButton("\u2713 Confirm Order")
        self._confirm_btn.setStyleSheet(style_action_button("primary"))
        self._confirm_btn.clicked.connect(self._confirm_order)
        lay.addWidget(self._confirm_btn)
        self._cancel_btn = QPushButton("Cancel Order")
        self._cancel_btn.setStyleSheet(style_action_button("danger"))
        self._cancel_btn.clicked.connect(self._cancel_order)
        lay.addWidget(self._cancel_btn)
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(style_action_button("neutral"))
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)
        return bar

    # ==================================================================
    #  STATE TRACKING FOR AUTO-REFRESH
    # ==================================================================
    def _get_state_snapshot(self) -> dict:
        """Snapshot of SO and linked PO statuses for change detection."""
        so = get_sales_order(self._so_id)
        if not so:
            return {}
        # Track SO status and lines
        lines_data = []
        try:
            lines = get_so_lines(self._so_id)
            for line in lines:
                lines_data.append((line.get("id"), line.get("quantity"), line.get("status")))
        except Exception:
            pass
        return {
            "so_status": so.get("status"),
            "so_updated": so.get("updated_at"),
            "lines": lines_data,
        }

    # ==================================================================
    #  REFRESH
    # ==================================================================
    def _refresh(self) -> None:
        self._lines_table.blockSignals(True)
        so = get_sales_order(self._so_id)
        if not so:
            return
        db_status = (so.get("status") or "draft").lower()
        lines = get_so_lines(self._so_id)

        # Enrich lines with live PO data scoped to this SO.
        # `on_po_qty` should represent outstanding inbound units, not total ordered.
        from db import get_db
        for line in lines:
            pid = line.get("product_id")
            if not pid:
                continue
            with get_db() as conn:
                try:
                    row = conn.execute(
                        "SELECT "
                        "  COALESCE(SUM(CASE "
                        "      WHEN pol.quantity_ordered > pol.quantity_received "
                        "      THEN pol.quantity_ordered - pol.quantity_received "
                        "      ELSE 0 END), 0) AS on_po_outstanding, "
                        "  COALESCE(SUM(pol.quantity_received), 0) AS total_received "
                        "FROM purchase_order_lines pol "
                        "JOIN purchase_orders po ON po.id = pol.po_id "
                        "WHERE po.linked_so_id = ? "
                        "  AND pol.product_id = ? "
                        "  AND po.status NOT IN ('cancelled', 'void', 'closed')",
                        (self._so_id, pid),
                    ).fetchone()
                except sqlite3.OperationalError as exc:
                    if "no such column: po.linked_so_id" not in str(exc):
                        raise
                    row = conn.execute(
                        "SELECT "
                        "  COALESCE(SUM(CASE "
                        "      WHEN pol.quantity_ordered > pol.quantity_received "
                        "      THEN pol.quantity_ordered - pol.quantity_received "
                        "      ELSE 0 END), 0) AS on_po_outstanding, "
                        "  COALESCE(SUM(pol.quantity_received), 0) AS total_received "
                        "FROM purchase_order_lines pol "
                        "JOIN purchase_orders po ON po.id = pol.po_id "
                        "WHERE po.notes LIKE ? "
                        "  AND pol.product_id = ? "
                        "  AND po.status NOT IN ('cancelled', 'void', 'closed')",
                        (f"%From SO #{self._so_id}%", pid),
                    ).fetchone()
            line["on_po_qty"] = int(row[0]) if row else 0
            line["received_qty"] = int(row[1]) if row else 0

        # Header
        self._title_label.setText(f"SO #{so.get('so_number', '')}")
        total = float(so.get("total", 0) or 0)
        self._total_label.setText(f"${total:,.2f}")
        cust = so.get("customer_company") or so.get("customer_name", "\u2014")
        self._customer_label.setText(f"Customer: {cust}")
        priority = (so.get("priority") or "normal").upper()
        self._priority_label.setText(f"Priority: {priority}")
        ordered_date = str(so.get("created_at", "") or "")[:10]
        promised = so.get("promised_date") or "\u2014"
        self._date_label.setText(f"Ordered: {ordered_date}  \u00b7  Promised: {promised}")

        # Derive overall status
        if db_status == "cancelled":
            overall = "CANCELLED"
        elif db_status == "invoiced":
            overall = "INVOICED"
        elif db_status in ("quote", "draft"):
            overall = "DRAFT" if not lines else derive_so_status(lines)
        else:
            overall = derive_so_status(lines) if lines else "CONFIRMED"

        bg, fg = _BADGE_COLORS.get(overall, ("#E5E7EB", "#374151"))
        self._header_status.setText(overall.replace("_", " "))
        self._header_status.setStyleSheet(
            f"font-weight: 600; font-size: 12px; padding: 4px 12px; "
            f"border-radius: 4px; background: {bg}; color: {fg};"
        )
        self._update_progress_strip(overall)

        # Summary cards
        statuses = [derive_so_line_status(l) for l in lines]
        self._card_lines[2].setText(str(len(lines)))
        self._card_allocated[2].setText(str(sum(1 for s in statuses if s == "ALLOCATED")))
        self._card_needs_po[2].setText(str(sum(1 for s in statuses if s == "NEEDS_PO")))
        self._card_on_po[2].setText(str(sum(1 for s in statuses if s == "ON_PO")))
        self._card_picked[2].setText(str(sum(1 for s in statuses if s == "PICKED")))
        self._card_ready[2].setText(str(sum(1 for s in statuses if s in ("READY_TO_INVOICE", "INVOICED"))))

        self._update_next_action(overall, lines, statuses, so)

        # Lines table
        self._lines_table.setRowCount(len(lines))
        for i, line in enumerate(lines):
            def _item(text):
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it

            # ── Discount line rendering (em-dash SKU, red italic, parens total)
            if (line.get("line_kind") or "product") == "discount":
                lt = float(line.get("line_total", 0) or 0)
                disc_color = QColor("#b91c1c")

                def _disc_item(text):
                    it = QTableWidgetItem(str(text) if text else "")
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    f = it.font(); f.setItalic(True); it.setFont(f)
                    it.setForeground(disc_color)
                    return it

                self._lines_table.setItem(i, _C_ID, _item(line.get("id")))
                self._lines_table.setItem(i, _C_SKU, _disc_item("—"))
                self._lines_table.setItem(i, _C_DESC, _disc_item(line.get("description") or "Discount"))
                self._lines_table.setItem(i, _C_ORD, _disc_item(""))
                self._lines_table.setItem(i, _C_ALLOC, _disc_item(""))
                self._lines_table.setItem(i, _C_PICKED, _disc_item(""))
                self._lines_table.setItem(i, _C_ONPO, _disc_item(""))
                self._lines_table.setItem(i, _C_RECV, _disc_item(""))
                self._lines_table.setItem(i, _C_PRICE, _disc_item(""))
                self._lines_table.setItem(i, _C_TOTAL, _disc_item(f"(${abs(lt):,.2f})"))
                self._lines_table.setItem(i, _C_STATUS, _disc_item("DISCOUNT"))
                continue
            self._lines_table.setItem(i, _C_ID, _item(line.get("id")))
            self._lines_table.setItem(i, _C_SKU, _item(line.get("sku")))
            self._lines_table.setItem(i, _C_DESC, _item(line.get("description")))
            self._lines_table.setItem(i, _C_ORD, _item(line.get("quantity_ordered", 0)))
            self._lines_table.setItem(i, _C_ALLOC, _item(line.get("allocated_qty", 0)))
            self._lines_table.setItem(i, _C_PICKED, _item(line.get("quantity_picked", 0)))
            self._lines_table.setItem(i, _C_ONPO, _item(line.get("on_po_qty", 0)))
            self._lines_table.setItem(i, _C_RECV, _item(line.get("received_qty", 0)))
            price = float(line.get("unit_price", 0) or 0)
            price_item = QTableWidgetItem(f"${price:,.2f}")
            price_item.setFlags(price_item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._lines_table.setItem(i, _C_PRICE, price_item)
            lt = float(line.get("line_total", 0) or 0)
            self._lines_table.setItem(i, _C_TOTAL, _item(f"${lt:,.2f}"))
            ls = derive_so_line_status(line)
            icon = _BADGE_ICONS.get(ls, "")
            status_item = _item(f"{icon} {ls.replace('_', ' ')}")
            sbg, sfg = _BADGE_COLORS.get(ls, ("#E5E7EB", "#374151"))
            status_item.setBackground(QColor(sbg))
            status_item.setForeground(QColor(sfg))
            self._lines_table.setItem(i, _C_STATUS, status_item)
        self._lines_table.resizeColumnsToContents()
        self._lines_table.horizontalHeader().setSectionResizeMode(_C_DESC, QHeaderView.ResizeMode.Stretch)

        self._update_linked_docs(so)
        self._update_activity()

        # Button visibility
        editable = db_status in ("quote", "draft", "confirmed")
        self._sku_input.setEnabled(editable)
        self._qty_input.setEnabled(editable)
        self._confirm_btn.setVisible(db_status in ("quote", "draft"))
        self._cancel_btn.setVisible(db_status not in ("invoiced", "cancelled", "closed"))
        self._po_btn.setEnabled(any(s in ("NEEDS_PO", "ON_PO") for s in statuses))
        self._pick_btn.setEnabled(
            any(s in ("ALLOCATED", "RECEIVED") for s in statuses)
            and db_status not in ("quote", "draft", "cancelled", "invoiced")
        )
        self._invoice_btn.setEnabled(db_status not in ("cancelled", "invoiced") and len(lines) > 0)

        # Tracking # + customer notes (Phase 4) — load from SO row.
        if hasattr(self, "_tracking_edit"):
            self._tracking_edit.blockSignals(True)
            self._tracking_edit.setText(str(so.get("tracking_number") or ""))
            self._tracking_edit.blockSignals(False)
        if hasattr(self, "_customer_notes_edit"):
            self._customer_notes_edit.blockSignals(True)
            self._customer_notes_edit.setPlainText(str(so.get("customer_notes") or ""))
            self._customer_notes_edit.blockSignals(False)

        saved_shipping = float(so.get("shipping", 0) or 0)
        saved_fuel = float(so.get("fuel_surcharge", 0) or 0)
        ship_method = so.get("ship_method", "") or ""
        # Default to Local Delivery for new SOs (no ship_method yet).
        chosen_carrier = "Local Delivery"
        for carrier in ("Local Delivery", "UPS", "FedEx", "Freight"):
            if carrier.lower() in ship_method.lower():
                chosen_carrier = carrier
                break
        self._carrier_combo.blockSignals(True)
        self._carrier_combo.setCurrentText(chosen_carrier)
        self._carrier_combo.blockSignals(False)
        self._shipping_spin.blockSignals(True)
        if chosen_carrier == "Local Delivery":
            self._ship_cost_label.setText("Fuel Surcharge $:")
            self._shipping_spin.setValue(saved_fuel)
        else:
            self._ship_cost_label.setText("Shipping $:")
            self._shipping_spin.setValue(saved_shipping)
        self._shipping_spin.blockSignals(False)
        self._cc_fee_check.blockSignals(True)
        self._cc_fee_check.setChecked(bool(int(so.get("cc_fee_enabled") or 0)))
        self._cc_fee_check.blockSignals(False)
        self._update_total_display()
        self._lines_table.blockSignals(False)

        # Show the Print Core Slip button only when at least one SO line
        # carries a core charge. Computed off live product data so the
        # button reflects the current state of the SO.
        try:
            from db import get_db
            ids = [int(l.get("product_id")) for l in lines if l.get("product_id")]
            has_core = False
            if ids:
                placeholders = ",".join(["%s"] * len(ids))
                with get_db() as conn:
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM products "
                        f"WHERE id IN ({placeholders}) "
                        f"AND COALESCE(core_charge, 0) > 0",
                        tuple(ids),
                    ).fetchone()
                has_core = bool(row and (row[0] if not hasattr(row, "keys") else row[0]))
            self._print_core_slip_btn.setVisible(bool(has_core))
        except Exception:
            self._print_core_slip_btn.setVisible(False)
    def _update_progress_strip(self, overall: str) -> None:
        stage_map = {
            "DRAFT": 1, "CONFIRMED": 1, "ALLOCATED": 1,
            "NEEDS_PO": 2, "PURCHASING": 2, "ON_PO": 2,
            "PARTIAL": 3, "RECEIVED": 3,
            "READY_TO_PICK": 4, "PICKED": 4,
            "READY_TO_INVOICE": 5, "INVOICED": 5,
            "CANCELLED": -1,
        }
        active = stage_map.get(overall, 1)
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

    def _update_next_action(self, overall, lines, statuses, so) -> None:
        self._next_btn1_action = None
        self._next_btn2_action = None
        db_status = (so.get("status") or "draft").lower()
        n_needs = sum(1 for s in statuses if s == "NEEDS_PO")
        n_alloc = sum(1 for s in statuses if s in ("ALLOCATED", "RECEIVED"))
        n_picked = sum(1 for s in statuses if s == "PICKED")
        n_onpo = sum(1 for s in statuses if s == "ON_PO")

        if db_status in ("quote", "draft"):
            self._next_icon.setText("\u2713")
            self._next_title.setText("Confirm this order to begin fulfilment")
            self._next_detail.setText(f"{len(lines)} line(s) ready")
            self._next_btn1.setText("Confirm Order")
            self._next_btn1.show()
            self._next_btn1_action = self._confirm_order
            self._next_btn2.hide()
        elif n_needs > 0:
            self._next_icon.setText("\U0001f4e6")
            if _manual_po_mode_enabled():
                self._next_title.setText(f"Review PO needs \u2014 {n_needs} item(s) require purchasing")
            else:
                self._next_title.setText(f"Create Purchase Order \u2014 {n_needs} item(s) need purchasing")
            vendors = set()
            for ln in lines:
                if derive_so_line_status(ln) == "NEEDS_PO":
                    vendors.add(ln.get("supplier") or ln.get("description", "Unknown"))
            self._next_detail.setText(f"Vendor(s): {', '.join(vendors)}")
            self._next_btn1.setText("Review & Create PO" if _manual_po_mode_enabled() else "Create PO")
            self._next_btn1.show()
            self._next_btn1_action = self._create_po
            self._next_btn2.setText("View Open POs")
            self._next_btn2.show()
            self._next_btn2_action = self._open_linked_po
        elif n_onpo > 0:
            self._next_icon.setText("\U0001f4ec")
            self._next_title.setText(f"Waiting on {n_onpo} item(s) from vendor(s)")
            self._next_detail.setText("Receive inventory when PO arrives")
            self._next_btn1.setText("Open PO")
            self._next_btn1.show()
            self._next_btn1_action = self._open_linked_po
            self._next_btn2.hide()
        elif n_alloc > 0 and n_picked == 0:
            self._next_icon.setText("\U0001f4cb")
            self._next_title.setText(f"Pick {n_alloc} allocated item(s)")
            self._next_detail.setText("Mark items as physically pulled from warehouse")
            self._next_btn1.setText("Pick Items")
            self._next_btn1.show()
            self._next_btn1_action = self._on_pick_items
            self._next_btn2.hide()
        elif n_picked > 0 or overall in ("READY_TO_INVOICE", "PICKED"):
            self._next_icon.setText("\U0001f4c4")
            self._next_title.setText("Ready to invoice")
            self._next_detail.setText("All items picked \u2014 create invoice for customer")
            self._next_btn1.setText("Create Invoice")
            self._next_btn1.show()
            self._next_btn1_action = self._convert_to_invoice
            self._next_btn2.hide()
        elif overall == "INVOICED":
            self._next_icon.setText("\u2705")
            self._next_title.setText("Order complete")
            self._next_detail.setText("Invoice has been created")
            self._next_btn1.hide()
            self._next_btn2.hide()
        else:
            self._next_icon.setText("\U0001f4cb")
            self._next_title.setText("No action needed")
            self._next_detail.setText("")
            self._next_btn1.hide()
            self._next_btn2.hide()

    def _run_next_btn1_action(self) -> None:
        if callable(self._next_btn1_action):
            self._next_btn1_action()

    def _run_next_btn2_action(self) -> None:
        if callable(self._next_btn2_action):
            self._next_btn2_action()

    def _update_linked_docs(self, so: dict) -> None:
        quote_id = so.get("quote_id")
        if quote_id:
            try:
                q = get_quote(quote_id)
                self._link_quote_frame[1].setText(
                    q.get("quote_number", f"QT #{quote_id}") if q else f"QT #{quote_id}"
                )
            except Exception:
                self._link_quote_frame[1].setText(f"QT #{quote_id}")
            self._link_quote_frame[2].setEnabled(True)
        else:
            self._link_quote_frame[1].setText("No source quote")
            self._link_quote_frame[2].setEnabled(False)

        # ── PO cards — clear old, build new ──
        self._linked_po_ids = []
        # Remove old PO card widgets
        while self._po_cards_container.count():
            item = self._po_cards_container.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        try:
            from db import get_db
            lines = get_so_lines(self._so_id)
            pids = [l.get("product_id") for l in lines if l.get("product_id")]
            if pids:
                with get_db() as conn:
                    ph = ",".join(["?"] * len(pids))
                    rows = conn.execute(
                        f"SELECT DISTINCT po.id, po.po_number, po.status, "
                        f"po.total, po.order_date, v.name as vendor_name "
                        f"FROM purchase_order_lines pol "
                        f"JOIN purchase_orders po ON po.id = pol.po_id "
                        f"LEFT JOIN vendors v ON v.id = po.vendor_id "
                        f"WHERE pol.product_id IN ({ph}) "
                        f"AND po.status NOT IN ('cancelled', 'received', 'closed') "
                        f"ORDER BY po.created_at DESC",
                        tuple(pids),
                    ).fetchall()
                for r in rows:
                    rd = dict(r) if hasattr(r, "keys") else {}
                    po_id = rd.get("id", 0)
                    self._linked_po_ids.append(po_id)
                    self._po_cards_container.addWidget(
                        self._make_po_card(
                            po_id,
                            rd.get("po_number", "?"),
                            rd.get("vendor_name", ""),
                            (rd.get("status") or "draft").upper(),
                            float(rd.get("total") or 0),
                            str(rd.get("order_date") or "")[:10],
                        )
                    )
        except Exception:
            pass

        if not self._linked_po_ids:
            no_po = QLabel("No POs yet")
            no_po.setStyleSheet("font-size: 11px; color: #9CA3AF; padding: 4px;")
            self._po_cards_container.addWidget(no_po)

        # Dynamic bottom PO button text
        n = len(self._linked_po_ids)
        if n == 0:
            self._po_btn.setText(
                "\U0001f4e6 Review PO Needs" if _manual_po_mode_enabled() else "\U0001f4e6 Create PO"
            )
        elif n == 1:
            self._po_btn.setText("\U0001f4e6 View PO")
        else:
            self._po_btn.setText(f"\U0001f4e6 View / Manage POs ({n})")

        inv_id = so.get("invoice_id")
        if inv_id:
            try:
                inv = get_invoice(inv_id)
                self._link_inv_frame[1].setText(
                    inv.get("invoice_number", f"INV #{inv_id}") if inv else f"INV #{inv_id}"
                )
                self._link_inv_frame[2].setText("Open Invoice")
            except Exception:
                self._link_inv_frame[1].setText(f"INV #{inv_id}")
        else:
            self._link_inv_frame[1].setText("Not yet invoiced")
            self._link_inv_frame[2].setText("Create Invoice")

    def _make_po_card(self, po_id: int, po_number: str, vendor: str,
                      status: str, total: float, date: str) -> QFrame:
        """Build a single clickable PO card for the sidebar."""
        _STATUS_COLORS = {
            "DRAFT": ("#E5E7EB", "#374151"), "SENT": ("#DBEAFE", "#1E40AF"),
            "ORDERED": ("#DBEAFE", "#1E40AF"), "PARTIAL": ("#FEF3C7", "#92400E"),
            "RECEIVED": ("#D1FAE5", "#065F46"), "CLOSED": ("#F3F4F6", "#6B7280"),
            "CANCELLED": ("#FEE2E2", "#991B1B"),
        }
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: white; border: 1px solid #E5E7EB; "
            "border-radius: 4px; padding: 4px 6px; }"
        )
        cl = QHBoxLayout(card)
        cl.setContentsMargins(6, 4, 6, 4)
        cl.setSpacing(6)

        info = QVBoxLayout()
        info.setSpacing(1)
        top = QLabel(f"<b>{po_number}</b> \u2014 {vendor}")
        top.setStyleSheet("font-size: 11px; color: #1F2937;")
        info.addWidget(top)
        detail = QLabel(f"${total:,.2f}  \u00b7  {date}")
        detail.setStyleSheet("font-size: 10px; color: #6B7280;")
        info.addWidget(detail)
        cl.addLayout(info, 1)

        bg, fg = _STATUS_COLORS.get(status, ("#E5E7EB", "#374151"))
        badge = QLabel(status)
        badge.setStyleSheet(
            f"font-size: 9px; font-weight: 600; padding: 2px 6px; "
            f"border-radius: 3px; background: {bg}; color: {fg};"
        )
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cl.addWidget(badge)

        open_btn = QPushButton("Open")
        open_btn.setMinimumSize(60, 24)
        open_btn.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; font-size: 10px; "
            "font-weight: 600; border: none; border-radius: 3px; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        open_btn.clicked.connect(lambda checked, pid=po_id: self._do_open_po(pid))
        cl.addWidget(open_btn)

        # Right-click context menu (#6) — Open / Reopen (revert to draft)
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.customContextMenuRequested.connect(
            lambda pos, pid=po_id, st=status: self._show_po_card_menu(pos, pid, st, card)
        )
        return card

    def _show_po_card_menu(self, pos, po_id: int, status: str, card: QFrame) -> None:
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        open_act = menu.addAction("Open PO")
        reopen_act = menu.addAction("Reopen as Draft")
        # Only allow reopening when the PO is past draft.
        reopen_act.setEnabled(str(status).upper() != "DRAFT")
        chosen = menu.exec(card.mapToGlobal(pos))
        if chosen is open_act:
            self._do_open_po(po_id)
        elif chosen is reopen_act:
            self._reopen_po(po_id)

    def _reopen_po(self, po_id: int) -> None:
        reply = QMessageBox.question(
            self,
            "Reopen Purchase Order",
            "Revert this PO to DRAFT status?\n\n"
            "It will become editable again. The vendor is NOT automatically notified.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            from db import update_po_status
            res = update_po_status(int(po_id), "draft")
            if not res.get("success"):
                QMessageBox.warning(self, "Reopen Failed",
                                    res.get("error", "Could not reopen PO"))
                return
            log_so_activity(self._so_id, "po_reopened", f"PO #{po_id} reverted to draft")
            self._refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Reopen Failed", str(exc))

    def _update_activity(self) -> None:
        self._activity_list.clear()
        entries = get_so_activity(self._so_id)
        if not entries:
            self._activity_list.addItem("No activity recorded yet")
            return
        for entry in entries:
            ts = str(entry.get("created_at", ""))[:16]
            action = entry.get("action", "")
            detail = entry.get("detail", "")
            text = f"{ts}  {action}: {detail}" if detail else f"{ts}  {action}"
            self._activity_list.addItem(text)

    # ==================================================================
    #  ACTIONS
    # ==================================================================
    def _add_line(self) -> None:
        sku = self._sku_input.text().strip()
        if not sku:
            return
        product = get_product_by_sku(sku)
        if not product:
            QMessageBox.warning(self, "Not Found", f"SKU '{sku}' not found.")
            return
        qty = self._qty_input.value()
        price = float(product.get("selling_price", 0) or 0)
        try:
            from db import compute_price
            so = get_sales_order(self._so_id) or {}
            cid = so.get("customer_id")
            r = compute_price(int(product["id"]), customer_id=int(cid) if cid else None)
            net = r.get("net_price")
            if net is not None and float(net) > 0:
                price = float(net)
        except Exception:
            pass
        add_so_line(self._so_id, product_id=product["id"], quantity=qty, unit_price=price)
        self._sku_input.clear()
        self._qty_input.setValue(1)
        self._refresh()
        self._show_suggested_sells(product["id"])

    def _add_discount_line(self) -> None:
        """Open the shared DiscountLineDialog and append a negative SO line."""
        # Compute current non-discount subtotal for % preview.
        subtotal = 0.0
        for ln in (get_so_lines(self._so_id) or []):
            if (ln.get("line_kind") or "product") == "discount":
                continue
            subtotal += float(ln.get("line_total") or 0)
        dlg = DiscountLineDialog(
            self,
            doc_type="so",
            product_subtotal=subtotal,
            default_description="Discount",
        )
        if dlg.exec() != dlg.DialogCode.Accepted or not dlg.result_payload:
            return
        payload = dlg.result_payload
        result = add_discount_line(
            "so",
            self._so_id,
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
        log_so_activity(
            self._so_id,
            "discount_added",
            f"{payload['description']} (${result.get('discount_amount', 0):,.2f})",
        )
        self._refresh()

    def _show_suggested_sells(self, product_id: int) -> None:
        try:
            from .suggested_sells_popup import SuggestedSellsPopup
            dlg = SuggestedSellsPopup(product_id, parent=self)
            if not dlg.has_suggestions:
                return
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            for item in dlg.selected_items():
                add_so_line(self._so_id, product_id=item["product_id"],
                            quantity=item["qty"], unit_price=item["selling_price"])
            self._refresh()
        except Exception:
            pass

    def _remove_line(self) -> None:
        row = self._lines_table.currentRow()
        if row < 0:
            return
        line_id = int(self._lines_table.item(row, _C_ID).text())
        remove_so_line(line_id)
        log_so_activity(self._so_id, "line_removed", f"Line {line_id} removed")
        self._refresh()

    def _on_line_price_changed(self, row: int, col: int) -> None:
        if col != _C_PRICE:
            return
        id_item = self._lines_table.item(row, _C_ID)
        price_item = self._lines_table.item(row, col)
        if not id_item or not price_item:
            return
        line_id = int(id_item.text())
        raw = price_item.text().replace("$", "").replace(",", "").strip()
        try:
            new_price = float(raw)
        except ValueError:
            self._refresh()
            return
        if new_price < 0:
            self._refresh()
            return
        update_so_line_price(line_id, new_price)
        self._lines_table.blockSignals(True)
        self._refresh()
        self._lines_table.blockSignals(False)

    def _on_lines_context_menu(self, pos) -> None:
        row = self._lines_table.rowAt(pos.y())
        if row < 0:
            return
        id_item = self._lines_table.item(row, _C_ID)
        if not id_item:
            return
        line_id = int(id_item.text())
        sku = self._lines_table.item(row, _C_SKU).text() if self._lines_table.item(row, _C_SKU) else "?"
        status_item = self._lines_table.item(row, _C_STATUS)
        line_status = status_item.text().strip() if status_item else ""
        so = get_sales_order(self._so_id)
        db_status = (so.get("status") or "draft").lower() if so else "draft"

        menu = QMenu(self)
        po_action = menu.addAction("\U0001f4e6 Create Purchase Order")
        po_action.setEnabled("NEEDS" in line_status or "OPEN" in line_status)
        menu.addSeparator()
        pick_action = menu.addAction("\U0001f4cb Mark as Picked")
        pick_action.setEnabled("ALLOCATED" in line_status and db_status not in ("quote", "draft"))
        menu.addSeparator()
        eta_action = menu.addAction("\U0001f4c5 Set ETA / Notes")
        menu.addSeparator()
        remove_action = menu.addAction("\U0001f5d1\ufe0f Remove Line")
        remove_action.setEnabled(db_status in ("quote", "draft", "confirmed"))

        action = menu.exec(self._lines_table.viewport().mapToGlobal(pos))
        if not action:
            return
        if action == po_action:
            self._create_po_for_line(row)
        elif action == pick_action:
            self._mark_line_picked(line_id)
        elif action == eta_action:
            self._set_line_eta(line_id, sku)
        elif action == remove_action:
            self._lines_table.setCurrentCell(row, 0)
            self._remove_line()

    def _create_po_for_line(self, row: int) -> None:
        lines = get_so_lines(self._so_id)
        if row >= len(lines):
            return
        line = lines[row]
        pid = line.get("product_id")
        if not pid:
            QMessageBox.warning(self, "Error", "Line has no product linked.")
            return
        so = get_sales_order(self._so_id)
        if so and (so.get("status") or "").lower() in ("quote", "draft"):
            update_sales_order(self._so_id, status="confirmed")
            log_so_activity(self._so_id, "status_change", "Auto-confirmed for PO creation")
        existing_po_by_vendor = self._resolve_po_merge_map([pid])
        if existing_po_by_vendor is None:
            return
        result = create_po_from_so(
            self._so_id,
            product_ids=[pid],
            existing_po_by_vendor=existing_po_by_vendor,
        )
        if result.get("success"):
            pos = result["purchase_orders"]
            log_so_activity(
                self._so_id, "po_created",
                " / ".join(
                    f"{po['po_number']} \u2014 {po['vendor']} ({po.get('action', 'created')})"
                    for po in pos
                ),
            )
            from db import get_db
            with get_db() as conn:
                conn.execute(
                    "UPDATE sales_order_lines SET on_po_qty = quantity_backordered WHERE id = ?",
                    (line.get("id"),),
                )
                conn.commit()
            self._refresh()
            if pos:
                self._do_open_po(pos[0]["po_id"])
        else:
            QMessageBox.warning(self, "Error", result.get("error", "Failed to create PO"))

    def _mark_line_picked(self, line_id: int) -> None:
        lines = get_so_lines(self._so_id)
        for line in lines:
            if line.get("id") == line_id:
                allocated = int(line.get("allocated_qty", 0) or 0)
                if allocated <= 0:
                    QMessageBox.warning(self, "Cannot Pick", "Item not allocated.")
                    return
                from db import get_db
                with get_db() as conn:
                    conn.execute(
                        "UPDATE sales_order_lines SET quantity_picked = ? WHERE id = ?",
                        (allocated, line_id),
                    )
                    conn.commit()
                log_so_activity(self._so_id, "line_picked", f"{line.get('sku', '?')} qty {allocated}")
                break
        self._refresh()

    def _on_pick_items(self) -> None:
        try:
            from jaks_inventory.ui.pick_dialog import PickDialog
            dlg = PickDialog(self._so_id, parent=self)
            all_picked = {"value": False}

            def _after_save(flag: bool) -> None:
                all_picked["value"] = bool(flag)

            dlg.pick_saved.connect(_after_save)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._refresh()
                if getattr(dlg, "create_invoice_requested", False) and all_picked["value"]:
                    self._convert_to_invoice()
        except Exception as e:
            QMessageBox.warning(self, "Picking", f"Could not open picking screen:\n{e}")

    def _set_line_eta(self, line_id: int, sku: str) -> None:
        lines = get_so_lines(self._so_id)
        current = ""
        for line in lines:
            if line.get("id") == line_id:
                current = line.get("notes", "") or ""
                break
        text, ok = QInputDialog.getText(self, f"ETA / Notes \u2014 {sku}",
                                        "Enter ETA tag or notes:", text=current)
        if ok:
            from db import get_db
            with get_db() as conn:
                conn.execute("UPDATE sales_order_lines SET notes=? WHERE id=?",
                             (text.strip() or None, line_id))
                conn.commit()
            self._refresh()

    def _confirm_order(self) -> None:
        update_sales_order(self._so_id, status="confirmed")
        log_so_activity(self._so_id, "status_change", "Order confirmed")
        self._refresh()

    def _cancel_order(self) -> None:
        reply = QMessageBox.question(
            self, "Cancel Order", "Cancel this sales order?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        update_sales_order(self._so_id, status="cancelled")
        log_so_activity(self._so_id, "status_change", "Order cancelled")
        self._refresh()

    def _create_po(self) -> None:
        so = get_sales_order(self._so_id)
        if not so:
            return
        db_status = (so.get("status") or "draft").lower()
        if db_status in ("quote", "draft"):
            reply = QMessageBox.question(
                self, "Confirm & Create POs",
                "This will confirm the order and create PO(s).\n\nProceed?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            update_sales_order(self._so_id, status="confirmed")
            log_so_activity(self._so_id, "status_change", "Auto-confirmed for PO creation")

        lines = get_so_lines(self._so_id)
        if not lines:
            QMessageBox.warning(self, "No Lines", "No line items.")
            return

        from db import get_db
        needs_po = []
        stocked = []
        with get_db() as conn:
            for line in lines:
                pid = line.get("product_id")
                if not pid:
                    continue
                bo = int(line.get("quantity_backordered", 0) or 0)
                alloc = int(line.get("allocated_qty", 0) or 0)
                sku = line.get("sku", "?")
                desc = (line.get("description") or "")[:50]
                row = conn.execute("SELECT supplier FROM products WHERE id=?", (pid,)).fetchone()
                vendor = (row[0] if row else None) or "Unknown"
                if bo > 0:
                    needs_po.append({"product_id": pid, "sku": sku, "description": desc,
                                     "qty": bo, "vendor": vendor})
                elif alloc > 0:
                    stocked.append({"sku": sku, "description": desc, "qty": alloc})

        if not needs_po:
            QMessageBox.information(self, "No PO Needed", "All items allocated from stock.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Create Purchase Order(s)")
        dlg.setMinimumWidth(520)
        dlay = QVBoxLayout(dlg)
        dlay.addWidget(QLabel(f"<b>{len(needs_po)} item(s) need purchasing</b>"))
        if stocked:
            lines_text = "\n".join(
                f"  \u2714 {s['sku']} \u2014 {s['description']} (Qty {s['qty']} allocated)"
                for s in stocked
            )
            lbl = QLabel(f"Already allocated:\n{lines_text}")
            lbl.setStyleSheet("font-size: 11px; color: #059669; margin: 4px 0;")
            lbl.setWordWrap(True)
            dlay.addWidget(lbl)
        dlay.addSpacing(6)
        dlay.addWidget(QLabel("<b>Items to order:</b>"))
        # Group checkboxes by vendor so the user can toggle a whole vendor
        # at once instead of clicking every line individually (#1).
        from collections import defaultdict
        by_vendor: dict[str, list[dict]] = defaultdict(list)
        for item in needs_po:
            by_vendor[item["vendor"]].append(item)
        checkboxes = []
        vendors = set(by_vendor.keys())
        for vendor_name in sorted(by_vendor.keys()):
            items = by_vendor[vendor_name]
            header_row = QHBoxLayout()
            header_cb = QCheckBox(f"All from {vendor_name} ({len(items)})")
            header_cb.setChecked(True)
            header_cb.setStyleSheet("font-weight: bold; color: #1f2937;")
            header_row.addWidget(header_cb)
            header_row.addStretch()
            dlay.addLayout(header_row)
            vendor_cbs: list[QCheckBox] = []
            for item in items:
                cb = QCheckBox(
                    f"    \u26a0 {item['sku']} \u2014 {item['description']}  "
                    f"(Qty: {item['qty']})"
                )
                cb.setChecked(True)
                cb.setProperty("product_id", item["product_id"])
                vendor_cbs.append(cb)
                checkboxes.append(cb)
                dlay.addWidget(cb)

            def _make_toggle(cbs=vendor_cbs, hdr=header_cb):
                def _on_header(checked: bool):
                    for c in cbs:
                        c.blockSignals(True)
                        c.setChecked(checked)
                        c.blockSignals(False)
                def _on_child(_=None):
                    states = [c.isChecked() for c in cbs]
                    hdr.blockSignals(True)
                    hdr.setChecked(all(states))
                    hdr.blockSignals(False)
                return _on_header, _on_child

            on_header, on_child = _make_toggle()
            header_cb.toggled.connect(on_header)
            for c in vendor_cbs:
                c.toggled.connect(on_child)
        dlay.addSpacing(8)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        create_btn = QPushButton(f"Create PO ({len(vendors)} vendor(s))")
        create_btn.setStyleSheet(
            "QPushButton { background: #059669; color: white; font-weight: bold; "
            "padding: 6px 16px; border-radius: 4px; border: none; }"
            "QPushButton:hover { background: #047857; }"
        )
        create_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(create_btn)
        dlay.addLayout(btn_row)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected_ids = [cb.property("product_id") for cb in checkboxes if cb.isChecked()]
        if not selected_ids:
            return
        existing_po_by_vendor = self._resolve_po_merge_map(selected_ids)
        if existing_po_by_vendor is None:
            return
        final_reply = QMessageBox.question(
            self,
            "Create Purchase Order(s)",
            f"Create PO(s) for {len(selected_ids)} selected item(s)?\n\n"
            "This will generate or update linked vendor POs.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if final_reply != QMessageBox.StandardButton.Yes:
            return
        result = create_po_from_so(
            self._so_id,
            product_ids=selected_ids,
            existing_po_by_vendor=existing_po_by_vendor,
        )
        if not result.get("success"):
            QMessageBox.warning(self, "Error", result.get("error", "No POs created"))
            return
        pos_created = result["purchase_orders"]
        for po in pos_created:
            action = "Added to existing PO" if po.get("action") == "merged" else "Created PO"
            log_so_activity(self._so_id, "po_created", f"{action}: {po['po_number']} \u2014 {po['vendor']}")
        with get_db() as conn:
            for line in lines:
                pid = line.get("product_id")
                if pid in selected_ids:
                    bo = int(line.get("quantity_backordered", 0) or 0)
                    conn.execute("UPDATE sales_order_lines SET on_po_qty = ? WHERE id = ?",
                                 (bo, line.get("id")))
            conn.commit()
        self._refresh()
        if pos_created:
            self._do_open_po(pos_created[0]["po_id"])

    def _resolve_po_merge_map(self, product_ids: list[int]) -> dict[int, int] | None:
        """Ask whether selected vendor groups should merge into open POs."""
        if not product_ids:
            return {}
        from db import get_db

        vendor_groups: dict[int, str] = {}
        with get_db() as conn:
            placeholders = ",".join("?" for _ in product_ids)
            rows = conn.execute(
                f"""
                SELECT DISTINCT v.id, v.name
                FROM products p
                LEFT JOIN vendors v ON v.name = p.supplier
                WHERE p.id IN ({placeholders})
                  AND v.id IS NOT NULL
                """,
                tuple(product_ids),
            ).fetchall()
        for row in rows:
            vendor_groups[row[0]] = row[1]

        existing_po_by_vendor: dict[int, int] = {}
        for vendor_id, vendor_name in vendor_groups.items():
            open_pos = find_open_purchase_orders_for_vendor(vendor_id)
            if not open_pos:
                continue
            choice = self._prompt_existing_vendor_po(vendor_name, open_pos)
            if choice == "cancel":
                return None
            if choice == "merge":
                existing_po_by_vendor[vendor_id] = int(open_pos[0]["id"])
        return existing_po_by_vendor

    def _prompt_existing_vendor_po(self, vendor_name: str, open_pos: list[dict]) -> str:
        """Prompt whether to merge into the newest active PO for a vendor."""
        existing = open_pos[0]
        extra = ""
        if len(open_pos) > 1:
            extra = f"\n{len(open_pos) - 1} other open PO(s) also exist for this vendor."

        while True:
            dlg = QDialog(self)
            dlg.setWindowTitle("Open PO Found")
            dlg.setMinimumWidth(420)
            dlay = QVBoxLayout(dlg)
            dlay.addWidget(QLabel(f"<b>Open PO found for {vendor_name}</b>"))
            dlay.addSpacing(4)
            dlay.addWidget(QLabel(existing.get("po_number", "")))
            dlay.addWidget(QLabel(f"Status: {(existing.get('status') or '').upper()}"))
            dlay.addWidget(QLabel(f"Lines: {int(existing.get('line_count') or 0)}"))
            dlay.addWidget(QLabel(f"Total: ${float(existing.get('total') or 0):,.2f}{extra}"))
            dlay.addSpacing(10)

            row = QHBoxLayout()
            merge_btn = QPushButton("Add to Existing PO")
            merge_btn.setDefault(True)
            new_btn = QPushButton("Create New PO")
            view_btn = QPushButton("View Existing PO")
            cancel_btn = QPushButton("Cancel")
            row.addWidget(merge_btn)
            row.addWidget(new_btn)
            row.addWidget(view_btn)
            row.addWidget(cancel_btn)
            dlay.addLayout(row)

            choice = {"value": "cancel"}
            merge_btn.clicked.connect(lambda: (choice.__setitem__("value", "merge"), dlg.accept()))
            new_btn.clicked.connect(lambda: (choice.__setitem__("value", "new"), dlg.accept()))
            cancel_btn.clicked.connect(dlg.reject)

            def _view_existing() -> None:
                choice["value"] = "view"
                dlg.accept()

            view_btn.clicked.connect(_view_existing)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return "cancel"
            if choice["value"] == "view":
                self._do_open_po(int(existing["id"]))
                continue
            return choice["value"]

    def _convert_to_invoice(self) -> None:
        lines = get_so_lines(self._so_id)
        if not lines:
            QMessageBox.warning(self, "No Lines", "No items to invoice.")
            return
        ready = []
        pending = []
        for line in lines:
            ls = derive_so_line_status(line)
            sku = line.get("sku", "?")
            desc = (line.get("description") or "")[:40]
            if ls in ("PICKED", "ALLOCATED", "RECEIVED", "READY_TO_INVOICE"):
                ready.append(f"\u2714 {sku} \u2014 {desc}")
            elif ls != "INVOICED":
                pending.append(f"\u26a0 {sku} \u2014 {desc} [{ls.replace('_', ' ')}]")

        if pending:
            dlg = QDialog(self)
            dlg.setWindowTitle("Invoice \u2014 Items Not Fully Ready")
            dlg.setMinimumWidth(480)
            dlay = QVBoxLayout(dlg)
            dlay.addWidget(QLabel("<b>Some items are not yet received or picked.</b>"))
            dlay.addSpacing(4)
            if ready:
                dlay.addWidget(QLabel("<b>Available Now:</b>"))
                for t in ready:
                    lbl = QLabel(t)
                    lbl.setStyleSheet("color: #059669; font-size: 11px; margin-left: 8px;")
                    dlay.addWidget(lbl)
            dlay.addSpacing(4)
            dlay.addWidget(QLabel("<b>Pending:</b>"))
            for t in pending:
                lbl = QLabel(t)
                lbl.setStyleSheet("color: #D97706; font-size: 11px; margin-left: 8px;")
                dlay.addWidget(lbl)
            dlay.addSpacing(10)
            self._inv_choice = None
            def set_choice(c):
                self._inv_choice = c
                dlg.accept()
            btn_col = QVBoxLayout()
            btn_col.setSpacing(4)
            if ready:
                b1 = QPushButton(f"Invoice Available Items Only ({len(ready)})")
                b1.setStyleSheet(
                    "QPushButton { background: #059669; color: white; font-weight: bold; "
                    "padding: 6px; border-radius: 4px; border: none; }"
                )
                b1.clicked.connect(lambda: set_choice("partial"))
                btn_col.addWidget(b1)
            b2 = QPushButton("Invoice All Items (Override)")
            b2.setStyleSheet(
                "QPushButton { background: #D97706; color: white; font-weight: bold; "
                "padding: 6px; border-radius: 4px; border: none; }"
            )
            b2.clicked.connect(lambda: set_choice("all"))
            btn_col.addWidget(b2)
            # Deposit invoice — hidden in v1 (feature not yet implemented)
            # b3 = QPushButton("Create Deposit Invoice")
            # b3.setStyleSheet(
            #     "QPushButton { background: #6366F1; color: white; font-weight: bold; "
            #     "padding: 6px; border-radius: 4px; border: none; }"
            # )
            # b3.clicked.connect(lambda: set_choice("deposit"))
            # btn_col.addWidget(b3)
            b4 = QPushButton("Cancel")
            b4.clicked.connect(dlg.reject)
            btn_col.addWidget(b4)
            dlay.addLayout(btn_col)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            if self._inv_choice == "deposit":
                # Defensive — button is hidden in v1, but in case any caller
                # still sets this choice, fall through cleanly.
                QMessageBox.information(self, "Deposit Invoice",
                                        "Deposit invoices are not available in this release.")
                return
        else:
            reply = QMessageBox.question(
                self, "Create Invoice", "All items are ready. Create invoice?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # P1.5 — "Invoice Available Items Only" → ship-available, backorder rest.
        if getattr(self, "_inv_choice", None) == "partial":
            result = convert_so_to_invoice_partial(self._so_id)
            if not result.get("success"):
                QMessageBox.warning(self, "Error",
                    result.get("error", "Could not create partial invoice"))
                return
            invoiced_n = result.get("invoiced_line_count", 0)
            backorder_n = result.get("backordered_line_count", 0)
            log_so_activity(
                self._so_id, "partial_invoiced",
                f"Invoice {result.get('invoice_number', '')} created for {invoiced_n} line(s); "
                f"{backorder_n} backordered",
            )
            inv_id = result.get("invoice_id")
            self._refresh()
            try:
                self.so_changed.emit()
            except Exception:
                pass
            if inv_id:
                self._post_invoice_actions(int(inv_id),
                                          result.get("invoice_number", ""))
            return

        result = convert_so_to_invoice(self._so_id)
        if result.get("error"):
            QMessageBox.warning(self, "Error", result["error"])
        else:
            inv_id = result.get("invoice_id")
            log_so_activity(self._so_id, "invoiced", f"Invoice {result.get('invoice_number', '')} created")
            self._refresh()
            try:
                self.so_changed.emit()
            except Exception:
                pass
            if inv_id:
                self._post_invoice_actions(int(inv_id),
                                          result.get("invoice_number", ""))

    def _post_invoice_actions(self, invoice_id: int, invoice_number: str) -> None:
        """Show the post-invoice action chooser (#10).

        Buttons: Print & Close · Print + View · Email Invoice · Cancel.
        Cancel just dismisses (the invoice is already created either way).
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("Invoice Created")
        dlg.setMinimumWidth(420)
        dlay = QVBoxLayout(dlg)
        dlay.addWidget(QLabel(
            f"<b>Invoice {invoice_number} created.</b><br>"
            "What would you like to do next?"
        ))
        dlay.addSpacing(8)

        choice = {"action": None}
        def pick(action: str):
            choice["action"] = action
            dlg.accept()

        col = QVBoxLayout()
        col.setSpacing(6)
        b_print = QPushButton("\U0001f5a8  Print && Close")
        b_print.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; font-weight: bold; "
            "padding: 8px; border-radius: 4px; border: none; }"
        )
        b_print.clicked.connect(lambda: pick("print_close"))
        col.addWidget(b_print)

        b_pv = QPushButton("\U0001f4c4  Print + View Invoice")
        b_pv.setStyleSheet(
            "QPushButton { background: #059669; color: white; font-weight: bold; "
            "padding: 8px; border-radius: 4px; border: none; }"
        )
        b_pv.clicked.connect(lambda: pick("print_view"))
        col.addWidget(b_pv)

        b_email = QPushButton("\u2709  Email Invoice")
        b_email.setStyleSheet(
            "QPushButton { background: #7C3AED; color: white; font-weight: bold; "
            "padding: 8px; border-radius: 4px; border: none; }"
        )
        b_email.clicked.connect(lambda: pick("email"))
        col.addWidget(b_email)

        b_cancel = QPushButton("Cancel")
        b_cancel.clicked.connect(dlg.reject)
        col.addWidget(b_cancel)

        dlay.addLayout(col)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        action = choice["action"]
        if action in ("print_close", "print_view"):
            try:
                from db import get_invoice
                from jaks_inventory.utils.pdf_invoice import generate_invoice_pdf
                inv = get_invoice(invoice_id) or {}
                path = generate_invoice_pdf(inv)
                # Always open the PDF — the user explicitly asked to print.
                import os, sys, subprocess
                if sys.platform.startswith("win"):
                    os.startfile(path)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except Exception as exc:
                QMessageBox.warning(self, "Print Failed",
                                    f"Could not generate invoice PDF:\n{exc}")
            if action == "print_view":
                try:
                    from jaks_inventory.ui.invoice_dialog import InvoiceDialog
                    d = InvoiceDialog(invoice_id, parent=self.parent())
                    d.exec()
                except Exception:
                    pass
        elif action == "email":
            try:
                from jaks_inventory.utils.notification_service import (
                    send_invoice_email, email_configured,
                )
                if not email_configured():
                    QMessageBox.warning(self, "Email Not Configured",
                                        "SMTP is not configured. Add credentials in Settings.")
                    return
                res = send_invoice_email(invoice_id)
                if res.get("success"):
                    QMessageBox.information(self, "Email Sent",
                                            "Invoice emailed to customer.")
                else:
                    QMessageBox.warning(self, "Email Failed",
                                        res.get("error", "Unknown error"))
            except Exception as exc:
                QMessageBox.warning(self, "Email Failed", str(exc))

    def _estimate_shipping(self) -> None:
        lines = get_so_lines(self._so_id)
        if not lines:
            self._shipping_est_label.setText("No items")
            return
        carrier = self._carrier_combo.currentText()
        if carrier == "Local Delivery":
            self._shipping_est_label.setText("Local delivery \u2014 fuel surcharge only")
            return
        service = "LTL" if carrier == "Freight" else "Ground"
        items = [{"product_id": l.get("product_id"), "quantity": l.get("quantity_ordered", 1)} for l in lines]
        result = estimate_order_shipping(items, carrier, service)
        est = result.get("estimated_cost", 0)
        weight = result.get("total_weight", 0)
        no_wt = result.get("items_without_weight", 0)
        if result.get("error"):
            self._shipping_est_label.setText(f"N/A \u2014 {result['error']}")
        elif weight <= 0:
            self._shipping_est_label.setText("N/A \u2014 no weights")
        else:
            note = f" ({no_wt} missing)" if no_wt else ""
            self._shipping_est_label.setText(f"Est: ${est:,.2f} ({weight:.1f} lbs){note}")
            self._shipping_spin.setValue(est)

    def _on_shipping_changed(self) -> None:
        self._update_total_display()

    def _on_cc_fee_toggled(self, checked: bool) -> None:
        """Persist the 3% CC convenience-fee toggle and refresh totals."""
        if not self._so_id:
            return
        try:
            update_sales_order(self._so_id, cc_fee_enabled=1 if checked else 0)
        except Exception:
            pass
        self._update_total_display()

    def _update_total_display(self) -> None:
        so = get_sales_order(self._so_id)
        if not so:
            return
        subtotal = float(so.get("total", 0) or 0)
        amount = self._shipping_spin.value()
        grand = subtotal + amount
        cc_amount = round(grand * 0.03, 2) if int(so.get("cc_fee_enabled") or 0) else 0.0
        grand_with_cc = grand + cc_amount
        self._total_label.setText(f"${subtotal:,.2f}")
        carrier = self._carrier_combo.currentText()
        cc_suffix = f" + 3% CC = ${grand_with_cc:,.2f}" if cc_amount > 0 else ""
        if carrier == "Local Delivery":
            self._grand_total_label.setText(f"(+ fuel = ${grand:,.2f}{cc_suffix})")
            update_sales_order(self._so_id, fuel_surcharge=amount, shipping=0,
                               ship_method="Local Delivery")
        else:
            self._grand_total_label.setText(f"(+ ship = ${grand:,.2f}{cc_suffix})")
            update_sales_order(self._so_id, shipping=amount, fuel_surcharge=0,
                               ship_method=f"{carrier} Ground")

    def _on_carrier_changed(self, carrier: str) -> None:
        """Swap the cost field label between Fuel Surcharge / Shipping and
        reload the saved value for the newly selected carrier mode."""
        so = get_sales_order(self._so_id) or {}
        self._shipping_spin.blockSignals(True)
        if carrier == "Local Delivery":
            self._ship_cost_label.setText("Fuel Surcharge $:")
            self._shipping_spin.setValue(float(so.get("fuel_surcharge", 0) or 0))
        else:
            self._ship_cost_label.setText("Shipping $:")
            self._shipping_spin.setValue(float(so.get("shipping", 0) or 0))
        self._shipping_spin.blockSignals(False)
        self._update_total_display()

    # ------------------------------------------------------------------
    #  Tracking # / customer notes / print / email customer  (Phase 4)
    # ------------------------------------------------------------------
    def _on_tracking_changed(self) -> None:
        if not self._so_id:
            return
        try:
            update_sales_order(
                self._so_id,
                tracking_number=self._tracking_edit.text().strip() or None,
            )
        except Exception:
            pass

    def _on_customer_notes_changed(self) -> None:
        if not self._so_id:
            return
        try:
            update_sales_order(
                self._so_id,
                customer_notes=self._customer_notes_edit.toPlainText().strip() or None,
            )
        except Exception:
            pass

    def eventFilter(self, obj, event):
        # Persist customer-notes when the editor loses focus.
        try:
            from PySide6.QtCore import QEvent
            if obj is getattr(self, "_customer_notes_edit", None) \
                    and event.type() == QEvent.Type.FocusOut:
                self._on_customer_notes_changed()
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _print_sales_order(self) -> None:
        so = get_sales_order(self._so_id)
        if not so:
            return
        try:
            from jaks_inventory.utils.pdf_sales_order import generate_sales_order_pdf
            path = generate_sales_order_pdf(so)
        except Exception as exc:
            QMessageBox.warning(self, "Print Failed", f"Could not generate SO PDF:\n{exc}")
            return
        # Open it in the system viewer (matches the quote print behavior).
        try:
            import os, sys, subprocess
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            QMessageBox.information(self, "Sales Order PDF", f"Saved to:\n{path}")

    def _print_core_slip(self) -> None:
        """Generate + open the preliminary core-return slip for this SO."""
        try:
            from jaks_inventory.utils.pdf_core_receipt import (
                generate_core_slip_for_sales_order,
            )
            path = generate_core_slip_for_sales_order(int(self._so_id))
        except ValueError as exc:
            QMessageBox.information(self, "No Cores", str(exc))
            return
        except Exception as exc:
            QMessageBox.warning(
                self, "Print Failed",
                f"Could not generate core slip:\n{exc}",
            )
            return
        try:
            import os, sys, subprocess
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            QMessageBox.information(self, "Core Slip", f"Saved to:\n{path}")

    def _email_customer_so(self) -> None:
        so = get_sales_order(self._so_id)
        if not so:
            return
        cust_id = so.get("customer_id")
        if not cust_id:
            QMessageBox.warning(self, "No Customer", "Assign a customer first.")
            return
        try:
            from db import get_customer
            cust = get_customer(int(cust_id)) or {}
        except Exception:
            cust = {}
        to_addr = (cust.get("email") or "").strip()
        if not to_addr:
            QMessageBox.warning(
                self,
                "No Email",
                "Customer has no email address on file. Update the customer record first.",
            )
            return
        try:
            from jaks_inventory.utils.notification_service import (
                send_sales_order_email, email_configured,
            )
        except ImportError:
            QMessageBox.warning(self, "Email Disabled",
                                "Sales-order email helper is not available.")
            return
        if not email_configured():
            QMessageBox.warning(self, "Email Not Configured",
                                "SMTP is not configured. Add credentials in Settings.")
            return
        reply = QMessageBox.question(
            self,
            "Email Sales Order",
            f"Email Sales Order {so.get('so_number', '')} to:\n  {to_addr}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            res = send_sales_order_email(int(self._so_id))
            if res.get("success"):
                QMessageBox.information(self, "Email Sent",
                                        f"Sales Order emailed to {to_addr}.")
                log_so_activity(self._so_id, "emailed",
                                f"Customer email sent to {to_addr}")
            else:
                QMessageBox.warning(self, "Email Failed",
                                    res.get("error", "Unknown error"))
        except Exception as exc:
            QMessageBox.warning(self, "Email Failed", str(exc))

    def _open_linked_quote(self) -> None:
        so = get_sales_order(self._so_id)
        if not so:
            return
        quote_id = so.get("quote_id")
        if not quote_id:
            return
        try:
            from jaks_inventory.ui.quote_dialog import QuoteDialog
            dlg = QuoteDialog(quote_id=quote_id, parent=self)
            dlg.exec()
            self._refresh()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _open_linked_po(self) -> None:
        if not self._linked_po_ids:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Question)
            msg.setWindowTitle("No Linked Purchase Order")
            msg.setText("This sales order has no linked PO yet.")
            msg.setInformativeText("Would you like to create a Purchase Order now?")
            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            # Safety default for production use.
            msg.setDefaultButton(QMessageBox.StandardButton.No)
            if msg.exec() == QMessageBox.StandardButton.Yes:
                self._create_po()
            return
        if len(self._linked_po_ids) == 1:
            self._do_open_po(self._linked_po_ids[0])
        else:
            # Show richer chooser with PO details
            from PySide6.QtWidgets import QScrollArea
            dlg = QDialog(self)
            dlg.setWindowTitle("Select Purchase Order")
            dlg.setMinimumWidth(520)
            dlg.setMinimumHeight(300)
            dlay = QVBoxLayout(dlg)
            dlay.addWidget(QLabel("<b>Multiple linked Purchase Orders:</b>"))
            dlay.addSpacing(6)
            
            # Scrollable container for PO buttons
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll_widget = QWidget()
            scroll_layout = QVBoxLayout(scroll_widget)
            
            chosen_id = [None]
            po_count = 0
            for pid in self._linked_po_ids:
                try:
                    po = get_purchase_order(pid)
                    if not po:
                        continue
                    po_num = po.get("po_number", "?")
                    vendor = po.get("vendor_name", "")
                    status = (po.get("status") or "draft").upper()
                    total = float(po.get("total") or 0)
                    date = str(po.get("order_date") or "")[:10]
                    btn = QPushButton(
                        f"{po_num}  \u2014  {vendor}  |  {status}  |  "
                        f"${total:,.2f}  |  {date}"
                    )
                    btn.setStyleSheet(
                        "QPushButton { text-align: left; padding: 10px 14px; "
                        "border: 1px solid #D1D5DB; border-radius: 4px; "
                        "font-size: 12px; font-weight: 600; "
                        "color: #1F2937; background: #F9FAFB; }"
                        "QPushButton:hover { background: #DBEAFE; "
                        "color: #1E3A8A; border-color: #3B82F6; }"
                    )
                    btn.clicked.connect(
                        lambda checked, p=pid: (chosen_id.__setitem__(0, p), dlg.accept())
                    )
                    scroll_layout.addWidget(btn)
                    po_count += 1
                except Exception as e:
                    # Log error but continue
                    err_lbl = QLabel(f"Error loading PO {pid}: {str(e)}")
                    err_lbl.setStyleSheet("color: #EF4444; font-size: 10px;")
                    scroll_layout.addWidget(err_lbl)
            
            scroll_layout.addStretch()
            scroll.setWidget(scroll_widget)
            dlay.addWidget(scroll)
            
            # Show count
            dlay.addSpacing(6)
            if po_count == 0:
                count_lbl = QLabel(
                    f"<i style='color: #EF4444;'>No Purchase Orders could be loaded "
                    f"({len(self._linked_po_ids)} IDs found)</i>"
                )
                dlay.addWidget(count_lbl)
            
            dlay.addSpacing(6)
            cancel_btn = QPushButton("Cancel")
            cancel_btn.clicked.connect(dlg.reject)
            dlay.addWidget(cancel_btn)
            if dlg.exec() == QDialog.DialogCode.Accepted and chosen_id[0]:
                self._do_open_po(chosen_id[0])

    def _do_open_po(self, po_id: int) -> None:
        try:
            from jaks_inventory.ui.po_dialog import PODialog
            dlg = PODialog(po_id, parent=self)
            dlg.po_changed.connect(self._refresh)
            dlg.exec()
            self._refresh()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not open PO:\n{e}")

    def _on_invoice_action(self) -> None:
        so = get_sales_order(self._so_id)
        if not so:
            return
        inv_id = so.get("invoice_id")
        if inv_id:
            try:
                from jaks_inventory.ui.invoice_dialog import InvoiceDialog
                dlg = InvoiceDialog(inv_id, parent=self)
                dlg.exec()
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))
        else:
            self._convert_to_invoice()


# ----------------------------------------------------------------------
# Non-modal helper: focus an existing SO dialog if it's already open.
# Callers should prefer this helper over instantiating ``SODetailDialog``
# directly so the user can keep multiple SOs (and the main menu) usable
# at the same time.
# ----------------------------------------------------------------------
_OPEN_SO_DIALOGS: dict[int, "SODetailDialog"] = {}


def open_so_detail(so_id: int, parent: QWidget | None = None,
                   on_closed=None) -> "SODetailDialog":
    """Open or focus a non-modal Sales Order detail dialog.

    Returns the dialog so callers can connect to ``so_changed`` if they
    need to refresh their own list views. ``on_closed`` (optional) is
    invoked when the dialog is destroyed.
    """
    existing = _OPEN_SO_DIALOGS.get(int(so_id))
    if existing is not None:
        try:
            existing.raise_()
            existing.activateWindow()
            return existing
        except RuntimeError:
            # Underlying C++ object was deleted; fall through to recreate
            _OPEN_SO_DIALOGS.pop(int(so_id), None)

    dlg = SODetailDialog(int(so_id), parent=parent)
    _OPEN_SO_DIALOGS[int(so_id)] = dlg

    def _cleanup(*_args) -> None:
        _OPEN_SO_DIALOGS.pop(int(so_id), None)
        if on_closed is not None:
            try:
                on_closed()
            except Exception:
                pass

    dlg.destroyed.connect(_cleanup)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg

