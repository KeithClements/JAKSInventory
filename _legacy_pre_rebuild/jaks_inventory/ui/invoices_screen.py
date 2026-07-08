"""Invoices management screen — dark mockup layout."""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFrame,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
    QComboBox,
    QMessageBox,
    QAbstractItemView,
    QLabel,
)
from PySide6.QtGui import QColor, QCursor, QAction
from PySide6.QtWidgets import QMenu

from db import get_all_invoices, get_invoice, update_invoice_status, record_payment, get_customer, finalize_invoice
from core.badges import INVOICE_BADGES, badge_colors
from core.format import format_date, format_money
from .invoice_dialog import InvoiceDialog
from .payment_dialog import PaymentDialog
from ..utils.pdf_invoice import generate_invoice_pdf
from ..utils.notification_service import (
    send_invoice_sms,
    send_invoice_email,
    send_tracking_sms,
    send_tracking_email,
    get_customer_contact,
    email_configured,
)
from ..utils.sms_service import is_configured as sms_configured

# ── Palette (matches Quotes / SO screens) ──────────────────────────────
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

_INV_STATUS_DISPLAY: dict[str, tuple[str, str]] = {
    "draft":    ("● DRAFT",    _TEXT_DIM),
    "sent":     ("✉ SENT",     _BLUE),
    "partial":  ("◐ PARTIAL",  _YELLOW),
    "paid":     ("✓ PAID",     _GREEN),
    "overdue":  ("⚠ OVERDUE",  _RED),
    "void":     ("⊘ VOID",     _TEXT_FAINT),
}


