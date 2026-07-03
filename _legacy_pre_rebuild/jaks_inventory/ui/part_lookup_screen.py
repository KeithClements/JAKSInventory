"""Part Interchange Lookup Screen - Cross-reference part numbers."""
from __future__ import annotations

import csv
import io

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import (
    search_interchanges,
    add_interchange,
    get_interchanges,
    delete_interchange,
    bulk_import_interchanges,
    get_product_by_sku,
    add_supersession,
    get_supersession_chain,
    get_supersessions_for_sku,
    delete_supersession,
    get_last_scrape_run,
)

from .product_detail_dialog import ProductDetailDialog
from .scraper_review_dialog import ScraperReviewDialog
from ..scraper import ScraperWorker


class _AddInterchangeDialog(QDialog):
    """Dialog to add an interchange to a product."""

    def __init__(self, sku: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Part Interchange")
        self.setMinimumWidth(400)

        form = QFormLayout()
        self.sku_edit = QLineEdit(sku)
        form.addRow("Product SKU:", self.sku_edit)

        self.number_edit = QLineEdit()
        self.number_edit.setPlaceholderText("e.g. 3406751, RE524382")
        form.addRow("Interchange #:", self.number_edit)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["OEM", "Aftermarket", "Cross-Reference", "Superseded", "NLA"])
        form.addRow("Type:", self.type_combo)

        self.mfg_edit = QLineEdit()
        self.mfg_edit.setPlaceholderText("e.g. Cummins, Fleetguard")
        form.addRow("Manufacturer:", self.mfg_edit)

        self.notes_edit = QLineEdit()
        form.addRow("Notes:", self.notes_edit)

        btns = QHBoxLayout()
        ok_btn = QPushButton("Add")
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


