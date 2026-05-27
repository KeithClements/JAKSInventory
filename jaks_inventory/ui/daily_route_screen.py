"""Daily Route Planner - Visit log with auto-populated deliveries and routing."""
from __future__ import annotations

import webbrowser
from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from db import (
    auto_populate_route,
    get_or_create_daily_route,
    get_daily_route,
    add_route_stop,
    update_route_stop,
    complete_route_stop,
    skip_route_stop,
    delete_route_stop,
    complete_daily_route,
    get_route_directions_url,
    get_stop_directions_url,
    get_all_customers,
)


STOP_ICONS = {
    "delivery": "🚚",
    "visit": "👤",
    "pickup": "📦",
    "call": "📞",
    "follow-up": "📋",
}

STATUS_COLORS = {
    "pending": "#FFA726",
    "completed": "#4CAF50",
    "skipped": "#9E9E9E",
}


class _KPICard(QFrame):
    def __init__(self, title: str, color: str = "#3d4a38", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(f"background: {color}; border-radius: 6px; padding: 8px;")
        self._title = QLabel(title)
        self._title.setStyleSheet("color: white; font-size: 10pt;")
        self._value = QLabel("0")
        self._value.setStyleSheet("color: white; font-size: 22pt; font-weight: bold;")
        self._sub = QLabel("")
        self._sub.setStyleSheet("color: #ccc; font-size: 9pt;")
        lay = QVBoxLayout()
        lay.addWidget(self._title)
        lay.addWidget(self._value)
        lay.addWidget(self._sub)
        self.setLayout(lay)

    def set_value(self, val: str, sub: str = ""):
        self._value.setText(str(val))
        self._sub.setText(sub)


class _AddStopDialog(QDialog):
    """Dialog to add a manual stop to the route."""

    def __init__(self, customers: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Route Stop")
        self.setMinimumWidth(440)

        form = QFormLayout()

        self.customer_combo = QComboBox()
        self.customer_combo.setEditable(True)
        self.customer_combo.addItem("(No customer)", 0)
        for c in customers:
            label = str(c.get("name", ""))
            company = c.get("company", "")
            if company:
                label += f" ({company})"
            self.customer_combo.addItem(label, c.get("id", 0))
        self.customer_combo.currentIndexChanged.connect(self._on_customer_changed)
        form.addRow("Customer:", self.customer_combo)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["visit", "delivery", "pickup", "call", "follow-up"])
        form.addRow("Stop Type:", self.type_combo)

        self.purpose_edit = QLineEdit()
        self.purpose_edit.setPlaceholderText("Reason for visit...")
        form.addRow("Purpose:", self.purpose_edit)

        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("Street address")
        form.addRow("Address:", self.address_edit)

        addr_row = QHBoxLayout()
        self.city_edit = QLineEdit()
        self.city_edit.setPlaceholderText("City")
        self.state_edit = QLineEdit()
        self.state_edit.setPlaceholderText("ST")
        self.state_edit.setMaximumWidth(50)
        self.zip_edit = QLineEdit()
        self.zip_edit.setPlaceholderText("ZIP")
        self.zip_edit.setMaximumWidth(80)
        addr_row.addWidget(self.city_edit, 1)
        addr_row.addWidget(self.state_edit)
        addr_row.addWidget(self.zip_edit)
        form.addRow("City/State/ZIP:", addr_row)

        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("hh:mm AP")
        form.addRow("Scheduled:", self.time_edit)

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Notes...")
        self.notes_edit.setMaximumHeight(80)
        form.addRow("Notes:", self.notes_edit)

        btns = QHBoxLayout()
        ok_btn = QPushButton("Add Stop")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(ok_btn)
        btns.addWidget(cancel_btn)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(btns)
        self.setLayout(layout)

    def _on_customer_changed(self, idx):
        """Auto-fill address from customer record."""
        cid = self.customer_combo.currentData()
        if not cid:
            return
        try:
            from db import get_customer
            cust = get_customer(cid)
            if cust:
                self.address_edit.setText(cust.get("address", "") or "")
                self.city_edit.setText(cust.get("city", "") or "")
                self.state_edit.setText(cust.get("state", "") or "")
                self.zip_edit.setText(cust.get("zip", "") or "")
        except Exception:
            pass


class DailyRouteScreen(QWidget):
    """Daily visit log and route planner with auto-populated deliveries."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stops: list[dict] = []
        self._route: dict | None = None
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        header = QLabel("🗺️ Daily Route Planner")
        header.setStyleSheet("font-size: 16pt; font-weight: bold; padding: 8px;")
        layout.addWidget(header)

        # KPIs
        kpi_row = QHBoxLayout()
        self._kpi_total = _KPICard("Total Stops")
        self._kpi_deliveries = _KPICard("Deliveries", "#2196F3")
        self._kpi_visits = _KPICard("Visits", "#4CAF50")
        self._kpi_completed = _KPICard("Completed", "#388E3C")
        self._kpi_remaining = _KPICard("Remaining", "#FF9800")
        for k in [self._kpi_total, self._kpi_deliveries, self._kpi_visits,
                  self._kpi_completed, self._kpi_remaining]:
            kpi_row.addWidget(k)
        layout.addLayout(kpi_row)

        # Date selector + toolbar
        toolbar = QHBoxLayout()

        self._date_edit = QDateEdit()
        self._date_edit.setCalendarPopup(True)
        self._date_edit.setDate(QDate.currentDate())
        self._date_edit.dateChanged.connect(self._refresh)
        toolbar.addWidget(QLabel("📅 Date:"))
        toolbar.addWidget(self._date_edit)

        today_btn = QPushButton("Today")
        today_btn.clicked.connect(self._go_today)
        toolbar.addWidget(today_btn)

        prev_btn = QPushButton("◀")
        prev_btn.setToolTip("Previous day")
        prev_btn.clicked.connect(lambda: self._shift_date(-1))
        next_btn = QPushButton("▶")
        next_btn.setToolTip("Next day")
        next_btn.clicked.connect(lambda: self._shift_date(1))
        toolbar.addWidget(prev_btn)
        toolbar.addWidget(next_btn)

        toolbar.addStretch()

        auto_btn = QPushButton("⚡ Auto-Plan Day")
        auto_btn.setToolTip("Pull today's deliveries + follow-ups into route")
        auto_btn.clicked.connect(self._auto_plan)
        toolbar.addWidget(auto_btn)

        add_btn = QPushButton("➕ Add Stop")
        add_btn.clicked.connect(self._add_stop)
        toolbar.addWidget(add_btn)

        directions_btn = QPushButton("🗺️ Open Route in Maps")
        directions_btn.setToolTip("Open full route directions in Google Maps")
        directions_btn.clicked.connect(self._open_full_route)
        toolbar.addWidget(directions_btn)

        refresh_btn = QPushButton("↻")
        refresh_btn.setToolTip("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        toolbar.addWidget(refresh_btn)

        layout.addLayout(toolbar)

        # Stops table
        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels([
            "ID", "#", "Type", "Customer", "Purpose", "Address",
            "Time", "Status", "Notes"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnHidden(0, True)
        self._table.cellDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._table, 1)

        # Action buttons
        action_row = QHBoxLayout()

        arrive_btn = QPushButton("✅ Arrive / Complete Stop")
        arrive_btn.clicked.connect(self._complete_stop)
        action_row.addWidget(arrive_btn)

        skip_btn = QPushButton("⏭️ Skip Stop")
        skip_btn.clicked.connect(self._skip_stop)
        action_row.addWidget(skip_btn)

        nav_btn = QPushButton("🧭 Directions to Stop")
        nav_btn.setToolTip("Open Google Maps directions to selected stop")
        nav_btn.clicked.connect(self._navigate_to_stop)
        action_row.addWidget(nav_btn)

        move_up_btn = QPushButton("⬆️ Move Up")
        move_up_btn.clicked.connect(lambda: self._move_stop(-1))
        move_down_btn = QPushButton("⬇️ Move Down")
        move_down_btn.clicked.connect(lambda: self._move_stop(1))
        action_row.addWidget(move_up_btn)
        action_row.addWidget(move_down_btn)

        del_btn = QPushButton("🗑️ Remove")
        del_btn.clicked.connect(self._delete_stop)
        action_row.addWidget(del_btn)

        action_row.addStretch()

        done_btn = QPushButton("🏁 Complete Day")
        done_btn.setStyleSheet("background-color: #388E3C; color: white; font-weight: bold; padding: 6px 16px;")
        done_btn.clicked.connect(self._complete_day)
        action_row.addWidget(done_btn)

        layout.addLayout(action_row)
        self.setLayout(layout)

    def _get_selected_date(self) -> str:
        return self._date_edit.date().toString("yyyy-MM-dd")

    def _go_today(self):
        self._date_edit.setDate(QDate.currentDate())

    def _shift_date(self, days: int):
        d = self._date_edit.date().addDays(days)
        self._date_edit.setDate(d)

    def _refresh(self):
        route_date = self._get_selected_date()
        try:
            self._route = get_daily_route(route_date)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            self._route = None

        self._stops = self._route.get("stops", []) if self._route else []
        self._populate_table()
        self._update_kpis()

    def _populate_table(self):
        self._table.setRowCount(len(self._stops))
        for i, stop in enumerate(self._stops):
            stype = str(stop.get("stop_type", "visit"))
            status = str(stop.get("status", "pending"))
            icon = STOP_ICONS.get(stype, "📍")
            status_color = STATUS_COLORS.get(status, "#666")

            def item(text, fg=None, bg=None):
                it = QTableWidgetItem(str(text) if text else "")
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if fg:
                    it.setForeground(QColor(fg))
                if bg:
                    it.setBackground(QColor(bg))
                return it

            self._table.setItem(i, 0, item(stop.get("id", "")))
            self._table.setItem(i, 1, item(stop.get("stop_order", i + 1)))
            self._table.setItem(i, 2, item(f"{icon} {stype.title()}"))

            # Customer name
            cust = str(stop.get("customer_name", "") or "")
            company = stop.get("customer_company", "") or ""
            if company:
                cust = f"{cust} ({company})" if cust else company
            self._table.setItem(i, 3, item(cust))

            self._table.setItem(i, 4, item(stop.get("purpose", "")))

            # Full address
            addr_parts = []
            for key in ["address", "city", "state", "zip"]:
                val = stop.get(key)
                if val:
                    addr_parts.append(str(val))
            self._table.setItem(i, 5, item(", ".join(addr_parts)))

            # Time
            sched = stop.get("scheduled_time", "") or ""
            arrival = stop.get("actual_arrival", "") or ""
            time_str = arrival if arrival else sched
            self._table.setItem(i, 6, item(time_str))

            # Status with color
            status_item = item(status.upper(), fg="white")
            status_item.setBackground(QColor(status_color))
            self._table.setItem(i, 7, status_item)

            self._table.setItem(i, 8, item(stop.get("notes", "") or ""))

        self._table.resizeColumnsToContents()

    def _update_kpis(self):
        total = len(self._stops)
        deliveries = sum(1 for s in self._stops if s.get("stop_type") == "delivery")
        visits = sum(1 for s in self._stops if s.get("stop_type") in ("visit", "follow-up"))
        completed = sum(1 for s in self._stops if s.get("status") == "completed")
        remaining = sum(1 for s in self._stops if s.get("status") == "pending")

        self._kpi_total.set_value(str(total), "stops planned")
        self._kpi_deliveries.set_value(str(deliveries), "packages")
        self._kpi_visits.set_value(str(visits), "customer visits")
        self._kpi_completed.set_value(str(completed), "done")
        self._kpi_remaining.set_value(str(remaining), "to go")

    def _get_selected_stop(self) -> dict | None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._stops):
            return None
        return self._stops[row]

    def _get_selected_stop_id(self) -> int | None:
        stop = self._get_selected_stop()
        if not stop:
            return None
        return stop.get("id")

    def _auto_plan(self):
        """Pull deliveries + follow-ups for the selected date."""
        route_date = self._get_selected_date()
        try:
            added = auto_populate_route(route_date)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        if not added:
            QMessageBox.information(
                self, "Auto-Plan",
                f"No new deliveries or follow-ups found for {route_date}.\n\n"
                "Add outbound shipments with this ship date or schedule\n"
                "CRM follow-ups for this date to auto-populate."
            )
        else:
            lines = [f"Added {len(added)} stop(s):\n"]
            for a in added:
                icon = "🚚" if a["type"] == "delivery" else "👤"
                lines.append(f"  {icon} {a['type'].title()} — {a['customer']}")
            QMessageBox.information(self, "Auto-Plan Complete", "\n".join(lines))

        self._refresh()

    def _add_stop(self):
        """Manually add a stop."""
        try:
            customers = get_all_customers()
        except Exception:
            customers = []

        dlg = _AddStopDialog(customers, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        route_date = self._get_selected_date()
        try:
            route = get_or_create_daily_route(route_date)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        cid = dlg.customer_combo.currentData() or None
        if cid == 0:
            cid = None
        try:
            add_route_stop(
                route_id=route["id"],
                customer_id=cid,
                stop_type=dlg.type_combo.currentText(),
                purpose=dlg.purpose_edit.text().strip() or None,
                address=dlg.address_edit.text().strip() or None,
                city=dlg.city_edit.text().strip() or None,
                state=dlg.state_edit.text().strip() or None,
                zip_code=dlg.zip_edit.text().strip() or None,
                scheduled_time=dlg.time_edit.time().toString("hh:mm"),
                notes=dlg.notes_edit.toPlainText().strip() or None,
            )
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _complete_stop(self):
        stop_id = self._get_selected_stop_id()
        if not stop_id:
            QMessageBox.information(self, "Info", "Select a stop first.")
            return
        try:
            complete_route_stop(stop_id)

            # If this stop has an activity_id, also mark the CRM activity complete
            stop = self._get_selected_stop()
            if stop and stop.get("activity_id"):
                try:
                    from db import complete_activity
                    complete_activity(stop["activity_id"])
                except Exception:
                    pass

            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _skip_stop(self):
        stop_id = self._get_selected_stop_id()
        if not stop_id:
            return
        reply = QMessageBox.question(
            self, "Skip Stop", "Skip this stop? You can add a reason.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                skip_route_stop(stop_id, "Skipped by user")
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _navigate_to_stop(self):
        """Open Google Maps directions to the selected stop."""
        stop = self._get_selected_stop()
        if not stop:
            QMessageBox.information(self, "Info", "Select a stop first.")
            return

        # Build destination address
        parts = []
        for key in ["address", "city", "state", "zip"]:
            val = stop.get(key)
            if val:
                parts.append(str(val))
        dest = ", ".join(parts)
        if not dest:
            QMessageBox.warning(self, "No Address", "This stop has no address set.")
            return

        # Determine origin: previous completed stop or Henderson
        row = self._table.currentRow()
        origin = "Henderson, NV"
        for prev_idx in range(row - 1, -1, -1):
            prev = self._stops[prev_idx]
            if prev.get("status") == "completed":
                prev_parts = []
                for key in ["address", "city", "state", "zip"]:
                    val = prev.get(key)
                    if val:
                        prev_parts.append(str(val))
                if prev_parts:
                    origin = ", ".join(prev_parts)
                break

        try:
            url = get_stop_directions_url(origin, dest)
            webbrowser.open(url)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _open_full_route(self):
        """Open the entire day's route in Google Maps with all stops."""
        if not self._stops:
            QMessageBox.information(self, "Info", "No stops planned for this day.")
            return

        pending_stops = [s for s in self._stops if s.get("status") == "pending"]
        stops_to_route = pending_stops if pending_stops else self._stops

        try:
            url = get_route_directions_url(stops_to_route)
            webbrowser.open(url)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _move_stop(self, direction: int):
        """Move selected stop up (-1) or down (+1)."""
        row = self._table.currentRow()
        if row < 0 or row >= len(self._stops):
            return
        new_row = row + direction
        if new_row < 0 or new_row >= len(self._stops):
            return

        # Swap stop_orders
        stop_ids = [s.get("id") for s in self._stops]
        stop_ids[row], stop_ids[new_row] = stop_ids[new_row], stop_ids[row]

        if self._route:
            try:
                from db import reorder_route_stops
                reorder_route_stops(self._route["id"], stop_ids)
                self._refresh()
                self._table.selectRow(new_row)
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _delete_stop(self):
        stop_id = self._get_selected_stop_id()
        if not stop_id:
            return
        reply = QMessageBox.question(
            self, "Remove Stop", "Remove this stop from the route?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                delete_route_stop(stop_id)
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _complete_day(self):
        """Mark the entire day's route as completed."""
        if not self._route:
            QMessageBox.information(self, "Info", "No route for this day.")
            return

        pending = sum(1 for s in self._stops if s.get("status") == "pending")
        if pending > 0:
            reply = QMessageBox.question(
                self, "Complete Day",
                f"There are {pending} pending stop(s). Complete the day anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        try:
            complete_daily_route(self._route["id"])
            QMessageBox.information(self, "Done", "Day's route marked as completed! 🏁")
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _on_double_click(self, row: int, _col: int):
        """Show stop details on double-click."""
        if row < 0 or row >= len(self._stops):
            return
        stop = self._stops[row]
        addr_parts = []
        for key in ["address", "city", "state", "zip"]:
            val = stop.get(key)
            if val:
                addr_parts.append(str(val))

        cust = str(stop.get("customer_name", "") or "")
        company = stop.get("customer_company", "") or ""
        if company:
            cust = f"{cust} ({company})" if cust else company

        detail = (
            f"Stop #{stop.get('stop_order', '')}\n"
            f"Type: {stop.get('stop_type', '')}\n"
            f"Customer: {cust or 'N/A'}\n"
            f"Phone: {stop.get('customer_phone', '') or 'N/A'}\n"
            f"Purpose: {stop.get('purpose', '') or 'N/A'}\n"
            f"Address: {', '.join(addr_parts) or 'N/A'}\n"
            f"Scheduled: {stop.get('scheduled_time', '') or 'N/A'}\n"
            f"Arrived: {stop.get('actual_arrival', '') or 'N/A'}\n"
            f"Status: {stop.get('status', 'pending')}\n"
            f"Notes: {stop.get('notes', '') or '(none)'}"
        )
        QMessageBox.information(self, "Stop Details", detail)

    def refresh(self):
        """Public refresh method."""
        self._refresh()