class InvoicesScreen(QWidget):
    """Screen for managing invoices — dark layout matching Quote List."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._load_invoices()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._load_invoices()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(10)

        # ── Header ──
        header = QHBoxLayout()
        title = QLabel("Invoices")
        title.setStyleSheet(
            f"QLabel {{ color: {_TEXT}; font-size: 20px; font-weight: 700; "
            f"background: transparent; }}"
        )
        sub = QLabel("Receivables · Aging · QBO Sync")
        sub.setStyleSheet(
            f"QLabel {{ color: {_TEXT_DIM}; font-size: 12px; "
            f"background: transparent; }}"
        )
        header.addWidget(title)
        header.addSpacing(10)
        header.addWidget(sub)
        header.addStretch()

        self._new_btn = QPushButton("+ New Invoice")
        self._new_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._new_btn.setStyleSheet(
            f"QPushButton {{ background: {_ACCENT}; color: white; font-weight: 700; "
            f"padding: 8px 16px; border-radius: 6px; font-size: 12px; border: none; }}"
            f"QPushButton:hover {{ background: {_ACCENT_DIM}; }}"
        )
        self._new_btn.clicked.connect(self._on_new)
        header.addWidget(self._new_btn)
        layout.addLayout(header)

        # ── KPI strip ──
        layout.addWidget(self._build_kpi_row())

        # ── Needs Attention chips ──
        layout.addWidget(self._build_attention_bar())

        # ── Filter row ──
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(
            "🔍   Search by invoice #, customer, SO/PO, ESN…"
        )
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMaximumWidth(280)
        self._search_edit.setStyleSheet(
            f"QLineEdit {{ background: {_PANEL2}; color: {_TEXT}; "
            f"padding: 7px 10px; border: 1px solid {_BORDER}; "
            f"border-radius: 6px; font-size: 12px; }}"
            f"QLineEdit:focus {{ border-color: {_ACCENT}; }}"
        )
        self._search_edit.textChanged.connect(lambda _: self._load_invoices())
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
        for label in ("All Status", "Draft", "Sent", "Partial",
                      "Paid", "Overdue", "Void"):
            self._status_combo.addItem(label)
        self._status_combo.setStyleSheet(combo_qss)
        self._status_combo.currentTextChanged.connect(self._on_status_filter)
        filter_row.addWidget(self._status_combo)

        self._aging_combo = QComboBox()
        for label in ("All Aging", "Current", "1–30", "31–60",
                      "61–90", "90+ days"):
            self._aging_combo.addItem(label)
        self._aging_combo.setStyleSheet(combo_qss)
        self._aging_combo.currentTextChanged.connect(lambda _: self._load_invoices())
        filter_row.addWidget(self._aging_combo)

        self._rep_combo = QComboBox()
        self._rep_combo.addItem("All Reps", userData=None)
        self._rep_combo.setStyleSheet(combo_qss)
        self._rep_combo.currentIndexChanged.connect(lambda _: self._load_invoices())
        filter_row.addWidget(self._rep_combo)

        self._date_combo = QComboBox()
        for label, days in (
            ("Last 30 days", 30), ("Last 7 days", 7),
            ("Last 90 days", 90), ("This year", 365), ("All time", None),
        ):
            self._date_combo.addItem(label, userData=days)
        self._date_combo.setStyleSheet(combo_qss)
        self._date_combo.currentIndexChanged.connect(lambda _: self._load_invoices())
        filter_row.addWidget(self._date_combo)

        filter_row.addStretch()

        self._view_btn = QPushButton("View / Edit")
        self._view_btn.setEnabled(False)
        self._view_btn.clicked.connect(self._on_view)
        self._view_btn.setStyleSheet(self._action_btn_style(_TEXT))
        filter_row.addWidget(self._view_btn)

        self._finalize_btn = QPushButton("✓ Finalize")
        self._finalize_btn.setEnabled(False)
        self._finalize_btn.setToolTip(
            "Commit this invoice: deducts inventory, materializes core returns "
            "and warranty certificates, locks the lines."
        )
        self._finalize_btn.clicked.connect(self._on_finalize)
        self._finalize_btn.setStyleSheet(self._action_btn_style(_YELLOW))
        filter_row.addWidget(self._finalize_btn)

        self._email_btn = QPushButton("📧 Resend")
        self._email_btn.setEnabled(False)
        self._email_btn.setToolTip("Send invoice or tracking via email")
        self._email_btn.clicked.connect(self._on_send_email)
        self._email_btn.setStyleSheet(self._action_btn_style(_BLUE))
        filter_row.addWidget(self._email_btn)

        self._sms_btn = QPushButton("📱 SMS")
        self._sms_btn.setEnabled(False)
        self._sms_btn.setToolTip("Send invoice or tracking via SMS")
        self._sms_btn.clicked.connect(self._on_send_sms)
        self._sms_btn.setStyleSheet(self._action_btn_style(_PURPLE))
        filter_row.addWidget(self._sms_btn)

        self._pdf_btn = QPushButton("📄 PDF")
        self._pdf_btn.setEnabled(False)
        self._pdf_btn.clicked.connect(self._on_print_pdf)
        self._pdf_btn.setStyleSheet(self._action_btn_style(_TEXT_DIM))
        filter_row.addWidget(self._pdf_btn)

        self._core_return_btn = QPushButton("↩ Core Return")
        self._core_return_btn.setEnabled(False)
        self._core_return_btn.setToolTip(
            "Receive a returned core and issue credit to the customer."
        )
        self._core_return_btn.clicked.connect(self._on_process_core_return)
        self._core_return_btn.setStyleSheet(self._action_btn_style(_TEAL))
        filter_row.addWidget(self._core_return_btn)

        self._void_btn = QPushButton("Void")
        self._void_btn.setEnabled(False)
        self._void_btn.clicked.connect(self._on_void)
        self._void_btn.setStyleSheet(self._action_btn_style(_RED))
        filter_row.addWidget(self._void_btn)

        self._payment_btn = QPushButton("$ Receive Payment")
        self._payment_btn.setEnabled(False)
        self._payment_btn.clicked.connect(self._on_payment)
        self._payment_btn.setStyleSheet(
            f"QPushButton {{ color: white; background: {_GREEN}; border: none; "
            f"padding: 6px 14px; border-radius: 5px; font-size: 12px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: #2ea043; }}"
            f"QPushButton:disabled {{ background: {_PANEL2}; color: {_TEXT_FAINT}; }}"
        )
        filter_row.addWidget(self._payment_btn)

        self._refresh_btn = QPushButton("🔄")
        self._refresh_btn.setToolTip("Refresh")
        self._refresh_btn.setStyleSheet(self._action_btn_style(_TEXT_DIM))
        self._refresh_btn.clicked.connect(self._load_invoices)
        filter_row.addWidget(self._refresh_btn)

        layout.addLayout(filter_row)

        # ── Table ──
        # Columns: 0 ID (hidden) | 1 Invoice # | 2 Customer | 3 SO/PO |
        #          4 Total | 5 Balance | 6 Status | 7 Issued | 8 Due |
        #          9 Aging | 10 QBO | 11 Activity
        self._table = QTableWidget(0, 12)
        self._table.setHorizontalHeaderLabels([
            "ID", "Invoice #", "Customer", "SO / PO",
            "Total", "Balance", "Status", "Issued",
            "Due", "Aging", "QBO", "Activity",
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
        hdr.setMinimumSectionSize(60)
        self._table.setColumnWidth(1, 110)   # Invoice #
        self._table.setColumnWidth(3, 110)   # SO/PO
        self._table.setColumnWidth(4, 100)   # Total
        self._table.setColumnWidth(5, 100)   # Balance
        self._table.setColumnWidth(6, 120)   # Status pill
        self._table.setColumnWidth(7, 90)    # Issued
        self._table.setColumnWidth(8, 90)    # Due
        self._table.setColumnWidth(9, 90)    # Aging
        self._table.setColumnWidth(10, 90)   # QBO
        self._table.setColumnWidth(11, 180)  # Activity

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.doubleClicked.connect(self._on_view)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        layout.addWidget(self._table, 1)

        # ── Summary footer ──
        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet(
            f"QLabel {{ color: {_TEXT_DIM}; font-size: 11px; padding: 4px 2px; "
            f"background: transparent; }}"
        )
        layout.addWidget(self._summary_label)

    # ------------------------------------------------------------------
    # Helpers — KPI strip
    # ------------------------------------------------------------------
    def _build_kpi_row(self) -> QWidget:
        wrap = QFrame()
        wrap.setStyleSheet("QFrame { background: transparent; }")
        grid = QHBoxLayout(wrap)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        self._kpi_cards: dict[str, tuple[QLabel, QLabel, QFrame]] = {}
        cards = [
            ("ar_total",  "A/R Outstanding", _ACCENT),
            ("current",   "Current",         _GREEN),
            ("a30",       "31–60 days",      _YELLOW),
            ("a60",       "61–90 days",      _ACCENT),
            ("a90",       "90+ days",        _RED),
            ("paid_mtd",  "Collected MTD",   _BLUE),
            ("dso",       "DSO",             _PURPLE),
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
    # Helpers — Needs Attention
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
            ("over_90",      "90+ overdue",       _RED),
            ("overdue",      "Overdue",           _YELLOW),
            ("promised",     "Payment promised",  _BLUE),
            ("disputed",     "Disputed",          _RED),
            ("qbo_fail",     "QBO sync failed",   _RED),
            ("partial",      "Partial payment",   _YELLOW),
            ("refund",       "Refund pending",    _PURPLE),
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
    # Helpers — row widgets
    # ------------------------------------------------------------------
    def _customer_cell(self, inv: dict) -> QWidget:
        w = QFrame()
        w.setStyleSheet("QFrame { background: transparent; border: none; }")
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 4, 10, 4)
        v.setSpacing(1)
        name = inv.get("customer_name") or "—"
        company = inv.get("customer_company")
        primary = name + (f"  ·  {company}" if company else "")
        nl = QLabel(primary)
        nl.setStyleSheet(f"QLabel {{ color: {_TEXT}; font-size: 12px; font-weight: 600; "
                         f"background: transparent; border: none; }}")
        v.addWidget(nl)
        bits = []
        for k in ("customer_city", "city"):
            if inv.get(k):
                bits.append(str(inv[k])); break
        for k in ("customer_state", "state"):
            if inv.get(k):
                if bits:
                    bits[-1] = f"{bits[-1]}, {inv[k]}"
                else:
                    bits.append(str(inv[k]))
                break
        for k in ("terms", "customer_terms", "payment_terms"):
            if inv.get(k):
                bits.append(str(inv[k])); break
        sl = QLabel("  ·  ".join(bits))
        sl.setStyleSheet(f"QLabel {{ color: {_TEXT_DIM}; font-size: 10px; "
                         f"background: transparent; border: none; }}")
        v.addWidget(sl)
        return w

    def _status_pill(self, inv: dict, *, computed_status: str | None = None) -> QWidget:
        status = (computed_status or inv.get("status") or "draft").lower()
        text, color = _INV_STATUS_DISPLAY.get(status, (status.upper(), _TEXT_DIM))
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

    def _aging_chip(self, days_overdue: int, balance: float) -> QWidget:
        if balance <= 0:
            label, color = "Paid", _GREEN
        elif days_overdue <= 0:
            label, color = "Current", _GREEN
        elif days_overdue <= 30:
            label, color = f"{days_overdue}d", _YELLOW
        elif days_overdue <= 60:
            label, color = f"{days_overdue}d", _ACCENT
        elif days_overdue <= 90:
            label, color = f"{days_overdue}d", _ACCENT_DIM
        else:
            label, color = f"{days_overdue}d", _RED
        w = QFrame()
        w.setStyleSheet("QFrame { background: transparent; border: none; }")
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 0, 8, 0)
        pill = QLabel(label)
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill.setStyleSheet(
            f"QLabel {{ color: {color}; "
            f"background: rgba(255,255,255,0.06); "
            f"border: 1px solid {color}; border-radius: 8px; "
            f"padding: 2px 8px; font-size: 10px; font-weight: 700; }}"
        )
        h.addWidget(pill)
        h.addStretch()
        return w

    def _qbo_cell(self, inv: dict) -> QWidget:
        synced = bool(inv.get("qbo_invoice_id"))
        failed = bool(inv.get("qbo_sync_error"))
        if failed:
            text, color = "✗ ERR", _RED
        elif synced:
            text, color = "✓ SYNCED", _GREEN
        else:
            text, color = "○ PENDING", _TEXT_DIM
        w = QFrame()
        w.setStyleSheet("QFrame { background: transparent; border: none; }")
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(0)
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"QLabel {{ color: {color}; font-size: 10px; font-weight: 700; "
            f"background: transparent; border: none; }}"
        )
        h.addWidget(lbl)
        h.addStretch()
        return w

    def _activity_cell(self, inv: dict, *, days_overdue: int) -> QWidget:
        status = (inv.get("status") or "draft").lower()
        finalized = bool(inv.get("finalized_at"))
        paid = float(inv.get("amount_paid") or 0) > 0
        synced = bool(inv.get("qbo_invoice_id"))
        sync_err = bool(inv.get("qbo_sync_error"))
        w = QFrame()
        w.setStyleSheet("QFrame { background: transparent; border: none; }")
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(3)
        flags = [
            ("✉", _BLUE,   status in ("sent", "partial", "paid", "overdue")),
            ("✓", _GREEN,  finalized),
            ("$", _GREEN,  paid),
            ("⏰", _YELLOW, days_overdue > 0),
            ("⚠", _RED,    days_overdue > 60 or sync_err),
            ("↻", _PURPLE, synced),
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

    def _sync_filter_choices(self, invoices: list[dict]) -> None:
        current = self._rep_combo.currentData() if hasattr(self, "_rep_combo") else None
        seen: set[str] = set()
        values: list[str] = []
        for inv in invoices:
            for k in ("rep", "sales_rep", "created_by"):
                v = inv.get(k)
                if v and str(v) not in seen:
                    seen.add(str(v)); values.append(str(v)); break
        values.sort(key=str.lower)
        self._rep_combo.blockSignals(True)
        self._rep_combo.clear()
        self._rep_combo.addItem("All Reps", userData=None)
        for v in values:
            self._rep_combo.addItem(v, userData=v)
        if current:
            idx = self._rep_combo.findData(current)
            if idx >= 0:
                self._rep_combo.setCurrentIndex(idx)
        self._rep_combo.blockSignals(False)

    def _load_invoices(self) -> None:
        """Load invoices into the table."""
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)

        status_filter = self._status_combo.currentText().lower()
        if status_filter in ("all status", "all", "overdue"):
            # 'overdue' is a computed flavour — fetch all and filter below.
            db_status = None
        else:
            db_status = status_filter

        invoices = get_all_invoices(status=db_status, limit=500)

        # Date window
        days = self._date_combo.currentData()
        if days:
            cutoff = date.today() - timedelta(days=int(days))
            def _in_window(o):
                v = o.get("invoice_date") or o.get("created_at")
                if not v:
                    return True
                try:
                    d = datetime.fromisoformat(str(v)[:19]).date()
                except Exception:
                    return True
                return d >= cutoff
            invoices = [o for o in invoices if _in_window(o)]

        # Refresh rep dropdown before filtering by it
        self._sync_filter_choices(invoices)

        rep_pick = self._rep_combo.currentData()
        if rep_pick:
            invoices = [o for o in invoices if (o.get("rep") or o.get("sales_rep")
                                                or o.get("created_by") or "") == rep_pick]

        # Search
        search = self._search_edit.text().strip().lower()
        if search:
            invoices = [
                o for o in invoices
                if search in (o.get("invoice_number") or "").lower()
                or search in (o.get("customer_name") or "").lower()
                or search in (o.get("customer_company") or "").lower()
                or search in str(o.get("so_number") or "").lower()
                or search in str(o.get("customer_po") or "").lower()
            ]

        today = date.today()

        # Compute per-invoice days_overdue and effective status for filtering.
        def _days_overdue(inv: dict) -> int:
            due = inv.get("due_date")
            if not due:
                return 0
            try:
                dd = datetime.fromisoformat(str(due)[:10]).date()
            except Exception:
                return 0
            return max(0, (today - dd).days)

        # Aging-bucket filter
        bucket = self._aging_combo.currentText().lower()
        if bucket and bucket not in ("all aging", "all"):
            def _in_bucket(inv):
                bal = float(inv.get("total") or 0) - float(inv.get("amount_paid") or 0)
                if bal <= 0:
                    return False
                d = _days_overdue(inv)
                if "current" in bucket:    return d <= 0
                if "1–30" in bucket:       return 1 <= d <= 30
                if "31–60" in bucket:      return 31 <= d <= 60
                if "61–90" in bucket:      return 61 <= d <= 90
                if "90+" in bucket:        return d > 90
                return True
            invoices = [o for o in invoices if _in_bucket(o)]

        # Status='Overdue' synthetic filter
        if status_filter == "overdue":
            invoices = [o for o in invoices
                        if _days_overdue(o) > 0
                        and (float(o.get("total") or 0)
                             - float(o.get("amount_paid") or 0)) > 0
                        and (o.get("status") or "").lower() != "void"]

        self._table.setRowCount(len(invoices))

        # KPI / chip accumulators
        ar_total = 0.0
        current = 0.0
        bucket30 = bucket60 = bucket90 = 0.0
        paid_mtd = 0.0
        attn = {"over_90": 0, "overdue": 0, "promised": 0, "disputed": 0,
                "qbo_fail": 0, "partial": 0, "refund": 0}
        days_sum = 0; days_count = 0

        def cell(text: str, *, align=None, color=None) -> QTableWidgetItem:
            it = QTableWidgetItem("" if text is None else str(text))
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if align is not None:
                it.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
            if color is not None:
                it.setForeground(QColor(color))
            return it

        for row_idx, inv in enumerate(invoices):
            status = str(inv.get("status") or "draft").lower()
            total = float(inv.get("total") or 0)
            paid = float(inv.get("amount_paid") or 0)
            balance = max(0.0, total - paid)
            d_over = _days_overdue(inv)
            finalized = bool(inv.get("finalized_at"))

            # KPI accumulation
            if status != "void" and balance > 0:
                ar_total += balance
                if d_over <= 0:        current += balance
                elif d_over <= 30:     pass  # 1-30 collapses into Current visualisation
                elif d_over <= 60:     bucket30 += balance
                elif d_over <= 90:     bucket60 += balance
                else:                  bucket90 += balance
                if d_over > 0:         attn["overdue"] += 1
                if d_over > 90:        attn["over_90"] += 1
            try:
                idate = inv.get("invoice_date")
                if idate:
                    di = datetime.fromisoformat(str(idate)[:10]).date()
                    if di.month == today.month and di.year == today.year:
                        paid_mtd += paid
                    if paid > 0 and inv.get("paid_at"):
                        pd = datetime.fromisoformat(str(inv["paid_at"])[:10]).date()
                        days_sum += max(0, (pd - di).days)
                        days_count += 1
            except Exception:
                pass
            if status == "partial":           attn["partial"] += 1
            if inv.get("qbo_sync_error"):     attn["qbo_fail"] += 1
            if inv.get("payment_promised_at"): attn["promised"] += 1
            if inv.get("disputed"):           attn["disputed"] += 1
            if inv.get("refund_pending"):     attn["refund"] += 1

            # Computed display status: promote "sent" to "overdue" when due.
            disp_status = status
            if status in ("sent", "partial") and d_over > 0 and balance > 0:
                disp_status = "overdue"

            # ── Row cells ──
            self._table.setItem(row_idx, 0, cell(inv.get("id")))

            num = cell(inv.get("invoice_number"), color=_ACCENT)
            f = num.font(); f.setBold(True); num.setFont(f)
            self._table.setItem(row_idx, 1, num)

            ci = cell("")
            ci.setData(Qt.ItemDataRole.UserRole, inv.get("customer_name") or "")
            self._table.setItem(row_idx, 2, ci)
            self._table.setCellWidget(row_idx, 2, self._customer_cell(inv))

            so_po = inv.get("so_number") or inv.get("customer_po") or "—"
            self._table.setItem(row_idx, 3, cell(so_po, color=_TEXT_DIM))

            self._table.setItem(row_idx, 4,
                cell(format_money(total), align=Qt.AlignmentFlag.AlignRight))

            bal_color = _GREEN if balance == 0 else (_RED if d_over > 30 else _ACCENT)
            self._table.setItem(row_idx, 5,
                cell(format_money(balance), align=Qt.AlignmentFlag.AlignRight,
                     color=bal_color))

            self._table.setItem(row_idx, 6, cell(""))
            self._table.setCellWidget(row_idx, 6,
                                       self._status_pill(inv, computed_status=disp_status))

            self._table.setItem(row_idx, 7,
                cell(format_date(inv.get("invoice_date")),
                     align=Qt.AlignmentFlag.AlignCenter, color=_TEXT_DIM))

            due_color = _RED if d_over > 0 and balance > 0 else _TEXT
            self._table.setItem(row_idx, 8,
                cell(format_date(inv.get("due_date")),
                     align=Qt.AlignmentFlag.AlignCenter, color=due_color))

            self._table.setItem(row_idx, 9, cell(""))
            self._table.setCellWidget(row_idx, 9, self._aging_chip(d_over, balance))

            self._table.setItem(row_idx, 10, cell(""))
            self._table.setCellWidget(row_idx, 10, self._qbo_cell(inv))

            self._table.setItem(row_idx, 11, cell(""))
            self._table.setCellWidget(row_idx, 11,
                                       self._activity_cell(inv, days_overdue=d_over))

        self._table.setSortingEnabled(True)

        # KPI updates
        def _set_kpi(key: str, val: str, sub: str = "") -> None:
            card = self._kpi_cards.get(key)
            if not card:
                return
            card[0].setText(val); card[1].setText(sub)

        def _fmt(amount: float) -> str:
            if amount >= 1000:
                return f"${amount/1000:,.1f}k"
            return f"${amount:,.0f}"

        _set_kpi("ar_total",  _fmt(ar_total),  f"{len(invoices)} invoices")
        _set_kpi("current",   _fmt(current),   "not yet due")
        _set_kpi("a30",       _fmt(bucket30),  "31–60 days")
        _set_kpi("a60",       _fmt(bucket60),  "61–90 days")
        _set_kpi("a90",       _fmt(bucket90),  "90+ days")
        _set_kpi("paid_mtd",  _fmt(paid_mtd),  "this month")
        dso_val = f"{days_sum/days_count:.0f}d" if days_count else "—"
        _set_kpi("dso",       dso_val,         "avg days to pay")

        for key, count in attn.items():
            self._update_chip(key, count)

        # Footer summary
        self._summary_label.setText(
            f"{len(invoices)} invoices  ·  outstanding {format_money(ar_total)}  ·  "
            f"collected MTD {format_money(paid_mtd)}  ·  overdue {attn['overdue']}"
        )

    def _on_status_filter(self, text: str) -> None:
        """Handle status filter change."""
        self._load_invoices()

    def _on_selection_changed(self) -> None:
        """Update button states based on selection."""
        invoice = self._get_selected_invoice()
        has_selection = invoice is not None
        
        self._view_btn.setEnabled(has_selection)
        self._pdf_btn.setEnabled(has_selection)

        status = str(invoice.get("status") or "").lower() if invoice else ""
        finalized = bool(invoice.get("finalized_at")) if invoice else False

        # Finalize whenever inventory has not been committed yet. Status is
        # payment/customer-facing and must not trap sent/partial invoices.
        can_finalize = has_selection and not finalized and status != "void"
        self._finalize_btn.setEnabled(can_finalize)

        # Payment only after finalization commits inventory/cores/serials.
        can_pay = has_selection and finalized and status in ("sent", "partial")
        self._payment_btn.setEnabled(can_pay)

        # Core return only when this invoice has at least one actionable core
        # (pending or received). Cheap query — single indexed lookup.
        can_core = False
        if has_selection:
            try:
                from db import get_core_returns_for_invoice
                cores = get_core_returns_for_invoice(int(invoice.get("id"))) or []
                can_core = any(
                    str(c.get("status") or "").lower() in ("pending", "received")
                    for c in cores
                )
            except Exception:
                can_core = False
        self._core_return_btn.setEnabled(can_core)

        # Void only for non-void, non-paid
        can_void = has_selection and invoice.get("status") not in ("void", "paid")
        self._void_btn.setEnabled(can_void)

        # SMS / Email for non-draft invoices. Draft preview/print stays in the
        # invoice dialog; list-level notifications should be final/customer docs.
        can_notify = has_selection and finalized and status != "draft"
        self._sms_btn.setEnabled(can_notify)
        self._email_btn.setEnabled(can_notify)

    def _get_selected_invoice_id(self) -> int | None:
        """Get the ID of the selected invoice."""
        selected = self._table.selectedItems()
        if not selected:
            return None
        row = selected[0].row()
        id_item = self._table.item(row, 0)
        return int(id_item.text()) if id_item else None

    def _get_selected_invoice(self) -> dict | None:
        """Get the selected invoice data."""
        inv_id = self._get_selected_invoice_id()
        if not inv_id:
            return None
        return get_invoice(inv_id)

    def _on_new(self) -> None:
        """Create a new invoice."""
        dialog = InvoiceDialog(parent=self)
        if dialog.exec():
            self._load_invoices()

    def _on_view(self) -> None:
        """View/edit selected invoice."""
        invoice_id = self._get_selected_invoice_id()
        if not invoice_id:
            return
        
        dialog = InvoiceDialog(invoice_id=invoice_id, parent=self)
        if dialog.exec():
            self._load_invoices()

    def _on_payment(self) -> None:
        """Record a payment on selected invoice using the full PaymentDialog."""
        invoice = self._get_selected_invoice()
        if not invoice:
            return

        balance = float(invoice.get("balance_due") or 0)
        if balance <= 0:
            # Fall back to total - paid if balance_due not populated
            balance = float(invoice.get("total") or 0) - float(invoice.get("amount_paid") or 0)

        dlg = PaymentDialog(invoice_id=invoice["id"], current_balance=balance, parent=self)
        if dlg.exec():
            self._load_invoices()

    def _on_print_pdf(self) -> None:
        """Generate and open a PDF for the selected invoice."""
        invoice = self._get_selected_invoice()
        if not invoice:
            return
        from ..utils.pdf_helpers import safe_generate_pdf
        pdf_path = safe_generate_pdf(generate_invoice_pdf, invoice, parent=self)
        if pdf_path:
            try:
                self.window().statusBar().showMessage(f"PDF saved: {pdf_path}")
            except Exception:
                pass

    def _on_print_core_receipt(self) -> None:
        """Generate the customer-signable core return receipt for this invoice."""
        invoice = self._get_selected_invoice()
        if not invoice:
            return
        from ..utils.pdf_helpers import safe_generate_pdf
        from ..utils.pdf_core_receipt import generate_core_receipt_pdf
        try:
            pdf_path = safe_generate_pdf(
                generate_core_receipt_pdf, int(invoice["id"]), parent=self,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Core Receipt Failed", str(exc))
            return
        if pdf_path:
            try:
                self.window().statusBar().showMessage(f"Core receipt saved: {pdf_path}")
            except Exception:
                pass

    def _on_process_core_return(self) -> None:
        """Receive a returned core and issue credit, all from the invoices list."""
        invoice = self._get_selected_invoice()
        if not invoice:
            return
        from .core_return_flow import process_core_return_for_invoice
        if process_core_return_for_invoice(int(invoice["id"]), parent=self):
            self._load_invoices()

    def _on_finalize(self) -> None:
        """Commit an unfinalized invoice: deduct stock, create cores + warranty certs."""
        invoice = self._get_selected_invoice()
        if not invoice:
            return
        status = str(invoice.get("status") or "").lower()
        if invoice.get("finalized_at"):
            QMessageBox.information(
                self, "Already Finalized",
                "This invoice has already been finalized.",
            )
            return
        if status == "void":
            QMessageBox.information(
                self, "Cannot Finalize",
                "Voided invoices cannot be finalized.",
            )
            return
        extra = ""
        if status and status != "draft":
            extra = (
                f"\n\nNote: this invoice status is currently '{status}', but "
                "it has not been finalized yet. Finalizing will commit "
                "inventory, cores, serials, and warranties without using "
                "status as the gate."
            )
        reply = QMessageBox.question(
            self, "Finalize Invoice",
            f"Finalize invoice {invoice.get('invoice_number')}?\n\n"
            "This will:\n"
            "  • deduct inventory for every line\n"
            "  • create core-return rows for any core charges\n"
            "  • issue warranty certificates for warrantied parts\n"
            "  • lock the invoice for editing\n\n"
            "This action cannot be undone except by reverting/voiding the invoice."
            f"{extra}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            result = finalize_invoice(int(invoice["id"]))
            if not result.get("success"):
                QMessageBox.critical(
                    self,
                    "Finalize Failed",
                    result.get("error") or "Unknown error.",
                )
                return
        except Exception as exc:
            QMessageBox.critical(self, "Finalize Failed", str(exc))
            return
        try:
            self.window().statusBar().showMessage(
                f"Invoice {invoice.get('invoice_number')} finalized.", 5000,
            )
        except Exception:
            pass
        self._load_invoices()

    def _on_void(self) -> None:
        """Void selected invoice."""
        invoice = self._get_selected_invoice()
        if not invoice:
            return
        
        reply = QMessageBox.question(
            self, "Void Invoice",
            f"Void invoice {invoice.get('invoice_number')}?\n\n"
            "Note: This will NOT reverse inventory deductions.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            update_invoice_status(invoice["id"], "void")
            self._load_invoices()

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------
    def _on_context_menu(self, pos) -> None:
        """Show right-click context menu for the invoice row under the cursor."""
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        # Select the row under the cursor so the helper getters work
        self._table.selectRow(index.row())
        invoice = self._get_selected_invoice()
        if not invoice:
            return

        status = invoice.get("status", "draft")
        menu = QMenu(self)

        act_open = QAction("👁️  Open", self)
        act_open.triggered.connect(self._on_view)
        menu.addAction(act_open)

        act_pdf = QAction("📄  Print PDF", self)
        act_pdf.triggered.connect(self._on_print_pdf)
        menu.addAction(act_pdf)

        # Core return receipt — only enabled when this invoice has linked cores.
        try:
            from db import get_core_returns_for_invoice
            cores_on_inv = get_core_returns_for_invoice(int(invoice.get("id"))) or []
        except Exception:
            cores_on_inv = []
        has_cores = bool(cores_on_inv)
        has_actionable_cores = any(
            str(c.get("status") or "").lower() in ("pending", "received")
            for c in cores_on_inv
        )
        act_core_pdf = QAction("🖨  Print Core Receipt", self)
        act_core_pdf.setEnabled(has_cores)
        act_core_pdf.triggered.connect(self._on_print_core_receipt)
        menu.addAction(act_core_pdf)

        act_core_proc = QAction("↩  Process Core Return…", self)
        act_core_proc.setEnabled(has_actionable_cores)
        act_core_proc.triggered.connect(self._on_process_core_return)
        menu.addAction(act_core_proc)

        menu.addSeparator()

        act_finalize = QAction("✓  Finalize Invoice…", self)
        finalized = bool(invoice.get("finalized_at"))
        act_finalize.setEnabled(not finalized and status != "void")
        act_finalize.triggered.connect(self._on_finalize)
        menu.addAction(act_finalize)

        act_pay = QAction("💵  Record Payment…", self)
        act_pay.setEnabled(finalized and status in ("sent", "partial"))
        act_pay.triggered.connect(self._on_payment)
        menu.addAction(act_pay)

        act_email = QAction("📧  Send Email…", self)
        act_email.setEnabled(finalized and status != "draft")
        act_email.triggered.connect(self._on_send_email)
        menu.addAction(act_email)

        act_sms = QAction("📱  Send SMS…", self)
        act_sms.setEnabled(status != "draft")
        act_sms.triggered.connect(self._on_send_sms)
        menu.addAction(act_sms)

        menu.addSeparator()

        act_void = QAction("❌  Void", self)
        act_void.setEnabled(status not in ("void", "paid"))
        act_void.triggered.connect(self._on_void)
        menu.addAction(act_void)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # SMS / Email notification
    # ------------------------------------------------------------------
    def _on_send_sms(self) -> None:
        """Send invoice or tracking info via SMS."""
        invoice = self._get_selected_invoice()
        if not invoice:
            return

        if not sms_configured():
            QMessageBox.warning(self, "SMS Not Configured",
                                "Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and "
                                "TWILIO_PHONE_NUMBER in your .env file.")
            return

        contact = get_customer_contact(invoice["customer_id"])
        phone = contact["phone"]
        if not phone:
            QMessageBox.warning(self, "No Phone",
                                "This customer has no phone number on file.\n"
                                "Add one via Customers → Customer Maintenance.")
            return

        # Let user choose what to send
        tracking = invoice.get("tracking_number", "")
        choices = ["Invoice Summary"]
        if tracking:
            choices.append(f"Tracking Number ({tracking})")

        from PySide6.QtWidgets import QInputDialog
        choice, ok = QInputDialog.getItem(
            self, "Send SMS",
            f"Sending to {contact['name']} ({phone}):",
            choices, 0, False,
        )
        if not ok:
            return

        if "Tracking" in choice:
            result = send_tracking_sms(invoice, phone)
        else:
            result = send_invoice_sms(invoice, phone)

        if result.get("success"):
            QMessageBox.information(self, "SMS Sent",
                                    f"SMS sent to {phone} successfully.")
        else:
            QMessageBox.critical(self, "SMS Failed", result.get("error", "Unknown error"))

    def _on_send_email(self) -> None:
        """Send invoice or tracking info via email."""
        invoice = self._get_selected_invoice()
        if not invoice:
            return

        if not email_configured():
            QMessageBox.warning(self, "Email Not Configured",
                                "Configure SMTP settings in Settings → Email/SMTP.")
            return

        contact = get_customer_contact(invoice["customer_id"])
        email = contact["email"]
        if not email:
            QMessageBox.warning(self, "No Email",
                                "This customer has no email address on file.\n"
                                "Add one via Customers → Customer Maintenance.")
            return

        tracking = invoice.get("tracking_number", "")
        choices = ["Invoice Summary"]
        if tracking:
            choices.append(f"Tracking Number ({tracking})")

        from PySide6.QtWidgets import QInputDialog
        choice, ok = QInputDialog.getItem(
            self, "Send Email",
            f"Sending to {contact['name']} ({email}):",
            choices, 0, False,
        )
        if not ok:
            return

        from jaks_inventory.utils.email_worker import EmailWorker

        if "Tracking" in choice:
            func = lambda: send_tracking_email(invoice, email)
        else:
            func = lambda: send_invoice_email(invoice, email)

        self._email_btn.setEnabled(False)
        self._email_btn.setText("⏳ Sending…")
        self._email_worker = EmailWorker(func, parent=self)

        def _on_result(result: dict) -> None:
            self._email_btn.setEnabled(True)
            self._email_btn.setText("📧 Send Email")
            if result.get("success"):
                QMessageBox.information(self, "Email Sent",
                                        f"Email sent to {email} successfully.")
            else:
                QMessageBox.critical(self, "Email Failed", result.get("error", "Unknown error"))

        self._email_worker.finished_result.connect(_on_result)
        self._email_worker.start()

    def refresh(self) -> None:
        """Public method to refresh the invoice list."""
        self._load_invoices()
