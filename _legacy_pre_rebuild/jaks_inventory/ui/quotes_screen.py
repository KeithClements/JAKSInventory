"""Quotes management screen — diesel-shop workflow layout.

Layout (per UX spec):
  ┌─ KPI strip ────────────────────────────────────────────────────────┐
  │  Open · Awaiting Follow-up · Won today · Lost today · Revenue MTD │
  │  Conversion · Avg Margin                                          │
  ├─ ⚠ Needs Attention (clickable chips, task queue) ─────────────────┤
  ├─ Status tabs · Search · Action buttons ───────────────────────────┤
  ├─ Quote table:                                                      │
  │   Quote#│Customer│Engine/Platform│Total│Margin│Status│Age│         │
  │   Follow-Up│Rep│Src│Activity icons (sms/email/viewed/replied/      │
  │   attach/core/freight/unavail)                                     │
  └────────────────────────────────────────────────────────────────────┘

Dark theme (orange accent) to match the diesel-shop mockup.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QCursor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from db import get_all_quotes, get_quote, update_quote, convert_quote_to_invoice, convert_quote_to_so
from .quote_dialog import QuoteDialog


# Status badge colors (bg, fg) - shared with other screens
from core.badges import QUOTE_BADGES as _BADGE


# ============================================================================
# DARK THEME PALETTE (matches the diesel-shop mockup)
# ============================================================================
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

# Dark-theme status badges (override the shared light-theme ones for this screen)
_DARK_STATUS_BADGES = {
    "draft":            ("rgba(88,166,255,0.15)",  _BLUE),
    "sent":             ("rgba(88,166,255,0.18)",  _BLUE),
    "needs_follow_up":  ("rgba(210,153,34,0.18)",  _YELLOW),
    "accepted":         ("rgba(63,185,80,0.18)",   _GREEN),
    "invoiced":         ("rgba(63,185,80,0.25)",   _GREEN),
    "lost":             ("rgba(248,81,73,0.18)",   _RED),
    "void":             ("rgba(188,140,255,0.18)", _PURPLE),
    "expired":          ("rgba(248,81,73,0.18)",   _RED),
}

# Priority display config  (display_text, fg_color)
_PRIORITY_DISPLAY = {
    "high":                ("🔥 HIGH", "#DC2626"),
    "medium":              ("MEDIUM", "#F59E0B"),
    "low":                 ("LOW", "#6B7280"),
    "waiting_on_customer": ("⏳ WAITING ON CUST", "#7C3AED"),
    "waiting_on_vendor":   ("⏳ WAITING ON VENDOR", "#2563EB"),
    # Legacy fallbacks
    "normal":              ("MEDIUM", "#F59E0B"),
    "rush":                ("🔥 HIGH", "#DC2626"),
    "will-call":           ("MEDIUM", "#F59E0B"),
}

_PRIORITY_OPTIONS = ["high", "medium", "low", "waiting_on_customer", "waiting_on_vendor"]

_TAB_STATUSES = ["All", "Draft", "Sent", "Needs Follow-up", "Accepted", "Unfinished", "Invoiced", "Lost", "Void"]

# Map display name → DB status
_TAB_STATUS_MAP = {
    "All": None,
    "Draft": "draft",
    "Sent": "sent",
    "Needs Follow-up": "needs_follow_up",
    "Accepted": "accepted",
    "Unfinished": "__unfinished__",
    "Invoiced": "invoiced",
    "Lost": "lost",
    "Void": "void",
}


class _StatusBadgeItem(QTableWidgetItem):
    """Table item rendered with status text + colored background (dark theme)."""

    def __init__(self, status: str) -> None:
        display = status.replace("_", " ").upper() if status else ""
        super().__init__(display)
        bg, fg = _DARK_STATUS_BADGES.get(
            (status or "").lower(),
            (f"rgba(139,152,165,0.15)", _TEXT_DIM),
        )
        # QColor doesn't parse rgba() strings — translate manually.
        self.setBackground(_parse_rgba(bg))
        self.setForeground(QColor(fg))
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFlags(self.flags() & ~Qt.ItemFlag.ItemIsEditable)


def _parse_rgba(s: str) -> QColor:
    """Tiny helper: 'rgba(r,g,b,a)' → QColor (a is 0–1 float)."""
    if s.startswith("rgba"):
        nums = s[s.find("(") + 1: s.find(")")].split(",")
        r, g, b = (int(n.strip()) for n in nums[:3])
        a = int(float(nums[3].strip()) * 255)
        return QColor(r, g, b, a)
    return QColor(s)


# Structured reasons for losing a quote (used in _QuoteCloseOutDialog)
_LOST_REASONS = [
    "Price too high",
    "Lead time too long",
    "Customer went with competitor",
    "Customer went direct to OEM",
    "Part no longer needed",
    "Could not source part",
    "No response from customer",
    "Other",
]


class _QuoteCloseOutDialog(QDialog):
    """Unified outcome dialog for follow-up / close-out on a quote.

    Replaces the previous two-step "Set Follow-Up Date" + "Mark as Lost" flow
    with a single dialog presenting all realistic outcomes side-by-side.

    After .exec() returns Accepted, read the public attributes:
        outcome: one of 'pending', 'won', 'lost', 'void'
        follow_up_date: ISO date string (only when outcome=='pending')
        next_action: free text (only when outcome=='pending')
        lost_reason / lost_notes: strings (only when outcome=='lost')
    """

    def __init__(self, quote: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtCore import QDate
        from PySide6.QtWidgets import (
            QButtonGroup, QDateEdit, QDialogButtonBox, QRadioButton,
            QStackedWidget, QTextEdit,
        )

        self._quote = quote
        self.outcome: str = "pending"
        self.follow_up_date: str = ""
        self.next_action: str = ""
        self.lost_reason: str = ""
        self.lost_notes: str = ""

        self.setWindowTitle(f"Close Out Quote {quote.get('quote_number', '')}")
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        header = QLabel(
            f"<b>Quote {quote.get('quote_number', '')}</b>"
            f" &nbsp; <span style='color:#6B7280;'>{quote.get('customer_company') or quote.get('customer_name') or ''}</span>"
        )
        header.setStyleSheet("font-size: 12pt;")
        root.addWidget(header)
        hint = QLabel("What's the outcome of this quote?")
        hint.setStyleSheet("color:#6B7280;")
        root.addWidget(hint)

        # ── Radio choices ──
        self._grp = QButtonGroup(self)
        self._rb_pending = QRadioButton("⏳  Still pending — reschedule follow-up")
        self._rb_won = QRadioButton("✓  Won — customer accepted")
        self._rb_lost = QRadioButton("✗  Lost")
        self._rb_void = QRadioButton("🚫  Void — cancel this quote")
        for i, rb in enumerate([self._rb_pending, self._rb_won, self._rb_lost, self._rb_void]):
            rb.setStyleSheet("font-size: 11pt; padding: 3px;")
            self._grp.addButton(rb, i)
            root.addWidget(rb)
        self._rb_pending.setChecked(True)

        # ── Dynamic details panel (switches with selection) ──
        self._stack = QStackedWidget()

        # pending panel
        pending_w = QWidget()
        p_lay = QVBoxLayout(pending_w)
        p_lay.setContentsMargins(24, 4, 0, 0)
        p_lay.addWidget(QLabel("Follow up on:"))
        self._fu_date = QDateEdit()
        self._fu_date.setCalendarPopup(True)
        self._fu_date.setDisplayFormat("MM/dd/yyyy")
        existing_fu = quote.get("follow_up_date") or ""
        if existing_fu:
            try:
                from datetime import datetime
                d = datetime.strptime(existing_fu[:10], "%Y-%m-%d")
                self._fu_date.setDate(QDate(d.year, d.month, d.day))
            except Exception:
                self._fu_date.setDate(QDate.currentDate().addDays(7))
        else:
            self._fu_date.setDate(QDate.currentDate().addDays(7))
        p_lay.addWidget(self._fu_date)
        p_lay.addWidget(QLabel("Next action (optional):"))
        self._na_edit = QLineEdit(quote.get("next_action") or "")
        self._na_edit.setPlaceholderText("e.g. Call Bob about core charge")
        p_lay.addWidget(self._na_edit)
        p_lay.addStretch()
        self._stack.addWidget(pending_w)

        # won panel (no extra inputs today — could extend for auto-SO conversion)
        won_w = QWidget()
        w_lay = QVBoxLayout(won_w)
        w_lay.setContentsMargins(24, 4, 0, 0)
        w_lay.addWidget(QLabel(
            "Status will be set to <b>Accepted</b>. You can convert it to a Sales Order\n"
            "from the Quotes screen afterward."
        ))
        w_lay.addStretch()
        self._stack.addWidget(won_w)

        # lost panel
        lost_w = QWidget()
        l_lay = QVBoxLayout(lost_w)
        l_lay.setContentsMargins(24, 4, 0, 0)
        l_lay.addWidget(QLabel("Reason:"))
        from PySide6.QtWidgets import QComboBox as _QComboBox
        self._lost_combo = _QComboBox()
        try:
            from db.inventory import get_lost_reasons
            reason_labels = [r.get("label") for r in get_lost_reasons(active_only=True) if r.get("label")]
        except Exception:
            reason_labels = list(_LOST_REASONS)
        if not reason_labels:
            reason_labels = list(_LOST_REASONS)
        self._lost_combo.addItems(reason_labels)
        existing_reason = quote.get("lost_reason") or ""
        if existing_reason and existing_reason in reason_labels:
            self._lost_combo.setCurrentText(existing_reason)
        elif existing_reason:
            # Historic value not in active list — keep it available.
            self._lost_combo.addItem(existing_reason)
            self._lost_combo.setCurrentText(existing_reason)
        l_lay.addWidget(self._lost_combo)
        l_lay.addWidget(QLabel("Notes (optional):"))
        self._lost_notes = QTextEdit()
        self._lost_notes.setPlainText(quote.get("lost_notes") or "")
        self._lost_notes.setFixedHeight(80)
        l_lay.addWidget(self._lost_notes)
        l_lay.addStretch()
        self._stack.addWidget(lost_w)

        # void panel
        void_w = QWidget()
        v_lay = QVBoxLayout(void_w)
        v_lay.setContentsMargins(24, 4, 0, 0)
        v_lay.addWidget(QLabel(
            "Status will be set to <b>Void</b>. Use this when the quote was a mistake,\n"
            "a duplicate, or no longer relevant."
        ))
        v_lay.addStretch()
        self._stack.addWidget(void_w)

        root.addWidget(self._stack, 1)
        self._grp.idClicked.connect(self._stack.setCurrentIndex)

        # ── Buttons ──
        from PySide6.QtWidgets import QDialogButtonBox
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setText("Save Outcome")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _on_accept(self) -> None:
        idx = self._grp.checkedId()
        if idx == 0:       # pending
            self.outcome = "pending"
            self.follow_up_date = self._fu_date.date().toString("yyyy-MM-dd")
            self.next_action = self._na_edit.text().strip()
        elif idx == 1:     # won
            self.outcome = "won"
        elif idx == 2:     # lost
            self.outcome = "lost"
            self.lost_reason = self._lost_combo.currentText()
            self.lost_notes = self._lost_notes.toPlainText().strip()
        elif idx == 3:     # void
            self.outcome = "void"
        self.accept()


class QuotesScreen(QWidget):
    """Screen listing all quotes with status tabs, search, and quick actions."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active_status = "All"
        self._build_ui()
        self._load_quotes()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._load_quotes()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # Cascade dark theme across the whole screen
        self.setStyleSheet(f"""
            QWidget {{ background: {_BG}; color: {_TEXT};
                       font-family: 'Segoe UI', 'Inter', sans-serif; }}
            QToolTip {{ background: {_PANEL2}; color: {_TEXT};
                        border: 1px solid {_BORDER}; }}
            QScrollBar:vertical {{ background: {_BG}; width: 10px; }}
            QScrollBar::handle:vertical {{ background: {_BORDER_HI};
                                           border-radius: 5px; min-height: 30px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar:horizontal {{ background: {_BG}; height: 10px; }}
            QScrollBar::handle:horizontal {{ background: {_BORDER_HI};
                                             border-radius: 5px; min-width: 30px; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 10)
        layout.setSpacing(10)

        # ── Header: title + new-quote button ──
        header = QHBoxLayout()
        title = QLabel("Quotes")
        title.setStyleSheet(f"QLabel {{ font-size: 20px; font-weight: 700; color: {_TEXT}; letter-spacing: 0.3px; background: transparent; }}")
        header.addWidget(title)
        sub = QLabel("Diesel rebuilds · Parts · Service")
        sub.setStyleSheet(f"QLabel {{ color: {_TEXT_DIM}; font-size: 11px; padding-left: 10px; background: transparent; }}")
        header.addWidget(sub)
        header.addStretch()

        self._new_btn = QPushButton("＋  New Quote")
        self._new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._new_btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: white; font-weight: 700; "
            f"padding: 8px 18px; border-radius: 6px; font-size: 12px; border: none; }}"
            f"QPushButton:hover {{ background: {_ACCENT_DIM}; }}"
        )
        self._new_btn.clicked.connect(self._on_new)
        header.addWidget(self._new_btn)
        layout.addLayout(header)

        # ── KPI strip ──
        layout.addWidget(self._build_kpi_row())

        # ── Needs Attention chips ──
        layout.addWidget(self._build_attention_bar())

        # ── Search + filter dropdowns + action buttons ──
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("🔍   Search by quote #, customer, ESN, part…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMaximumWidth(280)
        self._search_edit.setStyleSheet(
            f"QLineEdit {{ background: {_PANEL2}; color: {_TEXT}; "
            f"padding: 7px 10px; border: 1px solid {_BORDER}; "
            f"border-radius: 6px; font-size: 12px; }}"
            f"QLineEdit:focus {{ border-color: {_ACCENT}; }}"
        )
        self._search_edit.textChanged.connect(lambda _: self._load_quotes())
        filter_row.addWidget(self._search_edit)

        # Status / Rep / Source / Date dropdown filters
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
        for label in _TAB_STATUSES:
            self._status_combo.addItem(
                "All Status" if label == "All" else label, userData=label
            )
        self._status_combo.setStyleSheet(combo_qss)
        self._status_combo.currentIndexChanged.connect(self._on_status_combo_changed)
        filter_row.addWidget(self._status_combo)

        self._rep_combo = QComboBox()
        self._rep_combo.addItem("All Reps", userData=None)
        self._rep_combo.setStyleSheet(combo_qss)
        self._rep_combo.currentIndexChanged.connect(lambda _: self._load_quotes())
        filter_row.addWidget(self._rep_combo)

        self._source_combo = QComboBox()
        self._source_combo.addItem("All Sources", userData=None)
        self._source_combo.setStyleSheet(combo_qss)
        self._source_combo.currentIndexChanged.connect(lambda _: self._load_quotes())
        filter_row.addWidget(self._source_combo)

        self._date_combo = QComboBox()
        for label, days in (
            ("Last 30 days", 30), ("Last 7 days", 7),
            ("Last 90 days", 90), ("This year", 365), ("All time", None),
        ):
            self._date_combo.addItem(label, userData=days)
        self._date_combo.setStyleSheet(combo_qss)
        self._date_combo.currentIndexChanged.connect(lambda _: self._load_quotes())
        filter_row.addWidget(self._date_combo)

        filter_row.addStretch()

        # Action buttons (enabled when row selected)
        self._view_btn = QPushButton("View / Edit")
        self._view_btn.setEnabled(False)
        self._view_btn.clicked.connect(self._on_view)
        self._view_btn.setStyleSheet(self._action_btn_style(_TEXT))
        filter_row.addWidget(self._view_btn)

        self._to_so_btn = QPushButton("→ Sales Order")
        self._to_so_btn.setEnabled(False)
        self._to_so_btn.clicked.connect(self._on_convert_to_so)
        self._to_so_btn.setStyleSheet(self._action_btn_style(_BLUE))
        filter_row.addWidget(self._to_so_btn)

        self._convert_btn = QPushButton("→ Invoice")
        self._convert_btn.setEnabled(False)
        self._convert_btn.clicked.connect(self._on_convert)
        self._convert_btn.setStyleSheet(self._action_btn_style(_GREEN))
        filter_row.addWidget(self._convert_btn)

        self._void_btn = QPushButton("Void")
        self._void_btn.setEnabled(False)
        self._void_btn.clicked.connect(self._on_void)
        self._void_btn.setStyleSheet(self._action_btn_style(_RED))
        filter_row.addWidget(self._void_btn)

        self._follow_up_btn = QPushButton("📞  Close-Out / Follow-Up")
        self._follow_up_btn.setEnabled(False)
        self._follow_up_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._follow_up_btn.clicked.connect(self._on_follow_up)
        self._follow_up_btn.setToolTip(
            "Record the outcome of this quote: reschedule follow-up, mark won, "
            "mark lost with reason, or void."
        )
        self._follow_up_btn.setStyleSheet(
            f"QPushButton {{ color: white; background: {_ACCENT}; border: none; "
            f"padding: 6px 14px; border-radius: 5px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {_ACCENT_DIM}; }}"
            f"QPushButton:disabled {{ background: {_PANEL2}; color: {_TEXT_FAINT}; }}"
        )
        filter_row.addWidget(self._follow_up_btn)

        layout.addLayout(filter_row)

        # ── Table ──
        # Columns: 0 ID (hidden) | 1 Quote# | 2 Customer | 3 Engine/Platform |
        #          4 Total | 5 Margin | 6 Status | 7 Age | 8 Follow-Up |
        #          9 Rep | 10 Source | 11 Activity
        self._table = QTableWidget(0, 12)
        self._table.setHorizontalHeaderLabels([
            "ID", "Quote #", "Customer", "Engine / Platform",
            "Total", "Margin", "Status", "Age",
            "Follow-Up", "Rep", "Source", "Activity",
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.setColumnHidden(0, True)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setDefaultSectionSize(44)  # taller for 2-line customer
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
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)       # customer
        hdr.setSectionResizeMode(11, QHeaderView.ResizeMode.Fixed)         # activity icons
        hdr.setMinimumSectionSize(60)
        self._table.setColumnWidth(1, 100)   # Quote #
        self._table.setColumnWidth(3, 140)   # Engine/Platform
        self._table.setColumnWidth(4, 100)   # Total
        self._table.setColumnWidth(5, 80)    # Margin
        self._table.setColumnWidth(6, 110)   # Status
        self._table.setColumnWidth(7, 70)    # Age
        self._table.setColumnWidth(8, 100)   # Follow-Up
        self._table.setColumnWidth(9, 80)    # Rep
        self._table.setColumnWidth(10, 90)   # Source
        self._table.setColumnWidth(11, 200)  # Activity icons
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.doubleClicked.connect(self._on_view)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._table, 1)

        # ── Summary footer ──
        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(
            f"QLabel {{ color: {_TEXT_DIM}; font-size: 11px; padding: 4px 2px; background: transparent; }}"
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

        # Cards are populated with stub values; _refresh_kpis() overwrites them
        # whenever quotes are reloaded. TODO: wire to db.kpis aggregate query
        # once that helper exists.
        self._kpi_cards: dict[str, tuple[QLabel, QLabel]] = {}
        cards = [
            ("open",          "Open Quotes",       _BLUE),
            ("follow_up",     "Awaiting Follow-Up", _YELLOW),
            ("won_today",     "Won Today",         _GREEN),
            ("lost_today",    "Lost Today",        _RED),
            ("revenue_mtd",   "Revenue MTD",       _ACCENT),
            ("conv_rate",     "Conversion",        _PURPLE),
            ("avg_margin",    "Avg Margin",        _GREEN),
        ]
        for key, label, color in cards:
            card = self._build_kpi_card(label, "—", color)
            self._kpi_cards[key] = card  # (value_label, sub_label)
            grid.addWidget(card[2], 1)   # card[2] is the frame
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
    # Needs-Attention chip strip
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

        # Chip definitions — counts are populated by _refresh_attention().
        # TODO: wire each chip's click to apply a real filter on the table.
        self._attention_chips: dict[str, QLabel] = {}
        chips = [
            ("aged",         "Older than 3 days", _RED),
            ("no_followup",  "No follow-up",      _YELLOW),
            ("large",        "Large $ (>$10k)",   _ACCENT),
            ("replied",      "Customer replied",  _GREEN),
            ("parts_na",     "Parts unavailable", _RED),
            ("core_info",    "Awaiting core info", _PURPLE),
            ("freight",      "Freight pending",   _BLUE),
        ]
        for key, label, color in chips:
            chip = self._make_chip(label, 0, color)
            self._attention_chips[key] = chip
            h.addWidget(chip)

        h.addStretch()
        return wrap

    def _make_chip(self, label: str, count: int, color: str) -> QLabel:
        """Build a Needs-Attention chip: colored circle badge + label.

        The chip is a QFrame holding a circular count badge on the left and
        a dim label on the right. The whole chip is clickable.
        """
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

        # Stash refs so _update_chip can mutate without rebuilding.
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
        self._active_status = data or "All"
        self._load_quotes()

    def _on_tab_clicked(self, status: str) -> None:
        # Legacy entry point — kept for any callers in shortcut handlers.
        self._active_status = status
        idx = self._status_combo.findData(status)
        if idx >= 0:
            self._status_combo.setCurrentIndex(idx)
        else:
            self._load_quotes()

    def _sync_filter_choices(self, quotes: list[dict]) -> None:
        """Repopulate Rep / Source dropdowns with the values present in *quotes*.

        Keeps the user's current selection if it still applies, otherwise
        falls back to the catch-all entry.
        """
        def _refresh(combo: QComboBox, all_label: str, keys: tuple[str, ...]):
            current = combo.currentData()
            values: list[str] = []
            seen: set[str] = set()
            for q in quotes:
                for k in keys:
                    v = q.get(k)
                    if v and str(v) not in seen:
                        seen.add(str(v))
                        values.append(str(v))
                        break
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

        _refresh(self._rep_combo, "All Reps", ("rep", "sales_rep"))
        _refresh(self._source_combo, "All Sources", ("source",))

    # ------------------------------------------------------------------
    # Per-row helpers
    # ------------------------------------------------------------------
    def _customer_cell(self, q: dict) -> QWidget:
        """Two-line customer cell: bold name + dim sub (city · terms)."""
        w = QFrame()
        w.setStyleSheet("QFrame { background: transparent; border: none; }")
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 4, 10, 4)
        v.setSpacing(1)

        name = q.get("customer_name") or "—"
        company = q.get("customer_company")
        primary = f"{name}" + (f"  ·  {company}" if company else "")
        nl = QLabel(primary)
        nl.setStyleSheet(f"QLabel {{ color: {_TEXT}; font-size: 12px; font-weight: 600; background: transparent; border: none; }}")
        v.addWidget(nl)

        # Sub-line: prefer city/state if available; fall back to terms / phone.
        bits = []
        for key in ("customer_city", "city"):
            if q.get(key):
                bits.append(str(q[key]))
                break
        for key in ("customer_state", "state"):
            if q.get(key):
                if bits:
                    bits[-1] = f"{bits[-1]}, {q[key]}"
                else:
                    bits.append(str(q[key]))
                break
        for key in ("terms", "customer_terms", "payment_terms"):
            if q.get(key):
                bits.append(str(q[key]))
                break
        sub_text = "  ·  ".join(bits) if bits else ""
        sl = QLabel(sub_text)
        sl.setStyleSheet(f"QLabel {{ color: {_TEXT_DIM}; font-size: 10px; background: transparent; border: none; }}")
        v.addWidget(sl)
        return w

    def _engine_label(self, q: dict) -> str:
        """Pull engine / platform info from whichever fields the DB exposes."""
        for key in ("engine_family", "engine_model", "engine", "vehicle_make",
                    "platform", "esn_family"):
            val = q.get(key)
            if val:
                return str(val)
        esn = q.get("esn") or q.get("engine_serial")
        if esn:
            return f"ESN {esn}"
        return "—"

    def _activity_cell(self, q: dict) -> QWidget:
        """Inline strip of small colored badges summarizing activity.

        Each flag in the quote renders as an 18x18 rounded square badge
        with a single glyph; dim/empty slots are still drawn so the row
        keeps a consistent footprint (matches the HTML mockup).

        TODO: replace these heuristics with real per-quote activity flags
        from the comments / messages tables.
        """
        w = QFrame()
        w.setStyleSheet("QFrame { background: transparent; border: none; }")
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(3)

        glyphs = [
            ("sms_sent",         "\U0001F4AC", _BLUE,     "SMS sent"),
            ("email_sent",       "\u2709",     _BLUE,     "Email sent"),
            ("viewed_by_cust",   "\U0001F441", _GREEN,    "Customer viewed"),
            ("customer_replied", "\u21A9",     _GREEN,    "Customer replied"),
            ("has_attachments",  "\U0001F4CE", _TEXT_DIM, "Attachments"),
            ("core_pending",     "\u267B",     _PURPLE,   "Core pending"),
            ("freight_pending",  "\U0001F69A", _BLUE,     "Freight pending"),
            ("parts_unavail",    "\u26A0",     _RED,      "Parts unavailable"),
        ]
        for key, glyph, color, tip in glyphs:
            active = bool(q.get(key))
            badge = QLabel(glyph)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(18, 18)
            badge.setToolTip(tip)
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
    # Data
    # ------------------------------------------------------------------
    def _load_quotes(self) -> None:
        # Remember current selection so it can be restored after reload
        prev_qid = self._get_selected_id()

        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)

        status_filter = _TAB_STATUS_MAP.get(self._active_status)
        if status_filter == "__unfinished__":
            quotes = get_all_quotes(status=None, limit=500)
            quotes = [q for q in quotes
                      if (q.get("status") or "draft") == "draft"
                      and (q.get("line_count") or 0) == 0]
        else:
            quotes = get_all_quotes(status=status_filter, limit=300)

        # Date window first (applies to dropdown population too).
        days = self._date_combo.currentData() if hasattr(self, "_date_combo") else None
        if days:
            cutoff = date.today() - timedelta(days=int(days))
            def _in_window(q):
                v = q.get("created_at") or q.get("created") or q.get("date")
                if not v:
                    return True
                try:
                    d = datetime.fromisoformat(str(v)[:19]).date()
                except Exception:
                    return True
                return d >= cutoff
            quotes = [q for q in quotes if _in_window(q)]

        # Refresh the Rep / Source dropdown choices from the (status+date)
        # filtered set BEFORE applying the rep/source picks themselves —
        # otherwise picking one rep would hide all other reps.
        self._sync_filter_choices(quotes)

        # Rep / Source dropdown filters
        rep_pick = self._rep_combo.currentData() if hasattr(self, "_rep_combo") else None
        if rep_pick:
            quotes = [q for q in quotes if (q.get("rep") or q.get("sales_rep") or "") == rep_pick]
        src_pick = self._source_combo.currentData() if hasattr(self, "_source_combo") else None
        if src_pick:
            quotes = [q for q in quotes if (q.get("source") or "") == src_pick]

        # Client-side search filter
        search_text = self._search_edit.text().strip().lower()
        if search_text:
            quotes = [q for q in quotes
                      if search_text in (q.get("quote_number") or "").lower()
                      or search_text in (q.get("customer_name") or "").lower()
                      or search_text in (q.get("customer_company") or "").lower()
                      or search_text in str(q.get("total") or "")
                      or search_text in (q.get("esn") or "").lower()]

        self._table.setRowCount(len(quotes))
        total_value = 0.0
        today = date.today()

        def item(text: str, *, align=None, color=None) -> QTableWidgetItem:
            it = QTableWidgetItem("" if text is None else str(text))
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if align is not None:
                it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            if color is not None:
                it.setForeground(QColor(color))
            return it

        for row_idx, q in enumerate(quotes):
            status = q.get("status", "draft")

            # 0  ID (hidden)
            self._table.setItem(row_idx, 0, item(q.get("id")))

            # 1  Quote #
            qn = item(q.get("quote_number"), align=Qt.AlignmentFlag.AlignLeft, color=_ACCENT)
            f = qn.font()
            f.setBold(True)
            qn.setFont(f)
            self._table.setItem(row_idx, 1, qn)

            # 2  Customer — display via cell widget; underlying item text
            # is blank so it doesn't bleed through behind the widget. The
            # name is stored in UserRole for sort/search.
            ci = item("")
            ci.setData(Qt.ItemDataRole.UserRole, q.get("customer_name") or "")
            self._table.setItem(row_idx, 2, ci)
            self._table.setCellWidget(row_idx, 2, self._customer_cell(q))

            # 3  Engine / Platform
            self._table.setItem(row_idx, 3,
                item(self._engine_label(q), color=_TEXT_DIM))

            # 4  Total
            total = float(q.get("total") or 0) or float(q.get("calc_total") or 0)
            total_value += total
            self._table.setItem(row_idx, 4,
                item(f"${total:,.2f}", align=Qt.AlignmentFlag.AlignRight))

            # 5  Margin — pull from DB if available, otherwise '—'
            margin_pct = q.get("margin_pct")
            if margin_pct is None:
                rev = float(q.get("total") or q.get("calc_total") or 0)
                cost = float(q.get("cost_total") or q.get("calc_cost") or 0)
                if rev > 0 and cost > 0:
                    margin_pct = (rev - cost) / rev * 100.0
            if margin_pct is None:
                m_item = item("—", align=Qt.AlignmentFlag.AlignRight, color=_TEXT_FAINT)
            else:
                mp = float(margin_pct)
                if mp >= 30:
                    m_color = _GREEN
                elif mp >= 15:
                    m_color = _YELLOW
                else:
                    m_color = _RED
                m_item = item(f"{mp:.0f}%", align=Qt.AlignmentFlag.AlignRight, color=m_color)
                ff = m_item.font(); ff.setBold(True); m_item.setFont(ff)
            self._table.setItem(row_idx, 5, m_item)

            # 6  Status badge
            self._table.setItem(row_idx, 6, _StatusBadgeItem(status))

            # 7  Age (days open)
            created = str(q.get("created_at", ""))[:10] if q.get("created_at") else ""
            if created and status not in ("invoiced", "void", "lost"):
                try:
                    days = (today - date.fromisoformat(created)).days
                    if days >= 7:
                        c = _RED
                    elif days >= 3:
                        c = _YELLOW
                    else:
                        c = _TEXT
                    self._table.setItem(row_idx, 7,
                        item(f"{days}d", align=Qt.AlignmentFlag.AlignCenter, color=c))
                except ValueError:
                    self._table.setItem(row_idx, 7, item("", align=Qt.AlignmentFlag.AlignCenter))
            else:
                self._table.setItem(row_idx, 7,
                    item("—", align=Qt.AlignmentFlag.AlignCenter, color=_TEXT_FAINT))

            # 8  Follow-up
            fu_date = str(q.get("follow_up_date", ""))[:10] if q.get("follow_up_date") else ""
            if fu_date:
                try:
                    fu = date.fromisoformat(fu_date)
                    if fu < today:
                        fu_color, fu_text = _RED, f"⚠ {fu_date}"
                    elif fu == today:
                        fu_color, fu_text = _YELLOW, f"today"
                    else:
                        fu_color, fu_text = _TEXT, fu_date
                except ValueError:
                    fu_color, fu_text = _TEXT_DIM, fu_date
                self._table.setItem(row_idx, 8,
                    item(fu_text, align=Qt.AlignmentFlag.AlignCenter, color=fu_color))
            else:
                self._table.setItem(row_idx, 8,
                    item("—", align=Qt.AlignmentFlag.AlignCenter, color=_TEXT_FAINT))

            # 9  Rep
            rep = (q.get("rep") or q.get("assigned_to") or q.get("created_by") or "—")
            self._table.setItem(row_idx, 9,
                item(str(rep), align=Qt.AlignmentFlag.AlignCenter, color=_TEXT_DIM))

            # 10 Source
            src = (q.get("source") or q.get("lead_source") or "—")
            self._table.setItem(row_idx, 10,
                item(str(src), align=Qt.AlignmentFlag.AlignCenter, color=_TEXT_DIM))

            # 11 Activity icons (cell widget)
            self._table.setItem(row_idx, 11, item(""))
            self._table.setCellWidget(row_idx, 11, self._activity_cell(q))

            # Fade terminal-state rows.
            if status in ("expired", "void", "lost"):
                faint = QColor(_TEXT_FAINT)
                for col in range(self._table.columnCount()):
                    cell = self._table.item(row_idx, col)
                    if cell is not None and not isinstance(cell, _StatusBadgeItem):
                        cell.setForeground(faint)

        self._table.setSortingEnabled(True)
        self._table.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        # Keep stretch + fixed columns honored after sorting
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        # Refresh KPI / attention counts off the loaded set.
        self._refresh_kpis(quotes)
        self._refresh_attention(quotes)

        self._summary_label.setText(
            f"{len(quotes)} quotes  ·  ${total_value:,.2f} pipeline value"
        )

        # Restore previous selection if still visible
        if prev_qid is not None:
            for r in range(self._table.rowCount()):
                it = self._table.item(r, 0)
                if it and str(it.text()) == str(prev_qid):
                    self._table.selectRow(r)
                    break

    # ------------------------------------------------------------------
    # KPI / Attention computation
    # ------------------------------------------------------------------
    def _refresh_kpis(self, quotes: list[dict]) -> None:
        """Populate the KPI cards from the currently-loaded quote set.

        TODO: replace with proper aggregate queries (db.kpis.quotes_*) so
        these reflect *all* quotes, not just the filtered tab/search set.
        """
        today = date.today()
        first_of_month = today.replace(day=1)

        open_count = 0
        awaiting = 0
        won_today = 0
        lost_today = 0
        revenue_mtd = 0.0
        won_total = 0
        decided_total = 0
        margin_samples: list[float] = []

        for q in quotes:
            status = (q.get("status") or "").lower()
            total = float(q.get("total") or q.get("calc_total") or 0)
            updated = str(q.get("updated_at") or "")[:10]
            created = str(q.get("created_at") or "")[:10]

            if status in ("draft", "sent", "needs_follow_up"):
                open_count += 1
            if status == "needs_follow_up":
                awaiting += 1

            if status in ("accepted", "invoiced"):
                won_total += 1
                decided_total += 1
                if updated == str(today):
                    won_today += 1
            elif status == "lost":
                decided_total += 1
                if updated == str(today):
                    lost_today += 1

            if status == "invoiced" and created >= str(first_of_month):
                revenue_mtd += total

            rev = float(q.get("total") or q.get("calc_total") or 0)
            cost = float(q.get("cost_total") or q.get("calc_cost") or 0)
            if rev > 0 and cost > 0:
                margin_samples.append((rev - cost) / rev * 100.0)

        conv_rate = (won_total / decided_total * 100.0) if decided_total else None
        avg_margin = (sum(margin_samples) / len(margin_samples)) if margin_samples else None

        def _set(key: str, value: str, sub: str = "") -> None:
            if key not in self._kpi_cards:
                return
            val_lbl, sub_lbl, _frame = self._kpi_cards[key]
            val_lbl.setText(value)
            sub_lbl.setText(sub)

        _set("open",        str(open_count),                    "in pipeline")
        _set("follow_up",   str(awaiting),                      "need callback")
        _set("won_today",   str(won_today),                     "accepted today")
        _set("lost_today",  str(lost_today),                    "lost today")
        _set("revenue_mtd", f"${revenue_mtd:,.0f}",             "invoiced MTD")
        _set("conv_rate",   "—" if conv_rate is None else f"{conv_rate:.0f}%", "won / decided")
        _set("avg_margin",  "—" if avg_margin is None else f"{avg_margin:.0f}%",
             f"{len(margin_samples)} quotes")

    def _refresh_attention(self, quotes: list[dict]) -> None:
        """Update the chip counts. TODO: derive from real flags."""
        today = date.today()
        counts = {k: 0 for k in self._attention_chips}
        for q in quotes:
            status = (q.get("status") or "").lower()
            if status in ("void", "lost", "invoiced", "expired"):
                continue

            # Aged > 3 days with no terminal status
            created = str(q.get("created_at") or "")[:10]
            if created:
                try:
                    if (today - date.fromisoformat(created)).days >= 3:
                        counts["aged"] += 1
                except ValueError:
                    pass

            if not q.get("follow_up_date"):
                counts["no_followup"] += 1

            total = float(q.get("total") or q.get("calc_total") or 0)
            if total >= 10_000:
                counts["large"] += 1

            if q.get("customer_replied"):
                counts["replied"] += 1
            if q.get("parts_unavail"):
                counts["parts_na"] += 1
            if q.get("core_pending"):
                counts["core_info"] += 1
            if q.get("freight_pending"):
                counts["freight"] += 1

        for k, n in counts.items():
            self._update_chip(k, n)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------
    def _on_selection_changed(self) -> None:
        qid = self._get_selected_id()
        has = qid is not None
        self._view_btn.setEnabled(has)
        self._follow_up_btn.setEnabled(has)

        if has:
            q = get_quote(qid)
            status = (q.get("status") or "").lower() if q else ""
            self._convert_btn.setEnabled(status in ("draft", "sent", "accepted"))
            self._to_so_btn.setEnabled(status in ("accepted", "sent"))
            self._void_btn.setEnabled(status not in ("invoiced", "void"))
        else:
            self._convert_btn.setEnabled(False)
            self._to_so_btn.setEnabled(False)
            self._void_btn.setEnabled(False)

    def _get_selected_id(self) -> int | None:
        selected = self._table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        id_item = self._table.item(row, 0)
        return int(id_item.text()) if id_item else None

    # ------------------------------------------------------------------
    # Right-click context menu
    # ------------------------------------------------------------------
    def _show_context_menu(self, pos) -> None:
        item = self._table.itemAt(pos)
        if not item:
            return
        row = item.row()
        id_item = self._table.item(row, 0)
        if not id_item:
            return
        qid = int(id_item.text())
        q = get_quote(qid)
        if not q:
            return
        status = (q.get("status") or "draft").lower()
        is_active = status not in ("invoiced", "void", "accepted", "lost")
        is_convertible = status not in ("invoiced", "void", "lost")

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: white; border: 1px solid #ccc; padding: 4px; font-size: 10pt; }"
            "QMenu::item { padding: 7px 28px; }"
            "QMenu::item:selected { background: #e3f2fd; color: #0d47a1; }"
            "QMenu::separator { height: 1px; background: #e0e0e0; margin: 3px 8px; }"
        )

        # View / Edit
        view_action = menu.addAction("📝 View / Edit")
        print_action = menu.addAction("🖨️ Print Quote")
        duplicate_action = menu.addAction("📑 Duplicate Quote")
        menu.addSeparator()

        # Conversions
        convert_so_action = menu.addAction("📋 → Convert to Sales Order")
        convert_inv_action = menu.addAction("📄 → Convert to Invoice")
        menu.addSeparator()

        # Status management
        follow_up_action = menu.addAction("📅 Set Follow-Up Date")
        lost_action = menu.addAction("✗ Mark as Lost")
        menu.addSeparator()

        # Admin
        priority_action = menu.addAction("🔺 Change Priority")
        void_action = menu.addAction("🚫 Void Quote")
        delete_action = menu.addAction("❌ Delete Quote")

        # Enable/disable based on current status
        convert_so_action.setEnabled(is_convertible)
        convert_inv_action.setEnabled(is_convertible)
        follow_up_action.setEnabled(is_active or status == "sent")
        lost_action.setEnabled(status not in ("invoiced", "void", "accepted", "lost"))
        void_action.setEnabled(status not in ("invoiced", "void"))
        delete_action.setEnabled(status == "draft")

        # Grey out already-lost
        if status == "lost":
            lost_action.setText("✎ Edit Lost Reason")
            lost_action.setEnabled(True)

        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if not action:
            return

        if action == view_action:
            dlg = QuoteDialog(quote_id=qid, parent=self)
            if dlg.exec():
                self._load_quotes()
            self._maybe_open_created_so(dlg)
        elif action == print_action:
            self._print_quote(qid)
        elif action == duplicate_action:
            self._ctx_duplicate_quote(qid)
        elif action == convert_so_action:
            self._ctx_convert_to_so(qid)
        elif action == convert_inv_action:
            self._ctx_convert_to_invoice(qid)
        elif action == follow_up_action:
            self._set_follow_up_date(qid, q)
        elif action == lost_action:
            self._mark_lost(qid, q)
        elif action == priority_action:
            self._change_priority(qid)
        elif action == void_action:
            self._void_quote(qid)
        elif action == delete_action:
            self._delete_quote(qid)

    def _print_quote(self, qid: int) -> None:
        q = get_quote(qid)
        if not q:
            return
        try:
            import os, time
            from jaks_inventory.utils.quote_pdf import generate_quote_pdf
            pdf_path = generate_quote_pdf(q)
            time.sleep(0.3)
            try:
                os.startfile(str(pdf_path), "print")
            except AttributeError:
                import subprocess
                subprocess.Popen(["xdg-open", str(pdf_path)])
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not print:\n{e}")

    def _set_follow_up_date(self, qid: int, q: dict) -> None:
        """Open the unified close-out dialog (defaults to 'Still pending')."""
        self._open_close_out_dialog(qid, q)

    def _mark_lost(self, qid: int, q: dict) -> None:
        """Open the unified close-out dialog — user picks 'Lost' to capture reason."""
        self._open_close_out_dialog(qid, q)

    def _ctx_convert_to_invoice(self, qid: int) -> None:
        q = get_quote(qid)
        if not q or not q.get("customer_id"):
            QMessageBox.warning(self, "No Customer", "Assign a customer before converting.")
            return
        selected_lines = [l for l in q.get("lines", []) if l.get("is_selected")]
        if not selected_lines:
            QMessageBox.warning(self, "No Lines", "No selected lines to invoice.")
            return
        reply = QMessageBox.question(
            self, "Convert to Invoice",
            f"Convert quote {q['quote_number']} to an invoice with {len(selected_lines)} line(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                result = convert_quote_to_invoice(qid)
                QMessageBox.information(self, "Invoice Created",
                    f"Invoice #{result.get('invoice_number', '')} created.")
                self._load_quotes()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _ctx_duplicate_quote(self, qid: int) -> None:
        ans = QMessageBox.question(
            self, "Duplicate Quote",
            "Create a new draft quote from this one?",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            from db.inventory import duplicate_quote
            res = duplicate_quote(int(qid))
        except Exception as exc:
            QMessageBox.critical(self, "Duplicate Failed", str(exc))
            return
        if not res.get("success"):
            QMessageBox.critical(
                self, "Duplicate Failed",
                str(res.get("error") or "Unknown error"),
            )
            return
        new_id = res["quote_id"]
        try:
            self._load_quotes()
        except Exception:
            pass
        dlg = QuoteDialog(quote_id=int(new_id), parent=self)
        if dlg.exec():
            self._load_quotes()
        self._maybe_open_created_so(dlg)

    def _ctx_convert_to_so(self, qid: int) -> None:
        q = get_quote(qid)
        if not q or not q.get("customer_id"):
            QMessageBox.warning(self, "No Customer", "Assign a customer before converting.")
            return
        selected_lines = [l for l in q.get("lines", []) if l.get("is_selected")]
        if not selected_lines:
            QMessageBox.warning(self, "No Lines", "No selected lines to convert.")
            return
        display_names = ["🔥 High", "Medium", "Low", "⏳ Waiting on Customer", "⏳ Waiting on Vendor"]
        priority, ok = QInputDialog.getItem(
            self, "Sales Order Priority", "Priority:", display_names, 0, False)
        if not ok:
            return
        idx = display_names.index(priority)
        db_priority = _PRIORITY_OPTIONS[idx]
        try:
            result = convert_quote_to_so(qid, priority=db_priority)
            if not result.get("success") and result.get("overridable"):
                credit = result.get("credit_check") or {}
                detail = (
                    f"{result.get('error', 'Credit limit exceeded')}\n\n"
                    f"Outstanding: ${credit.get('outstanding_balance', 0):,.2f}\n"
                    f"Credit limit: ${credit.get('eff_credit_limit', 0):,.2f}\n\n"
                    "Override and create the sales order anyway?"
                )
                if QMessageBox.question(
                    self, "Credit Limit Exceeded", detail,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                ) == QMessageBox.StandardButton.Yes:
                    result = convert_quote_to_so(
                        qid, priority=db_priority, allow_credit_override=True,
                    )
            if result.get("success"):
                QMessageBox.information(self, "Sales Order Created",
                    f"SO {result['so_number']} created.")
                self._load_quotes()
                so_id = (
                    result.get("so_id")
                    or result.get("sales_order_id")
                    or result.get("id")
                )
                if so_id:
                    try:
                        from .so_detail_dialog import open_so_detail
                        open_so_detail(
                            int(so_id), parent=self, on_closed=self._load_quotes,
                        )
                    except Exception as exc:
                        QMessageBox.warning(
                            self, "Open Sales Order",
                            f"Sales order created but could not be opened:\n{exc}",
                        )
            else:
                QMessageBox.warning(self, "Error", result.get("error", "Failed"))
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _change_priority(self, qid: int) -> None:
        display_names = ["🔥 High", "Medium", "Low", "⏳ Waiting on Customer", "⏳ Waiting on Vendor"]
        priority, ok = QInputDialog.getItem(
            self, "Change Priority", "New priority:", display_names, 0, False)
        if ok:
            idx = display_names.index(priority)
            db_priority = _PRIORITY_OPTIONS[idx]
            update_quote(qid, priority=db_priority)
            self._load_quotes()

    def _void_quote(self, qid: int) -> None:
        q = get_quote(qid)
        if not q:
            return
        reply = QMessageBox.question(
            self, "Void Quote",
            f"Void quote {q.get('quote_number', '')}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            update_quote(qid, status="void")
            self._load_quotes()

    def _delete_quote(self, qid: int) -> None:
        q = get_quote(qid)
        if not q:
            return
        reply = QMessageBox.question(
            self, "Delete Quote",
            f"Permanently delete draft quote {q.get('quote_number', '')}?\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            from db.database import get_db
            with get_db() as conn:
                conn.execute("DELETE FROM quote_comments WHERE quote_id = ?", (qid,))
                conn.execute("DELETE FROM quote_lines WHERE quote_id = ?", (qid,))
                conn.execute("DELETE FROM quotes WHERE id = ?", (qid,))
            self._load_quotes()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _on_new(self) -> None:
        open_new = True
        while open_new:
            open_new = False
            dlg = QuoteDialog(parent=self)
            dlg.quote_saved_new.connect(lambda: None)  # keep signal wired
            if dlg.exec():
                self._load_quotes()
            self._maybe_open_created_so(dlg)
            if dlg._save_and_new:
                open_new = True

    def _on_view(self) -> None:
        qid = self._get_selected_id()
        if not qid:
            return
        dlg = QuoteDialog(quote_id=qid, parent=self)
        if dlg.exec():
            self._load_quotes()
        self._maybe_open_created_so(dlg)

    def _maybe_open_created_so(self, dlg) -> None:
        """If the quote dialog converted to an SO, open it now."""
        # Open a duplicated quote first if requested.
        dup_id = getattr(dlg, "_duplicated_to_quote_id", None)
        if dup_id:
            try:
                new_dlg = QuoteDialog(quote_id=int(dup_id), parent=self)
                if new_dlg.exec():
                    self._load_quotes()
                # Recurse in case the new dialog also converted/duplicated.
                self._maybe_open_created_so(new_dlg)
            except Exception:
                pass
        so_id = getattr(dlg, "_created_so_id", None)
        if not so_id:
            return
        try:
            from .so_detail_dialog import open_so_detail
            open_so_detail(int(so_id), parent=self, on_closed=self._load_quotes)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "Open Sales Order",
                f"Sales order created but could not be opened:\n{exc}",
            )
        # Refresh in case the SO change updated quote status display.
        try:
            self._load_quotes()
        except Exception:
            pass

    def _on_convert(self) -> None:
        qid = self._get_selected_id()
        if not qid:
            return
        q = get_quote(qid)
        if not q:
            return

        if not q.get("customer_id"):
            QMessageBox.warning(self, "No Customer", "Assign a customer before converting.")
            return

        selected_lines = [l for l in q.get("lines", []) if l.get("is_selected")]
        if not selected_lines:
            QMessageBox.warning(self, "No Lines", "No selected lines to invoice.")
            return

        reply = QMessageBox.question(
            self, "Convert to Invoice",
            f"Convert quote {q['quote_number']} to an invoice with {len(selected_lines)} line(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            result = convert_quote_to_invoice(qid)
            QMessageBox.information(
                self, "Invoice Created",
                f"Invoice #{result.get('invoice_number', '')} created from quote {q['quote_number']}."
            )
            self._load_quotes()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Conversion failed:\n{e}")

    def _on_convert_to_so(self) -> None:
        qid = self._get_selected_id()
        if not qid:
            return
        q = get_quote(qid)
        if not q:
            return

        if not q.get("customer_id"):
            QMessageBox.warning(self, "No Customer", "Assign a customer before converting.")
            return

        selected_lines = [l for l in q.get("lines", []) if l.get("is_selected")]
        if not selected_lines:
            QMessageBox.warning(self, "No Lines", "No selected lines to convert.")
            return

        from PySide6.QtWidgets import QInputDialog
        priorities = ["normal", "rush", "will-call"]
        priority, ok = QInputDialog.getItem(
            self, "Sales Order Priority",
            f"Convert quote {q['quote_number']} to a sales order with "
            f"{len(selected_lines)} line(s).\n\nPriority:",
            priorities, 0, False,
        )
        if not ok:
            return

        try:
            result = convert_quote_to_so(qid, priority=priority)
            if not result.get("success") and result.get("overridable"):
                credit = result.get("credit_check") or {}
                detail = (
                    f"{result.get('error', 'Credit limit exceeded')}\n\n"
                    f"Outstanding: ${credit.get('outstanding_balance', 0):,.2f}\n"
                    f"Credit limit: ${credit.get('eff_credit_limit', 0):,.2f}\n\n"
                    "Override and create the sales order anyway?"
                )
                if QMessageBox.question(
                    self, "Credit Limit Exceeded", detail,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                ) == QMessageBox.StandardButton.Yes:
                    result = convert_quote_to_so(
                        qid, priority=priority, allow_credit_override=True,
                    )
            if not result.get("success"):
                QMessageBox.warning(self, "Error", result.get("error", "Conversion failed"))
                return
            msg = f"Sales Order {result['so_number']} created from quote {q['quote_number']}."
            if priority == "rush":
                msg += "\n\nRUSH — review purchasing now."
            QMessageBox.information(self, "Sales Order Created", msg)
            self._load_quotes()
            so_id = result.get("so_id") or result.get("sales_order_id") or result.get("id")
            if so_id:
                try:
                    from .so_detail_dialog import open_so_detail
                    open_so_detail(int(so_id), parent=self, on_closed=self._load_quotes)
                except Exception as exc:
                    QMessageBox.warning(
                        self, "Open Sales Order",
                        f"Sales order created but could not be opened:\n{exc}",
                    )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Conversion failed:\n{e}")

    def _on_void(self) -> None:
        qid = self._get_selected_id()
        if not qid:
            return
        q = get_quote(qid)
        if not q:
            return

        reply = QMessageBox.question(
            self, "Void Quote",
            f"Void quote {q.get('quote_number', '')}? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        update_quote(qid, status="void")
        self._load_quotes()

    def _on_follow_up(self) -> None:
        """Open the unified close-out dialog for the selected quote."""
        qid = self._get_selected_id()
        if not qid:
            return
        q = get_quote(qid)
        if not q:
            return
        self._open_close_out_dialog(qid, q)

    def _open_close_out_dialog(self, qid: int, q: dict) -> None:
        """Present the outcome dialog and apply the chosen outcome."""
        dlg = _QuoteCloseOutDialog(q, parent=self)
        if not dlg.exec():
            return

        if dlg.outcome == "pending":
            # Still open — keep the quote alive and schedule the next touchpoint.
            new_status = q.get("status") or "draft"
            if new_status == "draft":
                new_status = "sent"  # sending it out counts as the first follow-up
            update_quote(
                qid,
                status=new_status,
                follow_up_date=dlg.follow_up_date,
                next_action=dlg.next_action,
            )
            QMessageBox.information(
                self, "Follow-Up Scheduled",
                f"Follow-up set for {dlg.follow_up_date}.",
            )
        elif dlg.outcome == "won":
            update_quote(qid, status="accepted")
            QMessageBox.information(
                self, "Quote Won",
                f"Quote {q.get('quote_number', '')} marked Accepted.\n"
                "Use → Sales Order to convert it when you're ready.",
            )
        elif dlg.outcome == "lost":
            update_quote(
                qid,
                status="lost",
                lost_reason=dlg.lost_reason,
                lost_notes=dlg.lost_notes,
            )
        elif dlg.outcome == "void":
            update_quote(qid, status="void")

        self._load_quotes()
