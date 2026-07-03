"""Category Overview Screens – landing page when clicking a sidebar category."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .dashboard_screen import DashboardCard
from db.inventory import get_dashboard_stats


# Card definitions per category.
# Each card: (stat_key, title, subtitle, color, nav_target, fmt)
# fmt: "dollar" | "comma" | "pct" | "count"
_CATEGORY_CARDS: dict[str, dict] = {
    "SALES": {
        "title": "💲 Sales Overview",
        "subtitle": "Sales pipeline & daily activity",
        "cards": [
            ("month_revenue", "Current Month Sales", "Invoiced this month", "#10B981", "Invoices", "dollar"),
            ("missed_customers_count", "Missed Customers", "No invoice in 30 days", "#EF4444", "Customer Maintenance", "count"),
            ("quotes_pending_count", "Open Quotes", "Awaiting approval", "#8B5CF6", "Quotes", "count"),
            ("orders_waiting_count", "Aging Sales Orders", "Awaiting fulfillment", "#F59E0B", "Sales Orders", "count"),
            ("open_invoice_count", "Open Invoices", "Unpaid", "#3B82F6", "Invoices", "count"),
            ("quotes_follow_up_count", "Needs Follow-up", "Quotes needing attention", "#EA580C", "Quotes", "count"),
        ],
    },
    "INVENTORY": {
        "title": "📦 Inventory Overview",
        "subtitle": "Stock levels & health",
        "cards": [
            ("total_products", "Active SKUs", "Total products", "#6B8E23", "Products", "count"),
            ("total_quantity", "Total Quantity", "Units in stock", "#6B8E23", "Products", "comma"),
            ("inventory_value", "Inventory Value", "At cost", "#6B8E23", None, "dollar"),
            ("retail_value", "Retail Value", "At selling price", "#6B8E23", None, "dollar"),
            ("low_stock_count", "Low Stock", "Below reorder point", "#EF4444", "Low Stock & Reorder", "count"),
            ("out_of_stock_count", "Out of Stock", "Zero quantity", "#DC2626", "Low Stock & Reorder", "count"),
            ("fast_movers_count", "Fast Movers", "5+ sold in 30 days", "#10B981", "Products", "count"),
            ("dead_stock_count", "Dead Inventory", "No sales in 90 days", "#9CA3AF", "Products", "count"),
        ],
    },
    "PURCHASING": {
        "title": "🛒 Purchasing Overview",
        "subtitle": "Purchase orders & vendor activity",
        "cards": [
            ("pending_po_count", "Pending POs", "Awaiting receipt", "#3B82F6", "Purchase Orders", "count"),
            ("overdue_core_count", "Overdue Cores", "Past due", "#F59E0B", "Cores", "count"),
            ("low_stock_count", "Low Stock Items", "Need reordering", "#EF4444", "Low Stock & Reorder", "count"),
        ],
    },
    "ACCOUNTING": {
        "title": "💰 Accounting Overview",
        "subtitle": "Revenue, margins & receivables",
        "cards": [
            ("today_revenue", "Today's Revenue", "Invoiced today", "#10B981", "Invoices", "dollar"),
            ("week_revenue", "This Week", "Last 7 days", "#059669", None, "dollar"),
            ("month_revenue", "This Month", "Current month", "#047857", "Invoices", "dollar"),
            ("today_margin_pct", "Today's Margin", "Gross margin %", "#059669", "Margins", "pct"),
            ("week_margin_pct", "Week Margin", "Target: 30%+", "#10B981", "Margins", "pct"),
            ("overdue_invoice_count", "Overdue Invoices", "Past due", "#EF4444", "Aging AR", "count"),
        ],
    },
    "TOOLS": {
        "title": "🔧 Tools Overview",
        "subtitle": "Utilities & integration",
        "cards": [
            ("total_products", "Total Products", "In database", "#6B8E23", "Products", "count"),
        ],
    },
    "SYSTEM": {
        "title": "⚙️ System Overview",
        "subtitle": "Settings & configuration",
        "cards": [],
    },
}


def _format_value(raw, fmt: str) -> str:
    if raw is None:
        raw = 0
    if fmt == "dollar":
        return f"${float(raw):,.0f}"
    if fmt == "comma":
        return f"{int(raw):,}"
    if fmt == "pct":
        return f"{float(raw):.0f}%"
    return str(int(raw))


class CategoryOverviewScreen(QWidget):
    """Generic category landing page showing KPI cards relevant to the category."""

    navigate_to = Signal(str)

    def __init__(self, category_key: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._category_key = category_key
        self._cards: list[tuple[str, DashboardCard, str]] = []
        self._build_ui()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_data)
        self._refresh_timer.start(60_000)
        QTimer.singleShot(200, self._refresh_data)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        config = _CATEGORY_CARDS.get(self._category_key, {})
        card_defs = config.get("cards", [])
        title_text = config.get("title", self._category_key)
        subtitle_text = config.get("subtitle", "")

        main = QVBoxLayout(self)
        main.setContentsMargins(24, 20, 24, 20)
        main.setSpacing(16)

        # ── Header ──
        header = QHBoxLayout()
        title = QLabel(title_text)
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1F2937;")
        header.addWidget(title)
        header.addStretch()
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.setStyleSheet(
            "QPushButton { background-color: #6B8E23; color: white; border: none; "
            "padding: 8px 16px; border-radius: 4px; font-weight: 500; }"
            "QPushButton:hover { background-color: #556B2F; }"
        )
        refresh_btn.clicked.connect(self._refresh_data)
        header.addWidget(refresh_btn)
        main.addLayout(header)

        if subtitle_text:
            sub = QLabel(subtitle_text)
            sub.setStyleSheet("font-size: 13px; color: #6B7280; margin-bottom: 4px;")
            main.addWidget(sub)

        if not card_defs:
            empty = QLabel("No overview data for this section.")
            empty.setStyleSheet("font-size: 14px; color: #9CA3AF; padding: 40px;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            main.addWidget(empty)
            main.addStretch()
            return

        # ── Scrollable card grid ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(20)

        grid = QGridLayout()
        grid.setSpacing(14)

        for i, (stat_key, card_title, card_sub, color, nav_target, fmt) in enumerate(card_defs):
            actions = [("Open →", "open")] if nav_target else None
            card = DashboardCard(card_title, "—", card_sub, color, actions=actions)
            if nav_target:
                card.clicked.connect(lambda nt=nav_target: self.navigate_to.emit(nt))
                if actions:
                    card.action_button("open").clicked.connect(
                        lambda nt=nav_target: self.navigate_to.emit(nt)
                    )
                card.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            grid.addWidget(card, i // 4, i % 4)
            self._cards.append((stat_key, card, fmt))

        cl.addLayout(grid)
        cl.addStretch()
        scroll.setWidget(content)
        main.addWidget(scroll)

    # ------------------------------------------------------------------
    def _refresh_data(self) -> None:
        try:
            stats = get_dashboard_stats()
            for stat_key, card, fmt in self._cards:
                raw = stats.get(stat_key, 0)
                card.set_value(_format_value(raw, fmt))
        except Exception:
            pass
