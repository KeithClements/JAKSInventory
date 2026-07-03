"""Customer maintenance dialog — dark-themed redesign (May 2026).

Implements the layout from ``docs/mockups/customer_detail.html`` 1:1:

  ┌─ HEADER ─────────────────────────────────────────────────────────┐
  │ C-#### · Company       [ACTIVE] [TIER] [BAL]   chips  buttons   │
  ├──────────┬──────────────────────────────────────────────────────┤
  │ SIDEBAR  │ TABS:  Overview | Contacts | Addresses | Orders     │
  │ • KPIs   │        Invoices | Cores | Notes | Files              │
  │ • Account│                                                       │
  │ • Primary│ Overview = Company info + Billing/terms + Contacts   │
  │ • Quick  │           + Addresses table + Recent activity        │
  │   Actions│           + Internal notes                            │
  ├──────────┴──────────────────────────────────────────────────────┤
  │ FOOTER: audit meta              Deactivate Duplicate Cancel Save │
  └──────────────────────────────────────────────────────────────────┘

Public API preserved from previous version:
* ``CustomerDetailDialog(customer_id: int | None = None, parent=None)``
* Emits ``customer_saved(int)`` after a successful save
* Listens to ``core_events.cores_changed`` for cross-screen refresh
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import db

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Palette tokens — pulled from ui.theme.DARK so colours stay in sync.
# ---------------------------------------------------------------------------
try:
    from .theme import DARK as _T
except Exception:  # pragma: no cover - defensive
    _T = {
        "bg": "#1a1f24", "panel": "#222a30", "panel_alt": "#1f262c",
        "border": "#2f3942", "border_soft": "#262d34",
        "text": "#e6e9eb", "text_dim": "#9aa3ad", "text_muted": "#6b747e",
        "accent": "#7c8f4d", "accent_hover": "#8fa55a", "accent_text": "#ffffff",
        "success": "#5ed26a", "warning": "#f0b454", "danger": "#e06464",
        "selection": "#3a4a32", "row_alt": "#1d242a", "header_bg": "#161b20",
        "input_bg": "#161b20",
    }


def _money(v: Any) -> str:
    try:
        return f"${float(v or 0):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _money_short(v: Any) -> str:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return "$0"
    if abs(f) >= 1_000_000:
        return f"${f/1_000_000:.1f}M"
    if abs(f) >= 1_000:
        return f"${f/1_000:.1f}k"
    return f"${f:,.0f}"


def _row_to_dict(r: Any) -> dict:
    if r is None:
        return {}
    if isinstance(r, dict):
        return r
    if hasattr(r, "keys"):
        return {k: r[k] for k in r.keys()}
    try:
        return dict(r)
    except Exception:
        return {}


# ===========================================================================
# Small reusable widgets
# ===========================================================================
class _Pill(QLabel):
    """Rounded status pill — variant controls colour."""
    def __init__(self, text: str, variant: str = "neutral", parent=None) -> None:
        super().__init__(text, parent)
        self.setProperty("pill_variant", variant)
        self._apply(variant)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_text_and_variant(self, text: str, variant: str) -> None:
        self.setText(text)
        self._apply(variant)

    def _apply(self, variant: str) -> None:
        v = (variant or "neutral").lower()
        bg = _T["panel"]; fg = _T["text_dim"]; border = _T["border"]
        if v == "active":
            bg, fg, border = _T["accent"], _T["accent_text"], _T["accent"]
        elif v == "good":
            bg, fg, border = "rgba(94,210,106,0.15)", _T["success"], "rgba(94,210,106,0.4)"
        elif v == "warn":
            bg, fg, border = "rgba(240,180,84,0.18)", _T["warning"], "rgba(240,180,84,0.4)"
        elif v == "due" or v == "bad":
            bg, fg, border = "rgba(224,100,100,0.15)", _T["danger"], "rgba(224,100,100,0.4)"
        elif v == "tier":
            bg, fg, border = _T["panel"], _T["text_dim"], _T["border"]
        self.setStyleSheet(
            f"QLabel {{ background:{bg}; color:{fg}; border:1px solid {border};"
            f" border-radius:10px; padding:2px 9px; font-size:10pt; font-weight:600;"
            f" letter-spacing:0.3px; }}"
        )


class _Chip(QLabel):
    """Small keyboard-shortcut hint chip."""
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setStyleSheet(
            f"QLabel {{ background:{_T['panel_alt']}; color:{_T['text_muted']};"
            f" border:1px solid {_T['border']}; border-radius:4px;"
            f" padding:2px 6px; font-size:9pt; }}"
        )


class _SidebarCard(QFrame):
    """Card container used in the left sidebar."""
    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebarCard")
        self.setStyleSheet(
            f"#sidebarCard {{ background:{_T['panel']}; border:1px solid {_T['border']};"
            f" border-radius:6px; }}"
        )
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(12, 10, 12, 12)
        self._lay.setSpacing(8)
        if title:
            h = QLabel(title.upper())
            h.setStyleSheet(
                f"color:{_T['text_dim']}; font-size:9pt; font-weight:700;"
                f" letter-spacing:0.8px; background:transparent; border:none;"
            )
            self._lay.addWidget(h)

    def body(self) -> QVBoxLayout:
        return self._lay


class _KpiBox(QFrame):
    """Small KPI tile for the sidebar's at-a-glance grid."""
    def __init__(self, value: str, label: str, parent=None) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background:{_T['panel_alt']}; border:1px solid {_T['border_soft']};"
            f" border-radius:5px; }}"
            f"QLabel {{ background:transparent; border:none; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 8)
        lay.setSpacing(2)
        n = QLabel(value)
        n.setAlignment(Qt.AlignmentFlag.AlignCenter)
        n.setStyleSheet(f"color:{_T['text']}; font-size:14pt; font-weight:700;")
        l = QLabel(label.upper())
        l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l.setStyleSheet(
            f"color:{_T['text_muted']}; font-size:8pt; letter-spacing:0.5px;"
        )
        lay.addWidget(n)
        lay.addWidget(l)


