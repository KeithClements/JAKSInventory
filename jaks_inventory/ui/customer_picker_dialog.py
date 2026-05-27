"""Customer Quick-Pick dialog (F3 hotkey).

A small searchable picker that opens with the search box focused so a
user can type a name/company/phone/account # and press Enter to pick a
customer. Designed to flow into the Customer Profile dialog or be reused
as a quick lookup for any customer-first workflow.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
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
)

from db import get_all_customers, search_customers


class CustomerPickerDialog(QDialog):
    """Quick search → picks a customer_id.

    Result handling:
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cid = dlg.selected_customer_id  # int or None
            new = dlg.create_new_requested  # True if Ctrl+N pressed
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find Customer (F3)")
        self.setMinimumSize(720, 480)
        self.resize(820, 540)

        self.selected_customer_id: int | None = None
        self.create_new_requested: bool = False
        self._rows: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        hint = QLabel(
            "Type to search by name, company, phone, account #, or email. "
            "Enter picks · Esc cancels · Ctrl+N for new customer."
        )
        hint.setStyleSheet("color: #555;")
        root.addWidget(hint)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search customers…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        self._search.returnPressed.connect(self._accept_selected)
        root.addWidget(self._search)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            "Name", "Company", "Phone", "Account #", "Email",
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        header = self._table.horizontalHeader()
        for i in range(5):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(0, 180)
        self._table.setColumnWidth(1, 200)
        self._table.setColumnWidth(2, 120)
        self._table.setColumnWidth(3, 100)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.doubleClicked.connect(self._accept_selected)
        root.addWidget(self._table, 1)

        # Buttons
        btn_row = QHBoxLayout()
        new_btn = QPushButton("➕ New Customer (Ctrl+N)")
        new_btn.clicked.connect(self._on_new_customer)
        btn_row.addWidget(new_btn)
        btn_row.addStretch()
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Open Profile")
        bb.accepted.connect(self._accept_selected)
        bb.rejected.connect(self.reject)
        btn_row.addWidget(bb)
        root.addLayout(btn_row)

        # Ctrl+N shortcut for new customer
        QShortcut(
            QKeySequence("Ctrl+N"), self, activated=self._on_new_customer
        )

        # Initial population
        self._populate(get_all_customers(active_only=True))
        # Focus search box on open
        QTimer.singleShot(0, self._search.setFocus)

    # ──────────────────────────────────────────────────────────────────
    def _populate(self, rows: list[dict]) -> None:
        self._rows = list(rows or [])
        self._table.setRowCount(len(self._rows))
        for i, c in enumerate(self._rows):
            self._table.setItem(
                i, 0, QTableWidgetItem(str(c.get("name") or ""))
            )
            self._table.setItem(
                i, 1, QTableWidgetItem(str(c.get("company") or ""))
            )
            self._table.setItem(
                i, 2, QTableWidgetItem(str(c.get("phone") or ""))
            )
            self._table.setItem(
                i, 3, QTableWidgetItem(str(c.get("account_number") or ""))
            )
            self._table.setItem(
                i, 4, QTableWidgetItem(str(c.get("email") or ""))
            )
        if self._rows:
            self._table.selectRow(0)

    def _on_search_changed(self, text: str) -> None:
        text = (text or "").strip()
        try:
            if text:
                rows = search_customers(text)
            else:
                rows = get_all_customers(active_only=True)
        except Exception:
            rows = []
        self._populate(rows)

    def _accept_selected(self) -> None:
        # If focus is in search and only one match, take it.
        row = self._table.currentRow()
        if row < 0 and self._rows:
            row = 0
        if 0 <= row < len(self._rows):
            cid = self._rows[row].get("id")
            if cid:
                self.selected_customer_id = int(cid)
                self.accept()
                return
        # Nothing selected / no rows — do nothing (let user keep typing).

    def _on_new_customer(self) -> None:
        self.create_new_requested = True
        self.selected_customer_id = None
        self.accept()

    def keyPressEvent(self, ev) -> None:  # noqa: N802 (Qt naming)
        # Down/Up while focus is in the search box should move the row.
        if self._search.hasFocus() and ev.key() in (
            Qt.Key.Key_Down, Qt.Key.Key_Up,
        ):
            row = self._table.currentRow()
            if ev.key() == Qt.Key.Key_Down:
                row = min(self._table.rowCount() - 1, max(0, row) + 1)
            else:
                row = max(0, row - 1)
            if 0 <= row < self._table.rowCount():
                self._table.selectRow(row)
            ev.accept()
            return
        super().keyPressEvent(ev)
