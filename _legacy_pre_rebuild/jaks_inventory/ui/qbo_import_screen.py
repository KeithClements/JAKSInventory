"""QBO Import Wizard — import customers, vendors, products, invoices from QBO CSV exports."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFileDialog,
    QMessageBox,
    QGroupBox,
    QTextEdit,
    QProgressBar,
)

from db import (
    import_customers_from_csv,
    import_vendors_from_csv,
    import_products_from_csv,
    import_invoices_from_csv,
    import_invoice_lines_from_csv,
    reset_all_data,
)


def _read_csv(path: str) -> list[dict]:
    """Read a CSV file and return list of dicts."""
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")  # handles BOM from Excel
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


class _ImportTab(QWidget):
    """Single import tab for one entity type (customers, vendors, etc.)."""

    def __init__(self, entity_name: str, import_fn, expected_cols: list[str],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entity = entity_name
        self._import_fn = import_fn
        self._expected = expected_cols
        self._rows: list[dict] = []
        self._file_path = ""
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        # Instructions
        info = QGroupBox(f"Import {self._entity}")
        info_layout = QVBoxLayout()

        steps = QLabel(
            f"1. In QBO, export the {self._entity} list as CSV\n"
            f"2. Click 'Load CSV' below to select the file\n"
            f"3. Preview the data, then click 'Import'\n\n"
            f"Expected columns: {', '.join(self._expected)}"
        )
        steps.setWordWrap(True)
        steps.setStyleSheet("color: #ccc; padding: 5px;")
        info_layout.addWidget(steps)
        info.setLayout(info_layout)
        layout.addWidget(info)

        # File selection row
        file_row = QHBoxLayout()
        self._file_label = QLabel("No file selected")
        self._file_label.setStyleSheet("color: gray;")
        load_btn = QPushButton("Load CSV")
        load_btn.setMaximumWidth(120)
        load_btn.clicked.connect(self._load_file)
        file_row.addWidget(self._file_label, 1)
        file_row.addWidget(load_btn)
        layout.addLayout(file_row)

        # Preview table
        self._preview = QTableWidget()
        self._preview.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._preview.setAlternatingRowColors(True)
        self._preview.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._preview, 1)

        # Status + Import button
        bottom = QHBoxLayout()
        self._status = QLabel("")
        self._import_btn = QPushButton(f"Import {self._entity}")
        self._import_btn.setEnabled(False)
        self._import_btn.setMaximumWidth(200)
        self._import_btn.clicked.connect(self._run_import)
        self._import_btn.setStyleSheet(
            "QPushButton { background-color: #6B8E23; color: white; padding: 8px 16px; }"
            "QPushButton:disabled { background-color: #555; }"
        )
        bottom.addWidget(self._status, 1)
        bottom.addWidget(self._import_btn)
        layout.addLayout(bottom)

        self.setLayout(layout)

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, f"Select {self._entity} CSV",
            "", "CSV Files (*.csv);;All Files (*.*)"
        )
        if not path:
            return
        try:
            self._rows = _read_csv(path)
            self._file_path = path
            self._file_label.setText(f"{Path(path).name}  ({len(self._rows)} rows)")
            self._file_label.setStyleSheet("color: #6B8E23;")
            self._populate_preview()
            self._import_btn.setEnabled(len(self._rows) > 0)
            self._status.setText(f"{len(self._rows)} rows loaded")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to read file:\n{e}")

    def _populate_preview(self) -> None:
        if not self._rows:
            return
        cols = list(self._rows[0].keys())
        show = self._rows[:50]  # preview first 50 rows

        self._preview.setColumnCount(len(cols))
        self._preview.setRowCount(len(show))
        self._preview.setHorizontalHeaderLabels(cols)

        for r, row in enumerate(show):
            for c, col in enumerate(cols):
                val = str(row.get(col, ""))
                self._preview.setItem(r, c, QTableWidgetItem(val))

        self._preview.resizeColumnsToContents()

    def _run_import(self) -> None:
        if not self._rows:
            return
        reply = QMessageBox.question(
            self, "Confirm Import",
            f"Import {len(self._rows)} {self._entity.lower()} records?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            result = self._import_fn(self._rows)
            parts = []
            if result.get("imported"):
                parts.append(f"{result['imported']} imported")
            if result.get("updated"):
                parts.append(f"{result['updated']} updated")
            if result.get("skipped"):
                parts.append(f"{result['skipped']} skipped")
            if result.get("errors"):
                parts.append(f"{len(result['errors'])} errors")

            msg = ", ".join(parts) if parts else "No changes"
            self._status.setText(msg)
            self._status.setStyleSheet(
                "color: #6B8E23;" if not result.get("errors")
                else "color: orange;"
            )

            detail = msg
            if result.get("errors"):
                detail += "\n\nErrors:\n" + "\n".join(result["errors"][:20])

            QMessageBox.information(self, "Import Complete", detail)
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Import failed:\n{e}")


class QBOImportScreen(QWidget):
    """QBO Import Wizard — tabbed interface for importing QBO CSV exports."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        # Header
        header = QLabel("QBO Import Wizard")
        header.setStyleSheet("font-size: 16pt; font-weight: bold; padding: 5px;")
        layout.addWidget(header)

        desc = QLabel(
            "Import data from QuickBooks Online CSV exports. "
            "Import in order: Customers/Vendors first, then Products, then Invoices."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #aaa; padding: 0 5px 10px 5px;")
        layout.addWidget(desc)

        # Reset section
        reset_box = QGroupBox("Reset Database")
        reset_layout = QHBoxLayout()
        reset_info = QLabel(
            "Clear ALL data (products, customers, invoices, etc.) before importing. "
            "Settings and QBO credentials are preserved."
        )
        reset_info.setWordWrap(True)
        reset_info.setStyleSheet("color: #ccc;")
        self._reset_btn = QPushButton("Reset All Data")
        self._reset_btn.setMaximumWidth(160)
        self._reset_btn.setStyleSheet(
            "QPushButton { background-color: #8B0000; color: white; "
            "padding: 8px 16px; font-weight: bold; }"
        )
        self._reset_btn.clicked.connect(self._reset_data)
        reset_layout.addWidget(reset_info, 1)
        reset_layout.addWidget(self._reset_btn)
        reset_box.setLayout(reset_layout)
        layout.addWidget(reset_box)

        # Import tabs
        tabs = QTabWidget()
        tabs.addTab(
            _ImportTab("Customers", import_customers_from_csv,
                       ["Customer", "Company", "Email", "Phone",
                        "Street", "City", "State", "ZIP", "Terms"]),
            "1. Customers"
        )
        tabs.addTab(
            _ImportTab("Vendors", import_vendors_from_csv,
                       ["Vendor", "Contact", "Email", "Phone",
                        "Address", "Terms"]),
            "2. Vendors"
        )
        tabs.addTab(
            _ImportTab("Products", import_products_from_csv,
                       ["Product/Service", "SKU", "Type", "Description",
                        "Price", "Cost", "Qty On Hand"]),
            "3. Products"
        )
        tabs.addTab(
            _ImportTab("Invoices", import_invoices_from_csv,
                       ["Invoice No", "Customer", "Date", "Due Date",
                        "Status", "Total", "Balance"]),
            "4. Invoices"
        )
        tabs.addTab(
            _ImportTab("Invoice Lines", import_invoice_lines_from_csv,
                       ["Num", "Product/Service", "Qty", "Rate", "Amount"]),
            "5. Invoice Lines"
        )
        layout.addWidget(tabs, 1)

        self.setLayout(layout)

    def _reset_data(self) -> None:
        reply = QMessageBox.warning(
            self, "Confirm Reset",
            "This will DELETE ALL DATA from the database:\n\n"
            "- All products, customers, vendors\n"
            "- All invoices, purchase orders, sales orders\n"
            "- All serials, cores, transfers, routes\n\n"
            "Settings and QBO credentials will be preserved.\n\n"
            "Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Second confirmation
        reply2 = QMessageBox.critical(
            self, "FINAL WARNING",
            "This cannot be undone!\n\nType 'YES' mentally and click Yes to proceed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply2 != QMessageBox.StandardButton.Yes:
            return

        try:
            results = reset_all_data()
            total = sum(v for v in results.values() if v > 0)
            QMessageBox.information(
                self, "Reset Complete",
                f"Database reset complete.\n{total} total records deleted."
            )
        except Exception as e:
            QMessageBox.critical(self, "Reset Error", f"Reset failed:\n{e}")
