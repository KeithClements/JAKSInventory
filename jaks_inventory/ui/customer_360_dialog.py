"""Customer 360 Dialog - Unified view of CRM, sales, invoices, deliveries."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import get_customer_360


class Customer360Dialog(QDialog):
    """Full customer profile: contact info + CRM + sales + invoices + deliveries."""

    def __init__(self, customer_id: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Customer 360°")
        self.setMinimumSize(800, 600)

        try:
            self._data = get_customer_360(customer_id)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            self._data = {}

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()

        if not self._data:
            layout.addWidget(QLabel("Customer not found."))
            self.setLayout(layout)
            return

        # Header with name + company
        name = self._data.get("name", "")
        company = self._data.get("company", "")
        title = company if company else name
        if company and name:
            title = f"{company} — {name}"

        header = QLabel(f"👤 {title}")
        header.setStyleSheet("font-size: 16pt; font-weight: bold; padding: 6px;")
        layout.addWidget(header)

        # Contact + Revenue KPIs
        info_row = QHBoxLayout()

        # Contact card
        contact_frame = QFrame()
        contact_frame.setFrameShape(QFrame.Shape.StyledPanel)
        contact_frame.setStyleSheet("padding: 8px; background: #f5f5f5; border-radius: 6px;")
        contact_lay = QVBoxLayout()
        contact_lay.addWidget(QLabel(f"📧 {self._data.get('email', '') or 'N/A'}"))
        contact_lay.addWidget(QLabel(f"📱 {self._data.get('phone', '') or 'N/A'}"))
        addr_parts = []
        for k in ["address", "city", "state", "zip"]:
            v = self._data.get(k)
            if v:
                addr_parts.append(str(v))
        contact_lay.addWidget(QLabel(f"📍 {', '.join(addr_parts) or 'N/A'}"))
        contact_lay.addWidget(QLabel(f"💳 {self._data.get('payment_terms', 'Net 30')}"))
        tax = "Yes" if self._data.get("tax_exempt") else "No"
        contact_lay.addWidget(QLabel(f"Tax Exempt: {tax}"))
        contact_frame.setLayout(contact_lay)
        info_row.addWidget(contact_frame, 1)

        # Revenue card
        revenue_frame = QFrame()
        revenue_frame.setFrameShape(QFrame.Shape.StyledPanel)
        revenue_frame.setStyleSheet("padding: 8px; background: #3d4a38; border-radius: 6px;")
        rev_lay = QVBoxLayout()
        revenue = self._data.get("total_revenue", 0)
        paid_count = self._data.get("total_invoices_paid", 0)
        so_count = len(self._data.get("sales_orders", []))
        ship_count = len(self._data.get("shipments", []))
        followup_count = len(self._data.get("open_followups", []))

        for label, val, color in [
            ("Revenue", f"${revenue:,.2f}", "#66BB6A"),
            ("Paid Invoices", str(paid_count), "white"),
            ("Sales Orders", str(so_count), "white"),
            ("Shipments", str(ship_count), "#64B5F6"),
            ("Open Follow-ups", str(followup_count), "#FFA726" if followup_count else "white"),
        ]:
            lbl = QLabel(f"{label}: {val}")
            lbl.setStyleSheet(f"color: {color}; font-size: 11pt; font-weight: bold;")
            rev_lay.addWidget(lbl)
        revenue_frame.setLayout(rev_lay)
        info_row.addWidget(revenue_frame, 1)

        layout.addLayout(info_row)

        # Tabbed detail sections
        tabs = QTabWidget()

        # CRM Activities tab
        act_table = self._build_table(
            ["Date", "Type", "Subject", "Follow-up", "Done"],
            self._data.get("activities", []),
            lambda a: [
                str(a.get("created_at", ""))[:16],
                str(a.get("activity_type", "")).upper(),
                str(a.get("subject", "")),
                str(a.get("follow_up_date", "") or ""),
                "✅" if a.get("completed") else "",
            ]
        )
        tabs.addTab(act_table, f"📞 CRM ({len(self._data.get('activities', []))})")

        # Sales Orders tab
        so_table = self._build_table(
            ["SO #", "Status", "Total", "Date"],
            self._data.get("sales_orders", []),
            lambda s: [
                str(s.get("so_number", "")),
                str(s.get("status", "")).upper(),
                f"${float(s.get('total', 0) or 0):,.2f}",
                str(s.get("created_at", ""))[:10],
            ]
        )
        tabs.addTab(so_table, f"📋 Orders ({len(self._data.get('sales_orders', []))})")

        # Invoices tab
        inv_table = self._build_table(
            ["Invoice #", "Status", "Total", "Date"],
            self._data.get("invoices", []),
            lambda i: [
                str(i.get("invoice_number", "")),
                str(i.get("status", "")).upper(),
                f"${float(i.get('total', 0) or 0):,.2f}",
                str(i.get("created_at", ""))[:10],
            ]
        )
        tabs.addTab(inv_table, f"🧾 Invoices ({len(self._data.get('invoices', []))})")

        # Deliveries tab
        ship_table = self._build_table(
            ["Type", "Carrier", "Tracking", "Status", "Ship Date", "ETA", "Delivered"],
            self._data.get("shipments", []),
            lambda s: [
                "📥" if s.get("shipment_type") == "inbound" else "📤",
                str(s.get("carrier", "") or ""),
                str(s.get("tracking_number", "") or ""),
                str(s.get("status", "")).upper(),
                str(s.get("ship_date", "") or ""),
                str(s.get("estimated_arrival", "") or ""),
                str(s.get("actual_arrival", "") or ""),
            ]
        )
        tabs.addTab(ship_table, f"🚚 Deliveries ({len(self._data.get('shipments', []))})")

        # Open Follow-ups tab
        fu_table = self._build_table(
            ["Due Date", "Type", "Subject"],
            self._data.get("open_followups", []),
            lambda f: [
                str(f.get("follow_up_date", "")),
                str(f.get("activity_type", "")).upper(),
                str(f.get("subject", "")),
            ]
        )
        tabs.addTab(fu_table, f"📅 Follow-ups ({len(self._data.get('open_followups', []))})")

        layout.addWidget(tabs, 1)

        # Notes
        notes = self._data.get("notes", "") or ""
        if notes:
            notes_lbl = QLabel(f"📝 Notes: {notes}")
            notes_lbl.setWordWrap(True)
            notes_lbl.setStyleSheet("padding: 6px; background: #FFF9C4; border-radius: 4px;")
            layout.addWidget(notes_lbl)

        self.setLayout(layout)

    def _build_table(self, headers: list, data: list, row_fn) -> QTableWidget:
        """Build a read-only table from a list of dicts."""
        table = QTableWidget(len(data), len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        if headers:
            table.horizontalHeader().setSectionResizeMode(
                len(headers) - 1, QHeaderView.ResizeMode.Stretch)

        for i, item_data in enumerate(data):
            values = row_fn(item_data)
            for j, val in enumerate(values):
                it = QTableWidgetItem(str(val))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(i, j, it)

        table.resizeColumnsToContents()
        return table