class _ContactCard(QFrame):
    """Contact row used in the Overview tab's primary-contacts section."""
    edit_requested = Signal(int)
    delete_requested = Signal(int)

    def __init__(self, emp: dict, is_primary: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._emp_id = int(emp.get("id") or 0)
        self.setStyleSheet(
            f"QFrame {{ background:{_T['panel_alt']}; border:1px solid {_T['border_soft']};"
            f" border-radius:5px; }}"
            f"QLabel {{ background:transparent; border:none; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)

        info = QVBoxLayout()
        info.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        name = QLabel(emp.get("name") or "(no name)")
        name.setStyleSheet(f"color:{_T['text']}; font-weight:600;")
        title_row.addWidget(name)
        if is_primary:
            flag = QLabel("PRIMARY")
            flag.setStyleSheet(
                f"background:{_T['accent']}; color:#fff; font-size:8pt;"
                f" padding:1px 6px; border-radius:8px; font-weight:700;"
            )
            title_row.addWidget(flag)
        title_row.addStretch(1)
        info.addLayout(title_row)

        role = (emp.get("title") or "").strip()
        if role:
            r = QLabel(role)
            r.setStyleSheet(f"color:{_T['text_muted']}; font-size:9pt;")
            info.addWidget(r)

        phone = (emp.get("phone") or "").strip()
        email = (emp.get("email") or "").strip()
        if phone:
            p = QLabel(f"📞  {phone}")
            p.setStyleSheet(f"color:{_T['text_dim']}; font-size:10pt;")
            info.addWidget(p)
        if email:
            e = QLabel(f"✉️  {email}")
            e.setStyleSheet(f"color:{_T['text_dim']}; font-size:10pt;")
            info.addWidget(e)

        lay.addLayout(info, 1)

        actions = QVBoxLayout()
        actions.setSpacing(4)
        for label, slot in (("Edit", self._emit_edit), ("Remove", self._emit_delete)):
            b = QPushButton(label)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(22)
            b.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{_T['text_dim']};"
                f" border:1px solid {_T['border']}; border-radius:3px;"
                f" padding:2px 8px; font-size:9pt; }}"
                f"QPushButton:hover {{ color:{_T['text']};"
                f" border-color:{_T['accent']}; }}"
            )
            b.clicked.connect(slot)
            actions.addWidget(b)
        actions.addStretch(1)
        lay.addLayout(actions)

    def _emit_edit(self) -> None:
        self.edit_requested.emit(self._emp_id)

    def _emit_delete(self) -> None:
        self.delete_requested.emit(self._emp_id)


# ===========================================================================
# Main dialog
# ===========================================================================
class CustomerDetailDialog(QDialog):
    """Dark-themed customer maintenance dialog."""

    customer_saved = Signal(int)

    def __init__(self, customer_id: int | None = None, parent=None) -> None:
        super().__init__(parent)
        self.customer_id = int(customer_id) if customer_id else None
        self._customer: dict = {}
        self._employees: list[dict] = []
        self._dirty = False

        # Cross-screen events
        try:
            from .core_events import core_events
            core_events.cores_changed.connect(self._on_external_refresh)
            core_events.core_credit_issued.connect(
                lambda *_: self._on_external_refresh()
            )
        except Exception as exc:
            log.debug("core_events wiring skipped: %s", exc)

        self.setWindowTitle("Customer Profile")
        self.setMinimumSize(1180, 760)
        self.resize(1280, 820)
        self.setStyleSheet(f"QDialog {{ background:{_T['bg']}; color:{_T['text']}; }}")

        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self._sidebar = self._build_sidebar()
        self._sidebar.setFixedWidth(300)
        body.addWidget(self._sidebar)

        self._main_area = self._build_main_area()
        body.addWidget(self._main_area, 1)

        body_wrap = QWidget()
        body_wrap.setLayout(body)
        root.addWidget(body_wrap, 1)

        root.addWidget(self._build_footer())

    # ── Header ────────────────────────────────────────────────────────
    def _build_header(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("hdrBar")
        bar.setStyleSheet(
            f"#hdrBar {{ background:{_T['header_bg']};"
            f" border-bottom:1px solid {_T['border']}; }}"
            f"QLabel {{ background:transparent; }}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 10, 18, 10)
        lay.setSpacing(12)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self._lbl_title = QLabel("New Customer")
        self._lbl_title.setStyleSheet(
            f"color:{_T['text']}; font-size:13pt; font-weight:700; letter-spacing:0.3px;"
        )
        self._lbl_subtitle = QLabel("")
        self._lbl_subtitle.setStyleSheet(f"color:{_T['text_dim']}; font-size:9pt;")
        title_box.addWidget(self._lbl_title)
        title_box.addWidget(self._lbl_subtitle)
        lay.addLayout(title_box)

        self._pill_status = _Pill("ACTIVE", "active")
        self._pill_tier = _Pill("TIER · STANDARD", "tier")
        self._pill_balance = _Pill("BAL · $0.00", "good")
        lay.addWidget(self._pill_status)
        lay.addWidget(self._pill_tier)
        lay.addWidget(self._pill_balance)

        lay.addStretch(1)
        for txt in ("Ctrl+S Save", "F2 New Quote", "F5 Statement"):
            lay.addWidget(_Chip(txt))

        for label, handler, primary in (
            ("Email",       self._on_email,       False),
            ("SMS",         self._on_sms,         False),
            ("Statement",   self._on_statement,   False),
            ("New Quote",   self._on_new_quote,   False),
            ("Save Changes", self._on_save,       True),
        ):
            b = QPushButton(label)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if primary:
                b.setProperty("primary", True)
            b.clicked.connect(handler)
            lay.addWidget(b)

        return bar

    # ── Sidebar ───────────────────────────────────────────────────────
    def _build_sidebar(self) -> QWidget:
        outer = QWidget()
        outer.setStyleSheet(
            f"QWidget {{ background:{_T['panel_alt']};"
            f" border-right:1px solid {_T['border']}; }}"
        )
        scroll = QScrollArea(outer)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background:transparent; border:none; }")

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)

        # At a glance
        card = _SidebarCard("At a glance")
        grid = QGridLayout()
        grid.setSpacing(6)
        self._kpi_ytd    = _KpiBox("$0", "YTD Sales")
        self._kpi_orders = _KpiBox("0",  "Orders")
        self._kpi_owed   = _KpiBox("$0", "Owed")
        self._kpi_ontime = _KpiBox("—",  "On-time")
        grid.addWidget(self._kpi_ytd, 0, 0)
        grid.addWidget(self._kpi_orders, 0, 1)
        grid.addWidget(self._kpi_owed, 1, 0)
        grid.addWidget(self._kpi_ontime, 1, 1)
        card.body().addLayout(grid)
        lay.addWidget(card)

        # Account
        card = _SidebarCard("Account")
        self._side_kv_account = self._kv_grid([
            ("Type", "—"), ("Terms", "—"), ("Credit limit", "—"),
            ("Available", "—"), ("Tax exempt", "—"),
            ("Price level", "—"), ("Sales rep", "—"),
        ])
        card.body().addLayout(self._side_kv_account["layout"])
        lay.addWidget(card)

        # Primary contact
        card = _SidebarCard("Primary contact")
        self._side_kv_primary = self._kv_grid([
            ("Name", "—"), ("Role", "—"), ("Phone", "—"),
            ("Mobile", "—"), ("Email", "—"),
        ])
        card.body().addLayout(self._side_kv_primary["layout"])
        lay.addWidget(card)

        # Quick actions
        card = _SidebarCard("Quick actions")
        for label, handler in (
            ("📞  Log call",            self._on_log_call),
            ("📅  Schedule follow-up",  self._on_schedule_followup),
            ("📧  Send statement",      self._on_statement),
            ("💳  Record payment",      self._on_record_payment),
            ("📦  View open orders",    self._on_view_open_orders),
            ("⛔  Place on hold",       self._on_toggle_hold),
        ):
            b = QPushButton(label)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton {{ text-align:left; background:{_T['panel']};"
                f" color:{_T['text']}; border:1px solid {_T['border']};"
                f" border-radius:4px; padding:6px 10px; font-size:9pt; }}"
                f"QPushButton:hover {{ background:{_T['panel_alt']};"
                f" border-color:{_T['accent']}; }}"
            )
            if "hold" in label.lower():
                b.setProperty("danger", True)
                b.setStyleSheet(b.styleSheet() + (
                    f" QPushButton {{ color:{_T['danger']}; }}"
                ))
                self._btn_hold = b
            b.clicked.connect(handler)
            card.body().addWidget(b)
        lay.addWidget(card)

        lay.addStretch(1)
        scroll.setWidget(inner)
        wrap = QVBoxLayout(outer)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.addWidget(scroll)
        return outer

    def _kv_grid(self, rows: list[tuple[str, str]]) -> dict:
        """Helper — builds a 2-col key/value grid; returns dict with editable refs."""
        lay = QGridLayout()
        lay.setHorizontalSpacing(8)
        lay.setVerticalSpacing(3)
        lay.setContentsMargins(0, 0, 0, 0)
        value_labels: dict[str, QLabel] = {}
        for i, (k, v) in enumerate(rows):
            kl = QLabel(k)
            kl.setStyleSheet(
                f"color:{_T['text_muted']}; background:transparent;"
                f" border:none; font-size:9pt;"
            )
            vl = QLabel(v)
            vl.setStyleSheet(
                f"color:{_T['text']}; background:transparent;"
                f" border:none; font-size:9pt;"
            )
            vl.setWordWrap(True)
            lay.addWidget(kl, i, 0, Qt.AlignmentFlag.AlignTop)
            lay.addWidget(vl, i, 1)
            value_labels[k] = vl
        lay.setColumnStretch(1, 1)
        return {"layout": lay, "labels": value_labels}

    # ── Main / tabs ───────────────────────────────────────────────────
    def _build_main_area(self) -> QWidget:
        wrap = QWidget()
        wrap.setStyleSheet(f"QWidget {{ background:{_T['bg']}; }}")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_overview_tab(), "Overview")
        self._tabs.addTab(self._build_contacts_tab(), "Contacts")
        self._tabs.addTab(self._build_addresses_tab(), "Addresses")
        self._tabs.addTab(self._build_orders_tab(),    "Orders")
        self._tabs.addTab(self._build_invoices_tab(),  "Invoices")
        self._tabs.addTab(self._build_cores_tab(),     "Cores")
        self._tabs.addTab(self._build_notes_tab(),     "Notes")
        self._tabs.addTab(self._build_files_tab(),     "Files")
        lay.addWidget(self._tabs)
        return wrap

    # ----- Overview ---------------------------------------------------
    def _section(self, title: str, add_action: str = "") -> tuple[QFrame, QVBoxLayout, QPushButton | None]:
        sec = QFrame()
        sec.setObjectName("sec")
        sec.setStyleSheet(
            f"#sec {{ background:{_T['panel']}; border:1px solid {_T['border']};"
            f" border-radius:6px; }}"
            f"#sec > QLabel {{ background:transparent; }}"
        )
        v = QVBoxLayout(sec)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        h = QLabel(title.upper())
        h.setStyleSheet(
            f"color:{_T['text_dim']}; font-size:9pt; font-weight:700;"
            f" letter-spacing:0.8px; background:transparent;"
        )
        head.addWidget(h)
        head.addStretch(1)
        btn = None
        if add_action:
            btn = QPushButton(add_action)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background:transparent; border:none;"
                f" color:{_T['accent_hover']}; font-size:9pt; padding:0; }}"
                f"QPushButton:hover {{ text-decoration:underline; }}"
            )
            head.addWidget(btn)
        v.addLayout(head)

        return sec, v, btn

    def _build_overview_tab(self) -> QWidget:
        outer = QWidget()
        scroll = QScrollArea(outer)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(12)

        # Company info
        sec, v, _ = self._section("Company information")
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self.ed_account_no = self._mk_input(read_only=True)
        self.ed_company    = self._mk_input()
        self.ed_dba        = self._mk_input()
        self.cb_status     = self._mk_combo(["Active", "On hold", "Inactive"])
        self.cb_type       = self._mk_combo([
            "Retail", "Wholesale", "Wholesale · Shop", "Fleet", "Government",
        ])
        self.ed_sales_rep  = self._mk_input()
        self.ed_phone      = self._mk_input()
        self.ed_email      = self._mk_input()
        self.ed_website    = self._mk_input()

        self._grid_pairs(form, [
            ("Customer #", self.ed_account_no),
            ("Company name", self.ed_company),
            ("DBA", self.ed_dba),
            ("Status", self.cb_status),
            ("Type", self.cb_type),
            ("Sales rep", self.ed_sales_rep),
            ("Main phone", self.ed_phone),
            ("Main email", self.ed_email),
            ("Website", self.ed_website),
        ], cols=3)
        v.addLayout(form)
        lay.addWidget(sec)

        # Billing & terms
        sec, v, _ = self._section("Billing & terms")
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self.cb_terms       = self._mk_combo(
            ["Net 30", "Net 15", "Net 45", "Net 60", "COD", "Due on Receipt", "Prepay"]
        )
        self.ed_credit_lim  = self._mk_input()
        self.ed_balance     = self._mk_input(read_only=True)
        self.cb_price_level = self._mk_combo(
            ["Standard", "Wholesale", "Wholesale -10%", "List", "Fleet"]
        )
        self.cb_tax_exempt  = self._mk_combo(["No", "Yes"])
        self.ed_tax_id      = self._mk_input()
        self.ed_ship_method = self._mk_input()
        self.cb_po_required = self._mk_combo(["No", "Yes"])
        self._grid_pairs(form, [
            ("Payment terms", self.cb_terms),
            ("Credit limit", self.ed_credit_lim),
            ("Current balance", self.ed_balance),
            ("Price level", self.cb_price_level),
            ("Tax exempt", self.cb_tax_exempt),
            ("Tax-exempt #", self.ed_tax_id),
            ("Default ship method", self.ed_ship_method),
            ("PO required?", self.cb_po_required),
        ], cols=4)
        v.addLayout(form)
        lay.addWidget(sec)

        # Primary contacts
        sec, v, btn_add = self._section("Primary contacts", "+ Add contact")
        self._contacts_holder = QVBoxLayout()
        self._contacts_holder.setSpacing(6)
        v.addLayout(self._contacts_holder)
        if btn_add is not None:
            btn_add.clicked.connect(self._on_add_contact)
        lay.addWidget(sec)

        # Addresses
        sec, v, btn_add = self._section("Addresses", "+ Add address")
        self._addr_table = self._mk_table(
            ["Label", "Type", "Street", "City / State / ZIP", "Default", ""]
        )
        self._addr_table.setMinimumHeight(140)
        v.addWidget(self._addr_table)
        if btn_add is not None:
            btn_add.clicked.connect(self._on_add_address)
        lay.addWidget(sec)

        # Recent activity
        sec, v, view_all = self._section("Recent activity (last 90 days)", "View all →")
        self._activity_table = self._mk_table(
            ["Date", "Doc #", "Type", "Reference", "Amount", "Status"]
        )
        self._activity_table.setMinimumHeight(220)
        v.addWidget(self._activity_table)
        if view_all is not None:
            view_all.clicked.connect(lambda: self._tabs.setCurrentIndex(4))
        lay.addWidget(sec)

        # Internal notes
        sec, v, _ = self._section("Internal notes")
        twocol = QHBoxLayout()
        twocol.setSpacing(12)
        col1 = QVBoxLayout(); col1.setSpacing(4)
        col1.addWidget(self._small_label("Pinned note"))
        self.ed_notes = QPlainTextEdit()
        self.ed_notes.setMinimumHeight(70)
        col1.addWidget(self.ed_notes)
        col2 = QVBoxLayout(); col2.setSpacing(4)
        col2.addWidget(self._small_label("Tags"))
        self.ed_tags = QLineEdit()
        self.ed_tags.setPlaceholderText("comma-separated, e.g. diesel, fleet, net30")
        col2.addWidget(self.ed_tags)
        hint = QLabel("Used for filtering & reports.")
        hint.setStyleSheet(f"color:{_T['text_muted']}; font-size:9pt;")
        col2.addWidget(hint)
        col2.addStretch(1)
        twocol.addLayout(col1, 1)
        twocol.addLayout(col2, 1)
        v.addLayout(twocol)
        lay.addWidget(sec)

        lay.addStretch(1)
        scroll.setWidget(inner)
        owrap = QVBoxLayout(outer)
        owrap.setContentsMargins(0, 0, 0, 0)
        owrap.addWidget(scroll)
        return outer

    # ----- Other tabs (built minimal; reuse tables) ------------------
    def _build_contacts_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(2, 8, 2, 2); v.setSpacing(8)
        bar = QHBoxLayout()
        b = QPushButton("+ Add Contact"); b.clicked.connect(self._on_add_contact)
        bar.addWidget(b)
        b2 = QPushButton("Remove Selected"); b2.clicked.connect(self._on_remove_contact_selected)
        bar.addWidget(b2)
        bar.addStretch(1)
        v.addLayout(bar)
        self._contacts_table = self._mk_table(
            ["Name", "Role", "Phone", "Email", "Notes"]
        )
        self._contacts_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._contacts_table.itemChanged.connect(self._on_contact_cell_edited)
        v.addWidget(self._contacts_table, 1)
        return w

    def _build_addresses_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(2, 8, 2, 2); v.setSpacing(10)

        for title, prefix in (("Billing address", "bill"), ("Shipping address", "ship")):
            sec, sv, _ = self._section(title)
            form = QGridLayout(); form.setHorizontalSpacing(10); form.setVerticalSpacing(8)
            street = self._mk_input()
            city   = self._mk_input()
            state  = self._mk_input()
            zipc   = self._mk_input()
            setattr(self, f"ed_{prefix}_street", street)
            setattr(self, f"ed_{prefix}_city", city)
            setattr(self, f"ed_{prefix}_state", state)
            setattr(self, f"ed_{prefix}_zip", zipc)
            self._grid_pairs(form, [
                ("Street", street), ("City", city), ("State", state), ("ZIP", zipc),
            ], cols=4)
            sv.addLayout(form)
            v.addWidget(sec)

        self.chk_same_as_billing = QCheckBox("Shipping same as billing")
        self.chk_same_as_billing.toggled.connect(self._on_copy_billing)
        v.addWidget(self.chk_same_as_billing)
        v.addStretch(1)
        return w

    def _build_orders_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(2, 8, 2, 2)
        self._orders_table = self._mk_table(
            ["Date", "SKU", "Title", "Qty", "Unit", "Total", "Invoice #", "Status"]
        )
        v.addWidget(self._orders_table)
        return w

    def _build_invoices_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(2, 8, 2, 2)
        self._invoices_table = self._mk_table(
            ["Invoice #", "Date", "Total", "Status"]
        )
        v.addWidget(self._invoices_table)
        return w

    def _build_cores_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(2, 8, 2, 2)
        self._cores_table = self._mk_table(
            ["Invoice #", "SKU", "Title", "Core $", "Qty", "Status", "Date"]
        )
        v.addWidget(self._cores_table)
        return w

    def _build_notes_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(2, 8, 2, 2); v.setSpacing(8)
        v.addWidget(self._small_label("Activity log"))
        self._activity_log = self._mk_table(
            ["When", "Type", "Subject", "Notes", "Follow-up"]
        )
        v.addWidget(self._activity_log, 1)
        bar = QHBoxLayout()
        b = QPushButton("+ Log activity"); b.clicked.connect(self._on_log_call)
        bar.addWidget(b)
        bar.addStretch(1)
        v.addLayout(bar)
        return w

    def _build_files_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(2, 8, 2, 2)
        info = QLabel("Attachments coming soon — drag-and-drop PDF / image support.")
        info.setStyleSheet(f"color:{_T['text_dim']}; font-style:italic; padding:18px;")
        v.addWidget(info)
        v.addStretch(1)
        return w

    # ── Footer ────────────────────────────────────────────────────────
    def _build_footer(self) -> QFrame:
        bar = QFrame()
        bar.setStyleSheet(
            f"QFrame {{ background:{_T['header_bg']};"
            f" border-top:1px solid {_T['border']}; }}"
            f"QLabel {{ background:transparent; }}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 8, 18, 8)
        lay.setSpacing(10)
        self._lbl_audit_created = QLabel("")
        self._lbl_audit_modified = QLabel("")
        for lbl in (self._lbl_audit_created, self._lbl_audit_modified):
            lbl.setStyleSheet(f"color:{_T['text_muted']}; font-size:9pt;")
        lay.addWidget(self._lbl_audit_created)
        lay.addWidget(QLabel("·"))
        lay.addWidget(self._lbl_audit_modified)
        lay.addStretch(1)
        for label, handler, prop in (
            ("Deactivate", self._on_deactivate, ("danger", True)),
            ("Duplicate", self._on_duplicate, None),
            ("Cancel",    self.reject,          None),
            ("Save Changes", self._on_save,    ("primary", True)),
        ):
            b = QPushButton(label)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if prop:
                b.setProperty(*prop)
            b.clicked.connect(handler)
            lay.addWidget(b)
        return bar

    # ------------------------------------------------------------------
    # Helpers (widgets / styling)
    # ------------------------------------------------------------------
    def _mk_input(self, read_only: bool = False) -> QLineEdit:
        e = QLineEdit()
        if read_only:
            e.setReadOnly(True)
        return e

    def _mk_combo(self, items: list[str]) -> QComboBox:
        c = QComboBox()
        c.addItems(items)
        return c

    def _small_label(self, text: str) -> QLabel:
        l = QLabel(text.upper())
        l.setStyleSheet(
            f"color:{_T['text_muted']}; font-size:9pt; font-weight:600;"
            f" letter-spacing:0.6px; background:transparent;"
        )
        return l

    def _grid_pairs(
        self, grid: QGridLayout, pairs: list[tuple[str, QWidget]], cols: int = 3
    ) -> None:
        for idx, (label, widget) in enumerate(pairs):
            r = idx // cols
            c = idx % cols
            holder = QVBoxLayout(); holder.setSpacing(3); holder.setContentsMargins(0, 0, 0, 0)
            holder.addWidget(self._small_label(label))
            holder.addWidget(widget)
            w = QWidget(); w.setLayout(holder)
            grid.addWidget(w, r, c)
        for c in range(cols):
            grid.setColumnStretch(c, 1)

    def _mk_table(self, headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setAlternatingRowColors(True)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hh = t.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(True)
        return t

    # ------------------------------------------------------------------
    # Data load
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self.customer_id is None:
            self._lbl_title.setText("New Customer")
            self._lbl_subtitle.setText("Fill in the details and save to create.")
            self._pill_status.set_text_and_variant("DRAFT", "warn")
            self._pill_tier.set_text_and_variant("TIER · STANDARD", "tier")
            self._pill_balance.set_text_and_variant("BAL · $0.00", "good")
            return

        try:
            row = db.get_customer(self.customer_id)
        except Exception as exc:
            log.exception("get_customer failed")
            QMessageBox.warning(self, "Customer", f"Failed to load customer: {exc}")
            row = None
        if not row:
            self._lbl_title.setText(f"Customer #{self.customer_id} (not found)")
            return
        self._customer = _row_to_dict(row)

        self._populate_form()
        self._load_sidebar_stats()
        self._load_contacts()
        self._load_activity()
        self._load_invoices_and_orders()
        self._load_cores()
        self._load_activity_log()
        self._load_audit()

    def _populate_form(self) -> None:
        c = self._customer
        name    = (c.get("company") or c.get("name") or "").strip()
        acct    = c.get("account_number") or f"C-{self.customer_id:04d}"
        self._lbl_title.setText(f"{acct} · {name or '(unnamed)'}")
        sub_parts = []
        if c.get("created_at"):
            sub_parts.append(f"Customer since {_short_date(c['created_at'])}")
        self._lbl_subtitle.setText(" · ".join(sub_parts))

        # Status pill
        if int(c.get("on_hold") or 0):
            self._pill_status.set_text_and_variant("ON HOLD", "warn")
        elif int(c.get("is_active") or 1):
            self._pill_status.set_text_and_variant("ACTIVE", "active")
        else:
            self._pill_status.set_text_and_variant("INACTIVE", "tier")

        tier = (c.get("pricing_tier") or "STANDARD").upper()
        self._pill_tier.set_text_and_variant(f"TIER · {tier}", "tier")

        # Company info
        self.ed_account_no.setText(str(acct))
        self.ed_company.setText(c.get("company") or c.get("name") or "")
        self.ed_dba.setText(c.get("dba") or "")
        self._set_combo(self.cb_status,
            "On hold" if int(c.get("on_hold") or 0)
            else ("Active" if int(c.get("is_active") or 1) else "Inactive"))
        self._set_combo(self.cb_type, c.get("customer_type") or "Retail")
        self.ed_sales_rep.setText(c.get("sales_rep") or "")
        self.ed_phone.setText(c.get("phone") or "")
        self.ed_email.setText(c.get("email") or "")
        self.ed_website.setText(c.get("website") or "")

        # Billing & terms
        self._set_combo(self.cb_terms, c.get("payment_terms") or "Net 30")
        self.ed_credit_lim.setText(_money(c.get("credit_limit")))
        self._set_combo(self.cb_price_level, c.get("pricing_tier") or "Standard")
        self._set_combo(self.cb_tax_exempt, "Yes" if int(c.get("tax_exempt") or 0) else "No")
        self.ed_tax_id.setText(c.get("tax_id") or "")
        self.ed_ship_method.setText(c.get("delivery_notes") or "")
        self._set_combo(self.cb_po_required, "No")

        # Addresses
        self.ed_bill_street.setText(c.get("bill_to_address") or c.get("address") or "")
        self.ed_bill_city.setText(c.get("bill_to_city") or c.get("city") or "")
        self.ed_bill_state.setText(c.get("bill_to_state") or c.get("state") or "")
        self.ed_bill_zip.setText(c.get("bill_to_zip") or c.get("zip") or "")
        self.ed_ship_street.setText(c.get("ship_to_address") or "")
        self.ed_ship_city.setText(c.get("ship_to_city") or "")
        self.ed_ship_state.setText(c.get("ship_to_state") or "")
        self.ed_ship_zip.setText(c.get("ship_to_zip") or "")

        # Notes & tags
        self.ed_notes.setPlainText(c.get("notes") or "")
        self.ed_tags.setText(c.get("tags") or "")

        # Hold button label
        if hasattr(self, "_btn_hold"):
            self._btn_hold.setText("✅  Release hold" if int(c.get("on_hold") or 0)
                                   else "⛔  Place on hold")

    def _set_combo(self, combo: QComboBox, value: str) -> None:
        idx = combo.findText(value, Qt.MatchFlag.MatchFixedString)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.insertItem(0, value)
            combo.setCurrentIndex(0)

    def _load_sidebar_stats(self) -> None:
        if self.customer_id is None:
            return
        # Spending
        spend_annual = 0.0; orders = 0
        try:
            sp = db.get_customer_spending_summary(self.customer_id) or {}
            spend_annual = float(sp.get("annual") or 0)
        except Exception as exc:
            log.debug("spending_summary: %s", exc)
        try:
            invs = db.get_customer_invoices(self.customer_id) or []
            orders = len(invs)
        except Exception as exc:
            log.debug("invoices: %s", exc)

        # Aging / balance
        balance = 0.0
        try:
            aging = db.get_customer_aging(self.customer_id) or {}
            balance = float(aging.get("total_outstanding") or 0)
        except Exception as exc:
            log.debug("aging: %s", exc)

        credit_limit = float(self._customer.get("credit_limit") or 0)
        available = max(0.0, credit_limit - balance) if credit_limit > 0 else 0.0

        # On-time %
        ontime = "—"

        self._kpi_ytd._reset(_money_short(spend_annual), "YTD Sales") if False else None
        # Rebuild KPI labels
        self._replace_kpi(self._kpi_ytd, _money_short(spend_annual), "YTD Sales")
        self._replace_kpi(self._kpi_orders, str(orders), "Orders")
        self._replace_kpi(self._kpi_owed, _money_short(balance), "Owed")
        self._replace_kpi(self._kpi_ontime, ontime, "On-time")

        # Balance pill
        if balance <= 0:
            self._pill_balance.set_text_and_variant("BAL · $0.00", "good")
        else:
            self._pill_balance.set_text_and_variant(f"BAL · {_money(balance)}", "due")

        # Account card
        a = self._side_kv_account["labels"]
        a["Type"].setText(self._customer.get("customer_type") or "—")
        a["Terms"].setText(self._customer.get("payment_terms") or "—")
        a["Credit limit"].setText(_money(credit_limit) if credit_limit else "—")
        a["Available"].setText(_money(available) if credit_limit else "—")
        a["Tax exempt"].setText(
            ("Yes · " + self._customer.get("tax_id", "")).strip(" ·")
            if int(self._customer.get("tax_exempt") or 0) else "No"
        )
        a["Price level"].setText(self._customer.get("pricing_tier") or "Standard")
        a["Sales rep"].setText(self._customer.get("sales_rep") or "—")

    def _replace_kpi(self, box: _KpiBox, value: str, label: str) -> None:
        # Each _KpiBox has fixed layout: [QLabel value, QLabel label]
        lay = box.layout()
        if lay is None or lay.count() < 2:
            return
        v_lbl = lay.itemAt(0).widget()
        l_lbl = lay.itemAt(1).widget()
        if isinstance(v_lbl, QLabel):
            v_lbl.setText(value)
        if isinstance(l_lbl, QLabel):
            l_lbl.setText(label.upper())

    def _load_contacts(self) -> None:
        if self.customer_id is None:
            self._employees = []
        else:
            try:
                self._employees = [_row_to_dict(r)
                                   for r in (db.get_customer_employees(self.customer_id) or [])]
            except Exception as exc:
                log.debug("employees: %s", exc)
                self._employees = []

        # Sidebar primary
        p = self._side_kv_primary["labels"]
        if self._employees:
            e = self._employees[0]
            p["Name"].setText(e.get("name") or "—")
            p["Role"].setText(e.get("title") or "—")
            p["Phone"].setText(e.get("phone") or "—")
            p["Mobile"].setText(e.get("mobile") or e.get("phone") or "—")
            p["Email"].setText(e.get("email") or "—")
        else:
            for k in p:
                p[k].setText("—")

        # Overview cards
        while self._contacts_holder.count():
            item = self._contacts_holder.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        for idx, emp in enumerate(self._employees):
            card = _ContactCard(emp, is_primary=(idx == 0))
            card.edit_requested.connect(self._on_edit_contact)
            card.delete_requested.connect(self._on_delete_contact)
            self._contacts_holder.addWidget(card)
        if not self._employees:
            empty = QLabel("No contacts yet — click '+ Add contact' to create one.")
            empty.setStyleSheet(f"color:{_T['text_muted']}; font-style:italic; padding:8px;")
            self._contacts_holder.addWidget(empty)

        # Contacts tab table
        self._fill_contacts_table()

    def _fill_contacts_table(self) -> None:
        t = self._contacts_table
        try:
            t.blockSignals(True)
            t.setRowCount(0)
            for emp in self._employees:
                r = t.rowCount(); t.insertRow(r)
                for c, key in enumerate(("name", "title", "phone", "email", "notes")):
                    item = QTableWidgetItem(str(emp.get(key) or ""))
                    item.setData(Qt.ItemDataRole.UserRole, int(emp.get("id") or 0))
                    t.setItem(r, c, item)
        finally:
            t.blockSignals(False)

    def _load_activity(self) -> None:
        """Overview's recent activity table."""
        t = self._activity_table
        t.setRowCount(0)
        rows: list[dict] = []
        if self.customer_id is None:
            return
        try:
            for r in (db.get_customer_invoices(self.customer_id) or [])[:20]:
                d = _row_to_dict(r)
                rows.append({
                    "date": _short_date(d.get("created_at") or d.get("invoice_date")),
                    "doc":  d.get("invoice_number") or f"INV-{d.get('id','')}",
                    "type": "Invoice",
                    "ref":  d.get("notes") or "",
                    "amt":  _money(d.get("total")),
                    "status": (d.get("status") or "").upper(),
                })
        except Exception as exc:
            log.debug("activity load: %s", exc)

        for r in rows[:25]:
            row = t.rowCount(); t.insertRow(row)
            for c, key in enumerate(("date", "doc", "type", "ref", "amt", "status")):
                it = QTableWidgetItem(str(r.get(key, "")))
                if key == "amt":
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                t.setItem(row, c, it)

    def _load_invoices_and_orders(self) -> None:
        if self.customer_id is None:
            return
        try:
            invs = [_row_to_dict(r) for r in (db.get_customer_invoices(self.customer_id) or [])]
        except Exception as exc:
            log.debug("invoices: %s", exc); invs = []
        t = self._invoices_table; t.setRowCount(0)
        for inv in invs:
            r = t.rowCount(); t.insertRow(r)
            t.setItem(r, 0, QTableWidgetItem(inv.get("invoice_number") or ""))
            t.setItem(r, 1, QTableWidgetItem(_short_date(inv.get("created_at") or inv.get("invoice_date"))))
            it = QTableWidgetItem(_money(inv.get("total")))
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.setItem(r, 2, it)
            t.setItem(r, 3, QTableWidgetItem((inv.get("status") or "").upper()))

        try:
            hist = [_row_to_dict(r) for r in (db.get_customer_purchase_history(self.customer_id) or [])]
        except Exception as exc:
            log.debug("history: %s", exc); hist = []
        t = self._orders_table; t.setRowCount(0)
        for h in hist:
            r = t.rowCount(); t.insertRow(r)
            cells = [
                _short_date(h.get("invoice_date")),
                h.get("sku") or "",
                h.get("product_title") or "",
                str(h.get("quantity") or ""),
                _money(h.get("unit_price")),
                _money(h.get("line_total")),
                h.get("invoice_number") or "",
                (h.get("invoice_status") or "").upper(),
            ]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(str(val))
                if c in (4, 5):
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                t.setItem(r, c, it)

    def _load_cores(self) -> None:
        if self.customer_id is None:
            return
        try:
            rows = [_row_to_dict(r) for r in (db.get_customer_outstanding_cores(self.customer_id) or [])]
        except Exception as exc:
            log.debug("cores: %s", exc); rows = []
        t = self._cores_table; t.setRowCount(0)
        for r in rows:
            row = t.rowCount(); t.insertRow(row)
            cells = [
                r.get("invoice_number") or "",
                r.get("sku") or "",
                r.get("title") or "",
                _money(r.get("core_charge")),
                str(r.get("quantity") or ""),
                (r.get("core_status") or "").upper(),
                _short_date(r.get("invoice_date")),
            ]
            for c, val in enumerate(cells):
                it = QTableWidgetItem(str(val))
                if c == 3:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                t.setItem(row, c, it)

    def _load_activity_log(self) -> None:
        if self.customer_id is None:
            return
        try:
            rows = [_row_to_dict(r) for r in (db.get_customer_activities(self.customer_id) or [])]
        except Exception as exc:
            log.debug("activities: %s", exc); rows = []
        t = self._activity_log; t.setRowCount(0)
        for a in rows:
            row = t.rowCount(); t.insertRow(row)
            cells = [
                _short_date(a.get("created_at")),
                (a.get("activity_type") or "").title(),
                a.get("subject") or "",
                a.get("notes") or "",
                _short_date(a.get("follow_up_date")) if a.get("follow_up_date") else "",
            ]
            for c, val in enumerate(cells):
                t.setItem(row, c, QTableWidgetItem(str(val)))

    def _load_audit(self) -> None:
        c = self._customer
        if c.get("created_at"):
            self._lbl_audit_created.setText(f"Created {_long_date(c['created_at'])}")
        else:
            self._lbl_audit_created.setText("New customer (not saved)")
        if c.get("updated_at"):
            self._lbl_audit_modified.setText(f"Last modified {_long_date(c['updated_at'])}")
        else:
            self._lbl_audit_modified.setText("")

    # ------------------------------------------------------------------
    # Address tab helpers
    # ------------------------------------------------------------------
    def _on_copy_billing(self, on: bool) -> None:
        if not on:
            return
        self.ed_ship_street.setText(self.ed_bill_street.text())
        self.ed_ship_city.setText(self.ed_bill_city.text())
        self.ed_ship_state.setText(self.ed_bill_state.text())
        self.ed_ship_zip.setText(self.ed_bill_zip.text())

    # ------------------------------------------------------------------
    # Contact handlers
    # ------------------------------------------------------------------
    def _on_add_contact(self) -> None:
        if self.customer_id is None:
            QMessageBox.information(self, "Save first",
                "Save the customer before adding contacts.")
            return
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Add contact", "Name:")
        if not ok or not name.strip():
            return
        try:
            db.add_customer_employee(self.customer_id, name.strip())
        except Exception as exc:
            QMessageBox.warning(self, "Contact", f"Failed: {exc}")
            return
        self._load_contacts()

    def _on_remove_contact_selected(self) -> None:
        t = self._contacts_table
        row = t.currentRow()
        if row < 0:
            return
        item = t.item(row, 0)
        if not item:
            return
        emp_id = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        self._on_delete_contact(emp_id)

    def _on_edit_contact(self, emp_id: int) -> None:
        # Switch to Contacts tab and select the row.
        self._tabs.setCurrentIndex(1)
        for r in range(self._contacts_table.rowCount()):
            it = self._contacts_table.item(r, 0)
            if it and int(it.data(Qt.ItemDataRole.UserRole) or 0) == emp_id:
                self._contacts_table.selectRow(r)
                self._contacts_table.editItem(it)
                return

    def _on_delete_contact(self, emp_id: int) -> None:
        if emp_id <= 0:
            return
        if QMessageBox.question(self, "Remove contact", "Remove this contact?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            db.delete_customer_employee(emp_id)
        except Exception as exc:
            QMessageBox.warning(self, "Contact", f"Failed: {exc}")
            return
        self._load_contacts()

    def _on_contact_cell_edited(self, item: QTableWidgetItem) -> None:
        if item is None:
            return
        row = item.row()
        col = item.column()
        first = self._contacts_table.item(row, 0)
        if not first:
            return
        emp_id = int(first.data(Qt.ItemDataRole.UserRole) or 0)
        if emp_id <= 0:
            return
        field = ("name", "title", "phone", "email", "notes")[col]
        try:
            db.update_customer_employee(emp_id, **{field: item.text().strip()})
        except Exception as exc:
            log.warning("update contact: %s", exc)

    def _on_add_address(self) -> None:
        # Schema embeds bill+ship as columns; switch to Addresses tab.
        self._tabs.setCurrentIndex(2)

    # ------------------------------------------------------------------
    # Quick-action handlers
    # ------------------------------------------------------------------
    def _on_email(self) -> None:
        addr = (self.ed_email.text() or "").strip()
        if not addr:
            QMessageBox.information(self, "Email", "No email address on file.")
            return
        import webbrowser
        webbrowser.open(f"mailto:{addr}")

    def _on_sms(self) -> None:
        QMessageBox.information(self, "SMS", "SMS gateway not yet configured.")

    def _on_statement(self) -> None:
        if self.customer_id is None:
            QMessageBox.information(self, "Statement", "Save the customer first.")
            return
        try:
            from .statement_dialog import StatementDialog
            StatementDialog(self.customer_id, self).exec()
        except Exception:
            QMessageBox.information(self, "Statement",
                "Statement PDF is generated from the Customers list — open from there.")

    def _on_new_quote(self) -> None:
        if self.customer_id is None:
            QMessageBox.information(self, "Quote", "Save the customer first.")
            return
        try:
            from .quote_dialog import QuoteDialog
            QuoteDialog(customer_id=self.customer_id, parent=self).exec()
        except Exception as exc:
            QMessageBox.warning(self, "Quote", f"Failed to open quote: {exc}")

    def _on_log_call(self) -> None:
        if self.customer_id is None:
            QMessageBox.information(self, "Log call", "Save the customer first.")
            return
        from PySide6.QtWidgets import QInputDialog
        subj, ok = QInputDialog.getText(self, "Log call", "Subject:")
        if not ok or not subj.strip():
            return
        try:
            db.add_customer_activity(self.customer_id, "call", subj.strip())
        except Exception as exc:
            QMessageBox.warning(self, "Activity", f"Failed: {exc}")
            return
        self._load_activity_log()

    def _on_schedule_followup(self) -> None:
        if self.customer_id is None:
            QMessageBox.information(self, "Follow-up", "Save the customer first.")
            return
        from PySide6.QtWidgets import QInputDialog
        subj, ok = QInputDialog.getText(self, "Schedule follow-up", "Subject:")
        if not ok or not subj.strip():
            return
        date, ok = QInputDialog.getText(
            self, "Follow-up date", "YYYY-MM-DD:",
            text=datetime.now().strftime("%Y-%m-%d"),
        )
        if not ok:
            return
        try:
            db.add_customer_activity(
                self.customer_id, "followup", subj.strip(),
                follow_up_date=date.strip() or None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Follow-up", f"Failed: {exc}")
            return
        self._load_activity_log()

    def _on_record_payment(self) -> None:
        QMessageBox.information(self, "Record payment",
            "Open the Invoice screen for this customer to record a payment.")

    def _on_view_open_orders(self) -> None:
        self._tabs.setCurrentIndex(3)

    def _on_toggle_hold(self) -> None:
        if self.customer_id is None:
            QMessageBox.information(self, "Hold", "Save the customer first.")
            return
        on_hold_now = int(self._customer.get("on_hold") or 0)
        new_val = 0 if on_hold_now else 1
        try:
            db.update_customer(self.customer_id, on_hold=new_val)
        except Exception as exc:
            QMessageBox.warning(self, "Hold", f"Failed: {exc}")
            return
        self._customer["on_hold"] = new_val
        self._populate_form()

    # ------------------------------------------------------------------
    # Save / deactivate / duplicate
    # ------------------------------------------------------------------
    def _collect_form(self) -> dict:
        on_hold_val = 1 if self.cb_status.currentText() == "On hold" else 0
        is_active   = 0 if self.cb_status.currentText() == "Inactive" else 1
        try:
            credit_limit = float(
                (self.ed_credit_lim.text() or "0").replace("$", "").replace(",", "")
            )
        except ValueError:
            credit_limit = 0.0
        return {
            "company":        self.ed_company.text().strip(),
            "name":           self.ed_company.text().strip()
                              or self.ed_dba.text().strip()
                              or "(unnamed)",
            "dba":            self.ed_dba.text().strip(),
            "phone":          self.ed_phone.text().strip(),
            "email":          self.ed_email.text().strip(),
            "website":        self.ed_website.text().strip(),
            "customer_type":  self.cb_type.currentText(),
            "sales_rep":      self.ed_sales_rep.text().strip(),
            "payment_terms":  self.cb_terms.currentText(),
            "credit_limit":   credit_limit,
            "pricing_tier":   self.cb_price_level.currentText(),
            "tax_exempt":     1 if self.cb_tax_exempt.currentText() == "Yes" else 0,
            "tax_id":         self.ed_tax_id.text().strip(),
            "delivery_notes": self.ed_ship_method.text().strip(),
            "address":        self.ed_bill_street.text().strip(),
            "city":           self.ed_bill_city.text().strip(),
            "state":          self.ed_bill_state.text().strip(),
            "zip":            self.ed_bill_zip.text().strip(),
            "bill_to_address": self.ed_bill_street.text().strip(),
            "bill_to_city":    self.ed_bill_city.text().strip(),
            "bill_to_state":   self.ed_bill_state.text().strip(),
            "bill_to_zip":     self.ed_bill_zip.text().strip(),
            "ship_to_address": self.ed_ship_street.text().strip(),
            "ship_to_city":    self.ed_ship_city.text().strip(),
            "ship_to_state":   self.ed_ship_state.text().strip(),
            "ship_to_zip":     self.ed_ship_zip.text().strip(),
            "notes":          self.ed_notes.toPlainText().strip(),
            "tags":           self.ed_tags.text().strip(),
            "on_hold":        on_hold_val,
            "is_active":      is_active,
        }

    def _on_save(self) -> None:
        data = self._collect_form()
        if not data["company"] and not data["name"]:
            QMessageBox.warning(self, "Save", "Company / name is required.")
            return
        try:
            if self.customer_id is None:
                new_id = db.create_customer(**{
                    k: v for k, v in data.items()
                    if k in {
                        "name", "company", "email", "phone", "address", "city",
                        "state", "zip", "payment_terms", "tax_exempt", "notes",
                        "ship_to_address",
                    }
                })
                self.customer_id = int(new_id)
                # Apply remaining fields the create_customer signature may not accept.
                extras = {k: v for k, v in data.items()
                          if k not in {"name", "company", "email", "phone", "address",
                                       "city", "state", "zip", "payment_terms",
                                       "tax_exempt", "notes", "ship_to_address"}}
                if extras:
                    try:
                        db.update_customer(self.customer_id, **extras)
                    except Exception as exc:
                        log.debug("update_customer extras: %s", exc)
            else:
                db.update_customer(self.customer_id, **data)
        except Exception as exc:
            log.exception("save customer failed")
            QMessageBox.warning(self, "Save", f"Failed to save: {exc}")
            return

        self.customer_saved.emit(int(self.customer_id))
        try:
            from .core_events import core_events
            core_events.cores_changed.emit()
        except Exception:
            pass
        self._load()
        QMessageBox.information(self, "Saved", "Customer saved.")

    def _on_deactivate(self) -> None:
        if self.customer_id is None:
            self.reject(); return
        if QMessageBox.question(self, "Deactivate",
                "Mark this customer inactive?") != QMessageBox.StandardButton.Yes:
            return
        try:
            db.update_customer(self.customer_id, is_active=0)
        except Exception as exc:
            QMessageBox.warning(self, "Deactivate", f"Failed: {exc}")
            return
        self.customer_saved.emit(int(self.customer_id))
        self.accept()

    def _on_duplicate(self) -> None:
        if self.customer_id is None:
            QMessageBox.information(self, "Duplicate", "Save the customer first.")
            return
        try:
            data = self._collect_form()
            data["name"]    = (data.get("name") or "Customer") + " (copy)"
            data["company"] = (data.get("company") or "") + " (copy)"
            new_id = db.create_customer(**{
                k: v for k, v in data.items()
                if k in {
                    "name", "company", "email", "phone", "address", "city",
                    "state", "zip", "payment_terms", "tax_exempt", "notes",
                    "ship_to_address",
                }
            })
        except Exception as exc:
            QMessageBox.warning(self, "Duplicate", f"Failed: {exc}")
            return
        QMessageBox.information(self, "Duplicated",
            f"Created customer #{new_id}.")
        self.customer_saved.emit(int(new_id))

    # ------------------------------------------------------------------
    # External refresh
    # ------------------------------------------------------------------
    def _on_external_refresh(self, *_args, **_kw) -> None:
        if self.customer_id is not None:
            try:
                self._load_sidebar_stats()
                self._load_cores()
                self._load_activity()
            except Exception as exc:
                log.debug("external refresh: %s", exc)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def _short_date(v: Any) -> str:
    if not v:
        return ""
    s = str(v)
    try:
        if "T" in s:
            s = s.split("T", 1)[0]
        if " " in s:
            s = s.split(" ", 1)[0]
        dt = datetime.strptime(s[:10], "%Y-%m-%d")
        return dt.strftime("%b %d, %Y")
    except Exception:
        return s


def _long_date(v: Any) -> str:
    if not v:
        return ""
    s = str(v)
    try:
        s2 = s.replace("T", " ")
        dt = datetime.strptime(s2[:19], "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%b %d, %Y %H:%M")
    except Exception:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
            return dt.strftime("%b %d, %Y")
        except Exception:
            return s
