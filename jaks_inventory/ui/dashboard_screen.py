"""Dashboard Screen - Actionable control center with KPIs and workflow tiles."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from decimal import Decimal

log = logging.getLogger(__name__)

from PySide6.QtCore import Qt, QMargins, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtCharts import QChart, QChartView, QLineSeries, QPieSeries, QBarSeries, QBarSet, QBarCategoryAxis, QValueAxis
    HAS_CHARTS = True
except ImportError:
    HAS_CHARTS = False

from db.inventory import get_dashboard_stats, get_revenue_trend, get_quote_conversion_stats, get_top_customers, get_top_parts
from db import get_setting, set_setting


# ---------------------------------------------------------------------------
# Reusable card widget
# ---------------------------------------------------------------------------

class DashboardCard(QFrame):
    """KPI card with optional action buttons that make the dashboard actionable."""

    clicked = Signal()

    def __init__(
        self,
        title: str,
        value: str = "0",
        subtitle: str = "",
        color: str = "#6B8E23",
        actions: list[tuple[str, str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._color = color
        self._action_key_map: dict[str, QPushButton] = {}
        self.setObjectName("dashboardCard")
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._apply_style(color)
        self.setMinimumSize(180, 130)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(4)

        self._title_label = QLabel(title)
        self._title_label.setStyleSheet("color: #9aa6b2; font-size: 11px; font-weight: 600; letter-spacing:.4px; text-transform:uppercase; background:transparent;")
        layout.addWidget(self._title_label)

        self._value_label = QLabel(value)
        self._value_label.setStyleSheet("color: #e6edf3; font-size: 26px; font-weight: bold; background:transparent;")
        self._value_label.setWordWrap(True)
        layout.addWidget(self._value_label)

        self._subtitle_label = QLabel(subtitle)
        self._subtitle_label.setStyleSheet("color: #8a939d; font-size: 11px; background:transparent;")
        self._subtitle_label.setWordWrap(True)
        layout.addWidget(self._subtitle_label)

        if actions:
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 4, 0, 0)
            btn_row.setSpacing(6)
            for label, key in actions:
                btn = QPushButton(label)
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: transparent; color: {color}; border: 1px solid {color};
                        padding: 4px 10px; border-radius: 3px; font-size: 11px; font-weight: 600;
                    }}
                    QPushButton:hover {{ background: {color}; color: #0f1419; }}
                """)
                btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                self._action_key_map[key] = btn
                btn_row.addWidget(btn)
            btn_row.addStretch()
            layout.addLayout(btn_row)

        layout.addStretch()

    def _apply_style(self, color: str) -> None:
        self.setStyleSheet(f"""
            QFrame#dashboardCard {{
                background-color: #1a2128;
                border: 1px solid #2d3a45;
                border-radius: 8px;
                border-left: 4px solid {color};
            }}
            QFrame#dashboardCard:hover {{
                border: 1px solid {color};
                border-left: 4px solid {color};
                background-color: #232c35;
            }}
        """)

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle_label.setText(subtitle)

    def action_button(self, key: str) -> QPushButton | None:
        return self._action_key_map.get(key)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Activity Feed widget
# ---------------------------------------------------------------------------

