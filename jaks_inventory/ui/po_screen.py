"""Purchase Orders management screen with Low Stock tab."""
from __future__ import annotations

import re
from datetime import date, datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from db import (
    add_or_merge_po_line,
    create_purchase_order,
    find_open_purchase_orders_for_vendor,
    get_all_purchase_orders,
    get_purchase_order,
    get_sales_order,
    update_po_status,
    update_purchase_order,
    list_vendor_core_returns,
    mark_vendor_core_shipped,
    mark_vendor_core_credited,
    write_off_vendor_core,
    void_vendor_core,
)
from core.badges import PO_BADGES, badge_colors
from core.format import format_date, format_money

from .po_dialog import PODialog
from .receive_dialog import ReceiveDialog
from .restock_wizard_dialog import RestockWizardDialog
from .so_detail_dialog import SODetailDialog, open_so_detail


class POScreen(QWidget):
    """Screen for managing purchase orders with low stock overview."""

    def __init__(self, parent: QWidget | None = None, initial_tab: int = 0) -> None:
        super().__init__(parent)
        
        self._tabs = QTabWidget()
        
        # Tab 1: Purchase Orders
        po_tab = self._build_po_tab()
        self._tabs.addTab(po_tab, "📋 Purchase Orders")
        
        # Tab 2: Low Stock / Restock
        low_stock_tab = self._build_low_stock_tab()
        self._tabs.addTab(low_stock_tab, "⚠️ Low Stock")

        # Tab 3: PO Receipts
        receipts_tab = self._build_receipts_tab()
        self._tabs.addTab(receipts_tab, "📥 PO Receipts")

        # Tab 4: Vendor Cores (cores we owe back to vendors)
        vendor_cores_tab = self._build_vendor_cores_tab()
        self._tabs.addTab(vendor_cores_tab, "🔄 Vendor Cores")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        
        layout = QVBoxLayout()
        layout.addWidget(self._tabs)
        self.setLayout(layout)
        
        if 0 <= initial_tab < self._tabs.count():
            self._tabs.setCurrentIndex(initial_tab)

        self._load_pos()

    def _build_po_tab(self) -> QWidget:
        """Build the Purchase Orders tab."""
        widget = QWidget()
        # Filter row
        self._status_filter = QComboBox()
        self._status_filter.addItem("All Statuses", "")
        self._status_filter.addItem("Draft", "draft")
        self._status_filter.addItem("Sent", "sent")
        self._status_filter.addItem("Confirmed", "confirmed")
        self._status_filter.addItem("Partial Received", "partial")
        self._status_filter.addItem("Received", "received")
        self._status_filter.addItem("Closed", "closed")
        self._status_filter.addItem("Cancelled", "cancelled")
        self._status_filter.currentIndexChanged.connect(self._load_pos)

        self._vendor_filter = QComboBox()
        self._vendor_filter.addItem("All Vendors", None)
        self._vendor_filter.currentIndexChanged.connect(self._load_pos)

        self._new_btn = QPushButton("➕ New PO")
        self._new_btn.clicked.connect(self._on_new)

        self._restock_btn = QPushButton("📦 Auto-Restock")
        self._restock_btn.setToolTip("Generate draft POs from below-min-stock products")
        self._restock_btn.clicked.connect(self._on_restock)

        self._submit_btn = QPushButton("📤 Mark Sent")
        self._submit_btn.clicked.connect(self._on_submit)
        self._submit_btn.setEnabled(False)
        self._submit_btn.setToolTip("Change status to 'Sent'")

        self._confirm_btn = QPushButton("✅ Confirm")
        self._confirm_btn.clicked.connect(self._on_confirm)
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.setToolTip("Vendor acknowledged — change status to 'Confirmed'")

        self._receive_btn = QPushButton("📥 Receive PO")
        self._receive_btn.clicked.connect(self._on_receive)
        self._receive_btn.setEnabled(False)

        self._view_btn = QPushButton("👁 View")
        self._view_btn.clicked.connect(self._on_view)
        self._view_btn.setEnabled(False)

        self._reopen_btn = QPushButton("🔓 Reopen")
        self._reopen_btn.clicked.connect(self._on_reopen)
        self._reopen_btn.setEnabled(False)

        self._cancel_btn = QPushButton("❌ Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._cancel_btn.setEnabled(False)

        # Focus mode toggle
        self._focus_mode_btn = QPushButton("🎯 Focus Problems")
        self._focus_mode_btn.setCheckable(True)
        self._focus_mode_btn.setToolTip(
            "Show only what needs action: overdue, SO-linked, partial, or high-value POs.\n"
            "Hides drafts with no urgency and closed/cancelled POs."
        )
        self._focus_mode_btn.setStyleSheet(
            "QPushButton:checked { background: #FEF3C7; border: 1px solid #F59E0B; color: #92400E; }"
        )
        self._focus_mode_btn.toggled.connect(lambda _: self._load_pos())

        # Show history toggle (cancelled + closed)
        self._show_history_btn = QPushButton("📜 Show History")
        self._show_history_btn.setCheckable(True)
        self._show_history_btn.setToolTip(
            "Include Cancelled and Closed POs in the table.\nDefault: hidden so you can focus on active work."
        )
        self._show_history_btn.setStyleSheet(
            "QPushButton:checked { background: #F3F4F6; border: 1px solid #9CA3AF; color: #374151; }"
        )
        self._show_history_btn.toggled.connect(lambda _: self._load_pos())

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Status:"))
        filter_row.addWidget(self._status_filter)
        filter_row.addWidget(QLabel("Vendor:"))
        filter_row.addWidget(self._vendor_filter)
        filter_row.addStretch()
        filter_row.addWidget(self._focus_mode_btn)
        filter_row.addWidget(self._show_history_btn)
        filter_row.addWidget(self._restock_btn)
        filter_row.addWidget(self._new_btn)
        filter_row.addWidget(self._submit_btn)
        filter_row.addWidget(self._confirm_btn)
        filter_row.addWidget(self._receive_btn)
        filter_row.addWidget(self._view_btn)
        filter_row.addWidget(self._reopen_btn)
        filter_row.addWidget(self._cancel_btn)

        # Vendor summary panel (hidden until rows are loaded)
        self._vendor_summary_widget = QWidget()
        self._vendor_summary_widget.setStyleSheet(
            "QWidget { background: #F0FDF4; border: 1px solid #A7F3D0; "
            "border-radius: 6px; margin-bottom: 4px; }"
        )
        self._vendor_summary_layout = QHBoxLayout(self._vendor_summary_widget)
        self._vendor_summary_layout.setContentsMargins(10, 6, 10, 6)
        self._vendor_summary_widget.setVisible(False)

        # Quick Decision Strip — clickable stat tiles above the table
        self._strip_widget = QWidget()
        self._strip_widget.setStyleSheet(
            "QWidget { background: #FAFAF9; border: 1px solid #E5E7EB; "
            "border-radius: 6px; }"
        )
        self._strip_layout = QHBoxLayout(self._strip_widget)
        self._strip_layout.setContentsMargins(8, 4, 8, 4)
        self._strip_layout.setSpacing(8)
        self._strip_widget.setVisible(False)

        # Table — extended columns
        self._table = QTableWidget(0, 16)
        self._table.setHorizontalHeaderLabels([
            "PO #", "Vendor", "Status", "Order Date", "Expected",
            "Linked SO", "Health", "Lines", "Received", "Outstanding", "Age", "Overdue", "Total", "$ Outstanding", "Due In", "Actions"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setSortingEnabled(True)
        self._table.cellDoubleClicked.connect(self._on_table_double_click)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.cellClicked.connect(self._on_table_cell_clicked)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_po_context_menu)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setStyleSheet(
            "QTableWidget::item:selected { background: #DDEBFF; color: #111827; }"
        )

        self._status_label = QLabel("")

        layout = QVBoxLayout()
        layout.addLayout(filter_row)
        layout.addWidget(self._strip_widget)
        layout.addWidget(self._vendor_summary_widget)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._status_label)
        widget.setLayout(layout)

        self._pos: list[dict] = []
        self._restore_column_widths()
        self._table.horizontalHeader().sectionResized.connect(self._save_column_widths)
        return widget

    def _build_low_stock_tab(self) -> QWidget:
        """Build the Low Stock tab showing products below threshold."""
        widget = QWidget()
        
        # Controls
        self._ls_threshold = QSpinBox()
        self._ls_threshold.setRange(0, 999)
        self._ls_threshold.setValue(5)
        self._ls_threshold.setPrefix("Below: ")
        
        ls_refresh_btn = QPushButton("↻ Refresh")
        ls_refresh_btn.clicked.connect(self._load_low_stock)
        
        ls_restock_btn = QPushButton("📦 Auto-Restock")
        ls_restock_btn.setToolTip("Generate draft POs from below-min-stock products")
        ls_restock_btn.clicked.connect(self._on_restock)
        
        ctrl_row = QHBoxLayout()
        ctrl_row.addWidget(QLabel("Threshold:"))
        ctrl_row.addWidget(self._ls_threshold)
        ctrl_row.addWidget(ls_refresh_btn)
        ctrl_row.addStretch()
        ctrl_row.addWidget(ls_restock_btn)
        
        # Table
        self._ls_table = QTableWidget(0, 6)
        self._ls_table.setHorizontalHeaderLabels([
            "SKU", "Title", "Qty On Hand", "Min Qty", "Deficit", "Vendor"
        ])
        self._ls_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._ls_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._ls_table.setSortingEnabled(True)
        self._ls_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        
        self._ls_status = QLabel("")
        
        layout = QVBoxLayout()
        layout.addLayout(ctrl_row)
        layout.addWidget(self._ls_table, 1)
        layout.addWidget(self._ls_status)
        widget.setLayout(layout)
        
        # Load on first show
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._low_stock_loaded = False
        self._receipts_loaded = False
        
        return widget

    def _on_tab_changed(self, index: int) -> None:
        """Load low stock data when that tab is selected."""
        if index == 1 and not self._low_stock_loaded:
            self._load_low_stock()
        elif index == 2 and not self._receipts_loaded:
            self._load_receipts()

    def _build_receipts_tab(self) -> QWidget:
        """Build the PO receipts workflow tab (full warehouse receiving)."""
        from .po_receipts_screen import POReceiptsWorkflow
        self._receipts_workflow = POReceiptsWorkflow()
        self._receipts_workflow.receipts_changed.connect(self._load_pos)
        self._receipts_loaded = True  # widget loads itself
        return self._receipts_workflow

    def _load_receipts(self) -> None:
        """Refresh the receipts workflow widget if it exists."""
        if hasattr(self, "_receipts_workflow"):
            self._receipts_workflow.refresh()
        self._receipts_loaded = True

    def _load_low_stock(self) -> None:
        """Load low stock products."""
        try:
            from db import get_low_stock_products
            threshold = self._ls_threshold.value()
            products = get_low_stock_products(threshold)
        except Exception as e:
            self._ls_status.setText(f"Error: {e}")
            return
        
        self._low_stock_loaded = True
        self._ls_table.setSortingEnabled(False)
        self._ls_table.setRowCount(len(products))
        
        for row_idx, p in enumerate(products):
            def item(text: str) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            
            qty = int(p.get("qty_on_hand", 0) or 0)
            min_qty = int(p.get("min_qty", 0) or 0)
            deficit = min_qty - qty
            
            self._ls_table.setItem(row_idx, 0, item(str(p.get("sku", "") or "")))
            self._ls_table.setItem(row_idx, 1, item(str(p.get("title", "") or "")))
            
            qty_item = QTableWidgetItem()
            qty_item.setData(Qt.ItemDataRole.DisplayRole, qty)
            if qty <= 0:
                qty_item.setBackground(QColor(255, 200, 200))
            elif qty < min_qty:
                qty_item.setBackground(QColor(255, 255, 200))
            self._ls_table.setItem(row_idx, 2, qty_item)
            
            min_item = QTableWidgetItem()
            min_item.setData(Qt.ItemDataRole.DisplayRole, min_qty)
            self._ls_table.setItem(row_idx, 3, min_item)
            
            deficit_item = QTableWidgetItem()
            deficit_item.setData(Qt.ItemDataRole.DisplayRole, deficit)
            if deficit > 0:
                deficit_item.setForeground(QColor("red"))
            self._ls_table.setItem(row_idx, 4, deficit_item)
            
            self._ls_table.setItem(row_idx, 5, item(str(p.get("supplier", "") or "")))
        
        self._ls_table.resizeColumnsToContents()
        self._ls_table.setSortingEnabled(True)
        
        # Update tab text with count
        self._tabs.setTabText(1, f"⚠️ Low Stock ({len(products)})")
        self._ls_status.setText(f"{len(products)} products below threshold of {self._ls_threshold.value()}")

    def _load_pos(self) -> None:
        """Load purchase orders from database."""
        status = self._status_filter.currentData()
        vendor_id = getattr(self, "_vendor_filter", None) and self._vendor_filter.currentData()
        self._pos = get_all_purchase_orders(
            status=status if status else None,
            vendor_id=vendor_id if vendor_id else None,
        )
        # Default-hide cancelled+closed unless Show History is on or the user
        # explicitly filtered to one of those statuses.
        if (
            getattr(self, "_show_history_btn", None) is not None
            and not self._show_history_btn.isChecked()
            and status not in ("cancelled", "closed")
        ):
            self._pos = [
                p for p in self._pos
                if str(p.get("status") or "").lower() not in ("cancelled", "closed")
            ]
        self._populate_vendor_filter()
        self._populate_table()

    def _populate_vendor_filter(self) -> None:
        """Populate vendor dropdown from loaded PO data."""
        if not hasattr(self, "_vendor_filter"):
            return
        current = self._vendor_filter.currentData()
        vendors_seen: dict[int, str] = {}
        for po in get_all_purchase_orders():
            vid = po.get("vendor_id")
            vname = po.get("vendor_name") or ""
            if vid and vid not in vendors_seen:
                vendors_seen[vid] = vname
        self._vendor_filter.blockSignals(True)
        self._vendor_filter.clear()
        self._vendor_filter.addItem("All Vendors", None)
        for vid, vname in sorted(vendors_seen.items(), key=lambda x: x[1].lower()):
            self._vendor_filter.addItem(vname, vid)
        # restore selection
        for i in range(self._vendor_filter.count()):
            if self._vendor_filter.itemData(i) == current:
                self._vendor_filter.setCurrentIndex(i)
                break
        self._vendor_filter.blockSignals(False)

    # Row-level background for PO status
    _STATUS_ROW_BG = {
        "draft":     "#F3F4F6",  # gray — "not committed yet"
        "sent":      "#F5F3FF",
        "confirmed": "#FEFCE8",
        "ordered":   "#FEFCE8",
        "partial":   "#FFEDD5",  # light orange — work-in-progress
        "received":  "#F0FDF4",
        "closed":    "#F3F4F6",
        "cancelled": "#FEE2E2",  # stronger red tint
    }

    def _populate_table(self) -> None:
        """Populate table with PO data."""
        today = date.today()

        # Build duplicate SKU map across open POs for health warnings.
        open_po_skus: dict[str, list[str]] = {}
        po_lines_cache: dict[int, list[dict]] = {}
        for po in self._pos:
            po_id = po.get("id")
            if not po_id:
                continue
            status = str(po.get("status") or "").lower()
            if status in ("closed", "cancelled"):
                continue
            po_full = get_purchase_order(po_id)
            lines = (po_full or {}).get("lines", []) if po_full else []
            po_lines_cache[po_id] = lines
            po_num = str(po.get("po_number") or po_id)
            seen_sku: set[str] = set()
            for line in lines:
                sku = str(line.get("sku") or "").strip()
                if not sku or sku in seen_sku:
                    continue
                seen_sku.add(sku)
                open_po_skus.setdefault(sku, []).append(po_num)

        # Priority score pre-pass: compute scores for sorting + top-3 detection
        _po_scores: dict[int, tuple] = {}  # po_id → (score, overdue_days, so_ref)
        for _po in self._pos:
            _pid = _po.get("id") or 0
            _lines = po_lines_cache.get(_pid, [])
            _total_ord = sum(int(l.get("quantity_ordered") or 0) for l in _lines)
            _total_rcv = sum(int(l.get("quantity_received") or 0) for l in _lines)
            _outstnd = max(_total_ord - _total_rcv, 0)
            _ov_days = 0
            _ed = str(_po.get("expected_date") or "")[:10]
            if _outstnd > 0 and _ed:
                try:
                    _ov_days = max((today - datetime.strptime(_ed, "%Y-%m-%d").date()).days, 0)
                except ValueError:
                    pass
            _notes = str(_po.get("notes") or "")
            _so_refs = self._extract_so_refs(_notes)
            _so_r = str(_so_refs[0]) if _so_refs else ""
            _tot = float(_po.get("total") or 0)
            _st = str(_po.get("status") or "").lower()
            _score = 0
            if _ov_days > 0:
                _score += 100 + min(_ov_days, 50)
            if _so_r:
                _score += 50
            if _tot > 25000:
                _score += 30
            elif _tot > 10000:
                _score += 20
            elif _tot > 5000:
                _score += 10
            if _st == "draft":
                _score += 10
            _po_scores[_pid] = (_score, _ov_days, _so_r)

        self._pos.sort(key=lambda p: _po_scores.get(p.get("id") or 0, (0, 0, ""))[0], reverse=True)

        # Top 3 active POs by priority score get a subtle highlight
        _active_ids = [
            p.get("id") for p in self._pos
            if str(p.get("status") or "").lower() not in ("closed", "cancelled")
        ]
        _top3_ids: set = set(_active_ids[:3])

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(self._pos))

        # Vendor summary aggregation
        vendor_agg: dict[int, dict] = {}

        for row_idx, po in enumerate(self._pos):
            po_id_key = po.get("id") or 0
            _pscore, _poverdue, _pso_ref = _po_scores.get(po_id_key, (0, 0, ""))

            def item(text: str, row_bg: str = "") -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if row_bg:
                    it.setBackground(QColor(row_bg))
                return it

            status = str(po.get("status", "") or "").lower()
            row_bg = self._STATUS_ROW_BG.get(status, "")
            # Priority-based background overrides
            if _poverdue > 0:
                row_bg = "#FFF0F0"  # overdue: light red
            elif po_id_key in _top3_ids and status not in ("closed", "cancelled"):
                row_bg = "#FFFBEB"  # top-priority: light amber

            badge_bg, badge_fg = badge_colors(status, PO_BADGES)
            status_label = {
                "draft": "DRAFT", "sent": "SENT", "confirmed": "CONFIRMED",
                "ordered": "CONFIRMED", "partial": "PARTIAL RECV",
                "received": "RECEIVED", "closed": "CLOSED", "cancelled": "CANCELLED",
            }.get(status, status.upper())
            status_item = QTableWidgetItem(status_label)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            status_item.setBackground(QColor(badge_bg if not row_bg else badge_bg))
            status_item.setForeground(QColor(badge_fg))
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if row_bg:
                status_item.setBackground(QColor(badge_bg))

            # Full PO lines for received/outstanding
            lines = po_lines_cache.get(po.get("id"), [])
            total_ordered = sum(int(l.get("quantity_ordered") or 0) for l in lines)
            total_recv = sum(int(l.get("quantity_received") or 0) for l in lines)
            outstanding = max(total_ordered - total_recv, 0)

            # SO references from notes
            notes_txt = str(po.get("notes") or "")
            so_refs = self._extract_so_refs(notes_txt)
            so_ref = str(so_refs[0]) if so_refs else ""

            so_tooltip = ""
            so_icon = ""
            if so_refs:
                _so_lines: list[str] = []
                _urgency_rank = 0  # 0 green, 1 yellow, 2 red
                for _sid in so_refs[:6]:
                    so = get_sales_order(_sid)
                    so_status = str((so or {}).get("status") or "").lower()
                    if so_status in ("pending", "confirmed"):
                        _urgency_rank = max(_urgency_rank, 2)
                        _state = "Waiting on PO"
                    elif so_status in ("closed", "invoiced"):
                        _urgency_rank = max(_urgency_rank, 0)
                        _state = "Covered"
                    else:
                        _urgency_rank = max(_urgency_rank, 1)
                        _state = "Partially covered"
                    _so_lines.append(
                        f"SO-{_sid}: {(so or {}).get('customer_name') or 'Unknown'} | "
                        f"{format_money((so or {}).get('total') or 0)} | {_state}"
                    )
                so_icon = "🔴 " if _urgency_rank == 2 else ("🟡 " if _urgency_rank == 1 else "🟢 ")
                if len(so_refs) > 6:
                    _so_lines.append(f"(+{len(so_refs) - 6} more)")
                so_tooltip = "\n".join(_so_lines)

            # Age in days
            age_str = ""
            od_raw = str(po.get("order_date") or "")[:10]
            if od_raw:
                try:
                    od = datetime.strptime(od_raw, "%Y-%m-%d").date()
                    age_days = (today - od).days
                    age_str = f"{age_days}d"
                except ValueError:
                    pass

            overdue_days = 0
            ed_raw = str(po.get("expected_date") or "")[:10]
            if outstanding > 0 and ed_raw:
                try:
                    exp = datetime.strptime(ed_raw, "%Y-%m-%d").date()
                    overdue_days = max((today - exp).days, 0)
                except ValueError:
                    overdue_days = 0

            total = float(po.get("total", 0) or 0)

            def _item(text, bg=row_bg):
                return item(text, bg)

            _po_num_it = _item(str(po.get("po_number", "") or ""))
            _po_num_it.setData(Qt.ItemDataRole.UserRole, po.get("id"))
            self._table.setItem(row_idx, 0, _po_num_it)
            # Overdue indicator: prefix PO# with a thin red bar character + bold,
            # so the row visually "floats" attention to the left.
            if overdue_days > 0:
                _po_num_item = self._table.item(row_idx, 0)
                if _po_num_item:
                    _po_num_item.setText(f"▎ {_po_num_item.text()}")
                    _po_num_item.setForeground(QColor("#991B1B"))
                    _f_b = _po_num_item.font(); _f_b.setBold(True)
                    _po_num_item.setFont(_f_b)
            # Draft rows: italic PO number to signal "not committed yet"
            if status == "draft":
                _po_num_item = self._table.item(row_idx, 0)
                if _po_num_item:
                    _f = _po_num_item.font()
                    _f.setItalic(True)
                    _po_num_item.setFont(_f)
            self._table.setItem(row_idx, 1, _item(str(po.get("vendor_name", "") or "")))
            self._table.setItem(row_idx, 2, status_item)
            od_display = ""
            if od_raw:
                try:
                    od_display = datetime.strptime(od_raw, "%Y-%m-%d").strftime("%m/%d/%y")
                    od_ts = str(po.get("order_date") or "")[11:16]
                    if od_ts:
                        od_display += f" {od_ts}"
                except ValueError:
                    od_display = od_raw
            self._table.setItem(row_idx, 3, _item(od_display))
            ed_display = ""
            if ed_raw:
                try:
                    ed_display = datetime.strptime(ed_raw, "%Y-%m-%d").strftime("%m/%d/%y")
                    ed_ts = str(po.get("expected_date") or "")[11:16]
                    if ed_ts:
                        ed_display += f" {ed_ts}"
                except ValueError:
                    ed_display = ed_raw
            self._table.setItem(row_idx, 4, _item(ed_display))

            so_display = ""
            if so_refs:
                so_display = f"{so_icon}SO-{so_refs[0]}"
                if len(so_refs) > 1:
                    so_display += f" +{len(so_refs) - 1}"
            so_item = _item(so_display)
            if so_tooltip:
                so_item.setToolTip(so_tooltip)
            self._table.setItem(row_idx, 5, so_item)

            health_icons: list[str] = []
            health_tips: list[str] = []
            if so_refs:
                health_icons.append("📦")
                health_tips.append(f"Linked to {len(so_refs)} Sales Order(s)")
            if overdue_days > 0:
                health_icons.append("⚠")
                health_tips.append(f"Overdue by {overdue_days} day(s)")
            if total > 10000:
                health_icons.append("💰")
                health_tips.append("High value PO")
            dup_entries: list[str] = []
            dup_other_pos: set[str] = set()
            for l in lines:
                sku = str(l.get("sku") or "").strip()
                if not sku:
                    continue
                pos_for_sku = open_po_skus.get(sku, [])
                if len(pos_for_sku) > 1:
                    other = [pn for pn in pos_for_sku if pn != str(po.get("po_number") or po.get("id"))]
                    if other:
                        _qty_here = int(l.get("quantity_ordered") or 0)
                        dup_entries.append(f"{sku} (qty {_qty_here}): also on {', '.join(other[:2])}")
                        for pn in other:
                            dup_other_pos.add(pn)
            if dup_other_pos:
                # Show 🔍 with a count badge of *other* POs that share at least one SKU.
                health_icons.append(f"🔍 {len(dup_other_pos)}")
            if "Auto-generated restock" in notes_txt:
                health_icons.append("🧠")
                health_tips.append("Auto-restock generated")

            health_item = _item(" ".join(dict.fromkeys(health_icons)))
            if health_tips or dup_entries:
                health_item.setToolTip("\n".join(dict.fromkeys(health_tips + dup_entries)))
            self._table.setItem(row_idx, 6, health_item)

            self._table.setItem(row_idx, 7, _item(str(po.get("line_count", 0))))
            self._table.setItem(row_idx, 8, _item(str(total_recv) if lines else ""))

            # Outstanding with semantic state.
            out_item = _item(str(outstanding) if lines else "")
            if outstanding > 0:
                if overdue_days > 0:
                    out_item.setForeground(QColor("#991B1B"))
                    out_item.setText(f"⚠ {outstanding} overdue")
                else:
                    out_item.setForeground(QColor("#9A3412"))
                    out_item.setText(f"⏳ {outstanding}")
            self._table.setItem(row_idx, 9, out_item)

            self._table.setItem(row_idx, 10, _item(age_str))

            overdue_txt = f"⚠ {overdue_days}d" if overdue_days > 0 else "—"
            overdue_item = _item(overdue_txt)
            if overdue_days > 0:
                overdue_item.setForeground(QColor("#991B1B"))
            self._table.setItem(row_idx, 11, overdue_item)

            # Total column — color-code by value
            _total_item = _item(format_money(total))
            if total >= 25000:
                _total_item.setBackground(QColor("#FEF9C3"))
                _bold_f = QFont(); _bold_f.setBold(True)
                _total_item.setFont(_bold_f)
            elif total >= 10000:
                _bold_f2 = _total_item.font(); _bold_f2.setBold(True)
                _total_item.setFont(_bold_f2)
            self._table.setItem(row_idx, 12, _total_item)

            # $ Outstanding column — dollar value of unreceived items.
            outstanding_value = 0.0
            for l in lines:
                _qo = int(l.get("quantity_ordered") or 0)
                _qr = int(l.get("quantity_received") or 0)
                _outl = max(_qo - _qr, 0)
                if _outl > 0:
                    _uc = float(l.get("unit_cost") or 0)
                    _cc = float(l.get("core_charge") or 0)
                    outstanding_value += _outl * (_uc + _cc)
            out_val_item = _item(format_money(outstanding_value) if outstanding_value > 0 else "")
            if outstanding_value > 0:
                _bf = QFont(); _bf.setBold(True)
                out_val_item.setFont(_bf)
                if overdue_days > 0:
                    out_val_item.setForeground(QColor("#991B1B"))
                else:
                    out_val_item.setForeground(QColor("#9A3412"))
            self._table.setItem(row_idx, 13, out_val_item)

            # Due In column — days until expected (⚠ Xd late / Today / +Xd)
            _due_in_text = ""
            _due_in_color = ""
            if ed_raw and outstanding > 0:
                try:
                    _exp_date = datetime.strptime(ed_raw, "%Y-%m-%d").date()
                    _delta = (_exp_date - today).days
                    if _delta > 0:
                        _due_in_text = f"+{_delta}d"
                        _due_in_color = "#166534" if _delta >= 7 else "#92400E"
                    elif _delta == 0:
                        _due_in_text = "Today"
                        _due_in_color = "#92400E"
                    else:
                        _due_in_text = f"⚠ {-_delta}d late"
                        _due_in_color = "#991B1B"
                except ValueError:
                    pass
            _due_in_item = _item(_due_in_text)
            if _due_in_color:
                _due_in_item.setForeground(QColor(_due_in_color))
                _due_in_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row_idx, 14, _due_in_item)

            # Actions column — icons only with tooltips
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(2, 1, 2, 1)
            actions_layout.setSpacing(2)
            _recv_btn = QToolButton(); _recv_btn.setText("\U0001f4e6")
            _recv_btn.setToolTip("Receive items")
            _recv_btn.setEnabled(status in ("sent", "confirmed", "ordered", "partial"))
            _recv_btn.clicked.connect(lambda _=False, r=row_idx: self._receive_row_po(r))
            # Highlight Receive when this PO is overdue AND tied to a customer order —
            # this is the "do this first" combination.
            if status in ("sent", "confirmed", "ordered", "partial") and overdue_days > 0 and so_refs:
                _recv_btn.setStyleSheet(
                    "QToolButton { background: #FEE2E2; border: 1px solid #DC2626; "
                    "border-radius: 3px; font-weight: bold; }"
                )
                _recv_btn.setToolTip(
                    f"Receive items — OVERDUE {overdue_days}d, customer waiting"
                )
            _view_btn = QToolButton(); _view_btn.setText("\U0001f441")
            _view_btn.setToolTip("View / Edit PO")
            _view_btn.clicked.connect(lambda _=False, r=row_idx: self._open_row_po(r))
            _print_btn = QToolButton(); _print_btn.setText("\U0001f5a8")
            _print_btn.setToolTip("Print PO")
            _print_btn.clicked.connect(lambda _=False, r=row_idx: self._print_row_po(r))
            _cancel_btn = QToolButton(); _cancel_btn.setText("\u274c")
            _cancel_btn.setToolTip("Cancel PO")
            _cancel_btn.setEnabled(status in ("draft", "sent", "confirmed", "ordered"))
            _cancel_btn.clicked.connect(lambda _=False, r=row_idx: self._cancel_row_po(r))
            # Order: Receive (primary) → View → Print → Cancel
            actions_layout.addWidget(_recv_btn)
            actions_layout.addWidget(_view_btn)
            actions_layout.addWidget(_print_btn)
            actions_layout.addWidget(_cancel_btn)
            self._table.setCellWidget(row_idx, 15, actions_widget)

            # Vendor aggregation (active POs only)
            if status not in ("closed", "cancelled"):
                vid = int(po.get("vendor_id") or 0)
                vname = str(po.get("vendor_name") or "Unknown")
                agg = vendor_agg.setdefault(
                    vid,
                    {
                        "vendor_id": vid,
                        "vendor_name": vname,
                        "count": 0,
                        "total": 0.0,
                        "lines": 0,
                        "earliest_exp": None,
                        "draft_count": 0,
                        "cash_at_risk": 0.0,
                    },
                )
                agg["count"] += 1
                agg["total"] += total
                agg["lines"] += int(po.get("line_count") or 0)
                if status == "draft":
                    agg["draft_count"] += 1
                if status not in ("received",):
                    agg["cash_at_risk"] += total
                if ed_raw and (agg["earliest_exp"] is None or ed_raw < agg["earliest_exp"]):
                    agg["earliest_exp"] = ed_raw

            # Focus mode: hide rows that aren't actionable problems.
            #   Include: overdue, SO-linked, partial received, high $ (>10k).
            #   Exclude: drafts with no urgency (already excluded by lacking the above).
            if getattr(self, "_focus_mode_btn", None) and self._focus_mode_btn.isChecked():
                _is_problem = (
                    _poverdue > 0
                    or bool(_pso_ref)
                    or status == "partial"
                    or (outstanding > 0 and total >= 10000)
                )
                self._table.setRowHidden(row_idx, not _is_problem)

        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setSortingEnabled(True)
        self._status_label.setText(f"{len(self._pos)} purchase orders")

        # Update vendor summary panel
        self._refresh_vendor_summary(vendor_agg)
        # Update quick decision strip with high-level counts.
        self._refresh_decision_strip()

    def _refresh_vendor_summary(self, vendor_agg: dict) -> None:
        """Build the vendor summary panel cards."""
        # Clear old widgets
        while self._vendor_summary_layout.count():
            child = self._vendor_summary_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not vendor_agg or len(self._pos) == 0:
            self._vendor_summary_widget.setVisible(False)
            return

        self._vendor_summary_widget.setVisible(True)
        _total_car = sum(agg.get("cash_at_risk", agg.get("total", 0)) for agg in vendor_agg.values())
        # Build tooltip with vendor breakdown sorted by $ desc.
        _car_lines = [
            f"{agg.get('vendor_name')}: {format_money(agg.get('cash_at_risk', 0))}"
            for agg in sorted(
                vendor_agg.values(),
                key=lambda a: a.get("cash_at_risk", 0),
                reverse=True,
            )
            if agg.get("cash_at_risk", 0) > 0
        ]
        _car_tip = (
            "Cash tied up in active POs (excl. received).\n\n" + "\n".join(_car_lines)
            + "\n\nClick to filter to active POs not yet received."
        )
        _risk_btn = QPushButton(f"💰 Cash at Risk: {format_money(_total_car)}")
        _risk_btn.setStyleSheet(
            "QPushButton { color: #6B21A8; font-weight: bold; font-size: 11px; "
            "border: 1px solid #C4B5FD; background: #F5F3FF; border-radius: 10px; "
            "padding: 3px 10px; margin-right: 8px; }"
            "QPushButton:hover { background: #EDE9FE; }"
        )
        _risk_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        _risk_btn.setToolTip(_car_tip)
        _risk_btn.clicked.connect(self._filter_cash_at_risk)
        self._vendor_summary_layout.addWidget(_risk_btn)
        self._vendor_summary_layout.addWidget(QLabel("|"))
        self._vendor_summary_layout.addWidget(QLabel("🏭 Vendor Summary:"))
        for _vid, agg in sorted(vendor_agg.items(), key=lambda x: x[1].get("vendor_name", "").lower()):
            exp = agg["earliest_exp"] or ""
            if exp:
                try:
                    exp = datetime.strptime(exp, "%Y-%m-%d").strftime("%m/%d/%y")
                except ValueError:
                    pass
            chip = QPushButton(
                f"● {agg['vendor_name']}: "
                f"{agg['count']} PO(s) · "
                f"{format_money(agg['total'])} pending · "
                f"{agg['lines']} lines"
                + (f" · Exp: {exp}" if exp else "")
                + (f" · 💰 {format_money(agg.get('cash_at_risk', 0))} at risk" if agg.get("cash_at_risk", 0) > 0 else "")
            )
            chip.setStyleSheet(
                "QPushButton { font-size: 11px; color: #065F46; margin: 0 6px; "
                "border: 1px solid #A7F3D0; border-radius: 10px; padding: 2px 8px; background: #ECFDF5; }"
                "QPushButton:hover { background: #D1FAE5; }"
            )
            chip.setToolTip("Click to filter by this vendor")
            chip.clicked.connect(lambda _=False, vid=agg["vendor_id"]: self._filter_by_vendor(vid))
            chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            chip.customContextMenuRequested.connect(
                lambda pos, vid=agg["vendor_id"], c=chip: self._show_vendor_chip_menu(c, vid, pos)
            )
            self._vendor_summary_layout.addWidget(chip)

            if int(agg.get("draft_count") or 0) > 1:
                merge_btn = QPushButton("Merge Draft POs")
                merge_btn.setStyleSheet(
                    "QPushButton { font-size: 10px; color: #7C2D12; background: #FFEDD5;"
                    " border: 1px solid #FDBA74; border-radius: 8px; padding: 2px 6px; }"
                    "QPushButton:hover { background: #FED7AA; }"
                )
                merge_btn.clicked.connect(lambda _=False, vid=agg["vendor_id"]: self._merge_vendor_drafts(vid))
                self._vendor_summary_layout.addWidget(merge_btn)
        self._vendor_summary_layout.addStretch()

    def _on_selection_changed(self) -> None:
        """Enable/disable buttons based on selection and status."""
        selected = self._selected_pos()
        if not selected:
            self._submit_btn.setEnabled(False)
            self._confirm_btn.setEnabled(False)
            self._receive_btn.setEnabled(False)
            self._view_btn.setEnabled(False)
            self._reopen_btn.setEnabled(False)
            self._cancel_btn.setEnabled(False)
            return

        statuses = {str(po.get("status") or "").lower() for po in selected}
        all_draft = statuses == {"draft"}
        all_sent = statuses == {"sent"}
        all_receivable = statuses.issubset({"sent", "confirmed", "ordered", "partial"})
        all_cancelable = statuses.issubset({"draft", "sent", "confirmed", "ordered"})
        all_reopenable = statuses.issubset({"closed", "cancelled"})

        self._submit_btn.setEnabled(all_draft)
        self._confirm_btn.setEnabled(all_sent)
        self._receive_btn.setEnabled(all_receivable)
        self._view_btn.setEnabled(True)
        self._reopen_btn.setEnabled(all_reopenable)
        self._cancel_btn.setEnabled(all_cancelable)

        if any(s in {"closed", "cancelled"} for s in statuses) and not all_receivable:
            self._receive_btn.setToolTip("Cannot receive items on a closed PO")
        elif not all_receivable:
            self._receive_btn.setToolTip("Receive is available for Sent/Confirmed/Partial POs")
        else:
            self._receive_btn.setToolTip("Open receiving workflow")

    def _selected_rows(self) -> list[int]:
        rows = sorted({idx.row() for idx in self._table.selectionModel().selectedRows()})
        return [r for r in rows if 0 <= r < len(self._pos)]

    def _selected_pos(self) -> list[dict]:
        return [self._pos[r] for r in self._selected_rows()]

    def _extract_so_refs(self, notes_txt: str) -> list[int]:
        """Extract unique SO ids from PO notes in order of appearance."""
        refs: list[int] = []
        seen: set[int] = set()
        for m in re.finditer(r"From\s+SO\s*#\s*(\d+)", notes_txt or "", flags=re.IGNORECASE):
            sid = int(m.group(1))
            if sid not in seen:
                seen.add(sid)
                refs.append(sid)
        return refs

    def _on_table_double_click(self, row: int, col: int) -> None:
        if col == 5:
            self._open_linked_so_for_row(row)
            return
        self._open_row_po(row)

    def _on_table_cell_clicked(self, row: int, col: int) -> None:
        if col == 5:
            self._open_linked_so_for_row(row)

    def _show_po_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        self._table.selectRow(row)
        id_item = self._table.item(row, 0)
        po_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else None
        po = next((p for p in self._pos if p.get("id") == po_id), None) if po_id else None
        if not po:
            return
        status = str(po.get("status") or "").lower()

        # Linked SO column gets a focused SO picker instead of the full PO menu.
        col = self._table.columnAt(pos.x())
        if col == 5:
            so_refs = self._extract_so_refs(str(po.get("notes") or ""))
            so_menu = QMenu(self._table)
            if not so_refs:
                no_so = so_menu.addAction("No linked SOs")
                no_so.setEnabled(False)
                so_menu.exec(self._table.viewport().mapToGlobal(pos))
                return
            so_actions: dict[object, int] = {}
            for sid in so_refs:
                act = so_menu.addAction(f"Open SO-{sid}")
                so_actions[act] = sid
            if len(so_refs) > 1:
                so_menu.addSeparator()
                open_all = so_menu.addAction(f"Open all ({len(so_refs)})")
            else:
                open_all = None
            chosen = so_menu.exec(self._table.viewport().mapToGlobal(pos))
            if chosen is None:
                return
            if chosen is open_all:
                for sid in so_refs:
                    self._open_so_detail(sid)
            elif chosen in so_actions:
                self._open_so_detail(so_actions[chosen])
            return

        menu = QMenu(self._table)
        open_po = menu.addAction("Open PO")
        receive_po = menu.addAction("Receive")
        print_po = menu.addAction("Print")
        cancel_po = menu.addAction("Cancel")
        reopen_po = menu.addAction("Reopen")

        receive_po.setEnabled(status in ("sent", "confirmed", "ordered", "partial"))
        cancel_po.setEnabled(status in ("draft", "sent", "confirmed", "ordered"))
        reopen_po.setEnabled(status in ("closed", "cancelled"))

        so_refs = self._extract_so_refs(str(po.get("notes") or ""))
        so_map: dict[object, int] = {}
        if so_refs:
            so_menu = menu.addMenu("Open Linked SO")
            for sid in so_refs:
                so_act = so_menu.addAction(f"SO-{sid}")
                so_map[so_act] = sid

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == open_po:
            self._open_row_po(row)
        elif chosen == receive_po:
            self._receive_row_po(row)
        elif chosen == print_po:
            self._print_row_po(row)
        elif chosen == cancel_po:
            self._cancel_row_po(row)
        elif chosen == reopen_po:
            self._on_reopen()
        elif chosen in so_map:
            self._open_so_detail(so_map[chosen])

    def _open_linked_so_for_row(self, row: int) -> None:
        if row < 0:
            return
        id_item = self._table.item(row, 0)
        po_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else None
        po = next((p for p in self._pos if p.get("id") == po_id), None) if po_id else None
        if not po:
            return
        refs = self._extract_so_refs(str(po.get("notes") or ""))
        if not refs:
            return
        self._open_so_detail(refs[0])

    def _open_so_detail(self, so_id: int) -> None:
        if not so_id:
            return
        open_so_detail(int(so_id), parent=self)

    def _open_row_po(self, row: int) -> None:
        if row < 0:
            return
        id_item = self._table.item(row, 0)
        po_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else None
        if not po_id:
            return
        dlg = PODialog(po_id=po_id, parent=self)
        dlg.exec()
        self._load_pos()
        self._load_receipts()

    def _receive_row_po(self, row: int) -> None:
        if row < 0:
            return
        id_item = self._table.item(row, 0)
        po_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else None
        if not po_id:
            return
        dlg = ReceiveDialog(po_id=po_id, parent=self)
        if dlg.exec():
            self._load_pos()
            self._load_receipts()

    def _cancel_row_po(self, row: int) -> None:
        if row < 0:
            return
        id_item = self._table.item(row, 0)
        po_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else None
        if not po_id:
            return
        po = next((p for p in self._pos if p.get("id") == po_id), None)
        if not po:
            return
        po_number = po.get("po_number", "")
        reply = QMessageBox.question(
            self,
            "Cancel PO",
            f"Cancel {po_number}?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        result = update_po_status(po.get("id"), "cancelled")
        if result.get("success"):
            self._load_pos()
            self._load_receipts()
        else:
            QMessageBox.critical(self, "Error", f"Failed to cancel PO:\n{result.get('error')}")

    def _print_row_po(self, row: int) -> None:
        """Open PO dialog (which has print functionality) for the given row."""
        if row < 0:
            return
        id_item = self._table.item(row, 0)
        po_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else None
        if not po_id:
            return
        dlg = PODialog(po_id=po_id, parent=self)
        dlg.exec()
        self._load_pos()
        self._load_receipts()

    def _filter_by_vendor(self, vendor_id: int) -> None:
        for i in range(self._vendor_filter.count()):
            if self._vendor_filter.itemData(i) == vendor_id:
                self._vendor_filter.setCurrentIndex(i)
                self._load_pos()
                return

    def _filter_cash_at_risk(self) -> None:
        """Switch view to the 'Cash at Risk' lens: active not-yet-received POs.

        Implementation: turn on Focus Problems and clear status filter so the
        user sees the actionable subset.
        """
        # Clear status filter to ALL so partials/sent/etc. are all visible.
        try:
            self._status_filter.setCurrentIndex(0)
        except Exception:
            pass
        if not self._focus_mode_btn.isChecked():
            self._focus_mode_btn.setChecked(True)
        else:
            self._load_pos()

    def _refresh_decision_strip(self) -> None:
        """Build the 'Quick Decision Strip' tiles above the table."""
        if not hasattr(self, "_strip_layout"):
            return
        # Clear old tiles
        while self._strip_layout.count():
            child = self._strip_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        today = date.today()
        overdue = 0
        waiting_so = 0
        cash_at_risk = 0.0
        for po in self._pos:
            status = str(po.get("status") or "").lower()
            if status in ("closed", "cancelled", "received"):
                continue
            total = float(po.get("total") or 0)
            cash_at_risk += total
            ed = str(po.get("expected_date") or "")[:10]
            if ed:
                try:
                    if datetime.strptime(ed, "%Y-%m-%d").date() < today:
                        overdue += 1
                except ValueError:
                    pass
            if self._extract_so_refs(str(po.get("notes") or "")):
                waiting_so += 1

        if not self._pos:
            self._strip_widget.setVisible(False)
            return
        self._strip_widget.setVisible(True)

        def _tile(text: str, color: str, tip: str, on_click) -> QPushButton:
            btn = QPushButton(text)
            btn.setStyleSheet(
                f"QPushButton {{ background: {color}; border: 1px solid rgba(0,0,0,0.10); "
                "border-radius: 14px; padding: 4px 14px; font-weight: bold; font-size: 11px; "
                "color: #1F2937; }"
                f"QPushButton:hover {{ background: {color}; border: 1px solid rgba(0,0,0,0.30); }}"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tip)
            btn.clicked.connect(on_click)
            return btn

        self._strip_layout.addWidget(_tile(
            f"\u26a0 {overdue} Overdue", "#FEE2E2",
            "Click to focus on overdue and SO-linked POs",
            lambda: self._focus_mode_btn.setChecked(True),
        ))
        self._strip_layout.addWidget(_tile(
            f"\ud83d\udce6 {waiting_so} Waiting on Customers", "#FEF3C7",
            "Active POs linked to a Sales Order",
            lambda: self._focus_mode_btn.setChecked(True),
        ))
        self._strip_layout.addWidget(_tile(
            f"\ud83d\udcb0 {format_money(cash_at_risk)} Cash at Risk", "#EDE9FE",
            "Total $ tied up in active POs not yet received \u2014 click to focus",
            self._filter_cash_at_risk,
        ))
        self._strip_layout.addStretch()

    def _show_vendor_chip_menu(self, chip: QPushButton, vendor_id: int, pos) -> None:
        menu = QMenu(chip)
        open_all = menu.addAction("Open all POs")
        merge_drafts = menu.addAction("Combine Draft POs")
        create_new = menu.addAction("Create new PO for vendor")
        chosen = menu.exec(chip.mapToGlobal(pos))
        if chosen == open_all:
            self._filter_by_vendor(vendor_id)
        elif chosen == merge_drafts:
            self._merge_vendor_drafts(vendor_id)
        elif chosen == create_new:
            _drafts = [
                p for p in find_open_purchase_orders_for_vendor(vendor_id)
                if (p.get("status") or "").lower() == "draft"
            ]
            if _drafts:
                _draft_names = ", ".join(str(p.get("po_number")) for p in _drafts[:3])
                _r = QMessageBox.question(
                    self, "Existing Draft PO(s)",
                    f"Vendor has draft PO(s): {_draft_names}\n\nAdd to existing draft instead?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                )
                if _r == QMessageBox.StandardButton.Yes:
                    dlg = PODialog(po_id=_drafts[0].get("id"), parent=self)
                    dlg.exec()
                    self._load_pos()
                    return
                elif _r == QMessageBox.StandardButton.Cancel:
                    return
            res = create_purchase_order(vendor_id=vendor_id, notes="Manual vendor PO")
            if res.get("success"):
                self._load_pos()
                po_id = res.get("po_id")
                if po_id:
                    dlg = PODialog(po_id=po_id, parent=self)
                    dlg.exec()
                    self._load_pos()

    def _merge_vendor_drafts(self, vendor_id: int) -> None:
        open_pos = [po for po in find_open_purchase_orders_for_vendor(vendor_id) if (po.get("status") or "").lower() == "draft"]
        if len(open_pos) <= 1:
            QMessageBox.information(self, "Merge Drafts", "This vendor does not have multiple draft POs.")
            return
        target = sorted(open_pos, key=lambda p: int(p.get("id") or 0))[0]
        others = [po for po in open_pos if po.get("id") != target.get("id")]
        reply = QMessageBox.question(
            self,
            "Merge Draft POs",
            f"Merge {len(others)} draft PO(s) into {target.get('po_number')}?\n\n"
            "Source drafts will be cancelled after lines are merged.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        merged_count = 0
        # Track all SO refs across target + sources so they survive the merge.
        target_full = get_purchase_order(target.get("id")) or {}
        target_notes = str(target_full.get("notes") or "")
        all_refs: list[int] = list(self._extract_so_refs(target_notes))
        for po in others:
            src = get_purchase_order(po.get("id"))
            for line in (src or {}).get("lines", []):
                add_or_merge_po_line(
                    target.get("id"),
                    int(line.get("product_id") or 0),
                    int(line.get("quantity_ordered") or 0),
                    float(line.get("unit_cost") or 0),
                    line.get("notes"),
                )
            for _r in self._extract_so_refs(str((src or {}).get("notes") or "")):
                if _r not in all_refs:
                    all_refs.append(_r)
            update_po_status(po.get("id"), "cancelled")
            merged_count += 1
        # Persist merged SO refs back into target notes so column 5 / tooltip show all.
        if all_refs:
            base = re.sub(r"\s*\|\s*From\s+SO\s*#\s*\d+", "", target_notes, flags=re.IGNORECASE)
            base = re.sub(r"^From\s+SO\s*#\s*\d+\s*\|?\s*", "", base, flags=re.IGNORECASE).strip()
            so_tags = " | ".join(f"From SO #{r}" for r in all_refs)
            new_notes = f"{base} | {so_tags}".strip(" |") if base else so_tags
            try:
                update_purchase_order(target.get("id"), notes=new_notes)
            except Exception:
                pass
        self._load_pos()
        self._load_receipts()
        QMessageBox.information(
            self,
            "Merge Complete",
            f"Merged {merged_count} draft PO(s) into {target.get('po_number')}."
        )

    # ==================================================================
    #  VENDOR CORES TAB
    # ==================================================================
    _VCR_HEADERS = [
        "ID", "Status", "PO #", "Vendor", "SKU", "Description",
        "Qty", "Core Charge", "Due", "Tracking", "Credit #", "Credit $",
    ]

    def _build_vendor_cores_tab(self) -> QWidget:
        """Cores we owe back to vendors so the deposit can be recovered."""
        widget = QWidget()

        ctrl = QHBoxLayout()
        self._vcr_status_filter = QComboBox()
        self._vcr_status_filter.addItem("All open (pending + shipped)", "_open")
        self._vcr_status_filter.addItem("Pending", "pending")
        self._vcr_status_filter.addItem("Shipped", "shipped")
        self._vcr_status_filter.addItem("Credited", "credited")
        self._vcr_status_filter.addItem("Written off", "written_off")
        self._vcr_status_filter.addItem("Voided", "voided")
        self._vcr_status_filter.addItem("All", "_all")
        self._vcr_status_filter.currentIndexChanged.connect(self._load_vendor_cores)
        ctrl.addWidget(QLabel("Filter:"))
        ctrl.addWidget(self._vcr_status_filter)

        ctrl.addStretch()

        self._vcr_refresh_btn = QPushButton("↻ Refresh")
        self._vcr_refresh_btn.clicked.connect(self._load_vendor_cores)
        ctrl.addWidget(self._vcr_refresh_btn)

        self._vcr_ship_btn = QPushButton("📦 Mark Shipped")
        self._vcr_ship_btn.clicked.connect(self._on_vcr_mark_shipped)
        self._vcr_ship_btn.setEnabled(False)
        ctrl.addWidget(self._vcr_ship_btn)

        self._vcr_credit_btn = QPushButton("✅ Mark Credited")
        self._vcr_credit_btn.clicked.connect(self._on_vcr_mark_credited)
        self._vcr_credit_btn.setEnabled(False)
        ctrl.addWidget(self._vcr_credit_btn)

        self._vcr_writeoff_btn = QPushButton("⚠ Write Off")
        self._vcr_writeoff_btn.clicked.connect(self._on_vcr_writeoff)
        self._vcr_writeoff_btn.setEnabled(False)
        ctrl.addWidget(self._vcr_writeoff_btn)

        self._vcr_void_btn = QPushButton("✖ Void")
        self._vcr_void_btn.clicked.connect(self._on_vcr_void)
        self._vcr_void_btn.setEnabled(False)
        ctrl.addWidget(self._vcr_void_btn)

        self._vcr_table = QTableWidget(0, len(self._VCR_HEADERS))
        self._vcr_table.setHorizontalHeaderLabels(self._VCR_HEADERS)
        self._vcr_table.setColumnHidden(0, True)
        self._vcr_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._vcr_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._vcr_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._vcr_table.setSortingEnabled(True)
        self._vcr_table.itemSelectionChanged.connect(self._on_vcr_selection_changed)
        self._vcr_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

        self._vcr_summary_lbl = QLabel("")
        self._vcr_summary_lbl.setStyleSheet("font-weight: 600; color: #4B5563; padding: 4px;")

        layout = QVBoxLayout()
        layout.addLayout(ctrl)
        layout.addWidget(self._vcr_table, 1)
        layout.addWidget(self._vcr_summary_lbl)
        widget.setLayout(layout)

        self._vcr_rows: list[dict] = []
        return widget

    def _on_tab_changed(self, idx: int) -> None:
        try:
            label = self._tabs.tabText(idx)
        except Exception:
            return
        if "Vendor Cores" in label:
            self._load_vendor_cores()

    def _load_vendor_cores(self) -> None:
        if not hasattr(self, "_vcr_table"):
            return
        status = self._vcr_status_filter.currentData() if hasattr(self, "_vcr_status_filter") else "_open"
        if status == "_open":
            rows = list_vendor_core_returns(status="pending") + list_vendor_core_returns(status="shipped")
            # Re-sort: pending first, then shipped, both by due_date
            rows.sort(key=lambda r: (
                0 if r.get("status") == "pending" else 1,
                r.get("due_date") or "9999-12-31",
            ))
        elif status == "_all":
            rows = list_vendor_core_returns()
        else:
            rows = list_vendor_core_returns(status=status)
        self._vcr_rows = rows

        self._vcr_table.setSortingEnabled(False)
        self._vcr_table.setRowCount(len(rows))
        today = date.today()
        total_charge = 0.0
        overdue_count = 0
        for i, r in enumerate(rows):
            def it(text: str, fg: str = "") -> QTableWidgetItem:
                w = QTableWidgetItem(text)
                w.setFlags(w.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if fg:
                    w.setForeground(QColor(fg))
                return w

            status_v = str(r.get("status") or "")
            qty = int(r.get("qty") or 0)
            cc = float(r.get("core_charge") or 0)
            line_charge = qty * cc
            total_charge += line_charge

            due = str(r.get("due_date") or "")[:10]
            due_fg = ""
            if status_v == "pending" and due:
                try:
                    if datetime.strptime(due, "%Y-%m-%d").date() < today:
                        due_fg = "#DC2626"
                        overdue_count += 1
                except ValueError:
                    pass

            status_label = {
                "pending": "PENDING", "shipped": "SHIPPED",
                "credited": "CREDITED", "written_off": "WRITTEN OFF",
                "voided": "VOIDED",
            }.get(status_v, status_v.upper())
            status_bg = {
                "pending": "#FEF3C7", "shipped": "#DBEAFE",
                "credited": "#D1FAE5", "written_off": "#E5E7EB",
                "voided": "#FEE2E2",
            }.get(status_v, "#F3F4F6")
            status_fg = {
                "pending": "#92400E", "shipped": "#1E40AF",
                "credited": "#065F46", "written_off": "#4B5563",
                "voided": "#991B1B",
            }.get(status_v, "#374151")

            status_item = QTableWidgetItem(status_label)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            status_item.setBackground(QColor(status_bg))
            status_item.setForeground(QColor(status_fg))
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self._vcr_table.setItem(i, 0, it(str(r.get("id") or "")))
            self._vcr_table.setItem(i, 1, status_item)
            self._vcr_table.setItem(i, 2, it(str(r.get("po_number") or "")))
            self._vcr_table.setItem(i, 3, it(str(r.get("vendor_name") or "")))
            self._vcr_table.setItem(i, 4, it(str(r.get("sku") or "")))
            self._vcr_table.setItem(i, 5, it(str(r.get("title") or "")))
            self._vcr_table.setItem(i, 6, it(str(qty)))
            self._vcr_table.setItem(i, 7, it(format_money(line_charge)))
            self._vcr_table.setItem(i, 8, it(due, fg=due_fg))
            self._vcr_table.setItem(i, 9, it(str(r.get("tracking_number") or "")))
            self._vcr_table.setItem(i, 10, it(str(r.get("vendor_credit_number") or "")))
            credit_amt = r.get("credit_amount")
            self._vcr_table.setItem(i, 11, it(format_money(credit_amt) if credit_amt else ""))

        self._vcr_table.resizeColumnsToContents()
        self._vcr_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._vcr_table.setSortingEnabled(True)
        self._on_vcr_selection_changed()

        overdue_txt = f" • {overdue_count} OVERDUE" if overdue_count else ""
        self._vcr_summary_lbl.setText(
            f"{len(rows)} record(s) • Total deposit at stake: "
            f"{format_money(total_charge)}{overdue_txt}"
        )

    def _selected_vcr(self) -> dict | None:
        row = self._vcr_table.currentRow()
        if 0 <= row < len(self._vcr_rows):
            return self._vcr_rows[row]
        return None

    def _on_vcr_selection_changed(self) -> None:
        r = self._selected_vcr()
        status = (r or {}).get("status")
        self._vcr_ship_btn.setEnabled(status == "pending")
        self._vcr_credit_btn.setEnabled(status in ("pending", "shipped"))
        self._vcr_writeoff_btn.setEnabled(status in ("pending", "shipped"))
        self._vcr_void_btn.setEnabled(status in ("pending", "shipped"))

    def _on_vcr_mark_shipped(self) -> None:
        r = self._selected_vcr()
        if not r:
            return
        tracking, ok = QInputDialog.getText(
            self, "Mark Core Shipped",
            f"Tracking number for {r.get('sku')} core return (optional):",
        )
        if not ok:
            return
        res = mark_vendor_core_shipped(int(r["id"]), tracking_number=tracking.strip() or None)
        if not res.get("success"):
            QMessageBox.warning(self, "Update Failed", str(res.get("error")))
            return
        self._load_vendor_cores()

    def _on_vcr_mark_credited(self) -> None:
        r = self._selected_vcr()
        if not r:
            return
        default_amt = float((r.get("qty") or 0)) * float((r.get("core_charge") or 0))
        amt, ok = QInputDialog.getDouble(
            self, "Mark Core Credited",
            "Credit amount received from vendor:",
            default_amt, 0.0, 1_000_000.0, 2,
        )
        if not ok:
            return
        credit_no, ok2 = QInputDialog.getText(
            self, "Mark Core Credited",
            "Vendor credit / RA number (optional):",
        )
        if not ok2:
            return
        res = mark_vendor_core_credited(
            int(r["id"]),
            credit_amount=float(amt),
            vendor_credit_number=credit_no.strip() or None,
        )
        if not res.get("success"):
            QMessageBox.warning(self, "Update Failed", str(res.get("error")))
            return
        self._load_vendor_cores()

    def _on_vcr_writeoff(self) -> None:
        r = self._selected_vcr()
        if not r:
            return
        if QMessageBox.question(
            self, "Write Off Core",
            f"Write off {r.get('sku')} core deposit "
            f"({format_money(float(r.get('qty') or 0) * float(r.get('core_charge') or 0))})?\n\n"
            "Use this when the deposit will not be recovered.",
        ) != QMessageBox.StandardButton.Yes:
            return
        res = write_off_vendor_core(int(r["id"]))
        if not res.get("success"):
            QMessageBox.warning(self, "Update Failed", str(res.get("error")))
            return
        self._load_vendor_cores()

    def _on_vcr_void(self) -> None:
        r = self._selected_vcr()
        if not r:
            return
        if QMessageBox.question(
            self, "Void Core",
            f"Void core return for {r.get('sku')}?\n\n"
            "Use this for entries created in error.",
        ) != QMessageBox.StandardButton.Yes:
            return
        res = void_vendor_core(int(r["id"]))
        if not res.get("success"):
            QMessageBox.warning(self, "Update Failed", str(res.get("error")))
            return
        self._load_vendor_cores()

    def _save_column_widths(self) -> None:
        from PySide6.QtCore import QSettings
        settings = QSettings("JAKS", "Inventory")
        widths = [self._table.columnWidth(i) for i in range(self._table.columnCount())]
        settings.setValue("po_screen/column_widths", widths)

    def _restore_column_widths(self) -> None:
        from PySide6.QtCore import QSettings
        settings = QSettings("JAKS", "Inventory")
        widths = settings.value("po_screen/column_widths", [])
        if not widths:
            return
        try:
            for i, w in enumerate(widths):
                if i < self._table.columnCount():
                    self._table.setColumnWidth(i, int(w))
        except Exception:
            return

    def _on_new(self) -> None:
        """Open dialog to create new PO — checks for existing drafts if vendor filter is active."""
        vendor_id = self._vendor_filter.currentData() if hasattr(self, "_vendor_filter") else None
        if vendor_id:
            existing_drafts = [
                p for p in find_open_purchase_orders_for_vendor(vendor_id)
                if (p.get("status") or "").lower() == "draft"
            ]
            if existing_drafts:
                _names = ", ".join(str(p.get("po_number")) for p in existing_drafts[:3])
                _reply = QMessageBox.question(
                    self, "Existing Draft PO(s)",
                    f"This vendor already has draft PO(s): {_names}\n\n"
                    "Add items to an existing draft instead of creating a new one?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                )
                if _reply == QMessageBox.StandardButton.Yes:
                    dlg = PODialog(po_id=existing_drafts[0].get("id"), parent=self)
                    dlg.exec()
                    self._load_pos()
                    return
                elif _reply == QMessageBox.StandardButton.Cancel:
                    return
        dlg = PODialog(parent=self)
        if dlg.exec():
            self._load_pos()

    def _on_restock(self) -> None:
        """Open restock wizard to generate draft POs."""
        dlg = RestockWizardDialog(parent=self)
        if dlg.exec():
            self._load_pos()

    def _on_view(self) -> None:
        """View/edit selected PO."""
        rows = self._selected_rows()
        if not rows:
            return
        self._open_row_po(rows[0])

    def _on_reopen(self) -> None:
        """Reopen closed/cancelled PO(s) back to Draft."""
        selected = self._selected_pos()
        if not selected:
            return
        if not all((str(po.get("status") or "").lower() in ("closed", "cancelled")) for po in selected):
            QMessageBox.information(self, "Reopen", "Select only closed/cancelled POs to reopen.")
            return
        reply = QMessageBox.question(
            self,
            "Reopen PO",
            f"Reopen {len(selected)} PO(s) to Draft status?\n\nThis will allow additional receiving.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for po in selected:
            update_po_status(po.get("id"), "draft")
        self._load_pos()
        self._load_receipts()

    def _on_submit(self) -> None:
        """Change PO status to 'sent'."""
        selected = self._selected_pos()
        if not selected:
            return
        if not all((str(po.get("status") or "").lower() == "draft") for po in selected):
            QMessageBox.information(self, "Mark Sent", "Only Draft POs can be marked as Sent.")
            return

        reply = QMessageBox.question(
            self,
            "Mark as Sent",
            f"Mark {len(selected)} PO(s) as SENT?\n\nThis records each PO as sent to the vendor.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            for po in selected:
                result = update_po_status(po.get("id"), "sent")
                if not result.get("success"):
                    QMessageBox.critical(self, "Error", f"Failed to update PO:\n{result.get('error')}")
                    return
            self._load_pos()
            self._load_receipts()

    def _on_confirm(self) -> None:
        """Mark PO as Confirmed (vendor acknowledged)."""
        selected = self._selected_pos()
        if not selected:
            return
        if not all((str(po.get("status") or "").lower() == "sent") for po in selected):
            QMessageBox.information(self, "Confirm PO", "Only Sent POs can be confirmed.")
            return

        reply = QMessageBox.question(
            self,
            "Confirm PO",
            f"Mark {len(selected)} PO(s) as CONFIRMED?\n\nVendor has acknowledged these orders.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            for po in selected:
                result = update_po_status(po.get("id"), "confirmed")
                if not result.get("success"):
                    QMessageBox.critical(self, "Error", f"Failed to confirm PO:\n{result.get('error')}")
                    return
            self._load_pos()
            self._load_receipts()


    def _on_receive(self) -> None:
        """Open receive dialog for selected PO."""
        rows = self._selected_rows()
        if not rows:
            return
        selected = [self._pos[r] for r in rows]
        if not all((str(po.get("status") or "").lower() in ("sent", "confirmed", "ordered", "partial")) for po in selected):
            QMessageBox.information(self, "Receive", "Select only Sent/Confirmed/Partial POs for receiving.")
            return
        for row in rows:
            self._receive_row_po(row)

    def _on_cancel(self) -> None:
        """Cancel selected PO."""
        selected = self._selected_pos()
        if not selected:
            return
        if not all((str(po.get("status") or "").lower() in ("draft", "sent", "confirmed", "ordered")) for po in selected):
            QMessageBox.information(self, "Cancel", "Only Draft/Sent/Confirmed POs can be cancelled.")
            return
        
        reply = QMessageBox.question(
            self,
            "Cancel PO",
            f"Cancel {len(selected)} selected PO(s)?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            for po in selected:
                result = update_po_status(po.get("id"), "cancelled")
                if not result.get("success"):
                    QMessageBox.critical(self, "Error", f"Failed to cancel PO:\n{result.get('error')}")
                    return
            self._load_pos()
            self._load_receipts()


class POReceiptsScreen(POScreen):
    """Dedicated Purchasing destination focused on receiving POs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent=parent, initial_tab=2)