class PartLookupScreen(QWidget):
    """Part interchange lookup and management screen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._results: list[dict] = []

        # Search bar
        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Enter any part number (OEM, aftermarket, SKU)...")
        self._search_input.returnPressed.connect(self._search)
        self._search_btn = QPushButton("🔍 Search")
        self._search_btn.clicked.connect(self._search)
        self._add_btn = QPushButton("➕ Add Interchange")
        self._add_btn.clicked.connect(self._add_interchange)
        self._import_btn = QPushButton("📥 Import CSV")
        self._import_btn.clicked.connect(self._import_csv)
        self._scrape_btn = QPushButton("🕷️ Scrape")
        self._scrape_btn.setToolTip("Scrape DPD + ATL for cross-references & pricing")
        self._scrape_btn.clicked.connect(self._run_scrape)
        self._review_btn = QPushButton("📋 Review")
        self._review_btn.setToolTip("Review pending scraped data")
        self._review_btn.clicked.connect(self._open_review)
        search_row.addWidget(self._search_input, 1)
        search_row.addWidget(self._search_btn)
        search_row.addWidget(self._add_btn)
        search_row.addWidget(self._import_btn)
        search_row.addWidget(self._scrape_btn)
        search_row.addWidget(self._review_btn)

        # Scraper progress
        self._scrape_progress = QProgressBar()
        self._scrape_progress.setMaximumHeight(18)
        self._scrape_progress.hide()
        self._scrape_status = QLabel("")
        self._scrape_status.setStyleSheet("font-size: 10pt; color: #666; padding: 2px;")
        self._scrape_status.hide()
        self._scraper_worker: ScraperWorker | None = None

        self._result_label = QLabel("")
        self._result_label.setStyleSheet("font-size: 11pt; padding: 4px;")

        # Results table
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels([
            "SKU", "Product Title", "Interchange #", "Type",
            "Manufacturer", "On Hand", "Price", "Cost"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.cellDoubleClicked.connect(self._on_double_click)

        # Bottom: product interchanges viewer
        bot_row = QHBoxLayout()
        self._sku_for_list = QLineEdit()
        self._sku_for_list.setPlaceholderText("SKU to view interchanges...")
        self._sku_for_list.returnPressed.connect(self._view_interchanges)
        self._view_btn = QPushButton("View Interchanges")
        self._view_btn.clicked.connect(self._view_interchanges)
        bot_row.addWidget(self._sku_for_list, 1)
        bot_row.addWidget(self._view_btn)

        self._ic_table = QTableWidget(0, 5)
        self._ic_table.setHorizontalHeaderLabels([
            "ID", "Interchange #", "Type", "Manufacturer", "Notes"
        ])
        self._ic_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._ic_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._ic_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._ic_table.setMaximumHeight(180)

        self._delete_ic_btn = QPushButton("🗑 Delete Selected")
        self._delete_ic_btn.clicked.connect(self._delete_selected_ic)

        # Supersession chain section
        ss_header = QLabel("Supersession Chains")
        ss_header.setStyleSheet("font-size: 12pt; font-weight: bold; padding-top: 8px;")
        ss_row = QHBoxLayout()
        self._ss_sku_input = QLineEdit()
        self._ss_sku_input.setPlaceholderText("Enter SKU to trace supersession chain...")
        self._ss_sku_input.returnPressed.connect(self._trace_supersession)
        self._ss_trace_btn = QPushButton("🔗 Trace Chain")
        self._ss_trace_btn.clicked.connect(self._trace_supersession)
        self._ss_add_btn = QPushButton("➕ Add Supersession")
        self._ss_add_btn.clicked.connect(self._add_supersession)
        ss_row.addWidget(self._ss_sku_input, 1)
        ss_row.addWidget(self._ss_trace_btn)
        ss_row.addWidget(self._ss_add_btn)

        self._ss_label = QLabel("")
        self._ss_label.setStyleSheet("font-size: 11pt; padding: 4px;")
        self._ss_label.setWordWrap(True)

        self._ss_table = QTableWidget(0, 4)
        self._ss_table.setHorizontalHeaderLabels(["ID", "Old SKU", "New SKU", "Notes"])
        self._ss_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._ss_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._ss_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._ss_table.setMaximumHeight(140)

        self._ss_delete_btn = QPushButton("🗑 Delete Supersession")
        self._ss_delete_btn.clicked.connect(self._delete_supersession)

        layout = QVBoxLayout()
        layout.addLayout(search_row)
        layout.addWidget(self._scrape_progress)
        layout.addWidget(self._scrape_status)
        layout.addWidget(self._result_label)
        layout.addWidget(self._table, 1)
        layout.addLayout(bot_row)
        layout.addWidget(self._ic_table)
        layout.addWidget(self._delete_ic_btn)
        layout.addWidget(ss_header)
        layout.addLayout(ss_row)
        layout.addWidget(self._ss_label)
        layout.addWidget(self._ss_table)
        layout.addWidget(self._ss_delete_btn)
        self.setLayout(layout)

    def _search(self):
        query = self._search_input.text().strip()
        if not query:
            return
        try:
            self._results = search_interchanges(query)
            self._result_label.setText(f"Found {len(self._results)} result(s) for '{query}'")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))
            self._results = []
        self._populate_results()

    def _populate_results(self):
        self._table.setRowCount(len(self._results))
        for i, r in enumerate(self._results):
            def item(text):
                it = QTableWidgetItem(str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._table.setItem(i, 0, item(r.get("sku", "")))
            self._table.setItem(i, 1, item(r.get("title") or r.get("product_title", "")))
            self._table.setItem(i, 2, item(r.get("interchange_number", "")))
            self._table.setItem(i, 3, item(r.get("interchange_type", "")))
            self._table.setItem(i, 4, item(r.get("product_manufacturer") or r.get("manufacturer", "")))
            self._table.setItem(i, 5, item(r.get("qty_on_hand", 0)))
            price = float(r.get("selling_price") or 0)
            self._table.setItem(i, 6, item(f"${price:,.2f}"))
            cost = float(r.get("cost") or 0)
            self._table.setItem(i, 7, item(f"${cost:,.2f}"))
        self._table.resizeColumnsToContents()

    def _on_double_click(self, row: int, _col: int):
        if row < 0 or row >= len(self._results):
            return
        sku = str(self._results[row].get("sku", ""))
        if sku:
            dlg = ProductDetailDialog(sku, self)
            dlg.exec()

    def _add_interchange(self):
        # Pre-fill SKU if something selected
        sku = ""
        row = self._table.currentRow()
        if 0 <= row < len(self._results):
            sku = str(self._results[row].get("sku", ""))
        dlg = _AddInterchangeDialog(sku, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            product_sku = dlg.sku_edit.text().strip()
            number = dlg.number_edit.text().strip()
            if not product_sku or not number:
                QMessageBox.warning(self, "Missing", "SKU and Interchange # are required.")
                return
            try:
                product = get_product_by_sku(product_sku)
                if not product:
                    QMessageBox.warning(self, "Not Found", f"SKU '{product_sku}' not found.")
                    return
                add_interchange(
                    product_id=product["id"],
                    interchange_number=number,
                    interchange_type=dlg.type_combo.currentText(),
                    manufacturer=dlg.mfg_edit.text().strip() or None,
                    notes=dlg.notes_edit.text().strip() or None,
                )
                QMessageBox.information(self, "Added", f"Interchange '{number}' added to {product_sku}.")
                self._search()
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))

    def _view_interchanges(self):
        sku = self._sku_for_list.text().strip()
        if not sku:
            return
        try:
            product = get_product_by_sku(sku)
            if not product:
                QMessageBox.warning(self, "Not Found", f"SKU '{sku}' not found.")
                return
            ics = get_interchanges(product["id"])
            self._ic_table.setRowCount(len(ics))
            for i, ic in enumerate(ics):
                def item(text):
                    it = QTableWidgetItem(str(text))
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    return it
                self._ic_table.setItem(i, 0, item(ic.get("id", "")))
                self._ic_table.setItem(i, 1, item(ic.get("interchange_number", "")))
                self._ic_table.setItem(i, 2, item(ic.get("interchange_type", "")))
                self._ic_table.setItem(i, 3, item(ic.get("manufacturer", "")))
                self._ic_table.setItem(i, 4, item(ic.get("notes", "")))
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _delete_selected_ic(self):
        row = self._ic_table.currentRow()
        if row < 0:
            return
        ic_id_item = self._ic_table.item(row, 0)
        if not ic_id_item:
            return
        try:
            ic_id = int(ic_id_item.text())
        except ValueError:
            return
        reply = QMessageBox.question(self, "Delete", "Delete this interchange?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_interchange(ic_id)
            self._view_interchanges()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _import_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Interchanges CSV", "", "CSV Files (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            if not rows:
                QMessageBox.warning(self, "Empty", "No data found in CSV.")
                return
            result = bulk_import_interchanges(rows)
            QMessageBox.information(
                self, "Import Complete",
                f"Imported: {result['imported']}\n"
                f"Skipped (duplicates): {result['skipped']}\n"
                f"Errors: {len(result['errors'])}"
            )
            if result["errors"]:
                QMessageBox.warning(
                    self, "Import Errors",
                    "\n".join(result["errors"][:20])
                )
        except Exception as e:
            QMessageBox.critical(self, "Import Error", str(e))

    # ── Supersession methods ─────────────────────────────────
    def _trace_supersession(self):
        sku = self._ss_sku_input.text().strip()
        if not sku:
            return
        try:
            chain = get_supersession_chain(sku)
            if not chain:
                self._ss_label.setText(f"No supersession chain found for '{sku}'.")
                self._ss_table.setRowCount(0)
                return
            arrow = " → ".join(chain)
            self._ss_label.setText(f"Chain: {arrow}  (latest: {chain[-1]})")
            records = get_supersessions_for_sku(sku)
            self._ss_table.setRowCount(len(records))
            for i, r in enumerate(records):
                for j, key in enumerate(["id", "old_sku", "new_sku", "notes"]):
                    it = QTableWidgetItem(str(r.get(key, "")))
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self._ss_table.setItem(i, j, it)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _add_supersession(self):
        from PySide6.QtWidgets import QInputDialog
        old_sku, ok1 = QInputDialog.getText(self, "Supersession", "Old (superseded) SKU:")
        if not ok1 or not old_sku.strip():
            return
        new_sku, ok2 = QInputDialog.getText(self, "Supersession", "New (replacement) SKU:")
        if not ok2 or not new_sku.strip():
            return
        notes, _ = QInputDialog.getText(self, "Supersession", "Notes (optional):")
        try:
            add_supersession(old_sku.strip(), new_sku.strip(), notes=notes.strip() or None)
            QMessageBox.information(self, "Added", f"{old_sku.strip()} → {new_sku.strip()}")
            self._ss_sku_input.setText(old_sku.strip())
            self._trace_supersession()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _delete_supersession(self):
        row = self._ss_table.currentRow()
        if row < 0:
            return
        id_item = self._ss_table.item(row, 0)
        if not id_item:
            return
        try:
            ss_id = int(id_item.text())
        except ValueError:
            return
        reply = QMessageBox.question(self, "Delete", "Delete this supersession record?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_supersession(ss_id)
            self._trace_supersession()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # ── Scraper methods ──────────────────────────────────────
    def _run_scrape(self):
        """Launch background scraper worker for the current search query."""
        search_value = self._search_input.text().strip()
        if not search_value:
            QMessageBox.warning(self, "No Part Number", "Enter a part number to scrape.")
            return
        if self._scraper_worker is not None:
            QMessageBox.information(self, "Busy", "A scrape is already running.")
            return

        # Resolve product_id if possible
        product = get_product_by_sku(search_value)
        product_id = product["id"] if product else None

        self._scrape_btn.setEnabled(False)
        self._scrape_progress.setValue(0)
        self._scrape_progress.setMaximum(0)  # indeterminate
        self._scrape_progress.show()
        self._scrape_status.setText(
            f"Source: DPD+ATL+FP+HHP  |  Searching using: {search_value}"
        )
        self._scrape_status.show()

        self._scraper_worker = ScraperWorker(
            "xref", search_value, product_id=product_id,
        )
        self._scraper_worker.progress.connect(self._on_scrape_progress)
        self._scraper_worker.finished.connect(self._on_scrape_done)
        self._scraper_worker.error.connect(self._on_scrape_error)
        self._scraper_worker.start()

    def _on_scrape_progress(self, current: int, total: int, message: str):
        if total > 0:
            self._scrape_progress.setMaximum(total)
            self._scrape_progress.setValue(current)
        self._scrape_status.setText(message)

    def _on_scrape_done(self, result: dict):
        self._scraper_worker = None
        self._scrape_btn.setEnabled(True)
        self._scrape_progress.hide()

        xref = result.get("xref", result)
        found = int(xref.get("found", 0))
        errors = xref.get("errors", [])
        search_value = self._search_input.text().strip()

        if found > 0:
            msg = f"✔ Cross-refs found: {found} item(s) for {search_value}"
        else:
            msg = f"✖ No cross-ref results for: {search_value}"
        if errors:
            msg += f"  ({len(errors)} source error(s))"
        self._scrape_status.setText(msg)

        # Now run pricing scrape
        if search_value:
            product = get_product_by_sku(search_value)
            product_id = product["id"] if product else None
            self._scrape_btn.setEnabled(False)
            self._scrape_progress.show()
            self._scrape_progress.setMaximum(0)
            self._scrape_status.setText(
                f"Source: DPD+ATL+FP+HHP  |  Searching pricing for: {search_value}"
            )
            self._scraper_worker = ScraperWorker(
                "pricing", search_value, product_id=product_id,
            )
            self._scraper_worker.progress.connect(self._on_scrape_progress)
            self._scraper_worker.finished.connect(self._on_pricing_done)
            self._scraper_worker.error.connect(self._on_scrape_error)
            self._scraper_worker.start()

    def _on_pricing_done(self, result: dict):
        self._scraper_worker = None
        self._scrape_btn.setEnabled(True)
        self._scrape_progress.hide()

        pricing = result.get("pricing", result)
        found = int(pricing.get("found", 0))
        errors = pricing.get("errors", [])
        search_value = self._search_input.text().strip()

        if found > 0:
            msg = f"✔ Scraping complete — {found} price(s) found for {search_value}"
        else:
            msg = f"✖ No pricing results for: {search_value}"
        if errors:
            msg += f"  ({len(errors)} source error(s))"
        self._scrape_status.setText(msg)

        # Auto-open review dialog so user can accept/reject results
        product_id = None
        if search_value:
            product = get_product_by_sku(search_value)
            if product:
                product_id = product["id"]
        dlg = ScraperReviewDialog(product_id=product_id, parent=self)
        dlg.exec()

    def _on_scrape_error(self, error_msg: str):
        self._scraper_worker = None
        self._scrape_btn.setEnabled(True)
        self._scrape_progress.hide()
        search_value = self._search_input.text().strip()
        self._scrape_status.setText(f"✖ Error scraping {search_value}: {error_msg}")
        QMessageBox.warning(self, "Scrape Error", error_msg)

    def _open_review(self):
        """Open the scraper review dialog."""
        sku = self._search_input.text().strip()
        product_id = None
        if sku:
            product = get_product_by_sku(sku)
            if product:
                product_id = product["id"]
        dlg = ScraperReviewDialog(product_id=product_id, parent=self)
        dlg.exec()
