"""Sales Orders screen — dark mockup layout matching Quote List."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QMenu, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from db import (
    create_sales_order, list_sales_orders, get_sales_order, update_sales_order,
    add_so_line, get_so_lines, remove_so_line, convert_so_to_invoice,
    get_so_summary, get_backorder_lines,
    get_all_customers, get_product_by_sku, search_customers,
    create_po_from_so,
)
from db.inventory import derive_so_line_status

# ── Palette (shared with Quotes / Invoices screens) ────────────────────
_BG          = "#0f1419"
_PANEL       = "#1a2128"
_PANEL2      = "#232c35"
_BORDER      = "#2d3a45"
_BORDER_HI   = "#3d4d5c"
_TEXT        = "#e6edf3"
_TEXT_DIM    = "#8b98a5"
_TEXT_FAINT  = "#5d6b78"
_ACCENT      = "#ff8c42"
_ACCENT_DIM  = "#b85d1f"
_GREEN       = "#3fb950"
_RED         = "#f85149"
_YELLOW      = "#d29922"
_BLUE        = "#58a6ff"
_PURPLE      = "#bc8cff"
_TEAL        = "#39d0d8"

# Status → (pill text, pill color) — covers DB statuses + display labels.
_SO_STATUS_DISPLAY: dict[str, tuple[str, str]] = {
    "draft":     ("● OPEN",            _BLUE),
    "quote":     ("● OPEN",            _BLUE),
    "confirmed": ("● OPEN",            _BLUE),
    "picking":   ("⚙ IN BUILD",        _YELLOW),
    "ready":     ("📦 READY TO SHIP",  _PURPLE),
    "shipped":   ("🚚 SHIPPED",        _TEAL),
    "invoiced":  ("✓ INVOICED",        _GREEN),
    "cancelled": ("⏸ CANCELLED",       _TEXT_FAINT),
    "hold":      ("⏸ HOLD",            _YELLOW),
    "backorder": ("⚠ BACKORDERED",     _RED),
}

# Tab labels → list of DB status values that match.
_SO_TAB_STATUSES = [
    "All", "Open", "In Build", "Ready to Ship",
    "Shipped", "Backordered", "Hold", "Closed",
]
_SO_TAB_STATUS_MAP: dict[str, list[str] | None] = {
    "All": None,
    "Open": ["quote", "draft", "confirmed"],
    "In Build": ["picking"],
    "Ready to Ship": ["ready"],
    "Shipped": ["shipped"],
    "Backordered": ["backorder"],
    "Hold": ["hold"],
    "Closed": ["invoiced", "cancelled"],
}

# Badge colours kept for any external callers; not used in the new layout.
from core.badges import SO_BADGES, PRIORITY_BADGES, badge_colors


class _SOStatusBadge(QTableWidgetItem):
    def __init__(self, status: str) -> None:
        super().__init__(status.upper() if status else "")
        bg, fg = badge_colors(status, SO_BADGES)
        self.setBackground(QColor(bg))
        self.setForeground(QColor(fg))
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)


class _PriorityBadge(QTableWidgetItem):
    def __init__(self, priority: str) -> None:
        display = (priority or "normal").upper()
        super().__init__(display)
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)
        key = (priority or "").lower()
        if key in PRIORITY_BADGES:
            bg, fg = PRIORITY_BADGES[key]
            self.setBackground(QColor(bg))
            self.setForeground(QColor(fg))


class SalesOrdersScreen(QWidget):
    """Sales orders management — dark mockup layout."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active_tab = "All"
        self._build_ui()
        self._load_orders()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._load_orders()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(10)

        # ── Header row ──
        header = QHBoxLayout()
        title = QLabel("Sales Orders")
        title.setStyleSheet(
            f"QLabel {{ color: {_TEXT}; font-size: 20px; font-weight: 700; "
            f"background: transparent; }}"
        )
        sub = QLabel("Build · Pick · Pack · Ship")
        sub.setStyleSheet(
            f"QLabel {{ color: {_TEXT_DIM}; font-size: 12px; "
            f"background: transparent; }}"
        )
        header.addWidget(title)
        header.addSpacing(10)
        header.addWidget(sub)
        header.addStretch()

        new_btn = QPushButton("+ New Sales Order")
        new_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        new_btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: white; font-weight: 700; "
            f"padding: 8px 16px; border-radius: 6px; font-size: 12px; border: none; }}"
            f"QPushButton:hover {{ background: {_ACCENT_DIM}; }}"
        )
        new_btn.clicked.connect(self._new_so)
        header.addWidget(new_btn)
        layout.addLayout(header)

        # ── KPI strip ──
        layout.addWidget(self._build_kpi_row())

        # ── Needs Attention chips ──
        layout.addWidget(self._build_attention_bar())

        # ── Search + filter dropdowns + action buttons ──
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("🔍   Search by SO #, customer, ESN, part…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMaximumWidth(260)
        self._search_edit.setStyleSheet(
            f"QLineEdit {{ background: {_PANEL2}; color: {_TEXT}; "
            f"padding: 7px 10px; border: 1px solid {_BORDER}; "
            f"border-radius: 6px; font-size: 12px; }}"
            f"QLineEdit:focus {{ border-color: {_ACCENT}; }}"
        )
        self._search_edit.textChanged.connect(lambda _: self._load_orders())
        filter_row.addWidget(self._search_edit)

        combo_qss = (
            f"QComboBox {{ background: {_PANEL2}; color: {_TEXT}; "
            f"padding: 6px 10px; border: 1px solid {_BORDER}; "
            f"border-radius: 6px; font-size: 12px; min-width: 110px; }}"
            f"QComboBox:hover {{ border-color: {_BORDER_HI}; }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {_PANEL2}; "
            f"color: {_TEXT}; selection-background-color: {_ACCENT}; "
            f"border: 1px solid {_BORDER}; }}"
        )

        self._status_combo = QComboBox()
        for label in _SO_TAB_STATUSES:
            self._status_combo.addItem(
                "All Status" if label == "All" else label, userData=label
            )
        self._status_combo.setStyleSheet(combo_qss)
        self._status_combo.currentIndexChanged.connect(self._on_status_combo_changed)
        filter_row.addWidget(self._status_combo)

        self._rep_combo = QComboBox()
        self._rep_combo.addItem("All Reps", userData=None)
        self._rep_combo.setStyleSheet(combo_qss)
        self._rep_combo.currentIndexChanged.connect(lambda _: self._load_orders())
        filter_row.addWidget(self._rep_combo)

        self._wh_combo = QComboBox()
        self._wh_combo.addItem("All Warehouses", userData=None)
        self._wh_combo.setStyleSheet(combo_qss)
        self._wh_combo.currentIndexChanged.connect(lambda _: self._load_orders())
        filter_row.addWidget(self._wh_combo)

        self._date_combo = QComboBox()
        for label, days in (
            ("Last 30 days", 30), ("Last 7 days", 7),
            ("Last 90 days", 90), ("This year", 365), ("All time", None),
        ):
            self._date_combo.addItem(label, userData=days)
        self._date_combo.setStyleSheet(combo_qss)
        self._date_combo.currentIndexChanged.connect(lambda _: self._load_orders())
        filter_row.addWidget(self._date_combo)

        filter_row.addStretch()

        self._view_btn = QPushButton("View / Edit")
        self._view_btn.setEnabled(False)
        self._view_btn.clicked.connect(self._on_view_detail)
        self._view_btn.setStyleSheet(self._action_btn_style(_TEXT))
        filter_row.addWidget(self._view_btn)

        self._confirm_btn = QPushButton("✓ Confirm")
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.clicked.connect(lambda: self._set_status("confirmed"))
        self._confirm_btn.setStyleSheet(self._action_btn_style(_BLUE))
        filter_row.addWidget(self._confirm_btn)

        self._pick_btn = QPushButton("→ Pick Ticket")
        self._pick_btn.setEnabled(False)
        self._pick_btn.clicked.connect(lambda: self._set_status("picking"))
        self._pick_btn.setStyleSheet(self._action_btn_style(_YELLOW))
        filter_row.addWidget(self._pick_btn)

        self._ready_btn = QPushButton("→ Pack Slip")
        self._ready_btn.setEnabled(False)
        self._ready_btn.clicked.connect(lambda: self._set_status("ready"))
        self._ready_btn.setStyleSheet(self._action_btn_style(_PURPLE))
        filter_row.addWidget(self._ready_btn)

        self._ship_btn = QPushButton("🚚 Ship")
        self._ship_btn.setEnabled(False)
        self._ship_btn.clicked.connect(self._ship_order)
        self._ship_btn.setStyleSheet(self._action_btn_style(_TEAL))
        filter_row.addWidget(self._ship_btn)

        self._invoice_btn = QPushButton("→ Invoice")
        self._invoice_btn.setEnabled(False)
        self._invoice_btn.clicked.connect(self._convert_to_invoice)
        self._invoice_btn.setStyleSheet(self._action_btn_style(_GREEN))
        filter_row.addWidget(self._invoice_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(lambda: self._set_status("cancelled"))
        self._cancel_btn.setStyleSheet(self._action_btn_style(_RED))
        filter_row.addWidget(self._cancel_btn)

        self._email_btn = QPushButton("📞 Update Status")
        self._email_btn.setEnabled(False)
        self._email_btn.clicked.connect(self._on_send_email)
        self._email_btn.setStyleSheet(
            f"QPushButton {{ color: white; background: {_ACCENT}; border: none; "
            f"padding: 6px 14px; border-radius: 5px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {_ACCENT_DIM}; }}"
            f"QPushButton:disabled {{ background: {_PANEL2}; color: {_TEXT_FAINT}; }}"
        )
        filter_row.addWidget(self._email_btn)

        layout.addLayout(filter_row)

        # ── Table ──
        # Columns: 0 ID (hidden) | 1 SO # | 2 Customer | 3 Engine |
        #          4 Total | 5 Status | 6 Age | 7 Promise |
        #          8 Fulfillment | 9 Ship Via | 10 Rep | 11 Activity
        self._table = QTableWidget(0, 12)
        self._table.setHorizontalHeaderLabels([
            "ID", "SO #", "Customer", "Engine / Platform",
            "Total", "Status", "Age", "Promise",
            "Fulfillment", "Ship Via", "Rep", "Activity",
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.setColumnHidden(0, True)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setDefaultSectionSize(44)
        self._table.setShowGrid(False)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                background: {_PANEL};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 8px;
                gridline-color: {_BORDER};
                alternate-background-color: {_PANEL2};
                selection-background-color: rgba(255,140,66,0.20);
                selection-color: {_TEXT};
            }}
            QHeaderView::section {{
                background-color: {_PANEL2};
                color: {_TEXT_DIM};
                font-weight: 600;
                font-size: 10px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                padding: 8px 10px;
                border: none;
                border-bottom: 1px solid {_BORDER};
            }}
            QTableWidget::item {{ padding: 6px 10px; border: none;
                                  border-bottom: 1px solid {_BORDER}; }}
        """)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(11, QHeaderView.ResizeMode.Fixed)
        hdr.setMinimumSectionSize(60)
        self._table.setColumnWidth(1, 100)   # SO #
        self._table.setColumnWidth(3, 150)   # Engine
        self._table.setColumnWidth(4, 100)   # Total
        self._table.setColumnWidth(5, 140)   # Status pill
        self._table.setColumnWidth(6, 60)    # Age
        self._table.setColumnWidth(7, 100)   # Promise
        self._table.setColumnWidth(8, 110)   # Fulfillment progress
        self._table.setColumnWidth(9, 100)   # Ship Via
        self._table.setColumnWidth(10, 80)   # Rep
        self._table.setColumnWidth(11, 200)  # Activity
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.doubleClicked.connect(self._on_view_detail)
        layout.addWidget(self._table, 1)

        # ── Summary footer ──
        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(
            f"QLabel {{ color: {_TEXT_DIM}; font-size: 11px; padding: 4px 2px; "
            f"background: transparent; }}"
        )
        layout.addWidget(self._summary_label)

    # ------------------------------------------------------------------
    # KPI strip
    # ------------------------------------------------------------------
    def _build_kpi_row(self) -> QWidget:
        wrap = QFrame()
        wrap.setStyleSheet("QFrame { background: transparent; }")
        grid = QHBoxLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        self._kpi_cards: dict[str, tuple[QLabel, QLabel, QFrame]] = {}
        cards = [
            ("open",        "Open Orders",     _BLUE),
            ("in_build",    "In Build",        _YELLOW),
            ("ready",       "Ready to Ship",   _PURPLE),
            ("ship_today",  "Shipping Today",  _TEAL),
            ("backorder",   "Backordered",     _RED),
            ("booked_mtd",  "Booked MTD",      _ACCENT),
            ("avg_cycle",   "Avg Cycle",       _GREEN),
        ]
        for key, label, color in cards:
            card = self._build_kpi_card(label, "—", color)
            self._kpi_cards[key] = card
            grid.addWidget(card[2], 1)
        return wrap

    def _build_kpi_card(self, label: str, value: str, color: str
                        ) -> tuple[QLabel, QLabel, QFrame]:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {_PANEL}; border: 1px solid {_BORDER}; "
            f"border-radius: 8px; }}"
            f"QFrame:hover {{ border-color: {color}; }}"
        )
        card.setMinimumHeight(64)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 8)
        v.setSpacing(2)
        lbl = QLabel(label.upper())
        lbl.setStyleSheet(
            f"QLabel {{ color: {_TEXT_DIM}; font-size: 9px; font-weight: 700; "
            f"letter-spacing: 0.8px; background: transparent; border: none; }}"
        )
        v.addWidget(lbl)
        val = QLabel(value)
        val.setStyleSheet(
            f"QLabel {{ color: {color}; font-size: 18px; font-weight: 700; "
            f"background: transparent; border: none; }}"
        )
        v.addWidget(val)
        sub = QLabel("")
        sub.setStyleSheet(
            f"QLabel {{ color: {_TEXT_FAINT}; font-size: 10px; "
            f"background: transparent; border: none; }}"
        )
        v.addWidget(sub)
        return (val, sub, card)

    # ------------------------------------------------------------------
    # Needs Attention chip strip
    # ------------------------------------------------------------------
    def _build_attention_bar(self) -> QWidget:
        wrap = QFrame()
        wrap.setStyleSheet(
            f"QFrame {{ background: {_PANEL}; border: 1px solid {_BORDER}; "
            f"border-radius: 8px; }}"
        )
        h = QHBoxLayout(wrap)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(8)
        head = QLabel("⚠  NEEDS ATTENTION")
        head.setStyleSheet(
            f"QLabel {{ color: {_YELLOW}; font-size: 10px; font-weight: 700; "
            f"letter-spacing: 0.8px; background: transparent; border: none; }}"
        )
        h.addWidget(head)
        self._attention_chips: dict[str, QFrame] = {}
        chips = [
            ("past_promise", "Past promise date",   _RED),
            ("not_started",  "Build not started",   _YELLOW),
            ("parts_back",   "Parts backordered",   _RED),
            ("cores_missing","Cores not received",  _PURPLE),
            ("await_po",     "Awaiting customer PO",_BLUE),
            ("freight_q",    "Freight quote needed",_TEAL),
            ("credit_hold",  "Hold — credit",       _RED),
        ]
        for key, label, color in chips:
            chip = self._make_chip(label, 0, color)
            self._attention_chips[key] = chip
            h.addWidget(chip)
        h.addStretch()
        return wrap

    def _make_chip(self, label: str, count: int, color: str) -> QFrame:
        chip = QFrame()
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setProperty("base_label", label)
        chip.setProperty("base_color", color)
        h = QHBoxLayout(chip)
        h.setContentsMargins(4, 2, 10, 2)
        h.setSpacing(6)
        badge = QLabel(str(count))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(18, 18)
        badge.setObjectName("badge")
        h.addWidget(badge)
        text = QLabel(label)
        text.setObjectName("text")
        h.addWidget(text)
        chip._badge = badge  # type: ignore[attr-defined]
        chip._text = text    # type: ignore[attr-defined]
        self._style_chip(chip, count, color)
        return chip

    def _style_chip(self, chip: QFrame, count: int, color: str) -> None:
        active = count > 0
        badge_bg = color if active else _BORDER
        chip.setStyleSheet(
            f"QFrame {{ background: {_PANEL2}; border: 1px solid "
            f"{_BORDER if not active else _BORDER_HI}; border-radius: 12px; }}"
            f"QFrame:hover {{ border-color: {color}; }}"
            f"QLabel#badge {{ background: {badge_bg}; color: white; "
            f"border-radius: 9px; font-size: 10px; font-weight: 700; }}"
            f"QLabel#text {{ color: {_TEXT if active else _TEXT_FAINT}; "
            f"font-size: 11px; font-weight: 600; background: transparent; "
            f"border: none; }}"
        )

    def _update_chip(self, key: str, count: int) -> None:
        chip = self._attention_chips.get(key)
        if not chip:
            return
        color = chip.property("base_color") or _TEXT_DIM
        badge = getattr(chip, "_badge", None)
        if badge is not None:
            badge.setText(str(count))
        self._style_chip(chip, count, color)

    # ------------------------------------------------------------------
    # Per-row cell widgets
    # ------------------------------------------------------------------
    def _customer_cell(self, so: dict) -> QWidget:
        w = QFrame()
        w.setStyleSheet("QFrame { background: transparent; border: none; }")
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 4, 10, 4)
        v.setSpacing(1)
        name = so.get("customer_name") or "—"
        company = so.get("customer_company")
        primary = f"{name}" + (f"  ·  {company}" if company else "")
        nl = QLabel(primary)
        nl.setStyleSheet(f"QLabel {{ color: {_TEXT}; font-size: 12px; font-weight: 600; "
                         f"background: transparent; border: none; }}")
        v.addWidget(nl)
        bits = []
        for k in ("customer_city", "city"):
            if so.get(k):
                bits.append(str(so[k])); break
        for k in ("customer_state", "state"):
            if so.get(k):
                if bits:
                    bits[-1] = f"{bits[-1]}, {so[k]}"
                else:
                    bits.append(str(so[k]))
                break
        for k in ("terms", "customer_terms", "payment_terms"):
            if so.get(k):
                bits.append(str(so[k])); break
        sl = QLabel("  ·  ".join(bits))
        sl.setStyleSheet(f"QLabel {{ color: {_TEXT_DIM}; font-size: 10px; "
                         f"background: transparent; border: none; }}")
        v.addWidget(sl)
        return w

    def _engine_label(self, so: dict) -> str:
        for k in ("engine_family", "engine_model", "engine", "platform"):
            v = so.get(k)
            if v:
                return str(v)
        esn = so.get("esn") or so.get("engine_serial")
        return f"ESN {esn}" if esn else "—"

    def _status_pill(self, so: dict) -> QWidget:
        status = (so.get("status") or "draft").lower()
        text, color = _SO_STATUS_DISPLAY.get(status, (status.upper(), _TEXT_DIM))
        w = QFrame()
        w.setStyleSheet("QFrame { background: transparent; border: none; }")
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(0)
        pill = QLabel(text)
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill.setStyleSheet(
            f"QLabel {{ color: {color}; "
            f"background: rgba(255,255,255,0.06); "
            f"border: 1px solid {color}; border-radius: 10px; "
            f"padding: 2px 8px; font-size: 10px; font-weight: 700; "
            f"letter-spacing: 0.3px; }}"
        )
        h.addWidget(pill)
        h.addStretch()
        return w

    def _fulfillment_cell(self, so: dict, line_statuses: list[str]) -> QWidget:
        """Render a thin progress bar for the SO's pick/pack/ship progress."""
        status = (so.get("status") or "draft").lower()
        if status == "invoiced" or status == "shipped":
            pct, color = 100, _GREEN
        elif status == "ready":
            pct, color = 90, _PURPLE
        elif status == "picking":
            pct, color = 60, _YELLOW
        elif status == "confirmed":
            pct, color = 25, _BLUE
        elif status == "backorder":
            pct, color = 35, _RED
        else:
            pct, color = 5, _TEXT_DIM
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(pct)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setStyleSheet(
            f"QProgressBar {{ background: {_PANEL2}; border: none; "
            f"border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        )
        w = QFrame()
        w.setStyleSheet("QFrame { background: transparent; border: none; }")
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 0, 10, 0)
        h.addWidget(bar)
        return w

    def _activity_cell(self, so: dict, line_statuses: list[str]) -> QWidget:
        status = (so.get("status") or "draft").lower()
        w = QFrame()
        w.setStyleSheet("QFrame { background: transparent; border: none; }")
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(3)
        # 8 fixed badges: mail / customer ack / build / pack / ship / paid / core / parts-issue
        flags = [
            ("✉", _BLUE,   status not in ("draft",)),
            ("✓", _GREEN,  status not in ("draft", "quote")),
            ("⚒", _YELLOW, status in ("picking", "ready", "shipped", "invoiced")),
            ("📦", _PURPLE, status in ("ready", "shipped", "invoiced")),
            ("🚚", _TEAL,  status in ("shipped", "invoiced")),
            ("$",  _GREEN, status == "invoiced"),
            ("♻", _PURPLE, bool(so.get("core_total"))),
            ("⚠", _RED,   any(s == "NEEDS_PO" for s in line_statuses)
                          or status == "backorder"),
        ]
        for glyph, color, active in flags:
            badge = QLabel(glyph)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(18, 18)
            if active:
                badge.setStyleSheet(
                    f"QLabel {{ background: {color}; color: white; "
                    f"border-radius: 4px; font-size: 10px; }}"
                )
            else:
                badge.setStyleSheet(
                    f"QLabel {{ background: {_PANEL2}; color: {_TEXT_FAINT}; "
                    f"border: 1px solid {_BORDER}; border-radius: 4px; "
                    f"font-size: 10px; }}"
                )
            h.addWidget(badge)
        h.addStretch()
        return w

    # ------------------------------------------------------------------
    @staticmethod
    def _action_btn_style(color: str) -> str:
        return (
            f"QPushButton {{ color: {color}; background: {_PANEL2}; "
            f"border: 1px solid {_BORDER}; padding: 6px 12px; "
            f"border-radius: 5px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {_BORDER}; "
            f"border-color: {_BORDER_HI}; }}"
            f"QPushButton:disabled {{ color: {_TEXT_FAINT}; "
            f"border-color: {_BORDER}; background: {_PANEL}; }}"
        )

    def _apply_tab_styles(self) -> None:
        # Legacy no-op kept for backward compatibility — status is now
        # driven by the `_status_combo` dropdown filter.
        return

    def _on_status_combo_changed(self, _index: int) -> None:
        data = self._status_combo.currentData()
        self._active_tab = data or "All"
        self._load_orders()

    def _on_tab_clicked(self, tab: str) -> None:
        # Legacy entry point — keep for any external callers.
        self._active_tab = tab
        idx = self._status_combo.findData(tab)
        if idx >= 0:
            self._status_combo.setCurrentIndex(idx)
        else:
            self._load_orders()

    def _sync_filter_choices(self, orders: list[dict]) -> None:
        def _refresh(combo: QComboBox, all_label: str, keys: tuple[str, ...]):
            current = combo.currentData()
            seen: set[str] = set()
            values: list[str] = []
            for o in orders:
                for k in keys:
                    v = o.get(k)
                    if v and str(v) not in seen:
                        seen.add(str(v)); values.append(str(v)); break
            values.sort(key=str.lower)
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(all_label, userData=None)
            for v in values:
                combo.addItem(v, userData=v)
            if current:
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.blockSignals(False)

        _refresh(self._rep_combo, "All Reps", ("rep", "sales_rep", "created_by"))
        _refresh(self._wh_combo, "All Warehouses", ("warehouse", "location"))

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def _load_orders(self) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)

        status_list = _SO_TAB_STATUS_MAP.get(self._active_tab)
        if status_list and len(status_list) == 1:
            orders = list_sales_orders(status=status_list[0])
        else:
            orders = list_sales_orders()
        if status_list and len(status_list) > 1:
            orders = [o for o in orders if o.get("status") in status_list]

        # Date window
        days = self._date_combo.currentData() if hasattr(self, "_date_combo") else None
        if days:
            cutoff = date.today() - timedelta(days=int(days))
            def _in_window(o):
                v = o.get("created_at") or o.get("order_date") or o.get("date")
                if not v:
                    return True
                try:
                    d = datetime.fromisoformat(str(v)[:19]).date()
                except Exception:
                    return True
                return d >= cutoff
            orders = [o for o in orders if _in_window(o)]

        # Refresh rep / warehouse dropdowns BEFORE filtering by them.
        self._sync_filter_choices(orders)

        rep_pick = self._rep_combo.currentData() if hasattr(self, "_rep_combo") else None
        if rep_pick:
            orders = [o for o in orders if (o.get("rep") or o.get("sales_rep")
                                            or o.get("created_by") or "") == rep_pick]
        wh_pick = self._wh_combo.currentData() if hasattr(self, "_wh_combo") else None
        if wh_pick:
            orders = [o for o in orders if (o.get("warehouse") or o.get("location") or "") == wh_pick]

        search = self._search_edit.text().strip().lower()
        if search:
            orders = [o for o in orders
                      if search in (o.get("so_number") or "").lower()
                      or search in (o.get("customer_name") or "").lower()
                      or search in (o.get("customer_company") or "").lower()
                      or search in str(o.get("total") or "")]

        self._table.setRowCount(len(orders))
        total_value = 0.0
        today = date.today()
        kpi = {"open": 0, "in_build": 0, "ready": 0, "ship_today": 0,
               "backorder": 0, "booked_mtd": 0.0}
        attn = {"past_promise": 0, "not_started": 0, "parts_back": 0,
                "cores_missing": 0, "await_po": 0, "freight_q": 0, "credit_hold": 0}

        def cell(text: str, *, align=None, color=None) -> QTableWidgetItem:
            it = QTableWidgetItem("" if text is None else str(text))
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if align is not None:
                it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            if color is not None:
                it.setForeground(QColor(color))
            return it

        for i, so in enumerate(orders):
            status = (so.get("status") or "draft").lower()
            try:
                so_lines = get_so_lines(so.get("id")) or []
                line_statuses = [derive_so_line_status(l) for l in so_lines]
            except Exception:
                line_statuses = []

            # KPI accumulators
            if status in ("quote", "draft", "confirmed"): kpi["open"] += 1
            if status == "picking": kpi["in_build"] += 1
            if status == "ready":   kpi["ready"] += 1
            if status == "backorder" or any(s == "NEEDS_PO" for s in line_statuses):
                kpi["backorder"] += 1
            promised = so.get("promised_date")
            try:
                p_date = datetime.fromisoformat(str(promised)[:10]).date() if promised else None
            except Exception:
                p_date = None
            if p_date and p_date == today and status in ("ready", "shipped"):
                kpi["ship_today"] += 1
            try:
                created_v = so.get("created_at") or so.get("order_date")
                created_d = datetime.fromisoformat(str(created_v)[:19]).date() if created_v else None
                if created_d and created_d.month == today.month and created_d.year == today.year:
                    kpi["booked_mtd"] += float(so.get("total") or 0)
            except Exception:
                pass

            # Attention counters
            if p_date and p_date < today and status not in ("shipped", "invoiced", "cancelled"):
                attn["past_promise"] += 1
            if status in ("confirmed",) and not line_statuses:
                attn["not_started"] += 1
            if any(s == "NEEDS_PO" for s in line_statuses):
                attn["parts_back"] += 1
            if so.get("core_total") and status in ("ready", "shipped") and not so.get("cores_received"):
                attn["cores_missing"] += 1
            if not so.get("customer_po") and status in ("draft", "confirmed"):
                attn["await_po"] += 1
            if not so.get("ship_method") and status in ("ready",):
                attn["freight_q"] += 1
            if so.get("credit_hold"):
                attn["credit_hold"] += 1

            # Row cells
            self._table.setItem(i, 0, cell(so.get("id")))

            so_num = cell(so.get("so_number"), color=_ACCENT)
            f = so_num.font(); f.setBold(True); so_num.setFont(f)
            self._table.setItem(i, 1, so_num)

            ci = cell("")
            ci.setData(Qt.ItemDataRole.UserRole, so.get("customer_name") or "")
            self._table.setItem(i, 2, ci)
            self._table.setCellWidget(i, 2, self._customer_cell(so))

            self._table.setItem(i, 3, cell(self._engine_label(so), color=_TEXT_DIM))

            total = float(so.get("total") or 0)
            total_value += total
            self._table.setItem(i, 4,
                cell(f"${total:,.2f}", align=Qt.AlignmentFlag.AlignRight))

            self._table.setItem(i, 5, cell(""))
            self._table.setCellWidget(i, 5, self._status_pill(so))

            # Age
            age_text = "—"
            try:
                cv = so.get("created_at") or so.get("order_date")
                if cv:
                    cd = datetime.fromisoformat(str(cv)[:19]).date()
                    age_text = f"{(today - cd).days}d"
            except Exception:
                pass
            self._table.setItem(i, 6,
                cell(age_text, align=Qt.AlignmentFlag.AlignCenter, color=_TEXT_DIM))

            # Promise
            if p_date:
                ptxt = p_date.strftime("%b %d")
                pcolor = _RED if p_date < today and status not in ("shipped", "invoiced") else _TEXT
                self._table.setItem(i, 7,
                    cell(ptxt, align=Qt.AlignmentFlag.AlignCenter, color=pcolor))
            else:
                self._table.setItem(i, 7,
                    cell("—", align=Qt.AlignmentFlag.AlignCenter, color=_TEXT_FAINT))

            # Fulfillment progress bar
            self._table.setItem(i, 8, cell(""))
            self._table.setCellWidget(i, 8, self._fulfillment_cell(so, line_statuses))

            # Ship via
            self._table.setItem(i, 9,
                cell(so.get("ship_method") or "—", color=_TEXT_DIM))

            # Rep
            rep = so.get("rep") or so.get("sales_rep") or so.get("created_by") or "—"
            self._table.setItem(i, 10, cell(rep, color=_TEXT_DIM))

            # Activity badges
            self._table.setItem(i, 11, cell(""))
            self._table.setCellWidget(i, 11, self._activity_cell(so, line_statuses))

        self._table.setSortingEnabled(True)
        self._summary_label.setText(
            f"{len(orders)} orders  ·  ${total_value:,.2f} booked value"
        )

        # Push KPI values
        def _set_kpi(key: str, val: str, sub: str = "") -> None:
            card = self._kpi_cards.get(key)
            if not card:
                return
            card[0].setText(val); card[1].setText(sub)
        _set_kpi("open",        str(kpi["open"]),        "in fulfillment")
        _set_kpi("in_build",    str(kpi["in_build"]),    "on shop floor")
        _set_kpi("ready",       str(kpi["ready"]),       "staged")
        _set_kpi("ship_today",  str(kpi["ship_today"]),  "scheduled pickups")
        _set_kpi("backorder",   str(kpi["backorder"]),   "need parts")
        _set_kpi("booked_mtd",  f"${kpi['booked_mtd']/1000:,.0f}k"
                                if kpi['booked_mtd'] >= 1000
                                else f"${kpi['booked_mtd']:,.0f}", "this month")
        _set_kpi("avg_cycle",   "—",                     "quote → ship")

        for key, count in attn.items():
            self._update_chip(key, count)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------
    def _on_selection_changed(self) -> None:
        so_id = self._get_selected_id()
        has = so_id is not None
        self._view_btn.setEnabled(has)

        if has:
            so = get_sales_order(so_id)
            status = (so.get("status") or "draft").lower() if so else "draft"
            self._confirm_btn.setEnabled(status in ("quote", "draft"))
            self._pick_btn.setEnabled(status == "confirmed")
            self._ready_btn.setEnabled(status == "picking")
            self._ship_btn.setEnabled(status == "ready")
            self._invoice_btn.setEnabled(status in ("ready", "shipped"))
            self._cancel_btn.setEnabled(status not in ("invoiced", "cancelled"))
            self._email_btn.setEnabled(status not in ("cancelled",))
        else:
            for btn in (self._confirm_btn, self._pick_btn, self._ready_btn,
                        self._ship_btn, self._invoice_btn, self._cancel_btn,
                        self._email_btn):
                btn.setEnabled(False)

    def _get_selected_id(self) -> int | None:
        selected = self._table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        id_item = self._table.item(row, 0)
        return int(id_item.text()) if id_item else None

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _new_so(self) -> None:
        from .quote_dialog import QuoteDialog
        dlg = QuoteDialog(mode="sales_order", parent=self)
        dlg.exec()
        self._load_orders()

    def _on_view_detail(self, *_args) -> None:
        """Open a detail dialog for the selected sales order with line items."""
        so_id = self._get_selected_id()
        if not so_id:
            return
        from .so_detail_dialog import open_so_detail
        dlg = open_so_detail(so_id, parent=self, on_closed=self._load_orders)
        # Refresh list immediately when SO state changes (e.g. after
        # convert-to-invoice flips status to 'invoiced'), even before
        # the dialog is closed.
        try:
            dlg.so_changed.connect(self._load_orders)
        except Exception:
            pass

    def _on_context_menu(self, pos) -> None:
        row = self._table.rowAt(pos.y())
        if row < 0:
            return

        self._table.selectRow(row)
        so_id = self._get_selected_id()
        if not so_id:
            return

        so = get_sales_order(so_id)
        status = (so.get("status") or "draft").lower() if so else "draft"

        # Determine if any active line still needs purchasing.
        needs_po = False
        try:
            so_lines = get_so_lines(so_id) or []
            line_statuses = [derive_so_line_status(l) for l in so_lines]
            needs_po = any(s == "NEEDS_PO" for s in line_statuses)
        except Exception:
            line_statuses = []

        menu = QMenu(self)
        open_action = menu.addAction("Open Sales Order")
        menu.addSeparator()
        confirm_action = menu.addAction("Confirm Order")
        create_po_action = menu.addAction("Create PO for Needed Items")
        pick_action = menu.addAction("Start Picking")
        ready_action = menu.addAction("Mark Ready")
        ship_action = menu.addAction("Ship Order")
        invoice_action = menu.addAction("Convert to Invoice")
        email_action = menu.addAction("Email Customer")
        menu.addSeparator()
        cancel_action = menu.addAction("Cancel Order")

        confirm_action.setEnabled(status in ("quote", "draft"))
        create_po_action.setEnabled(needs_po and status not in ("cancelled", "invoiced"))
        if needs_po:
            blocked_tip = "Waiting on PO — create / receive a PO before this step."
            for act in (pick_action, ready_action, ship_action, invoice_action):
                act.setEnabled(False)
                act.setToolTip(blocked_tip)
                act.setText(f"{act.text()} \u2014 Needs PO")
        else:
            pick_action.setEnabled(status == "confirmed")
            ready_action.setEnabled(status == "picking")
            ship_action.setEnabled(status == "ready")
            invoice_action.setEnabled(status in ("ready", "shipped"))
        email_action.setEnabled(status != "cancelled")
        cancel_action.setEnabled(status not in ("invoiced", "cancelled"))

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is open_action:
            self._on_view_detail()
        elif chosen is confirm_action:
            self._set_status("confirmed")
        elif chosen is create_po_action:
            self._on_create_po(so_id)
        elif chosen is pick_action:
            self._set_status("picking")
        elif chosen is ready_action:
            self._set_status("ready")
        elif chosen is ship_action:
            self._ship_order()
        elif chosen is invoice_action:
            self._convert_to_invoice()
        elif chosen is email_action:
            self._on_send_email()
        elif chosen is cancel_action:
            self._set_status("cancelled")

    def _on_create_po(self, so_id: int) -> None:
        try:
            result = create_po_from_so(so_id)
        except Exception as exc:
            QMessageBox.critical(self, "Create PO", f"Failed to create PO:\n{exc}")
            return
        if not result.get("success"):
            QMessageBox.warning(
                self, "Create PO",
                result.get("error", "No purchasable lines need a PO."),
            )
            return
        pos = result.get("purchase_orders") or []
        # Link SO lines to each created PO
        try:
            from db.inventory import link_so_lines_to_po
            for po in pos:
                po_id = po.get("id") or po.get("po_id")
                if po_id:
                    link_so_lines_to_po(so_id, po_id)
        except Exception:
            pass
        msg = (
            f"Created {len(pos)} PO(s) for items needing purchase."
            if pos else "POs created."
        )
        QMessageBox.information(self, "Create PO", msg)
        self._load_orders()

    def _set_status(self, status: str) -> None:
        so_id = self._get_selected_id()
        if not so_id:
            return
        if status == "cancelled":
            reply = QMessageBox.question(self, "Cancel Order",
                "Cancel this order? Reserved inventory will be released.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        update_sales_order(so_id, status=status)
        # If status is related to PO receipt/fulfillment, update SO line statuses
        if status in ("ready", "shipped", "invoiced", "received"):
            try:
                from db.inventory import update_so_line_statuses_for_po
                from db import get_all_purchase_orders
                pos = get_all_purchase_orders(vendor_id=None, status=None)
                for po in pos:
                    if po.get("linked_so_id") == so_id:
                        po_id = po.get("id") or po.get("po_id")
                        if po_id:
                            update_so_line_statuses_for_po(po_id, new_status=status.upper())
            except Exception:
                pass
        self._load_orders()

    def _ship_order(self) -> None:
        so_id = self._get_selected_id()
        if not so_id:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Ship Order")
        form = QFormLayout(dlg)
        tracking = QLineEdit()
        tracking.setPlaceholderText("Tracking number (optional)")
        form.addRow("Tracking #:", tracking)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() == QDialog.Accepted:
            fields = {"status": "shipped"}
            t = tracking.text().strip()
            if t:
                fields["tracking_number"] = t
            update_sales_order(so_id, **fields)
            self._load_orders()

    def _convert_to_invoice(self) -> None:
        so_id = self._get_selected_id()
        if not so_id:
            return
        reply = QMessageBox.question(self, "Convert to Invoice",
            "Convert this sales order to an invoice?\n"
            "Reserved inventory will be released and an invoice created.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        result = convert_so_to_invoice(so_id)
        if result.get("error"):
            QMessageBox.warning(self, "Error", result["error"])
        else:
            QMessageBox.information(self, "Invoiced",
                f"Invoice {result['invoice_number']} created.")
            self._load_orders()

    # ------------------------------------------------------------------
    # Email
    # ------------------------------------------------------------------
    def _on_send_email(self) -> None:
        """Send the selected sales order or its tracking info via email."""
        from jaks_inventory.utils.notification_service import (
            email_configured, send_so_email, send_so_tracking_email,
            get_customer_contact,
        )
        from PySide6.QtWidgets import QInputDialog

        so_id = self._get_selected_id()
        if not so_id:
            return

        if not email_configured():
            QMessageBox.warning(self, "Email Not Configured",
                                "Configure SMTP settings in Settings → Email / SMTP.")
            return

        so = get_sales_order(so_id)
        if not so:
            return

        contact = get_customer_contact(so["customer_id"])
        email = contact["email"]
        if not email:
            QMessageBox.warning(self, "No Email",
                                "This customer has no email address on file.\n"
                                "Add one via Customers → Customer Maintenance.")
            return

        tracking = so.get("tracking_number", "") or ""
        choices = ["Order Confirmation"]
        if tracking:
            choices.append(f"Tracking Number ({tracking})")

        choice, ok = QInputDialog.getItem(
            self, "Send Email",
            f"Sending to {contact['name']} ({email}):",
            choices, 0, False,
        )
        if not ok:
            return

        from jaks_inventory.utils.email_worker import EmailWorker

        if "Tracking" in choice:
            func = lambda: send_so_tracking_email(so, email)
        else:
            lines = get_so_lines(so_id)
            func = lambda: send_so_email(so, lines, email)

        self._email_btn.setEnabled(False)
        self._email_btn.setText("⏳ Sending…")
        self._email_worker = EmailWorker(func, parent=self)

        def _on_result(result: dict) -> None:
            self._email_btn.setEnabled(True)
            self._email_btn.setText("📧 Email")
            if result.get("success"):
                QMessageBox.information(self, "Email Sent",
                                        f"Email sent to {email} successfully.")
            else:
                QMessageBox.critical(self, "Email Failed",
                                     result.get("error", "Unknown error"))

        self._email_worker.finished_result.connect(_on_result)
        self._email_worker.start()

