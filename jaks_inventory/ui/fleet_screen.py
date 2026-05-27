"""Fleet / Vehicle Registry Screen - Manage customer vehicles."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db import (
    create_vehicle,
    get_vehicle,
    list_vehicles,
    update_vehicle,
    search_vehicles,
    get_all_customers,
)


class _VehicleDialog(QDialog):
    """Dialog to create/edit a vehicle."""

    def __init__(self, vehicle: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Vehicle" if vehicle else "New Vehicle")
        self.setMinimumWidth(500)

        form = QFormLayout()
        self.customer_combo = QComboBox()
        self.customer_combo.addItem("(none)", None)
        try:
            for c in get_all_customers():
                name = c.get("name", "")
                company = c.get("company", "")
                display = f"{name} — {company}" if company else name
                self.customer_combo.addItem(display, c.get("id"))
        except Exception:
            pass
        form.addRow("Customer:", self.customer_combo)

        self.unit_edit = QLineEdit()
        self.unit_edit.setPlaceholderText("e.g. Unit-101")
        form.addRow("Unit #:", self.unit_edit)

        self.vin_edit = QLineEdit()
        form.addRow("VIN:", self.vin_edit)

        self.year_spin = QSpinBox()
        self.year_spin.setRange(1980, 2040)
        self.year_spin.setValue(2020)
        form.addRow("Year:", self.year_spin)

        self.make_edit = QLineEdit()
        self.make_edit.setPlaceholderText("e.g. Peterbilt, Kenworth, Freightliner")
        form.addRow("Make:", self.make_edit)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("e.g. 389, T680, Cascadia")
        form.addRow("Model:", self.model_edit)

        self.engine_make_edit = QLineEdit()
        self.engine_make_edit.setPlaceholderText("e.g. Cummins, Detroit, CAT")
        form.addRow("Engine Make:", self.engine_make_edit)

        self.engine_model_edit = QLineEdit()
        self.engine_model_edit.setPlaceholderText("e.g. ISX15, DD15, C15")
        form.addRow("Engine Model:", self.engine_model_edit)

        self.esn_edit = QLineEdit()
        form.addRow("ESN:", self.esn_edit)

        self.mileage_spin = QSpinBox()
        self.mileage_spin.setRange(0, 9999999)
        form.addRow("Mileage:", self.mileage_spin)

        self.plate_edit = QLineEdit()
        form.addRow("License Plate:", self.plate_edit)

        self.dot_edit = QLineEdit()
        form.addRow("DOT #:", self.dot_edit)

        self.notes_edit = QTextEdit()
        self.notes_edit.setMaximumHeight(60)
        form.addRow("Notes:", self.notes_edit)

        if vehicle:
            cid = vehicle.get("customer_id")
            if cid:
                idx = self.customer_combo.findData(cid)
                if idx >= 0:
                    self.customer_combo.setCurrentIndex(idx)
            self.unit_edit.setText(str(vehicle.get("unit_number") or ""))
            self.vin_edit.setText(str(vehicle.get("vin") or ""))
            self.year_spin.setValue(int(vehicle.get("year") or 2020))
            self.make_edit.setText(str(vehicle.get("make") or ""))
            self.model_edit.setText(str(vehicle.get("model") or ""))
            self.engine_make_edit.setText(str(vehicle.get("engine_make") or ""))
            self.engine_model_edit.setText(str(vehicle.get("engine_model") or ""))
            self.esn_edit.setText(str(vehicle.get("esn") or ""))
            self.mileage_spin.setValue(int(vehicle.get("mileage") or 0))
            self.plate_edit.setText(str(vehicle.get("license_plate") or ""))
            self.dot_edit.setText(str(vehicle.get("dot_number") or ""))
            self.notes_edit.setText(str(vehicle.get("notes") or ""))

        btns = QHBoxLayout()
        ok = QPushButton("Save")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(ok)
        btns.addWidget(cancel)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(btns)
        self.setLayout(layout)


class FleetScreen(QWidget):
    """Vehicle / fleet registry screen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._vehicles: list[dict] = []

        # Controls
        top = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search by unit #, VIN, make, model, ESN...")
        self._search_input.returnPressed.connect(self._search)
        self._search_btn = QPushButton("🔍 Search")
        self._search_btn.clicked.connect(self._search)
        self._new_btn = QPushButton("➕ New Vehicle")
        self._new_btn.clicked.connect(self._new_vehicle)
        self._refresh_btn = QPushButton("↻ Refresh")
        self._refresh_btn.clicked.connect(self._refresh)
        self._active_only = QCheckBox("Active Only")
        self._active_only.setChecked(True)
        self._active_only.stateChanged.connect(self._refresh)
        top.addWidget(self._search_input, 1)
        top.addWidget(self._search_btn)
        top.addWidget(self._active_only)
        top.addWidget(self._new_btn)
        top.addWidget(self._refresh_btn)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("font-size: 11pt; padding: 4px;")

        # Table
        self._table = QTableWidget(0, 10)
        self._table.setHorizontalHeaderLabels([
            "Unit #", "VIN", "Year", "Make", "Model",
            "Engine", "ESN", "Customer", "Mileage", "Active"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.cellDoubleClicked.connect(self._on_double_click)

        self._edit_btn = QPushButton("✏ Edit Selected")
        self._edit_btn.clicked.connect(self._edit_vehicle)

        layout = QVBoxLayout()
        layout.addLayout(top)
        layout.addWidget(self._count_label)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._edit_btn)
        self.setLayout(layout)

        self._refresh()

    def _refresh(self):
        try:
            self._vehicles = list_vehicles(active_only=self._active_only.isChecked())
        except Exception:
            self._vehicles = []
        self._populate()

    def _search(self):
        query = self._search_input.text().strip()
        if not query:
            self._refresh()
            return
        try:
            self._vehicles = search_vehicles(query)
        except Exception:
            self._vehicles = []
        self._populate()

    def _populate(self):
        self._count_label.setText(f"{len(self._vehicles)} vehicle(s)")
        self._table.setRowCount(len(self._vehicles))
        for i, v in enumerate(self._vehicles):
            def item(text):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._table.setItem(i, 0, item(v.get("unit_number") or "—"))
            self._table.setItem(i, 1, item(v.get("vin") or "—"))
            self._table.setItem(i, 2, item(v.get("year") or "—"))
            self._table.setItem(i, 3, item(v.get("make") or "—"))
            self._table.setItem(i, 4, item(v.get("model") or "—"))
            engine = f"{v.get('engine_make', '')} {v.get('engine_model', '')}".strip()
            self._table.setItem(i, 5, item(engine or "—"))
            self._table.setItem(i, 6, item(v.get("esn") or "—"))
            cust = v.get("customer_name") or v.get("customer_company") or "—"
            self._table.setItem(i, 7, item(cust))
            self._table.setItem(i, 8, item(v.get("mileage") or 0))
            active = "Yes" if v.get("is_active", True) else "No"
            self._table.setItem(i, 9, item(active))
        self._table.resizeColumnsToContents()

    def _on_double_click(self, row: int, _col: int):
        self._edit_at_row(row)

    def _edit_vehicle(self):
        row = self._table.currentRow()
        if row >= 0:
            self._edit_at_row(row)

    def _edit_at_row(self, row: int):
        if row < 0 or row >= len(self._vehicles):
            return
        v = self._vehicles[row]
        vid = v.get("id")
        try:
            full = get_vehicle(vid) or v
        except Exception:
            full = v
        dlg = _VehicleDialog(full, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                update_vehicle(
                    vid,
                    customer_id=dlg.customer_combo.currentData(),
                    unit_number=dlg.unit_edit.text().strip() or None,
                    vin=dlg.vin_edit.text().strip() or None,
                    year=dlg.year_spin.value(),
                    make=dlg.make_edit.text().strip() or None,
                    model=dlg.model_edit.text().strip() or None,
                    engine_make=dlg.engine_make_edit.text().strip() or None,
                    engine_model=dlg.engine_model_edit.text().strip() or None,
                    esn=dlg.esn_edit.text().strip() or None,
                    mileage=dlg.mileage_spin.value(),
                    license_plate=dlg.plate_edit.text().strip() or None,
                    dot_number=dlg.dot_edit.text().strip() or None,
                    notes=dlg.notes_edit.toPlainText().strip() or None,
                )
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _new_vehicle(self):
        dlg = _VehicleDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                create_vehicle(
                    customer_id=dlg.customer_combo.currentData(),
                    unit_number=dlg.unit_edit.text().strip() or None,
                    vin=dlg.vin_edit.text().strip() or None,
                    year=dlg.year_spin.value(),
                    make=dlg.make_edit.text().strip() or None,
                    model=dlg.model_edit.text().strip() or None,
                    engine_make=dlg.engine_make_edit.text().strip() or None,
                    engine_model=dlg.engine_model_edit.text().strip() or None,
                    esn=dlg.esn_edit.text().strip() or None,
                    mileage=dlg.mileage_spin.value(),
                    license_plate=dlg.plate_edit.text().strip() or None,
                    dot_number=dlg.dot_edit.text().strip() or None,
                    notes=dlg.notes_edit.toPlainText().strip() or None,
                )
                QMessageBox.information(self, "Created", "Vehicle added.")
                self._refresh()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
