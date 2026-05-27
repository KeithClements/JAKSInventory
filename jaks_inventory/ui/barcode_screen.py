"""Barcode Screen - Label generation and barcode scanning."""
from __future__ import annotations

import io
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap, QFont, QKeyEvent, QPainter
from PySide6.QtPrintSupport import QPrinter, QPrintDialog, QPrintPreviewDialog
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
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
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from db import browse_products, get_product_by_sku

# Try to import barcode libraries (optional)
try:
    import barcode
    from barcode.writer import ImageWriter
    BARCODE_AVAILABLE = True
except ImportError:
    BARCODE_AVAILABLE = False


class BarcodeScreen(QWidget):
    """Barcode label generation and scanning."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Tab widget
        self._tabs = QTabWidget()
        
        self._label_tab = LabelGeneratorTab()
        self._scanner_tab = BarcodeScannerTab()
        
        self._tabs.addTab(self._label_tab, "🏷️ Label Generator")
        self._tabs.addTab(self._scanner_tab, "📷 Barcode Scanner")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)
        self.setLayout(layout)


class LabelGeneratorTab(QWidget):
    """Generate barcode labels for products."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Search/select products
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search SKU or title...")
        self._search_input.returnPressed.connect(self._search)

        self._search_btn = QPushButton("🔍 Search")
        self._search_btn.clicked.connect(self._search)

        # Product list
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            "SKU", "Title", "Price", "Qty", "Select"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        # Label options
        options_box = QGroupBox("Label Options")
        options_layout = QHBoxLayout()

        self._label_type = QComboBox()
        self._label_type.addItems([
            "SKU + Price (Standard)",
            "SKU Only",
            "SKU + Title",
            "Full (SKU + Title + Price)",
        ])

        self._copies_spin = QSpinBox()
        self._copies_spin.setRange(1, 100)
        self._copies_spin.setValue(1)
        self._copies_spin.setPrefix("Copies: ")

        self._barcode_type = QComboBox()
        self._barcode_type.addItems(["Code128", "Code39", "EAN13", "UPC-A"])

        options_layout.addWidget(QLabel("Label Type:"))
        options_layout.addWidget(self._label_type)
        options_layout.addWidget(self._copies_spin)
        options_layout.addWidget(QLabel("Barcode:"))
        options_layout.addWidget(self._barcode_type)
        options_layout.addStretch()
        options_box.setLayout(options_layout)

        # Preview area
        preview_box = QGroupBox("Preview")
        preview_layout = QVBoxLayout()
        self._preview_label = QLabel()
        self._preview_label.setMinimumHeight(150)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setStyleSheet("background-color: white; border: 1px solid #ccc;")
        preview_layout.addWidget(self._preview_label)
        preview_box.setLayout(preview_layout)

        # Buttons
        self._preview_btn = QPushButton("👁️ Preview")
        self._preview_btn.clicked.connect(self._preview_label_fn)

        self._print_btn = QPushButton("🖨️ Print Labels")
        self._print_btn.clicked.connect(self._print_labels)

        self._export_btn = QPushButton("📥 Export as PNG")
        self._export_btn.clicked.connect(self._export_labels)

        if not BARCODE_AVAILABLE:
            self._preview_btn.setEnabled(False)
            self._print_btn.setEnabled(False)
            self._export_btn.setEnabled(False)
            self._preview_label.setText(
                "⚠️ Barcode library not installed.\n\n"
                "Run: pip install python-barcode pillow"
            )

        # Layout
        search_row = QHBoxLayout()
        search_row.addWidget(self._search_input, 1)
        search_row.addWidget(self._search_btn)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._preview_btn)
        btn_row.addWidget(self._print_btn)
        btn_row.addWidget(self._export_btn)
        btn_row.addStretch()

        layout = QVBoxLayout()
        layout.addLayout(search_row)
        layout.addWidget(self._table, 1)
        layout.addWidget(options_box)
        layout.addWidget(preview_box)
        layout.addLayout(btn_row)
        self.setLayout(layout)

        self._data: list[dict] = []

    def _search(self) -> None:
        """Search for products."""
        query = self._search_input.text().strip()
        try:
            self._data = browse_products(search=query, limit=100)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Search failed: {e}")
            return

        self._table.setRowCount(len(self._data))
        for row_idx, product in enumerate(self._data):
            sku = product.get("sku", "")
            title = product.get("title", "")
            price = Decimal(str(product.get("selling_price", 0) or 0))
            qty = product.get("qty_on_hand", 0) or 0

            items = [
                QTableWidgetItem(sku),
                QTableWidgetItem(title[:50] + "..." if len(title) > 50 else title),
                QTableWidgetItem(f"${price:.2f}"),
                QTableWidgetItem(str(qty)),
                QTableWidgetItem(""),
            ]

            for col_idx, item in enumerate(items):
                item.setData(Qt.ItemDataRole.UserRole, product)
                self._table.setItem(row_idx, col_idx, item)

    def _get_selected_products(self) -> list[dict]:
        """Get list of selected products."""
        selected_rows = set()
        for item in self._table.selectedItems():
            selected_rows.add(item.row())

        products = []
        for row in selected_rows:
            item = self._table.item(row, 0)
            if item:
                product = item.data(Qt.ItemDataRole.UserRole)
                if product:
                    products.append(product)
        return products

    def _generate_barcode(self, sku: str) -> QPixmap | None:
        """Generate barcode image for a SKU."""
        if not BARCODE_AVAILABLE:
            return None

        try:
            barcode_type = self._barcode_type.currentText().lower().replace("-", "")
            if barcode_type == "upca":
                barcode_type = "upca"
            elif barcode_type == "ean13":
                barcode_type = "ean13"
            else:
                barcode_type = "code128"

            # Generate barcode
            code = barcode.get(barcode_type, sku, writer=ImageWriter())
            
            # Write to bytes buffer
            buffer = io.BytesIO()
            code.write(buffer)
            buffer.seek(0)
            
            # Convert to QPixmap
            image = QImage()
            image.loadFromData(buffer.read())
            return QPixmap.fromImage(image)
        except Exception as e:
            log.warning("Barcode generation failed for %s: %s", sku, e)
            return None

    def _preview_label_fn(self) -> None:
        """Preview the first selected label."""
        products = self._get_selected_products()
        if not products:
            QMessageBox.information(self, "No Selection", "Select a product first.")
            return

        product = products[0]
        sku = product.get("sku", "")
        title = product.get("title", "")
        price = Decimal(str(product.get("selling_price", 0) or 0))

        pixmap = self._generate_barcode(sku)
        if pixmap:
            # Scale to fit
            scaled = pixmap.scaled(300, 120, Qt.AspectRatioMode.KeepAspectRatio)
            self._preview_label.setPixmap(scaled)
            self._preview_label.setText("")
        else:
            label_type = self._label_type.currentText()
            if "Full" in label_type:
                text = f"{sku}\n{title[:30]}\n${price:.2f}"
            elif "Title" in label_type:
                text = f"{sku}\n{title[:30]}"
            elif "Only" in label_type:
                text = sku
            else:
                text = f"{sku}\n${price:.2f}"
            self._preview_label.setText(text)

    def _print_labels(self) -> None:
        """Print labels for selected products via system print dialog."""
        products = self._get_selected_products()
        if not products:
            QMessageBox.information(self, "No Selection", "Select products first.")
            return
        if not BARCODE_AVAILABLE:
            QMessageBox.warning(
                self, "Barcode Library Missing",
                "Install barcode libs first:\n  pip install python-barcode pillow",
            )
            return

        copies = self._copies_spin.value()

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageOrientation(printer.pageLayout().orientation())
        dlg = QPrintDialog(printer, self)
        dlg.setWindowTitle("Print Barcode Labels")
        if dlg.exec() != QPrintDialog.DialogCode.Accepted:
            return

        try:
            self._render_labels_to_printer(printer, products, copies)
            QMessageBox.information(
                self, "Print Sent",
                f"Sent {len(products) * copies} label(s) to printer.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Print Error", f"Failed to print:\n{e}")

    def _render_labels_to_printer(self, printer: QPrinter,
                                  products: list[dict], copies: int) -> None:
        """Lay out labels in a grid and paint them onto the printer device.

        Default grid: 3 columns × 10 rows (Avery 5160-style 1"×2-5/8").
        Each cell shows: SKU | barcode image | title (truncated) | price.
        """
        painter = QPainter(printer)
        try:
            page = printer.pageRect(QPrinter.Unit.DevicePixel)
            page_w, page_h = int(page.width()), int(page.height())

            cols, rows = 3, 10
            margin = int(min(page_w, page_h) * 0.04)
            cell_w = (page_w - margin * 2) // cols
            cell_h = (page_h - margin * 2) // rows
            label_type = self._label_type.currentText()

            idx_in_page = 0
            first_label = True
            for product in products:
                pixmap = self._generate_barcode(product.get("sku", ""))
                for _ in range(copies):
                    if idx_in_page >= cols * rows:
                        printer.newPage()
                        idx_in_page = 0
                    if not first_label and idx_in_page == 0:
                        pass  # already on a new page
                    first_label = False

                    row = idx_in_page // cols
                    col = idx_in_page % cols
                    x = margin + col * cell_w
                    y = margin + row * cell_h
                    self._paint_one_label(
                        painter, x, y, cell_w, cell_h, product, pixmap, label_type
                    )
                    idx_in_page += 1
        finally:
            painter.end()

    def _paint_one_label(self, painter: QPainter, x: int, y: int,
                         w: int, h: int, product: dict,
                         barcode_pixmap: QPixmap | None,
                         label_type: str) -> None:
        """Paint a single label cell."""
        sku = product.get("sku", "")
        title = product.get("title", "") or ""
        price = Decimal(str(product.get("selling_price", 0) or 0))

        pad = max(4, int(min(w, h) * 0.05))
        text_x = x + pad
        text_y = y + pad

        # SKU header
        font = QFont()
        font.setBold(True)
        font.setPointSize(max(8, int(h * 0.10)))
        painter.setFont(font)
        painter.drawText(text_x, text_y + int(h * 0.10), sku)

        # Barcode (centered, ~50% of cell height)
        if barcode_pixmap and not barcode_pixmap.isNull():
            bc_h = int(h * 0.45)
            bc_w = w - pad * 2
            scaled = barcode_pixmap.scaled(
                bc_w, bc_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            bc_x = x + (w - scaled.width()) // 2
            bc_y = y + int(h * 0.18)
            painter.drawPixmap(bc_x, bc_y, scaled)

        # Title (if requested) and price (if requested)
        font.setBold(False)
        font.setPointSize(max(7, int(h * 0.08)))
        painter.setFont(font)
        text_y = y + int(h * 0.72)

        if "Full" in label_type or "Title" in label_type:
            t = title if len(title) <= 32 else title[:29] + "..."
            painter.drawText(text_x, text_y, t)
            text_y += int(h * 0.13)

        if "Full" in label_type or "Price" in label_type:
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(text_x, text_y, f"${price:.2f}")

    def _export_labels(self) -> None:
        """Export labels as PNG files."""
        products = self._get_selected_products()
        if not products:
            QMessageBox.information(self, "No Selection", "Select products first.")
            return

        folder = QFileDialog.getExistingDirectory(self, "Select Export Folder")
        if not folder:
            return

        exported = 0
        for product in products:
            sku = product.get("sku", "")
            pixmap = self._generate_barcode(sku)
            if pixmap:
                path = Path(folder) / f"{sku}_barcode.png"
                pixmap.save(str(path), "PNG")
                exported += 1

        QMessageBox.information(
            self, "Export Complete",
            f"Exported {exported} barcode images to:\n{folder}"
        )


class BarcodeScannerTab(QWidget):
    """Barcode scanner input handler."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Scanner input (captures USB scanner input)
        self._scan_input = QLineEdit()
        self._scan_input.setPlaceholderText("Scan barcode or type SKU...")
        self._scan_input.setFont(QFont("Consolas", 16))
        self._scan_input.returnPressed.connect(self._on_scan)

        self._scan_btn = QPushButton("🔍 Lookup")
        self._scan_btn.clicked.connect(self._on_scan)

        # Auto-focus timer (re-focus input after lookup)
        self._focus_timer = QTimer()
        self._focus_timer.setInterval(100)
        self._focus_timer.timeout.connect(self._focus_input)

        # Product result area
        self._result_box = QGroupBox("Product Info")
        result_layout = QVBoxLayout()
        
        self._result_sku = QLabel("SKU: —")
        self._result_sku.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        
        self._result_title = QLabel("Title: —")
        self._result_title.setWordWrap(True)
        
        self._result_price = QLabel("Price: —")
        self._result_price.setFont(QFont("Arial", 12))
        
        self._result_qty = QLabel("Qty On Hand: —")
        self._result_qty.setFont(QFont("Arial", 12))
        
        self._result_location = QLabel("Location: —")
        
        result_layout.addWidget(self._result_sku)
        result_layout.addWidget(self._result_title)
        result_layout.addWidget(self._result_price)
        result_layout.addWidget(self._result_qty)
        result_layout.addWidget(self._result_location)
        result_layout.addStretch()
        self._result_box.setLayout(result_layout)

        # Quick actions
        actions_box = QGroupBox("Quick Actions")
        actions_layout = QHBoxLayout()
        
        self._receive_btn = QPushButton("+1 Receive")
        self._receive_btn.clicked.connect(self._quick_receive)
        self._receive_btn.setEnabled(False)
        
        self._sell_btn = QPushButton("-1 Sell")
        self._sell_btn.clicked.connect(self._quick_sell)
        self._sell_btn.setEnabled(False)
        
        self._details_btn = QPushButton("📋 Full Details")
        self._details_btn.clicked.connect(self._show_details)
        self._details_btn.setEnabled(False)
        
        actions_layout.addWidget(self._receive_btn)
        actions_layout.addWidget(self._sell_btn)
        actions_layout.addWidget(self._details_btn)
        actions_layout.addStretch()
        actions_box.setLayout(actions_layout)

        # Scan history
        self._history = QTextEdit()
        self._history.setReadOnly(True)
        self._history.setMaximumHeight(150)
        self._history.setPlaceholderText("Scan history will appear here...")

        # Layout
        scan_row = QHBoxLayout()
        scan_row.addWidget(self._scan_input, 1)
        scan_row.addWidget(self._scan_btn)

        layout = QVBoxLayout()
        layout.addLayout(scan_row)
        layout.addWidget(self._result_box, 1)
        layout.addWidget(actions_box)
        layout.addWidget(QLabel("Scan History:"))
        layout.addWidget(self._history)
        self.setLayout(layout)

        self._current_product: dict | None = None

        # Start focus timer
        self._focus_timer.start()

    def _focus_input(self) -> None:
        """Keep focus on scan input for continuous scanning."""
        if self.isVisible() and not self._scan_input.hasFocus():
            self._scan_input.setFocus()

    def _on_scan(self) -> None:
        """Handle barcode scan / SKU lookup."""
        sku = self._scan_input.text().strip()
        if not sku:
            return

        self._scan_input.clear()

        try:
            product = get_product_by_sku(sku)
        except Exception as e:
            self._add_history(f"❌ Error: {e}")
            return

        if not product:
            self._result_sku.setText(f"SKU: {sku}")
            self._result_title.setText("Title: NOT FOUND")
            self._result_price.setText("Price: —")
            self._result_qty.setText("Qty: —")
            self._result_location.setText("Location: —")
            self._current_product = None
            self._receive_btn.setEnabled(False)
            self._sell_btn.setEnabled(False)
            self._details_btn.setEnabled(False)
            self._add_history(f"❌ {sku} - Not found")
            return

        self._current_product = product
        
        title = product.get("title", "—")
        price = Decimal(str(product.get("selling_price", 0) or 0))
        qty = int(product.get("qty_on_hand", 0) or 0)
        reserved = int(product.get("qty_reserved", 0) or 0)
        available = max(0, qty - reserved)
        location = product.get("bin_location", "") or "—"

        self._result_sku.setText(f"SKU: {sku}")
        self._result_title.setText(f"Title: {title}")
        self._result_price.setText(f"Price: ${price:.2f}")
        # "Available" is the salable number; show on-hand/reserved alongside
        # so the warehouse user can reconcile a physical count.
        self._result_qty.setText(
            f"Available: {available}  (On Hand: {qty}, Reserved: {reserved})"
        )
        self._result_location.setText(f"Location: {location}")

        self._receive_btn.setEnabled(True)
        self._sell_btn.setEnabled(True)
        self._details_btn.setEnabled(True)

        self._add_history(f"✅ {sku} - {title[:30]} (Avail: {available})")

    def _add_history(self, text: str) -> None:
        """Add entry to scan history."""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._history.append(f"[{timestamp}] {text}")

    def _quick_receive(self) -> None:
        """Quick receive +1 quantity."""
        if not self._current_product:
            return
        
        from db import adjust_product_quantity
        sku = self._current_product.get("sku", "")
        product_id = self._current_product.get("id")
        
        try:
            adjust_product_quantity(
                product_id=product_id,
                delta=1,
                transaction_type="receive",
                reference=f"Barcode scan",
                notes="Quick receive from scanner"
            )
            self._add_history(f"📥 {sku} +1 received")
            # Refresh display
            self._scan_input.setText(sku)
            self._on_scan()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to receive: {e}")

    def _quick_sell(self) -> None:
        """Quick sell -1 quantity."""
        if not self._current_product:
            return
        
        from db import adjust_product_quantity
        sku = self._current_product.get("sku", "")
        product_id = self._current_product.get("id")
        
        try:
            adjust_product_quantity(
                product_id=product_id,
                delta=-1,
                transaction_type="sale",
                reference=f"Barcode scan",
                notes="Quick sale from scanner"
            )
            self._add_history(f"📤 {sku} -1 sold")
            # Refresh display
            self._scan_input.setText(sku)
            self._on_scan()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to sell: {e}")

    def _show_details(self) -> None:
        """Open full product detail dialog."""
        if not self._current_product:
            return
        
        # Import here to avoid circular import
        from .product_detail_dialog import ProductDetailDialog
        dialog = ProductDetailDialog(self._current_product, parent=self)
        dialog.exec()
