"""Deliveries & ETA Screen - Track inbound/outbound shipments with ETAs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QDoubleSpinBox,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)
from PySide6.QtCore import QDate

from db import (
    create_shipment, list_shipments, get_shipment, update_shipment,
    confirm_delivery, get_delivery_summary, get_eta_board,
    get_all_customers, get_all_vendors,
)


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


class DeliveriesScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout()

        header = QLabel("🚚 Deliveries & ETA Tracking")
        header.setStyleSheet("font-size: 16pt; font-weight: bold; padding: 8px;")
        layout.addWidget(header)

        # KPIs
        kpi_row = QHBoxLayout()
        self._kpi_inbound = _KPICard("Inbound (to Henderson)")
        self._kpi_outbound = _KPICard("Outbound (to Customer)")
        self._kpi_arriving_today = _KPICard("Arriving Today", "#2d5a2d")
        self._kpi_overdue = _KPICard("⚠ Overdue", "#8B0000")
        self._kpi_total = _KPICard("Total Active")
        for k in [self._kpi_inbound, self._kpi_outbound, self._kpi_arriving_today,
                   self._kpi_overdue, self._kpi_total]:
            kpi_row.addWidget(k)
        layout.addLayout(kpi_row)

        # Toolbar
        toolbar = QHBoxLayout()
        new_in_btn = QPushButton("📥 New Inbound")
        new_in_btn.clicked.connect(lambda: self._new_shipment("inbound"))
        new_out_btn = QPushButton("📤 New Outbound")
        new_out_btn.clicked.connect(lambda: self._new_shipment("outbound"))
        self._type_filter = QComboBox()
        self._type_filter.addItems(["All", "inbound", "outbound"])
        self._type_filter.currentTextChanged.connect(lambda: self._refresh())
        self._status_filter = QComboBox()
        self._status_filter.addItems(["All", "pending", "in_transit", "delivered"])
        self._status_filter.currentTextChanged.connect(lambda: self._refresh())
        toolbar.addWidget(new_in_btn)
        toolbar.addWidget(new_out_btn)
        toolbar.addWidget(QLabel("Type:"))
        toolbar.addWidget(self._type_filter)
        toolbar.addWidget(QLabel("Status:"))
        toolbar.addWidget(self._status_filter)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Shipments table
        self._table = QTableWidget()
        self._table.setColumnCount(10)
        self._table.setHorizontalHeaderLabels([
            "Type", "Carrier", "Tracking #", "From", "To",
            "Ship Date", "ETA", "Actual", "Status", "Freight $"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

        # Actions
        actions = QHBoxLayout()
        self._deliver_btn = QPushButton("✅ Confirm Delivery")
        self._deliver_btn.clicked.connect(self._confirm_delivery)
        self._edit_btn = QPushButton("✏ Edit Shipment")
        self._edit_btn.clicked.connect(self._edit_shipment)
        self._eta_btn = QPushButton("📅 Update ETA")
        self._eta_btn.clicked.connect(self._update_eta)
        actions.addWidget(self._deliver_btn)
        actions.addWidget(self._edit_btn)
        actions.addWidget(self._eta_btn)
        actions.addStretch()
        layout.addLayout(actions)

        # ETA Board
        layout.addWidget(QLabel("📋 Upcoming ETA Board (next 14 days)"))
        self._eta_table = QTableWidget()
        self._eta_table.setColumnCount(7)
        self._eta_table.setHorizontalHeaderLabels([
            "ETA", "Type", "Carrier", "Tracking", "From/To", "SO/PO #", "Status"])
        self._eta_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._eta_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._eta_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._eta_table.setMaximumHeight(200)
        layout.addWidget(self._eta_table)

        self.setLayout(layout)
        self._shipments = []
        self._refresh()

    def _refresh(self):
        self._load_kpis()
        self._load_shipments()
        self._load_eta_board()

    def _load_kpis(self):
        try:
            s = get_delivery_summary()
            self._kpi_inbound.set_value(str(s.get("inbound_transit", 0)), "in transit")
            self._kpi_outbound.set_value(str(s.get("outbound_transit", 0)), "in transit")
            self._kpi_arriving_today.set_value(str(s.get("arriving_today", 0)))
            self._kpi_overdue.set_value(str(s.get("overdue", 0)))
            self._kpi_total.set_value(str(s.get("total_active", 0)))
        except Exception:
            pass

    def _load_shipments(self):
        stype = self._type_filter.currentText()
        sstatus = self._status_filter.currentText()
        try:
            self._shipments = list_shipments(
                shipment_type=None if stype == "All" else stype,
                status=None if sstatus == "All" else sstatus)
        except Exception:
            self._shipments = []
        self._table.setRowCount(len(self._shipments))
        for i, s in enumerate(self._shipments):
            stype_label = "📥 IN" if s.get("shipment_type") == "inbound" else "📤 OUT"
            self._table.setItem(i, 0, QTableWidgetItem(stype_label))
            self._table.setItem(i, 1, QTableWidgetItem(str(s.get("carrier", "") or "")))
            self._table.setItem(i, 2, QTableWidgetItem(str(s.get("tracking_number", "") or "")))
            origin = s.get("origin_address", "") or (s.get("vendor_name", "") or "")
            self._table.setItem(i, 3, QTableWidgetItem(str(origin)))
            dest = s.get("destination_address", "") or (s.get("customer_name", "") or "")
            self._table.setItem(i, 4, QTableWidgetItem(str(dest)))
            self._table.setItem(i, 5, QTableWidgetItem(str(s.get("ship_date", "") or "")))
            eta = str(s.get("estimated_arrival", "") or "")
            self._table.setItem(i, 6, QTableWidgetItem(eta))
            self._table.setItem(i, 7, QTableWidgetItem(str(s.get("actual_arrival", "") or "")))
            status = str(s.get("status", ""))
            item = QTableWidgetItem(status)
            if status == "delivered":
                item.setForeground(Qt.GlobalColor.green)
            elif eta and eta < str(QDate.currentDate().toString("yyyy-MM-dd")) and status == "in_transit":
                item.setForeground(Qt.GlobalColor.red)  # overdue
            self._table.setItem(i, 8, item)
            freight = float(s.get("freight_cost", 0) or 0)
            self._table.setItem(i, 9, QTableWidgetItem(f"${freight:,.2f}"))

    def _load_eta_board(self):
        try:
            board = get_eta_board(14)
        except Exception:
            board = []
        self._eta_table.setRowCount(len(board))
        for i, s in enumerate(board):
            self._eta_table.setItem(i, 0, QTableWidgetItem(str(s.get("estimated_arrival", ""))))
            stype_label = "📥 IN" if s.get("shipment_type") == "inbound" else "📤 OUT"
            self._eta_table.setItem(i, 1, QTableWidgetItem(stype_label))
            self._eta_table.setItem(i, 2, QTableWidgetItem(str(s.get("carrier", "") or "")))
            self._eta_table.setItem(i, 3, QTableWidgetItem(str(s.get("tracking_number", "") or "")))
            if s.get("shipment_type") == "inbound":
                ref = s.get("vendor_name", "") or s.get("origin_address", "")
            else:
                ref = s.get("customer_name", "") or s.get("destination_address", "")
            self._eta_table.setItem(i, 4, QTableWidgetItem(str(ref)))
            ref_num = s.get("so_number", "") or s.get("po_number", "") or ""
            self._eta_table.setItem(i, 5, QTableWidgetItem(str(ref_num)))
            self._eta_table.setItem(i, 6, QTableWidgetItem(str(s.get("status", ""))))

    def _new_shipment(self, shipment_type: str):
        dlg = QDialog(self)
        dlg.setWindowTitle(f"New {'Inbound' if shipment_type == 'inbound' else 'Outbound'} Shipment")
        dlg.setMinimumWidth(450)
        form = QFormLayout()

        carrier_edit = QLineEdit()
        form.addRow("Carrier:", carrier_edit)
        tracking_edit = QLineEdit()
        form.addRow("Tracking #:", tracking_edit)
        service_edit = QLineEdit()
        service_edit.setPlaceholderText("e.g. Ground, 2-Day, LTL")
        form.addRow("Service Level:", service_edit)

        if shipment_type == "inbound":
            origin_edit = QLineEdit()
            origin_edit.setPlaceholderText("Vendor / origin address")
            form.addRow("Origin:", origin_edit)
            dest_edit = QLineEdit("Henderson, NV")
            dest_edit.setReadOnly(True)
            form.addRow("Destination:", dest_edit)
        else:
            origin_edit = QLineEdit("Henderson, NV")
            origin_edit.setReadOnly(True)
            form.addRow("Origin:", origin_edit)
            dest_edit = QLineEdit()
            dest_edit.setPlaceholderText("Customer delivery address")
            form.addRow("Destination:", dest_edit)

        ship_date = QDateEdit(QDate.currentDate())
        ship_date.setCalendarPopup(True)
        form.addRow("Ship Date:", ship_date)
        eta_date = QDateEdit(QDate.currentDate().addDays(5))
        eta_date.setCalendarPopup(True)
        form.addRow("Estimated Arrival:", eta_date)

        weight_spin = QDoubleSpinBox()
        weight_spin.setRange(0, 99999)
        weight_spin.setSuffix(" lbs")
        form.addRow("Weight:", weight_spin)
        freight_cost = QDoubleSpinBox()
        freight_cost.setRange(0, 99999)
        freight_cost.setPrefix("$")
        form.addRow("Freight Cost:", freight_cost)
        freight_charge = QDoubleSpinBox()
        freight_charge.setRange(0, 99999)
        freight_charge.setPrefix("$")
        form.addRow("Freight Charge:", freight_charge)

        btns = QHBoxLayout()
        save_btn = QPushButton("Create Shipment")
        save_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btns.addWidget(save_btn)
        btns.addWidget(cancel_btn)
        lay = QVBoxLayout()
        lay.addLayout(form)
        lay.addLayout(btns)
        dlg.setLayout(lay)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                create_shipment(
                    shipment_type=shipment_type,
                    carrier=carrier_edit.text().strip(),
                    tracking_number=tracking_edit.text().strip(),
                    service_level=service_edit.text().strip(),
                    origin=origin_edit.text().strip(),
                    destination=dest_edit.text().strip(),
                    ship_date=ship_date.date().toString("yyyy-MM-dd"),
                    estimated_arrival=eta_date.date().toString("yyyy-MM-dd"),
                    weight_lbs=weight_spin.value(),
                    freight_cost=freight_cost.value(),
                    freight_charge=freight_charge.value(),
                )
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _confirm_delivery(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._shipments):
            QMessageBox.warning(self, "Error", "Select a shipment first.")
            return
        shipment = self._shipments[row]
        try:
            confirm_delivery(shipment["id"])
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _edit_shipment(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._shipments):
            return
        shipment = self._shipments[row]
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Shipment")
        form = QFormLayout()
        carrier = QLineEdit(str(shipment.get("carrier", "") or ""))
        form.addRow("Carrier:", carrier)
        tracking = QLineEdit(str(shipment.get("tracking_number", "") or ""))
        form.addRow("Tracking #:", tracking)
        status_combo = QComboBox()
        status_combo.addItems(["pending", "in_transit", "delivered"])
        idx = status_combo.findText(str(shipment.get("status", "")))
        if idx >= 0:
            status_combo.setCurrentIndex(idx)
        form.addRow("Status:", status_combo)
        notes = QLineEdit(str(shipment.get("notes", "") or ""))
        form.addRow("Notes:", notes)
        btns = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btns.addWidget(save_btn)
        btns.addWidget(cancel_btn)
        lay = QVBoxLayout()
        lay.addLayout(form)
        lay.addLayout(btns)
        dlg.setLayout(lay)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                update_shipment(shipment["id"],
                    carrier=carrier.text().strip(),
                    tracking_number=tracking.text().strip(),
                    status=status_combo.currentText(),
                    notes=notes.text().strip())
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _update_eta(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._shipments):
            return
        shipment = self._shipments[row]
        dlg = QDialog(self)
        dlg.setWindowTitle("Update ETA")
        form = QFormLayout()
        eta_date = QDateEdit(QDate.currentDate().addDays(3))
        eta_date.setCalendarPopup(True)
        form.addRow("New ETA:", eta_date)
        btns = QHBoxLayout()
        save_btn = QPushButton("Update")
        save_btn.clicked.connect(dlg.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btns.addWidget(save_btn)
        btns.addWidget(cancel_btn)
        lay = QVBoxLayout()
        lay.addLayout(form)
        lay.addLayout(btns)
        dlg.setLayout(lay)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                update_shipment(shipment["id"],
                    estimated_arrival=eta_date.date().toString("yyyy-MM-dd"))
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
