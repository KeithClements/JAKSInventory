"""ESN Engine Lookup Screen - Look up engine serial numbers and find compatible parts."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import (
    get_esn_cache,
    save_esn_cache,
    get_engine_compatibility,
    add_engine_compatibility,
    get_product_by_sku,
)


class ESNLookupScreen(QWidget):
    """ESN engine lookup screen with compatible parts display."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # --- Search section ---
        search_frame = QFrame()
        search_frame.setFrameShape(QFrame.Shape.StyledPanel)
        search_lay = QHBoxLayout()

        self._oem_combo = QComboBox()
        self._oem_combo.addItems(["Cummins", "Detroit Diesel", "Caterpillar",
                                   "PACCAR", "International/Navistar", "Volvo"])
        self._esn_input = QLineEdit()
        self._esn_input.setPlaceholderText("Enter Engine Serial Number...")
        self._esn_input.returnPressed.connect(self._lookup)
        self._lookup_btn = QPushButton("🔍 Look Up ESN")
        self._lookup_btn.clicked.connect(self._lookup)

        search_lay.addWidget(QLabel("OEM:"))
        search_lay.addWidget(self._oem_combo)
        search_lay.addWidget(self._esn_input, 1)
        search_lay.addWidget(self._lookup_btn)
        search_frame.setLayout(search_lay)

        # --- Engine info display ---
        info_frame = QFrame()
        info_frame.setFrameShape(QFrame.Shape.StyledPanel)
        info_grid = QGridLayout()

        labels = ["ESN:", "OEM:", "Engine Model:", "Horsepower:",
                  "Build Date:", "CPL:", "Application:", "Emission Level:"]
        self._info_labels = {}
        for i, lbl in enumerate(labels):
            row_n = i // 4
            col_n = (i % 4) * 2
            info_grid.addWidget(QLabel(lbl), row_n, col_n)
            val = QLabel("—")
            val.setStyleSheet("font-weight: bold;")
            self._info_labels[lbl.rstrip(":")] = val
            info_grid.addWidget(val, row_n, col_n + 1)

        info_frame.setLayout(info_grid)

        # --- Manual cache entry ---
        manual_frame = QFrame()
        manual_frame.setFrameShape(QFrame.Shape.StyledPanel)
        manual_lay = QFormLayout()

        self._man_engine = QLineEdit()
        self._man_engine.setPlaceholderText("e.g. ISX15, DD15, C15")
        manual_lay.addRow("Engine Model:", self._man_engine)

        self._man_hp = QLineEdit()
        self._man_hp.setPlaceholderText("e.g. 450, 525")
        manual_lay.addRow("Horsepower:", self._man_hp)

        self._man_cpl = QLineEdit()
        manual_lay.addRow("CPL:", self._man_cpl)

        self._man_build = QLineEdit()
        self._man_build.setPlaceholderText("MM/YYYY")
        manual_lay.addRow("Build Date:", self._man_build)

        self._man_app = QLineEdit()
        self._man_app.setPlaceholderText("e.g. On-Highway, Generator")
        manual_lay.addRow("Application:", self._man_app)

        self._man_emission = QLineEdit()
        self._man_emission.setPlaceholderText("e.g. EPA10, EPA13, EPA17")
        manual_lay.addRow("Emission:", self._man_emission)

        self._save_cache_btn = QPushButton("💾 Save to Cache")
        self._save_cache_btn.clicked.connect(self._save_manual)
        manual_lay.addRow("", self._save_cache_btn)
        manual_frame.setLayout(manual_lay)

        # --- Compatible parts table ---
        parts_label = QLabel("Compatible Parts")
        parts_label.setStyleSheet("font-size: 12pt; font-weight: bold; padding: 4px;")

        parts_bar = QHBoxLayout()
        self._add_compat_sku = QLineEdit()
        self._add_compat_sku.setPlaceholderText("Product SKU...")
        self._add_compat_cat = QComboBox()
        self._add_compat_cat.addItems([
            "Filters", "Injectors", "Turbo", "Gaskets", "Sensors",
            "Bearings", "Pistons", "Liners", "Water Pump", "Oil Pump",
            "Head", "Block", "Crankshaft", "Camshaft", "Other"
        ])
        self._add_compat_btn = QPushButton("➕ Add Part")
        self._add_compat_btn.clicked.connect(self._add_compatible_part)
        parts_bar.addWidget(QLabel("SKU:"))
        parts_bar.addWidget(self._add_compat_sku)
        parts_bar.addWidget(QLabel("Category:"))
        parts_bar.addWidget(self._add_compat_cat)
        parts_bar.addWidget(self._add_compat_btn)

        self._parts_table = QTableWidget(0, 6)
        self._parts_table.setHorizontalHeaderLabels([
            "SKU", "Title", "Category", "On Hand", "Price", "OEM Part #"
        ])
        self._parts_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._parts_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._parts_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        # Layout
        left = QVBoxLayout()
        left.addWidget(search_frame)
        left.addWidget(info_frame)
        left.addWidget(QLabel("Manual Entry / Update Cache:"))
        left.addWidget(manual_frame)
        left.addStretch()

        right = QVBoxLayout()
        right.addWidget(parts_label)
        right.addLayout(parts_bar)
        right.addWidget(self._parts_table, 1)

        main_h = QHBoxLayout()
        main_h.addLayout(left, 1)
        main_h.addLayout(right, 2)

        layout = QVBoxLayout()
        layout.addLayout(main_h, 1)
        self.setLayout(layout)

        self._current_engine_model = None

    def _lookup(self):
        esn = self._esn_input.text().strip()
        if not esn:
            return
        oem = self._oem_combo.currentText()

        # Check cache first
        cached = None
        try:
            cached = get_esn_cache(esn)
        except Exception:
            pass

        if cached:
            self._display_info(cached)
            engine_model = cached.get("engine_model") or ""
            if engine_model:
                self._current_engine_model = engine_model
                self._load_compatible_parts(engine_model)
        else:
            # No cache — show empty and let user fill manually
            self._clear_info()
            self._info_labels["ESN"].setText(esn)
            self._info_labels["OEM"].setText(oem)
            self._man_engine.clear()
            QMessageBox.information(
                self, "Not Cached",
                f"ESN '{esn}' not found in local cache.\n\n"
                "Fill in the engine details manually and click 'Save to Cache'.\n\n"
                "Future: API integration with OEM databases for auto-lookup."
            )

    def _display_info(self, data: dict):
        field_map = {
            "ESN": "esn", "OEM": "oem", "Engine Model": "engine_model",
            "Horsepower": "horsepower", "Build Date": "build_date",
            "CPL": "cpl", "Application": "application",
            "Emission Level": "emission_level"
        }
        for display_name, key in field_map.items():
            val = data.get(key) or "—"
            self._info_labels[display_name].setText(str(val))
        # Fill manual fields too
        self._man_engine.setText(str(data.get("engine_model") or ""))
        self._man_hp.setText(str(data.get("horsepower") or ""))
        self._man_cpl.setText(str(data.get("cpl") or ""))
        self._man_build.setText(str(data.get("build_date") or ""))
        self._man_app.setText(str(data.get("application") or ""))
        self._man_emission.setText(str(data.get("emission_level") or ""))

    def _clear_info(self):
        for lbl in self._info_labels.values():
            lbl.setText("—")

    def _save_manual(self):
        esn = self._esn_input.text().strip()
        if not esn:
            QMessageBox.warning(self, "Missing", "Enter an ESN first.")
            return
        oem = self._oem_combo.currentText()
        engine_model = self._man_engine.text().strip()
        try:
            save_esn_cache(
                esn=esn, oem=oem,
                engine_model=engine_model or None,
                horsepower=self._man_hp.text().strip() or None,
                build_date=self._man_build.text().strip() or None,
                cpl=self._man_cpl.text().strip() or None,
                application=self._man_app.text().strip() or None,
                emission_level=self._man_emission.text().strip() or None,
            )
            QMessageBox.information(self, "Saved", f"ESN '{esn}' cached.")
            if engine_model:
                self._current_engine_model = engine_model
                self._load_compatible_parts(engine_model)
            self._lookup()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _load_compatible_parts(self, engine_model: str):
        try:
            parts = get_engine_compatibility(engine_model)
        except Exception:
            parts = []
        self._parts_table.setRowCount(len(parts))
        for i, p in enumerate(parts):
            def item(text):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._parts_table.setItem(i, 0, item(p.get("sku") or "—"))
            self._parts_table.setItem(i, 1, item(p.get("title") or "—"))
            self._parts_table.setItem(i, 2, item(p.get("category", "")))
            self._parts_table.setItem(i, 3, item(p.get("qty_on_hand", 0)))
            price = float(p.get("selling_price") or 0)
            self._parts_table.setItem(i, 4, item(f"${price:,.2f}"))
            self._parts_table.setItem(i, 5, item(p.get("oem_part_number") or "—"))
        self._parts_table.resizeColumnsToContents()

    def _add_compatible_part(self):
        if not self._current_engine_model:
            QMessageBox.warning(self, "No Engine", "Look up an ESN first.")
            return
        sku = self._add_compat_sku.text().strip()
        if not sku:
            return
        product = None
        try:
            product = get_product_by_sku(sku)
        except Exception:
            pass
        if not product:
            QMessageBox.warning(self, "Not Found", f"SKU '{sku}' not found.")
            return
        try:
            add_engine_compatibility(
                engine_model=self._current_engine_model,
                category=self._add_compat_cat.currentText(),
                product_id=product["id"],
            )
            self._add_compat_sku.clear()
            self._load_compatible_parts(self._current_engine_model)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
