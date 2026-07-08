"""Modal product picker dialog used by invoice / SO / quote / PO dialogs.

Pops up a search box and a results table, returns the selected product dict
(or None on cancel) via :py:meth:`ProductPickerDialog.pick`.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import browse_products


class ProductPickerDialog(QDialog):
    """Modal dialog: search products and pick one.

    Returns the selected product dict via :py:meth:`selected_product` after
    ``exec()`` — or use the convenience class method :py:meth:`pick`.
    """

    def __init__(self, parent: Optional[QWidget] = None, initial_search: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Find Product")
        self.resize(900, 520)
        self._selected: Optional[dict] = None
        self._build_ui()
        if initial_search:
            self._search_edit.setText(initial_search)
            self._do_search()
        else:
            # Show the first page of products on open
            self._do_search()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(
            "🔍  Part #, OEM, SKU, description, engine, truck…"
        )
        self._search_edit.returnPressed.connect(self._do_search)
        top.addWidget(self._search_edit, 1)

        search_btn = QPushButton("Search")
        search_btn.setDefault(True)
        search_btn.clicked.connect(self._do_search)
        top.addWidget(search_btn)
        root.addLayout(top)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "SKU", "Title", "OEM Part #", "Available", "Price", "Manufacturer"
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hdr.setStretchLastSection(False)
        self._table.setColumnWidth(0, 120)
        self._table.setColumnWidth(1, 320)
        self._table.setColumnWidth(2, 130)
        self._table.setColumnWidth(3, 70)
        self._table.setColumnWidth(4, 90)
        self._table.setColumnWidth(5, 130)
        self._table.doubleClicked.connect(self._accept_selection)
        root.addWidget(self._table, 1)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #6B7280; font-size: 11px;")
        root.addWidget(self._status_label)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Select")
        bb.accepted.connect(self._accept_selection)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    # ------------------------------------------------------------------
    def _do_search(self) -> None:
        query = self._search_edit.text().strip()
        try:
            results = browse_products(search=query or None, limit=200)
        except Exception as exc:
            self._status_label.setText(f"Search failed: {exc}")
            results = []
        self._populate(results)

    def _populate(self, products: list[dict]) -> None:
        self._table.setRowCount(0)
        self._results = products
        self._table.setRowCount(len(products))
        for row, p in enumerate(products):
            def cell(text):
                it = QTableWidgetItem("" if text is None else str(text))
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                return it
            self._table.setItem(row, 0, cell(p.get("sku")))
            self._table.setItem(row, 1, cell(p.get("title") or p.get("brief_description")))
            self._table.setItem(row, 2, cell(p.get("oem_part_number") or ""))
            is_inventoried = str(p.get("product_type") or "inventoried") == "inventoried"
            on_hand_raw = p.get("qty_on_hand")
            reserved_raw = p.get("qty_reserved") or 0
            try:
                _on_hand = int(on_hand_raw) if on_hand_raw is not None else 0
                _reserved = int(reserved_raw)
            except (TypeError, ValueError):
                _on_hand, _reserved = 0, 0
            _available = max(0, _on_hand - _reserved)
            qty_display = ("" if on_hand_raw is None else _available) if is_inventoried else "—"
            qty_item = cell(qty_display)
            if is_inventoried:
                qty_item.setToolTip(
                    f"On Hand: {_on_hand}\nReserved: {_reserved}\nAvailable: {_available}"
                )
                if _available <= 0 and _on_hand > 0:
                    # Fully reserved — amber row hint via foreground only.
                    from PySide6.QtGui import QBrush, QColor
                    qty_item.setForeground(QBrush(QColor("#b45309")))
            self._table.setItem(row, 3, qty_item)
            price = p.get("selling_price") or 0
            try:
                self._table.setItem(row, 4, cell(f"${float(price):,.2f}"))
            except (TypeError, ValueError):
                self._table.setItem(row, 4, cell(price))
            self._table.setItem(row, 5, cell(p.get("manufacturer") or ""))
        self._status_label.setText(f"{len(products)} result(s)")
        if products:
            self._table.selectRow(0)

    def _accept_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if 0 <= idx < len(self._results):
            self._selected = self._results[idx]
            self.accept()

    # ------------------------------------------------------------------
    def selected_product(self) -> Optional[dict]:
        return self._selected

    @classmethod
    def pick(
        cls,
        parent: Optional[QWidget] = None,
        initial_search: str = "",
    ) -> Optional[dict]:
        """Open the picker modally and return the selected product or None."""
        dlg = cls(parent=parent, initial_search=initial_search)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.selected_product()
        return None
