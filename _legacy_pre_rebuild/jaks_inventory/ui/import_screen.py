"""Bulk Product Import Screen - CSV/Excel import with column mapping and auto-SKU."""
from __future__ import annotations

import csv
import io
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import (
    bulk_import_products,
    get_import_history,
    list_sku_prefixes,
    create_sku_prefix,
    delete_sku_prefix,
    generate_sku,
    get_next_sku_preview,
    get_all_vendors,
)


# Standard column names we map to
PRODUCT_FIELDS = [
    "(skip)", "sku", "title", "manufacturer", "category",
    "cost", "selling_price", "qty_on_hand",
]


class ImportScreen(QWidget):
    """Bulk product import from CSV/Excel with auto-SKU generation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._raw_rows: list[dict] = []
        self._headers: list[str] = []
        self._column_map: dict[int, str] = {}  # col_index -> field name

        layout = QVBoxLayout()

        # ── Top: File picker ──
        file_row = QHBoxLayout()
        self._file_label = QLabel("No file loaded")
        self._file_label.setStyleSheet("font-size: 11pt;")
        browse_btn = QPushButton("📂 Browse CSV / Excel")
        browse_btn.clicked.connect(self._browse_file)
        file_row.addWidget(self._file_label, 1)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)

        # ── SKU Prefix Config ──
        prefix_group = QGroupBox("Auto-SKU Prefixes  (e.g. PAI → JAKS-PAI-000001)")
        pg_layout = QVBoxLayout()

        prefix_row = QHBoxLayout()
        self._prefix_input = QLineEdit()
        self._prefix_input.setPlaceholderText("Prefix (e.g. PAI)")
        self._prefix_input.setMaximumWidth(120)
        self._prefix_mfg = QLineEdit()
        self._prefix_mfg.setPlaceholderText("Manufacturer")
        self._prefix_mfg.setMaximumWidth(200)
        self._prefix_vendor = QComboBox()
        self._prefix_vendor.addItem("(no vendor)", 0)
        self._prefix_vendor.setMaximumWidth(180)
        add_prefix_btn = QPushButton("➕ Add Prefix")
        add_prefix_btn.clicked.connect(self._add_prefix)
        prefix_row.addWidget(QLabel("Prefix:"))
        prefix_row.addWidget(self._prefix_input)
        prefix_row.addWidget(QLabel("Mfg:"))
        prefix_row.addWidget(self._prefix_mfg)
        prefix_row.addWidget(QLabel("Vendor:"))
        prefix_row.addWidget(self._prefix_vendor)
        prefix_row.addWidget(add_prefix_btn)
        prefix_row.addStretch()
        pg_layout.addLayout(prefix_row)

        self._prefix_table = QTableWidget(0, 5)
        self._prefix_table.setHorizontalHeaderLabels(
            ["ID", "Prefix", "Next SKU Preview", "Manufacturer", "Vendor"])
        self._prefix_table.setMaximumHeight(120)
        self._prefix_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._prefix_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._prefix_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        del_prefix_btn = QPushButton("🗑 Delete Selected Prefix")
        del_prefix_btn.clicked.connect(self._delete_prefix)

        prefix_btns = QHBoxLayout()
        prefix_btns.addWidget(del_prefix_btn)
        prefix_btns.addStretch()
        pg_layout.addWidget(self._prefix_table)
        pg_layout.addLayout(prefix_btns)
        prefix_group.setLayout(pg_layout)
        layout.addWidget(prefix_group)

        # ── Import Options ──
        opts_row = QHBoxLayout()
        self._auto_sku_combo = QComboBox()
        self._auto_sku_combo.addItem("(no auto-SKU)", "")
        self._auto_sku_combo.setMinimumWidth(160)
        self._update_existing_cb = QCheckBox("Update existing SKUs")
        self._has_header_cb = QCheckBox("First row is header")
        self._has_header_cb.setChecked(True)
        opts_row.addWidget(QLabel("Auto-SKU Prefix for rows without SKU:"))
        opts_row.addWidget(self._auto_sku_combo)
        opts_row.addWidget(self._update_existing_cb)
        opts_row.addWidget(self._has_header_cb)
        opts_row.addStretch()
        layout.addLayout(opts_row)

        # ── Column Mapping ──
        self._mapping_row = QHBoxLayout()
        self._mapping_combos: list[QComboBox] = []
        self._mapping_container = QWidget()
        self._mapping_container.setLayout(self._mapping_row)
        layout.addWidget(QLabel("Column Mapping (assign each file column to a product field):"))
        layout.addWidget(self._mapping_container)

        # ── Preview Table ──
        self._preview_table = QTableWidget()
        self._preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._preview_table.setMaximumHeight(250)
        layout.addWidget(QLabel("Preview (first 20 rows):"))
        layout.addWidget(self._preview_table)

        # ── Import Button ──
        import_row = QHBoxLayout()
        self._import_btn = QPushButton("🚀 Import Products")
        self._import_btn.setStyleSheet("font-size: 13pt; padding: 8px 24px; background: #2e7d32; color: white;")
        self._import_btn.clicked.connect(self._run_import)
        self._import_btn.setEnabled(False)
        self._row_count_label = QLabel("")
        import_row.addWidget(self._import_btn)
        import_row.addWidget(self._row_count_label)
        import_row.addStretch()
        layout.addLayout(import_row)

        # ── Import History ──
        layout.addWidget(QLabel("Recent Imports:"))
        self._history_table = QTableWidget(0, 7)
        self._history_table.setHorizontalHeaderLabels(
            ["Date", "File", "Type", "Total", "Imported", "Updated", "Errors"])
        self._history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._history_table.setMaximumHeight(150)
        layout.addWidget(self._history_table)

        self.setLayout(layout)
        self._load_prefixes()
        self._load_history()

    # ── Prefix management ──────────────────────────────────────────

    def _load_prefixes(self):
        try:
            vendors = get_all_vendors()
        except Exception:
            vendors = []
        self._prefix_vendor.clear()
        self._prefix_vendor.addItem("(no vendor)", 0)
        for v in vendors:
            self._prefix_vendor.addItem(str(v.get("name", "")), v.get("id", 0))

        # Reload auto-SKU combo
        self._auto_sku_combo.clear()
        self._auto_sku_combo.addItem("(no auto-SKU)", "")

        try:
            prefixes = list_sku_prefixes()
        except Exception:
            prefixes = []
        self._prefix_table.setRowCount(len(prefixes))
        for i, p in enumerate(prefixes):
            prefix_str = str(p.get("prefix", ""))
            try:
                preview = get_next_sku_preview(prefix_str)
            except Exception:
                preview = f"JAKS-{prefix_str}-??????"
            for j, val in enumerate([
                p.get("id", ""), prefix_str, preview,
                p.get("manufacturer", ""), p.get("vendor_name", "")
            ]):
                it = QTableWidgetItem(str(val or ""))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._prefix_table.setItem(i, j, it)
            self._auto_sku_combo.addItem(f"JAKS-{prefix_str}-...", prefix_str)

    def _add_prefix(self):
        prefix = self._prefix_input.text().strip().upper()
        if not prefix:
            QMessageBox.warning(self, "Missing", "Enter a prefix (e.g. PAI).")
            return
        mfg = self._prefix_mfg.text().strip()
        vid = self._prefix_vendor.currentData()
        try:
            create_sku_prefix(prefix, manufacturer=mfg or None,
                              vendor_id=vid if vid else None)
            self._prefix_input.clear()
            self._prefix_mfg.clear()
            self._load_prefixes()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _delete_prefix(self):
        row = self._prefix_table.currentRow()
        if row < 0:
            return
        id_item = self._prefix_table.item(row, 0)
        if not id_item:
            return
        try:
            pid = int(id_item.text())
        except ValueError:
            return
        if QMessageBox.question(self, "Delete", "Delete this prefix?") != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_sku_prefix(pid)
            self._load_prefixes()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ── File loading ───────────────────────────────────────────────

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Import File", "",
            "Spreadsheets (*.csv *.xlsx *.xls *.tsv);;All Files (*)")
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in (".xlsx", ".xls"):
                self._load_excel(path)
            elif ext == ".tsv":
                self._load_csv(path, delimiter="\t")
            else:
                self._load_csv(path)
            self._file_label.setText(f"Loaded: {os.path.basename(path)}  ({len(self._raw_rows)} rows)")
            self._build_column_mapping()
            self._populate_preview()
            self._import_btn.setEnabled(len(self._raw_rows) > 0)
            self._row_count_label.setText(f"{len(self._raw_rows)} rows ready")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def _load_csv(self, path: str, delimiter: str = ","):
        with open(path, "r", encoding="utf-8-sig") as f:
            if self._has_header_cb.isChecked():
                reader = csv.DictReader(f, delimiter=delimiter)
                self._headers = reader.fieldnames or []
                self._raw_rows = list(reader)
            else:
                reader = csv.reader(f, delimiter=delimiter)
                all_rows = list(reader)
                if not all_rows:
                    self._raw_rows = []
                    self._headers = []
                    return
                ncols = max(len(r) for r in all_rows)
                self._headers = [f"Column {i+1}" for i in range(ncols)]
                self._raw_rows = []
                for r in all_rows:
                    row_dict = {}
                    for j, h in enumerate(self._headers):
                        row_dict[h] = r[j] if j < len(r) else ""
                    self._raw_rows.append(row_dict)

    def _load_excel(self, path: str):
        try:
            import openpyxl
        except ImportError:
            QMessageBox.warning(
                self, "Missing Package",
                "Install openpyxl to import Excel files:\n\npip install openpyxl\n\n"
                "For now, save as CSV and import that.")
            return
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        if self._has_header_cb.isChecked():
            header_row = next(rows_iter, None)
            if not header_row:
                self._raw_rows = []
                self._headers = []
                return
            self._headers = [str(h or f"Column {i+1}") for i, h in enumerate(header_row)]
        else:
            first = next(rows_iter, None)
            if not first:
                self._raw_rows = []
                self._headers = []
                return
            self._headers = [f"Column {i+1}" for i in range(len(first))]
            self._raw_rows = [{self._headers[j]: str(v or "") for j, v in enumerate(first)}]

        for row_vals in rows_iter:
            row_dict = {}
            for j, h in enumerate(self._headers):
                row_dict[h] = str(row_vals[j] if j < len(row_vals) and row_vals[j] is not None else "")
            self._raw_rows.append(row_dict)
        wb.close()

    # ── Column mapping ─────────────────────────────────────────────

    def _build_column_mapping(self):
        # Clear existing combos
        for combo in self._mapping_combos:
            combo.setParent(None)
            combo.deleteLater()
        self._mapping_combos.clear()
        # Remove all items from layout
        while self._mapping_row.count():
            item = self._mapping_row.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()

        for i, header in enumerate(self._headers):
            col_layout = QVBoxLayout()
            lbl = QLabel(header)
            lbl.setStyleSheet("font-size: 9pt; font-weight: bold;")
            combo = QComboBox()
            for field in PRODUCT_FIELDS:
                combo.addItem(field)
            # Auto-detect mapping
            h_lower = header.lower().strip()
            auto_map = {
                "sku": "sku", "part": "sku", "part number": "sku", "item": "sku",
                "title": "title", "name": "title", "description": "title", "product": "title",
                "manufacturer": "manufacturer", "brand": "manufacturer", "mfg": "manufacturer",
                "category": "category", "type": "category",
                "cost": "cost", "wholesale": "cost",
                "price": "selling_price", "selling_price": "selling_price", "sell": "selling_price", "retail": "selling_price",
                "qty": "qty_on_hand", "quantity": "qty_on_hand", "qty_on_hand": "qty_on_hand",
                "on hand": "qty_on_hand", "stock": "qty_on_hand",
            }
            mapped = auto_map.get(h_lower, "(skip)")
            idx = PRODUCT_FIELDS.index(mapped) if mapped in PRODUCT_FIELDS else 0
            combo.setCurrentIndex(idx)
            col_layout.addWidget(lbl)
            col_layout.addWidget(combo)
            self._mapping_row.addLayout(col_layout)
            self._mapping_combos.append(combo)

        self._mapping_row.addStretch()

    def _get_mapped_rows(self) -> list[dict]:
        """Convert raw rows using current column mapping."""
        mapped = []
        for raw in self._raw_rows:
            row: dict = {}
            for i, header in enumerate(self._headers):
                if i < len(self._mapping_combos):
                    field = self._mapping_combos[i].currentText()
                    if field != "(skip)":
                        row[field] = raw.get(header, "")
            mapped.append(row)
        return mapped

    # ── Preview ────────────────────────────────────────────────────

    def _populate_preview(self):
        preview = self._raw_rows[:20]
        self._preview_table.setColumnCount(len(self._headers))
        self._preview_table.setHorizontalHeaderLabels(self._headers)
        self._preview_table.setRowCount(len(preview))
        for i, row in enumerate(preview):
            for j, h in enumerate(self._headers):
                val = str(row.get(h, ""))
                it = QTableWidgetItem(val)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._preview_table.setItem(i, j, it)
        self._preview_table.resizeColumnsToContents()

    # ── Import execution ───────────────────────────────────────────

    def _run_import(self):
        if not self._raw_rows:
            return
        mapped = self._get_mapped_rows()
        auto_prefix = self._auto_sku_combo.currentData() or None
        update = self._update_existing_cb.isChecked()
        filename = self._file_label.text().replace("Loaded: ", "").split("  ")[0]

        reply = QMessageBox.question(
            self, "Confirm Import",
            f"Import {len(mapped)} rows?\n\n"
            f"Auto-SKU prefix: {auto_prefix or '(none)'}\n"
            f"Update existing: {'Yes' if update else 'No'}")
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            result = bulk_import_products(
                mapped,
                auto_sku_prefix=auto_prefix,
                update_existing=update,
                filename=filename,
            )
            msg = (
                f"Import Complete!\n\n"
                f"New products: {result['imported']}\n"
                f"Updated: {result['updated']}\n"
                f"Skipped: {result['skipped']}\n"
                f"Errors: {len(result['errors'])}")
            if result["errors"]:
                msg += "\n\nFirst errors:\n" + "\n".join(result["errors"][:10])
            QMessageBox.information(self, "Import Result", msg)
            self._load_history()
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))

    # ── History ────────────────────────────────────────────────────

    def _load_history(self):
        try:
            batches = get_import_history(20)
        except Exception:
            batches = []
        self._history_table.setRowCount(len(batches))
        for i, b in enumerate(batches):
            for j, key in enumerate(["created_at", "filename", "source_type",
                                     "total_rows", "imported", "updated", "errors"]):
                val = str(b.get(key, "") or "")
                it = QTableWidgetItem(val)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._history_table.setItem(i, j, it)