class ActivityFeed(QFrame):
    """Recent activity stream."""

    _KIND_COLORS = {
        "quote":      "#bc8cff",
        "invoice":    "#58a6ff",
        "payment":    "#3fb950",
        "po":         "#f0a850",
        "receipt":    "#39d0d8",
        "qbo_error":  "#f85149",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame { background-color: #1a2128; border: 1px solid #2d3a45;
                     border-radius: 8px; }
        """)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        hdr = QLabel("\U0001F552  Recent Activity")
        hdr.setStyleSheet("font-size: 13px; font-weight: 700; color: #e6edf3; letter-spacing:.4px; border:none; background:transparent;")
        layout.addWidget(hdr)

        self._items_layout = QVBoxLayout()
        self._items_layout.setSpacing(4)
        layout.addLayout(self._items_layout)
        layout.addStretch()

    def set_items(self, items: list[dict]) -> None:
        while self._items_layout.count():
            w = self._items_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not items:
            lbl = QLabel("No recent activity")
            lbl.setStyleSheet("color: #8a939d; font-size: 12px; border:none; background:transparent;")
            self._items_layout.addWidget(lbl)
            return
        for item in items:
            kind = item.get("kind", "")
            color = self._KIND_COLORS.get(kind, "#9aa6b2")
            icon = item.get("icon") or "\u2022"
            title = item.get("title") or item.get("msg") or ""
            subtitle = item.get("subtitle") or ""
            ts = item.get("ts") or ""
            ts_short = str(ts)[5:16].replace("T", " ") if ts else ""
            row = QHBoxLayout()
            row.setSpacing(8)
            icon_lbl = QLabel(icon)
            icon_lbl.setStyleSheet(f"color:{color}; font-size:14px; border:none; background:transparent;")
            icon_lbl.setFixedWidth(20)
            row.addWidget(icon_lbl)
            text_lbl = QLabel(
                f"<span style='color:#e6edf3;'>{title}</span>"
                + (f" &nbsp; <span style='color:#8a939d; font-size:11px;'>— {subtitle}</span>" if subtitle else "")
            )
            text_lbl.setTextFormat(Qt.TextFormat.RichText)
            text_lbl.setStyleSheet("font-size:12px; border:none; padding:2px 0; background:transparent;")
            row.addWidget(text_lbl, 1)
            ts_lbl = QLabel(ts_short)
            ts_lbl.setStyleSheet("color:#6b7480; font-size:11px; border:none; background:transparent;")
            row.addWidget(ts_lbl)
            holder = QWidget()
            holder.setLayout(row)
            self._items_layout.addWidget(holder)


# ---------------------------------------------------------------------------
# Chart Widgets (require PySide6.QtCharts)
# ---------------------------------------------------------------------------

class RevenueChart(QFrame):
    """Line chart showing daily revenue + profit trend."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("QFrame { background: #1a2128; border: 1px solid #2d3a45; border-radius: 8px; }")
        self.setMinimumHeight(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)

        hdr = QHBoxLayout()
        hdr.addWidget(self._make_label("📊 Revenue Trend", bold=True))
        hdr.addStretch()
        self._range_combo = QComboBox()
        self._range_combo.addItems(["7 Days", "30 Days", "90 Days"])
        self._range_combo.setCurrentIndex(1)
        self._range_combo.setStyleSheet("QComboBox { padding: 3px 8px; border: 1px solid #2d3a45; background:#0f1419; color:#e6edf3; border-radius: 4px; font-size: 11px; }")
        self._range_combo.currentIndexChanged.connect(lambda: self.refresh())
        hdr.addWidget(self._range_combo)
        lay.addLayout(hdr)

        if HAS_CHARTS:
            self._chart = QChart()
            self._chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
            self._chart.legend().setVisible(True)
            self._chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
            self._chart.setMargins(QMargins(4, 4, 4, 4))
            self._chart.setBackgroundVisible(False)
            self._chart_view = QChartView(self._chart)
            self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._chart_view.setStyleSheet("border: none; background: transparent;")
            lay.addWidget(self._chart_view)
        else:
            lay.addWidget(self._make_label("Charts require PySide6.QtCharts"))

    def _make_label(self, text, bold=False):
        lbl = QLabel(text)
        style = "font-size: 13px; color: #e6edf3; background: transparent; border: none;"
        if bold:
            style += " font-weight: 700;"
        lbl.setStyleSheet(style)
        return lbl

    def refresh(self, data=None):
        if not HAS_CHARTS:
            return
        days_map = {0: 7, 1: 30, 2: 90}
        days = days_map.get(self._range_combo.currentIndex(), 30)
        try:
            trend = data if data else get_revenue_trend(days)
        except Exception as e:
            log.debug("revenue trend load failed: %s", e)
            trend = []

        self._chart.removeAllSeries()
        for ax in self._chart.axes():
            self._chart.removeAxis(ax)

        rev_series = QLineSeries()
        rev_series.setName("Revenue")
        rev_series.setColor(QColor("#3B82F6"))
        profit_series = QLineSeries()
        profit_series.setName("Profit")
        profit_series.setColor(QColor("#10B981"))

        max_val = 1.0
        for i, pt in enumerate(trend):
            rev_series.append(i, pt.get("revenue", 0))
            profit_series.append(i, pt.get("profit", 0))
            max_val = max(max_val, pt.get("revenue", 0), pt.get("profit", 0))

        self._chart.addSeries(rev_series)
        self._chart.addSeries(profit_series)

        ax_x = QValueAxis()
        ax_x.setRange(0, max(len(trend) - 1, 1))
        ax_x.setLabelFormat("%d")
        ax_x.setTickCount(min(len(trend), 8))
        ax_x.setLabelsVisible(False)
        self._chart.addAxis(ax_x, Qt.AlignmentFlag.AlignBottom)
        rev_series.attachAxis(ax_x)
        profit_series.attachAxis(ax_x)

        ax_y = QValueAxis()
        ax_y.setRange(0, max_val * 1.1)
        ax_y.setLabelFormat("$%.0f")
        self._chart.addAxis(ax_y, Qt.AlignmentFlag.AlignLeft)
        rev_series.attachAxis(ax_y)
        profit_series.attachAxis(ax_y)


class ConversionChart(QFrame):
    """Pie chart showing quote status breakdown."""

    _COLORS = {
        "draft": "#9CA3AF", "sent": "#3B82F6", "needs_follow_up": "#F59E0B",
        "accepted": "#10B981", "invoiced": "#8B5CF6", "void": "#EF4444", "expired": "#6B7280",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("QFrame { background: #1a2128; border: 1px solid #2d3a45; border-radius: 8px; }")
        self.setMinimumHeight(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lbl = QLabel("🎯 Quote Conversion")
        lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #e6edf3; background: transparent; border: none;")
        lay.addWidget(lbl)

        if HAS_CHARTS:
            self._chart = QChart()
            self._chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
            self._chart.legend().setAlignment(Qt.AlignmentFlag.AlignRight)
            self._chart.setBackgroundVisible(False)
            self._chart_view = QChartView(self._chart)
            self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._chart_view.setStyleSheet("border: none; background: transparent;")
            lay.addWidget(self._chart_view)
        else:
            lay.addWidget(QLabel("Charts require PySide6.QtCharts"))

    def refresh(self, data=None):
        if not HAS_CHARTS:
            return
        try:
            stats = data if data else get_quote_conversion_stats()
        except Exception as e:
            log.debug("quote conversion stats load failed: %s", e)
            stats = {}

        self._chart.removeAllSeries()
        series = QPieSeries()
        for status, count in stats.items():
            if count > 0:
                sl = series.append(status.replace("_", " ").title(), count)
                sl.setColor(QColor(self._COLORS.get(status, "#6B7280")))
        if series.count() == 0:
            sl = series.append("No Data", 1)
            sl.setColor(QColor("#E5E7EB"))
        self._chart.addSeries(series)


class TopItemsChart(QFrame):
    """Bar chart showing top customers or top parts."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setStyleSheet("QFrame { background: #1a2128; border: 1px solid #2d3a45; border-radius: 8px; }")
        self.setMinimumHeight(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)

        hdr = QHBoxLayout()
        self._title_label = QLabel("🏆 Top Customers")
        self._title_label.setStyleSheet("font-size: 13px; font-weight: 700; color: #e6edf3; background: transparent; border: none;")
        hdr.addWidget(self._title_label)
        hdr.addStretch()
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Top Customers", "Top Parts"])
        self._mode_combo.setStyleSheet("QComboBox { padding: 3px 8px; border: 1px solid #2d3a45; background:#0f1419; color:#e6edf3; border-radius: 4px; font-size: 11px; }")
        self._mode_combo.currentIndexChanged.connect(lambda: self.refresh())
        hdr.addWidget(self._mode_combo)
        lay.addLayout(hdr)

        if HAS_CHARTS:
            self._chart = QChart()
            self._chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
            self._chart.legend().setVisible(False)
            self._chart.setBackgroundVisible(False)
            self._chart_view = QChartView(self._chart)
            self._chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._chart_view.setStyleSheet("border: none; background: transparent;")
            lay.addWidget(self._chart_view)
        else:
            lay.addWidget(QLabel("Charts require PySide6.QtCharts"))

    def refresh(self):
        if not HAS_CHARTS:
            return
        is_customers = self._mode_combo.currentIndex() == 0
        self._title_label.setText("🏆 Top Customers" if is_customers else "🏆 Top Parts")
        try:
            if is_customers:
                items = get_top_customers(5, 30)
                labels = [i["name"][:15] for i in items]
                values = [i["revenue"] for i in items]
            else:
                items = get_top_parts(5, 30)
                labels = [i["sku"][:15] for i in items]
                values = [i["revenue"] for i in items]
        except Exception as e:
            log.debug("top items load failed: %s", e)
            labels, values = [], []

        self._chart.removeAllSeries()
        for ax in self._chart.axes():
            self._chart.removeAxis(ax)

        bar_set = QBarSet("Revenue")
        bar_set.setColor(QColor("#6B8E23"))
        for v in values:
            bar_set.append(v)
        series = QBarSeries()
        series.append(bar_set)
        self._chart.addSeries(series)

        ax_x = QBarCategoryAxis()
        ax_x.append(labels if labels else ["—"])
        self._chart.addAxis(ax_x, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(ax_x)

        ax_y = QValueAxis()
        ax_y.setRange(0, max(values) * 1.15 if values else 100)
        ax_y.setLabelFormat("$%.0f")
        self._chart.addAxis(ax_y, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(ax_y)


# ---------------------------------------------------------------------------
# Dashboard Screen
# ---------------------------------------------------------------------------

class DashboardScreen(QWidget):
    """Main dashboard – command center for a diesel parts counter."""

    navigate_to = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_data)
        self._refresh_timer.start(60000)

        QTimer.singleShot(100, self._refresh_data)

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 16, 20, 16)
        main_layout.setSpacing(14)

        # ── GREETING STRIP ──
        hero = QFrame()
        hero.setStyleSheet(
            "QFrame { background:#1a2128; border:1px solid #2d3a45; border-radius:8px; }"
        )
        hero_lay = QVBoxLayout(hero)
        hero_lay.setContentsMargins(20, 14, 20, 14)
        hero_lay.setSpacing(2)
        self._greeting_label = QLabel(self._greeting_text())
        self._greeting_label.setStyleSheet(
            "color:#e6edf3; font-size:20px; font-weight:700; background:transparent; border:none;"
        )
        hero_lay.addWidget(self._greeting_label)
        self._greeting_sub = QLabel(self._greeting_subtitle())
        self._greeting_sub.setStyleSheet(
            "color:#8a939d; font-size:11px; background:transparent; border:none;"
        )
        hero_lay.addWidget(self._greeting_sub)
        main_layout.addWidget(hero)

        # ── QUICK ACTIONS ROW ──
        actions_row = QHBoxLayout()
        actions_row.setSpacing(10)
        actions = [
            ("\u2795  New Quote", "#f0a850", self._on_new_quote, True),
            ("\U0001F4B5  Quick Sale", "#3fb950", lambda: self.navigate_to.emit("Invoices"), False),
            ("\U0001F4E5  Receive PO", "#58a6ff", lambda: self.navigate_to.emit("PO Receipts"), False),
            ("\U0001F4B0  Record Payment", "#3fb950", lambda: self.navigate_to.emit("Aging AR"), False),
            ("\U0001F50D  Find Part", "#39d0d8", lambda: self.navigate_to.emit("Part Finder"), False),
        ]
        for label, color, slot, primary in actions:
            btn = QPushButton(label)
            if primary:
                btn.setStyleSheet(
                    f"QPushButton {{ background:{color}; color:#0f1419; border:none; padding:10px 18px;"
                    " border-radius:6px; font-weight:700; font-size:12px; }}"
                    f" QPushButton:hover {{ background:#ffb066; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background:transparent; color:{color}; border:1px solid {color};"
                    " padding:10px 18px; border-radius:6px; font-weight:600; font-size:12px; }}"
                    f" QPushButton:hover {{ background:{color}; color:#0f1419; }}"
                )
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.clicked.connect(slot)
            actions_row.addWidget(btn)
        actions_row.addStretch()
        settings_btn = QPushButton("\u2699")
        settings_btn.setToolTip("Show/hide dashboard sections")
        settings_btn.setStyleSheet(
            "QPushButton { background:transparent; color:#8a939d; border:1px solid #2d3a45;"
            " padding:10px 14px; border-radius:6px; font-size:14px; }"
            " QPushButton:hover { color:#f0a850; border-color:#f0a850; }"
        )
        settings_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        settings_btn.clicked.connect(self._on_dashboard_settings)
        actions_row.addWidget(settings_btn)
        refresh_btn = QPushButton("\u21BB")
        refresh_btn.setToolTip("Refresh dashboard")
        refresh_btn.setStyleSheet(settings_btn.styleSheet())
        refresh_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        refresh_btn.clicked.connect(self._refresh_data)
        actions_row.addWidget(refresh_btn)
        main_layout.addLayout(actions_row)

        # Scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(20)

        # Section containers for show/hide via settings
        self._sections: dict[str, QWidget] = {}

        # ================================================================
        # ROW 1 — CRITICAL ALERTS (red/orange — see problems instantly)
        # ================================================================
        sec1 = QWidget()
        sec1_lay = QVBoxLayout(sec1)
        sec1_lay.setContentsMargins(0, 0, 0, 0)
        sec1_lay.setSpacing(8)
        sec1_lay.addWidget(self._section_label("🚨 Critical Alerts"))
        # "All clear" rollup — shown when every alert card is at zero
        self._all_clear_card = QFrame()
        self._all_clear_card.setStyleSheet(
            "QFrame { background:#1a2128; border:1px solid #3fb950; border-left:4px solid #3fb950; border-radius:8px; }"
        )
        ac_lay = QHBoxLayout(self._all_clear_card)
        ac_lay.setContentsMargins(16, 14, 16, 14)
        ac_icon = QLabel("\u2705")
        ac_icon.setStyleSheet("color:#3fb950; font-size:22px; background:transparent; border:none;")
        ac_lay.addWidget(ac_icon)
        ac_text = QLabel(
            "<b style='color:#3fb950;'>All clear.</b> "
            "<span style='color:#8a939d;'>No critical alerts — stock, cores, and invoices are healthy.</span>"
        )
        ac_text.setStyleSheet("font-size:13px; background:transparent; border:none;")
        ac_lay.addWidget(ac_text)
        ac_lay.addStretch()
        self._all_clear_card.setVisible(False)
        sec1_lay.addWidget(self._all_clear_card)
        g1 = QGridLayout(); g1.setSpacing(14)

        self._low_stock_card = DashboardCard(
            "Low Stock Items", "0", "Below reorder point", "#EF4444",
            actions=[("View & Reorder", "view"), ("Auto PO", "auto_po")],
        )
        self._low_stock_card.clicked.connect(lambda: self.navigate_to.emit("Low Stock & Reorder"))
        self._low_stock_card.action_button("view").clicked.connect(
            lambda: self.navigate_to.emit("Low Stock & Reorder"))
        self._low_stock_card.action_button("auto_po").clicked.connect(
            lambda: self.navigate_to.emit("Purchase Orders"))
        g1.addWidget(self._low_stock_card, 0, 0)

        self._out_of_stock_card = DashboardCard(
            "Out of Stock", "0", "Zero quantity on hand", "#DC2626",
            actions=[("Source Now", "source")],
        )
        self._out_of_stock_card.clicked.connect(lambda: self.navigate_to.emit("Low Stock & Reorder"))
        self._out_of_stock_card.action_button("source").clicked.connect(
            lambda: self.navigate_to.emit("Low Stock & Reorder"))
        g1.addWidget(self._out_of_stock_card, 0, 1)

        self._overdue_cores_card = DashboardCard(
            "Overdue Cores", "0", "Core returns past due", "#F59E0B",
            actions=[("View Cores", "view")],
        )
        self._overdue_cores_card.clicked.connect(lambda: self.navigate_to.emit("Cores"))
        self._overdue_cores_card.action_button("view").clicked.connect(
            lambda: self.navigate_to.emit("Cores"))
        g1.addWidget(self._overdue_cores_card, 0, 2)

        self._overdue_invoices_card = DashboardCard(
            "Overdue Invoices", "0", "Past due", "#EF4444",
            actions=[("View Overdue", "view")],
        )
        self._overdue_invoices_card.clicked.connect(lambda: self.navigate_to.emit("Aging AR"))
        self._overdue_invoices_card.action_button("view").clicked.connect(
            lambda: self.navigate_to.emit("Aging AR"))
        g1.addWidget(self._overdue_invoices_card, 0, 3)

        # P0.2 — inventory reservation drift (qty_reserved vs open SO allocations).
        self._inv_drift_card = DashboardCard(
            "Inventory Drift", "0", "Reserved qty mismatches", "#DC2626",
            actions=[("Audit & Fix", "fix")],
        )
        self._inv_drift_card.clicked.connect(self._on_inv_drift_clicked)
        self._inv_drift_card.action_button("fix").clicked.connect(
            self._on_inv_drift_fix)
        g1.addWidget(self._inv_drift_card, 0, 4)

        sec1_lay.addLayout(g1)
        self._sections["critical_alerts"] = sec1
        cl.addWidget(sec1)

        # ================================================================
        # ROW 2 — SALES & PIPELINE (green/blue — what's in motion)
        # ================================================================
        sec2 = QWidget()
        sec2_lay = QVBoxLayout(sec2)
        sec2_lay.setContentsMargins(0, 0, 0, 0)
        sec2_lay.setSpacing(8)
        sec2_lay.addWidget(self._section_label("📈 Sales & Pipeline"))
        g2 = QGridLayout(); g2.setSpacing(14)

        self._quotes_pending_card = DashboardCard(
            "Quotes Pending", "0", "$0 pipeline value", "#8B5CF6",
            actions=[("View All", "follow")],
        )
        self._quotes_pending_card.clicked.connect(lambda: self.navigate_to.emit("Quotes"))
        self._quotes_pending_card.action_button("follow").clicked.connect(
            lambda: self.navigate_to.emit("Quotes"))
        g2.addWidget(self._quotes_pending_card, 0, 0)

        self._follow_up_card = DashboardCard(
            "Needs Follow-up", "0", "Quotes awaiting response", "#EA580C",
            actions=[("Follow Up", "follow")],
        )
        self._follow_up_card.clicked.connect(lambda: self.navigate_to.emit("Quotes"))
        self._follow_up_card.action_button("follow").clicked.connect(
            lambda: self.navigate_to.emit("Quotes"))
        g2.addWidget(self._follow_up_card, 0, 1)

        self._orders_waiting_card = DashboardCard(
            "Open Sales Orders", "0", "Awaiting fulfillment", "#F59E0B",
            actions=[("View Orders", "view")],
        )
        self._orders_waiting_card.clicked.connect(lambda: self.navigate_to.emit("Sales Orders"))
        self._orders_waiting_card.action_button("view").clicked.connect(
            lambda: self.navigate_to.emit("Sales Orders"))
        g2.addWidget(self._orders_waiting_card, 0, 2)

        self._open_invoices_card = DashboardCard(
            "Open Invoices", "0", "Unpaid", "#3B82F6",
            actions=[("View Invoices", "view")],
        )
        self._open_invoices_card.clicked.connect(lambda: self.navigate_to.emit("Invoices"))
        self._open_invoices_card.action_button("view").clicked.connect(
            lambda: self.navigate_to.emit("Invoices"))
        g2.addWidget(self._open_invoices_card, 0, 3)

        self._pending_pos_card = DashboardCard(
            "Pending POs", "0", "Awaiting receipt", "#3B82F6",
            actions=[("Receive Items", "receive")],
        )
        self._pending_pos_card.clicked.connect(lambda: self.navigate_to.emit("Purchase Orders"))
        self._pending_pos_card.action_button("receive").clicked.connect(
            lambda: self.navigate_to.emit("Purchase Orders"))
        g2.addWidget(self._pending_pos_card, 1, 0)

        # Lost-sales awareness (30-day window) — encourages the sales team
        # to review why revenue is leaking so they can respond.
        self._lost_sales_card = DashboardCard(
            "Lost Sales (30d)", "0", "$0 — review reasons", "#B91C1C",
            actions=[("View Report", "view")],
        )
        self._lost_sales_card.clicked.connect(lambda: self.navigate_to.emit("Lost Sales"))
        self._lost_sales_card.action_button("view").clicked.connect(
            lambda: self.navigate_to.emit("Lost Sales"))
        g2.addWidget(self._lost_sales_card, 1, 1)

        sec2_lay.addLayout(g2)
        self._sections["sales_pipeline"] = sec2
        cl.addWidget(sec2)

        # ================================================================
        # ROW 2b — CHARTS (Revenue Trend, Quote Conversion, Top Items)
        # ================================================================
        sec_charts = QWidget()
        sec_charts_lay = QVBoxLayout(sec_charts)
        sec_charts_lay.setContentsMargins(0, 0, 0, 0)
        sec_charts_lay.setSpacing(8)
        sec_charts_lay.addWidget(self._section_label("📊 Analytics"))
        charts_row = QHBoxLayout()
        charts_row.setSpacing(14)

        self._revenue_chart = RevenueChart()
        charts_row.addWidget(self._revenue_chart, 2)
        self._conversion_chart = ConversionChart()
        charts_row.addWidget(self._conversion_chart, 1)
        self._top_items_chart = TopItemsChart()
        charts_row.addWidget(self._top_items_chart, 1)

        sec_charts_lay.addLayout(charts_row)
        self._sections["charts"] = sec_charts
        cl.addWidget(sec_charts)

        # ================================================================
        # ROW 3 — TODAY'S FINANCIALS (margins front and center)
        # ================================================================
        sec3 = QWidget()
        sec3_lay = QVBoxLayout(sec3)
        sec3_lay.setContentsMargins(0, 0, 0, 0)
        sec3_lay.setSpacing(8)
        sec3_lay.addWidget(self._section_label("💰 Today's Revenue & Margins"))
        g3 = QGridLayout(); g3.setSpacing(14)

        self._today_revenue_card = DashboardCard(
            "Today's Revenue", "$0", "Invoiced today", "#10B981")
        g3.addWidget(self._today_revenue_card, 0, 0)

        self._today_profit_card = DashboardCard(
            "Gross Profit Today", "$0", "0% margin", "#059669")
        g3.addWidget(self._today_profit_card, 0, 1)

        self._week_revenue_card = DashboardCard(
            "This Week", "$0", "Last 7 days", "#059669")
        g3.addWidget(self._week_revenue_card, 0, 2)

        self._week_margin_card = DashboardCard(
            "Avg Margin (Week)", "0%", "Target: 30%+", "#10B981",
            actions=[("View Margins", "view")],
        )
        self._week_margin_card.clicked.connect(lambda: self.navigate_to.emit("Margins"))
        self._week_margin_card.action_button("view").clicked.connect(
            lambda: self.navigate_to.emit("Margins"))
        g3.addWidget(self._week_margin_card, 0, 3)

        # NEW: second row of financial KPIs
        self._conversion_rate_card = DashboardCard(
            "Conversion %", "0%", "Quotes → Orders (30d)", "#8B5CF6")
        self._conversion_rate_card.clicked.connect(lambda: self.navigate_to.emit("Quotes"))
        g3.addWidget(self._conversion_rate_card, 1, 0)

        self._avg_order_card = DashboardCard(
            "Avg Order Value", "$0", "Last 30 days", "#3B82F6")
        g3.addWidget(self._avg_order_card, 1, 1)

        self._overdue_cores_value_card = DashboardCard(
            "Overdue Cores $", "$0", "Outstanding core value", "#F59E0B",
            actions=[("View Cores", "view")],
        )
        self._overdue_cores_value_card.clicked.connect(lambda: self.navigate_to.emit("Cores"))
        self._overdue_cores_value_card.action_button("view").clicked.connect(
            lambda: self.navigate_to.emit("Cores"))
        g3.addWidget(self._overdue_cores_value_card, 1, 2)

        self._vendor_cores_card = DashboardCard(
            "Vendor Cores Owed", "$0", "Deposits at stake on POs", "#7C3AED",
            actions=[("View POs", "view")],
        )
        self._vendor_cores_card.clicked.connect(lambda: self.navigate_to.emit("Purchase Orders"))
        self._vendor_cores_card.action_button("view").clicked.connect(
            lambda: self.navigate_to.emit("Purchase Orders"))
        g3.addWidget(self._vendor_cores_card, 2, 0)

        self._month_profit_card = DashboardCard(
            "Profit This Month", "$0", "Gross profit MTD", "#059669")
        g3.addWidget(self._month_profit_card, 1, 3)

        sec3_lay.addLayout(g3)
        self._sections["revenue_margins"] = sec3
        cl.addWidget(sec3)

        # ================================================================
        # ROW 4 — INVENTORY HEALTH
        # ================================================================
        sec4 = QWidget()
        sec4_lay = QVBoxLayout(sec4)
        sec4_lay.setContentsMargins(0, 0, 0, 0)
        sec4_lay.setSpacing(8)
        sec4_lay.addWidget(self._section_label("📦 Inventory Health"))
        g4 = QGridLayout(); g4.setSpacing(14)

        self._total_products_card = DashboardCard(
            "Active SKUs", "0", "Total products", "#6B8E23")
        self._total_products_card.clicked.connect(lambda: self.navigate_to.emit("Products"))
        g4.addWidget(self._total_products_card, 0, 0)

        self._total_qty_card = DashboardCard(
            "Total Quantity", "0", "Units in stock", "#6B8E23")
        g4.addWidget(self._total_qty_card, 0, 1)

        self._inventory_value_card = DashboardCard(
            "Inventory Value", "$0", "At cost", "#6B8E23")
        g4.addWidget(self._inventory_value_card, 0, 2)

        self._retail_value_card = DashboardCard(
            "Retail Value", "$0", "At selling price", "#6B8E23")
        g4.addWidget(self._retail_value_card, 0, 3)

        # Second row: fast movers + dead stock
        self._fast_movers_card = DashboardCard(
            "Fast Movers", "0", "5+ sold in 30 days", "#10B981",
            actions=[("View Items", "view")],
        )
        self._fast_movers_card.clicked.connect(lambda: self.navigate_to.emit("Products"))
        self._fast_movers_card.action_button("view").clicked.connect(
            lambda: self.navigate_to.emit("Products"))
        g4.addWidget(self._fast_movers_card, 1, 0)

        self._dead_stock_card = DashboardCard(
            "Dead Inventory", "0", "No sales in 90 days", "#9CA3AF",
            actions=[("View Items", "view")],
        )
        self._dead_stock_card.clicked.connect(lambda: self.navigate_to.emit("Products"))
        self._dead_stock_card.action_button("view").clicked.connect(
            lambda: self.navigate_to.emit("Products"))
        g4.addWidget(self._dead_stock_card, 1, 1)

        sec4_lay.addLayout(g4)
        self._sections["inventory_health"] = sec4
        cl.addWidget(sec4)

        # ================================================================
        # BOTTOM — ACTIVITY FEED
        # ================================================================
        sec5 = QWidget()
        sec5_lay = QVBoxLayout(sec5)
        sec5_lay.setContentsMargins(0, 0, 0, 0)
        self._activity_feed = ActivityFeed()
        sec5_lay.addWidget(self._activity_feed)
        self._sections["activity_feed"] = sec5
        cl.addWidget(sec5)

        cl.addStretch()
        scroll.setWidget(content)
        main_layout.addWidget(scroll)

        # Apply saved visibility
        self._apply_section_visibility()

    # ------------------------------------------------------------------
    # Section keys → display names
    # ------------------------------------------------------------------
    _SECTION_DISPLAY = {
        "critical_alerts": "🚨 Critical Alerts",
        "sales_pipeline": "📈 Sales & Pipeline",
        "charts": "📊 Analytics Charts",
        "revenue_margins": "💰 Today's Revenue & Margins",
        "inventory_health": "📦 Inventory Health",
        "activity_feed": "📋 Recent Activity",
    }

    def _get_hidden_sections(self) -> set[str]:
        raw = get_setting("dashboard_hidden_sections", "[]")
        try:
            return set(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return set()

    def _apply_section_visibility(self) -> None:
        hidden = self._get_hidden_sections()
        for key, widget in self._sections.items():
            widget.setVisible(key not in hidden)

    def _on_dashboard_settings(self) -> None:
        hidden = self._get_hidden_sections()

        dlg = QDialog(self)
        dlg.setWindowTitle("Dashboard Settings")
        dlg.setMinimumWidth(340)
        lay = QVBoxLayout(dlg)
        lay.setSpacing(12)

        lbl = QLabel("Choose which sections to show:")
        lbl.setStyleSheet("font-weight: bold; font-size: 12px;")
        lay.addWidget(lbl)

        checks: dict[str, QCheckBox] = {}
        for key, display in self._SECTION_DISPLAY.items():
            cb = QCheckBox(display)
            cb.setChecked(key not in hidden)
            cb.setStyleSheet("font-size: 12px; padding: 4px 0;")
            checks[key] = cb
            lay.addWidget(cb)

        lay.addSpacing(8)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("Save")
        save_btn.setStyleSheet(
            "QPushButton { background: #6B8E23; color:white; font-weight:bold; "
            "padding: 6px 20px; border-radius: 4px; border:none; }"
            "QPushButton:hover { background: #556B2F; }"
        )
        save_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("padding: 6px 16px;")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_hidden = [k for k, cb in checks.items() if not cb.isChecked()]
        set_setting("dashboard_hidden_sections", json.dumps(new_hidden))
        self._apply_section_visibility()

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "font-size: 11px; font-weight: 800; color: #8a939d;"
            " letter-spacing: 1px; padding: 4px 0;"
            " background: transparent; border: none;"
        )
        return lbl

    @staticmethod
    def _greeting_text() -> str:
        from datetime import datetime
        h = datetime.now().hour
        if h < 12:
            part = "morning"
        elif h < 17:
            part = "afternoon"
        else:
            part = "evening"
        return f"Good {part}, Keith — here's where the shop stands"

    @staticmethod
    def _greeting_subtitle() -> str:
        from datetime import datetime
        return datetime.now().strftime("%A, %B %d \u00b7 Updated %I:%M %p")

    def _on_new_quote(self) -> None:
        """Open a new quote dialog directly from the dashboard."""
        from .quote_dialog import QuoteDialog
        open_new = True
        while open_new:
            open_new = False
            dlg = QuoteDialog(parent=self)
            dlg.exec()
            if dlg._save_and_new:
                open_new = True

    def _refresh_data(self) -> None:
        try:
            stats = get_dashboard_stats()

            # Alerts
            self._low_stock_card.set_value(str(stats.get("low_stock_count", 0)))
            self._out_of_stock_card.set_value(str(stats.get("out_of_stock_count", 0)))
            self._overdue_cores_card.set_value(str(stats.get("overdue_core_count", 0)))
            self._overdue_invoices_card.set_value(str(stats.get("overdue_invoice_count", 0)))
            _alert_total = (
                int(stats.get("low_stock_count", 0) or 0)
                + int(stats.get("out_of_stock_count", 0) or 0)
                + int(stats.get("overdue_core_count", 0) or 0)
                + int(stats.get("overdue_invoice_count", 0) or 0)
            )

            # P0.2 — inventory reservation drift audit.
            try:
                from db import reconcile_qty_reserved
                drift = reconcile_qty_reserved(auto_fix=False)
                self._inv_drift_count = int(drift.get("drift_count", 0))
                self._inv_drift_total = int(drift.get("total_drift", 0))
                self._inv_drift_card.set_value(str(self._inv_drift_count))
                if self._inv_drift_count > 0:
                    self._inv_drift_card.set_subtitle(
                        f"{self._inv_drift_total} unit(s) off — click to fix"
                    )
                else:
                    self._inv_drift_card.set_subtitle("All reservations match ✓")
            except Exception as _drift_err:
                self._inv_drift_card.set_value("?")
                self._inv_drift_card.set_subtitle(f"audit failed: {_drift_err}")

            # Sales pipeline
            qp = stats.get("quotes_pending_count", 0)
            qv = stats.get("quotes_pending_value", 0)
            self._quotes_pending_card.set_value(str(qp))
            self._quotes_pending_card.set_subtitle(f"${qv:,.0f} pipeline value")

            self._follow_up_card.set_value(str(stats.get("quotes_follow_up_count", 0)))

            self._orders_waiting_card.set_value(str(stats.get("orders_waiting_count", 0)))
            self._open_invoices_card.set_value(str(stats.get("open_invoice_count", 0)))
            self._pending_pos_card.set_value(str(stats.get("pending_po_count", 0)))

            # Lost sales — 30-day window
            try:
                from datetime import date, timedelta
                from db.inventory import get_lost_sales_summary
                d_from = (date.today() - timedelta(days=30)).isoformat()
                d_to = date.today().isoformat()
                lost = get_lost_sales_summary(d_from, d_to)
                lost_count = lost.get("count", 0)
                lost_value = lost.get("total_value", 0)
                self._lost_sales_card.set_value(str(lost_count))
                top = lost.get("by_reason") or []
                if lost_count and top:
                    top_reason = top[0].get("reason") or "—"
                    self._lost_sales_card.set_subtitle(
                        f"${lost_value:,.0f} · top: {top_reason}"
                    )
                else:
                    self._lost_sales_card.set_subtitle(
                        f"${lost_value:,.0f} — no lost quotes in 30d"
                        if not lost_count else f"${lost_value:,.0f}"
                    )
            except Exception as e:
                log.debug("lost sales subtitle update failed: %s", e)

            # Financials
            self._today_revenue_card.set_value(f"${stats.get('today_revenue', 0):,.0f}")
            self._today_profit_card.set_value(f"${stats.get('today_gross_profit', 0):,.0f}")
            today_margin = stats.get('today_margin_pct', 0)
            self._today_profit_card.set_subtitle(f"{today_margin:.0f}% margin")
            self._week_revenue_card.set_value(f"${stats.get('week_revenue', 0):,.0f}")
            week_margin = stats.get('week_margin_pct', 0)
            self._week_margin_card.set_value(f"{week_margin:.0f}%")
            if week_margin >= 30:
                self._week_margin_card.set_subtitle("✓ Above target")
            else:
                self._week_margin_card.set_subtitle(f"Target: 30%+ (now {week_margin:.0f}%)")

            # New financial KPIs
            conv = stats.get("conversion_rate", 0)
            self._conversion_rate_card.set_value(f"{conv:.0f}%")
            self._avg_order_card.set_value(f"${stats.get('avg_order_value', 0):,.0f}")
            self._overdue_cores_value_card.set_value(f"${stats.get('overdue_cores_value', 0):,.0f}")
            self._month_profit_card.set_value(f"${stats.get('month_profit', 0):,.0f}")

            # Vendor cores (deposits owed back to us by vendors)
            vc_count = stats.get("vendor_cores_open_count", 0)
            vc_value = stats.get("vendor_cores_open_value", 0)
            vc_overdue = stats.get("vendor_cores_overdue_count", 0)
            self._vendor_cores_card.set_value(f"${vc_value:,.0f}")
            if vc_overdue:
                self._vendor_cores_card.set_subtitle(
                    f"{vc_count} open • {vc_overdue} OVERDUE"
                )
            else:
                self._vendor_cores_card.set_subtitle(f"{vc_count} open core return(s)")

            # Inventory health
            self._total_products_card.set_value(str(stats.get("total_products", 0)))
            self._total_qty_card.set_value(f"{stats.get('total_quantity', 0):,}")
            self._inventory_value_card.set_value(f"${stats.get('inventory_value', 0):,.0f}")
            self._retail_value_card.set_value(f"${stats.get('retail_value', 0):,.0f}")
            rv = stats.get("retail_value", 0)
            iv = stats.get("inventory_value", 0)
            if rv > 0:
                self._retail_value_card.set_subtitle(f"{((rv - iv) / rv * 100):.0f}% markup")

            self._dead_stock_card.set_value(str(stats.get("dead_stock_count", 0)))
            self._fast_movers_card.set_value(str(stats.get("fast_movers_count", 0)))

            # Activity feed — prefer the richer get_recent_events helper.
            try:
                from db import get_recent_events
                feed_items = get_recent_events(limit=20)
            except Exception:
                feed_items = stats.get("recent_activity", []) or []
            self._activity_feed.set_items(feed_items)

            # All-clear rollup: show the green banner when zero alerts,
            # otherwise hide it. (We do not hide the alert cards themselves
            # so the user can still see them at zero — the banner is just an
            # additional, friendlier summary.)
            try:
                self._all_clear_card.setVisible(_alert_total == 0)
            except Exception:
                pass

            # Refresh greeting timestamp
            try:
                self._greeting_label.setText(self._greeting_text())
                self._greeting_sub.setText(self._greeting_subtitle())
            except Exception:
                pass

            # Notify the main window so the status-bar refresh pill resets.
            try:
                w = self.window()
                if hasattr(w, "mark_data_refreshed"):
                    w.mark_data_refreshed()
            except Exception:
                pass

            # Charts
            try:
                self._revenue_chart.refresh()
                self._conversion_chart.refresh()
                self._top_items_chart.refresh()
            except Exception as ce:
                log.warning("Dashboard chart refresh failed: %s", ce)

        except Exception as e:
            log.warning("Dashboard data refresh failed: %s", e)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_data()

    # ------------------------------------------------------------------
    # P0.2 — Inventory drift actions
    # ------------------------------------------------------------------
    def _on_inv_drift_clicked(self) -> None:
        """Show a detail dialog of qty_reserved drifts (read-only)."""
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
            QPushButton, QHBoxLayout, QMessageBox, QHeaderView,
        )
        from db import reconcile_qty_reserved

        drift = reconcile_qty_reserved(auto_fix=False)
        drifts = drift.get("drifts") or []

        dlg = QDialog(self)
        dlg.setWindowTitle("Inventory Reservation Drift")
        dlg.setMinimumSize(720, 420)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(
            f"<b>{len(drifts)} product(s)</b> have a mismatch between "
            f"<code>qty_reserved</code> and the sum of open sales-order "
            f"allocations. Total drift: <b>{drift.get('total_drift', 0)}</b> "
            f"unit(s)."
        ))
        if not drifts:
            lay.addWidget(QLabel("✅ No drift detected."))
        else:
            tbl = QTableWidget(len(drifts), 5)
            tbl.setHorizontalHeaderLabels(
                ["SKU", "Title", "Stored", "Expected", "Delta"]
            )
            tbl.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.Stretch
            )
            tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            tbl.setAlternatingRowColors(True)
            for i, d in enumerate(drifts):
                tbl.setItem(i, 0, QTableWidgetItem(str(d.get("sku") or "")))
                tbl.setItem(i, 1, QTableWidgetItem(str(d.get("title") or "")))
                tbl.setItem(i, 2, QTableWidgetItem(str(d.get("stored", 0))))
                tbl.setItem(i, 3, QTableWidgetItem(str(d.get("expected", 0))))
                delta_item = QTableWidgetItem(f"{d.get('delta', 0):+d}")
                tbl.setItem(i, 4, delta_item)
            tbl.resizeColumnsToContents()
            lay.addWidget(tbl)

        row = QHBoxLayout()
        row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.reject)
        row.addWidget(close_btn)
        if drifts:
            fix_btn = QPushButton(f"Fix All ({len(drifts)})")
            fix_btn.setStyleSheet(
                "QPushButton { background: #DC2626; color: white; "
                "font-weight: bold; padding: 6px 14px; border-radius: 4px; }"
            )

            def _do_fix() -> None:
                if QMessageBox.question(
                    dlg, "Fix Drift",
                    f"Overwrite qty_reserved for {len(drifts)} product(s) "
                    f"with the computed value from open sales orders?\n\n"
                    "This is safe — it just resyncs cached totals.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                ) != QMessageBox.StandardButton.Yes:
                    return
                res = reconcile_qty_reserved(auto_fix=True)
                QMessageBox.information(
                    dlg, "Drift Fixed",
                    f"Updated {res.get('fixed', 0)} product(s).",
                )
                dlg.accept()
                self._refresh_data()

            fix_btn.clicked.connect(_do_fix)
            row.addWidget(fix_btn)
        lay.addLayout(row)
        dlg.exec()

    def _on_inv_drift_fix(self) -> None:
        """One-click auto-fix."""
        from PySide6.QtWidgets import QMessageBox
        from db import reconcile_qty_reserved

        audit = reconcile_qty_reserved(auto_fix=False)
        n = audit.get("drift_count", 0)
        if n == 0:
            QMessageBox.information(
                self, "Inventory Drift", "No drift detected — nothing to fix."
            )
            return
        if QMessageBox.question(
            self, "Fix Drift",
            f"Resync qty_reserved for {n} product(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        ) != QMessageBox.StandardButton.Yes:
            return
        res = reconcile_qty_reserved(auto_fix=True)
        QMessageBox.information(
            self, "Drift Fixed",
            f"Updated {res.get('fixed', 0)} product(s).",
        )
        self._refresh_data()
