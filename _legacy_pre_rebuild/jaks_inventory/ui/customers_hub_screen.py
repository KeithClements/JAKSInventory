"""Customers hub — consolidates CustomersScreen (operations / AR) and
CustomerListScreen (sales / territory) under a single tabbed view.

Replaces the two-entry CUSTOMERS nav (Customer Maintenance sub-item +
CustomerListScreen overview) with one cohesive screen styled to match
``docs/mockups/customers_screen.html`` and ``customer_detail.html``.

The two child screens are embedded as-is (no rewrite of their toolbars or
data loading); this hub adds a shared dark header with a global count
chip and a footer status strip surfacing portfolio-level KPIs.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QTabWidget,
)

from .customers_screen import CustomersScreen
from .customer_list_screen import CustomerListScreen

try:
    from .theme import DARK as _T
except Exception:  # pragma: no cover — theme is optional at import time
    _T = {
        "bg": "#1a1f24", "panel": "#222a30", "panel_alt": "#1f262c",
        "border": "#2f3942", "text": "#e6e9eb", "text_dim": "#9aa3ad",
        "text_muted": "#6b747e", "accent": "#7c8f4d", "accent_hover": "#8fa55a",
        "success": "#5ed26a", "warning": "#f0b454", "danger": "#e06464",
        "header_bg": "#161b20",
    }


class CustomersHubScreen(QWidget):
    """Consolidated Customers landing screen with tabs + KPI footer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        # Refresh KPI footer shortly after mount so child screens have
        # had a chance to populate their tables.
        QTimer.singleShot(300, self._refresh_kpis)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        # ── Tabs ──
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setStyleSheet(
            f"QTabWidget::pane {{ border: 0; background: {_T['bg']}; }}"
            f"QTabBar {{ background: {_T['bg']}; }}"
            f"QTabBar::tab {{ background: {_T['bg']}; color: {_T['text_dim']}; "
            "padding: 8px 18px; font-size: 11px; font-weight: 600; "
            "text-transform: uppercase; letter-spacing: 0.4px; "
            "border: 0; border-bottom: 2px solid transparent; }"
            f"QTabBar::tab:hover {{ color: {_T['text']}; }}"
            f"QTabBar::tab:selected {{ color: {_T['text']}; "
            f"border-bottom: 2px solid {_T['accent']}; }}"
        )

        self._ops_screen = CustomersScreen(parent=self)
        self._sales_screen = CustomerListScreen(parent=self)

        self._tabs.addTab(self._ops_screen, "⚙  Operations")
        self._tabs.addTab(self._sales_screen, "🗺  Sales & Territory")
        # Forward the overview screen's navigate_to signal up the tree if
        # the parent supports it (main_window expects this on overview
        # screens).
        if hasattr(self._sales_screen, "navigate_to"):
            self._sales_screen.navigate_to.connect(self._bubble_navigate)

        root.addWidget(self._tabs, 1)

        root.addWidget(self._build_footer())

    # ------------------------------------------------------------------
    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(
            f"QFrame {{ background: {_T['header_bg']}; "
            f"border-bottom: 1px solid {_T['border']}; }}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 10, 18, 10)
        lay.setSpacing(12)

        icon = QLabel("👥")
        icon.setStyleSheet("font-size: 18px;")
        lay.addWidget(icon)

        title = QLabel("Customers")
        title.setStyleSheet(
            f"color: {_T['text']}; font-size: 15px; font-weight: 700;"
        )
        lay.addWidget(title)

        self._count_chip = QLabel("…")
        self._count_chip.setStyleSheet(
            f"color: {_T['text_dim']}; background: {_T['panel']}; "
            f"border: 1px solid {_T['border']}; border-radius: 10px; "
            "padding: 2px 10px; font-size: 11px;"
        )
        lay.addWidget(self._count_chip)

        lay.addStretch()

        hint = QLabel(
            "Double-click a row to open · Add / Edit / Statement live in each tab's toolbar"
        )
        hint.setStyleSheet(f"color: {_T['text_muted']}; font-size: 11px;")
        lay.addWidget(hint)

        return bar

    # ------------------------------------------------------------------
    def _build_footer(self) -> QWidget:
        bar = QFrame()
        bar.setStyleSheet(
            f"QFrame {{ background: {_T['header_bg']}; "
            f"border-top: 1px solid {_T['border']}; }}"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 6, 18, 6)
        lay.setSpacing(18)

        # Each stat is (label, value-label-widget, color-key)
        self._stat_recv = self._make_stat(lay, "TOTAL RECEIVABLE", "—", "danger")
        self._add_sep(lay)
        self._stat_pastdue = self._make_stat(lay, "PAST DUE", "—", "danger")
        self._add_sep(lay)
        self._stat_cores = self._make_stat(lay, "OPEN CORES", "—", "warning")
        self._add_sep(lay)
        self._stat_hold = self._make_stat(lay, "ON HOLD", "—", "warning")
        self._add_sep(lay)
        self._stat_util = self._make_stat(lay, "AVG CREDIT UTIL.", "—", "text")

        lay.addStretch()

        self._stat_sync = self._make_stat(lay, "QBO SYNC", "● ready", "success")
        return bar

    def _make_stat(self, lay, label: str, value: str, color_key: str) -> QLabel:
        wrap = QHBoxLayout()
        wrap.setSpacing(6)
        lb = QLabel(label)
        lb.setStyleSheet(
            f"color: {_T['text_muted']}; font-size: 10px; "
            "text-transform: uppercase; letter-spacing: 0.6px;"
        )
        val = QLabel(value)
        color = _T.get(color_key, _T["text"])
        val.setStyleSheet(
            f"color: {color}; font-weight: 700; font-size: 12px;"
        )
        val.setProperty("_color_key", color_key)
        wrap.addWidget(lb)
        wrap.addWidget(val)
        container = QWidget()
        container.setLayout(wrap)
        lay.addWidget(container)
        return val

    def _add_sep(self, lay) -> None:
        sep = QFrame()
        sep.setFixedSize(1, 18)
        sep.setStyleSheet(f"background: {_T['border']};")
        lay.addWidget(sep)

    # ------------------------------------------------------------------
    def _bubble_navigate(self, name: str) -> None:
        """Re-emit child navigate_to calls so MainWindow can route them.

        MainWindow connects ``screen.navigate_to`` on each registered
        screen; since the hub itself isn't connected, we walk up looking
        for a parent that has a ``_navigate_to_screen`` slot.
        """
        try:
            target = self.parent()
            while target is not None and not hasattr(target, "_navigate_to_screen"):
                target = target.parent()
            if target is not None:
                target._navigate_to_screen(name)  # type: ignore[attr-defined]
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _refresh_kpis(self) -> None:
        """Compute and display portfolio-level KPIs in the footer + header."""
        try:
            from db import get_customer_grid_data
            rows = get_customer_grid_data() or []
        except Exception:
            rows = []

        total = len(rows)
        active = sum(1 for r in rows if not r.get("on_hold"))
        on_hold = sum(1 for r in rows if r.get("on_hold"))
        total_bal = sum(float(r.get("balance") or 0) for r in rows)
        past_due = sum(
            1 for r in rows if float(r.get("balance") or 0) > 0
        )
        open_cores = sum(int(r.get("open_core_count") or 0) for r in rows)
        core_val = sum(float(r.get("open_core_value") or 0) for r in rows)
        # Credit utilization: 1 - avail/limit, averaged across customers
        # that have a credit limit > 0.
        utils: list[float] = []
        for r in rows:
            limit = float(
                r.get("eff_credit_limit") or r.get("credit_limit") or 0
            )
            if limit > 0:
                avail = float(r.get("available_credit") or 0)
                utils.append(max(0.0, 1.0 - (avail / limit)))
        avg_util = (sum(utils) / len(utils) * 100) if utils else 0.0

        self._count_chip.setText(
            f"{total} total · {active} active · {on_hold} on hold"
        )
        self._stat_recv.setText(f"${total_bal:,.2f}")
        self._stat_pastdue.setText(
            f"{past_due} customer" + ("s" if past_due != 1 else "")
        )
        self._stat_cores.setText(f"{open_cores} · ${core_val:,.0f}")
        self._stat_hold.setText(str(on_hold))
        self._stat_util.setText(f"{avg_util:.0f}%")

    # ------------------------------------------------------------------
    # MainWindow contract — when the hub is shown as a category overview
    # it may be queried for a navigate_to signal. The CustomerListScreen
    # provides one; we proxy it.
    @property
    def navigate_to(self):  # pragma: no cover — convenience proxy
        return self._sales_screen.navigate_to
