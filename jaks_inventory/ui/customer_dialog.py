"""Customer add/edit dialog."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLineEdit,
    QTextEdit,
    QComboBox,
    QCheckBox,
    QPushButton,
    QMessageBox,
    QGroupBox,
    QScrollArea,
    QWidget,
    QLabel,
)

from db import get_customer


def _get_db_path() -> str:
    """Get the database path from JAKS_DB_PATH / DATABASE_URL or default."""
    explicit = os.environ.get("JAKS_DB_PATH")
    if explicit:
        return explicit
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("sqlite:///"):
        return db_url[10:]
    return str(Path(__file__).resolve().parent.parent.parent / "output" / "JAKs.db")


def _direct_create_customer(data: dict) -> int:
    """Create customer using direct sqlite3 connection to avoid cursor conflicts."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        # Auto-assign account number
        row = conn.execute(
            "SELECT MAX(CAST(account_number AS INTEGER)) FROM customers "
            "WHERE account_number IS NOT NULL AND account_number != ''"
        ).fetchone()
        current_max = row[0] if row and row[0] else 10000
        acct = str(int(current_max) + 1)

        cursor = conn.execute(
            """INSERT INTO customers (
                name, company, email, phone, address, city, state, zip,
                payment_terms, tax_exempt, tax_id, notes, account_number,
                ship_to_address, ship_to_city, ship_to_state, ship_to_zip,
                bill_to_address, bill_to_city, bill_to_state, bill_to_zip,
                is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (data["name"], data.get("company"), data.get("email"), data.get("phone"),
             data.get("address"), data.get("city"), data.get("state"), data.get("zip"),
             data.get("payment_terms", "Net 30"), data.get("tax_exempt", False),
             data.get("tax_id"), data.get("notes"),
             acct,
             data.get("ship_to_address"), data.get("ship_to_city"),
             data.get("ship_to_state"), data.get("ship_to_zip"),
             data.get("bill_to_address"), data.get("bill_to_city"),
             data.get("bill_to_state"), data.get("bill_to_zip"))
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def _direct_update_customer(customer_id: int, data: dict) -> None:
    """Update customer using direct sqlite3 connection to avoid cursor conflicts."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute(
            """UPDATE customers SET name=?, company=?, email=?, phone=?,
               address=?, city=?, state=?, zip=?,
               payment_terms=?, tax_exempt=?, tax_id=?, notes=?,
               ship_to_address=?, ship_to_city=?, ship_to_state=?, ship_to_zip=?,
               bill_to_address=?, bill_to_city=?, bill_to_state=?, bill_to_zip=?
               WHERE id=?""",
            (data["name"], data.get("company"), data.get("email"), data.get("phone"),
             data.get("address"), data.get("city"), data.get("state"), data.get("zip"),
             data.get("payment_terms", "Net 30"), data.get("tax_exempt", False),
             data.get("tax_id"), data.get("notes"),
             data.get("ship_to_address"), data.get("ship_to_city"),
             data.get("ship_to_state"), data.get("ship_to_zip"),
             data.get("bill_to_address"), data.get("bill_to_city"),
             data.get("bill_to_state"), data.get("bill_to_zip"),
             customer_id)
        )
        conn.commit()
    finally:
        conn.close()


class CustomerDialog(QDialog):
    """Dialog for adding or editing a customer."""

    def __init__(self, customer_id: int = None, parent=None):
        super().__init__(parent)
        self._customer_id = customer_id
        self._customer = None
        
        if customer_id:
            # Convert to dict immediately to release any DB cursor
            cust = get_customer(customer_id)
            self._customer = dict(cust) if cust else None
            self.setWindowTitle(f"Edit Customer: {self._customer.get('name', '') if self._customer else ''}")
        else:
            self.setWindowTitle("New Customer")
        
        self.setMinimumWidth(520)
        self.setMinimumHeight(500)
        self._build_ui()
        self._load_data()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()

        # Account number display (read-only, shown when editing)
        if self._customer and self._customer.get("account_number"):
            acct_lbl = QLabel(f"Account #: {self._customer['account_number']}")
            acct_lbl.setStyleSheet(
                "font-weight: bold; font-size: 12pt; color: #1565c0; "
                "padding: 4px 0; margin-bottom: 4px;"
            )
            layout.addWidget(acct_lbl)

        # Contact info group
        contact_group = QGroupBox("Contact Information")
        contact_layout = QFormLayout()
        
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Required")
        contact_layout.addRow("Name*:", self._name_edit)
        
        self._company_edit = QLineEdit()
        contact_layout.addRow("Company:", self._company_edit)
        
        self._email_edit = QLineEdit()
        self._email_edit.setPlaceholderText("email@example.com")
        contact_layout.addRow("Email:", self._email_edit)
        
        self._phone_edit = QLineEdit()
        self._phone_edit.setPlaceholderText("(555) 123-4567")
        contact_layout.addRow("Phone:", self._phone_edit)
        
        contact_group.setLayout(contact_layout)
        layout.addWidget(contact_group)

        # Primary Address group
        address_group = QGroupBox("Primary Address")
        address_layout = QFormLayout()
        
        self._address_edit = QLineEdit()
        address_layout.addRow("Address:", self._address_edit)
        
        city_row = QHBoxLayout()
        self._city_edit = QLineEdit()
        self._city_edit.setPlaceholderText("City")
        city_row.addWidget(self._city_edit, 2)
        
        self._state_edit = QLineEdit()
        self._state_edit.setPlaceholderText("ST")
        self._state_edit.setMaximumWidth(50)
        city_row.addWidget(self._state_edit)
        
        self._zip_edit = QLineEdit()
        self._zip_edit.setPlaceholderText("ZIP")
        self._zip_edit.setMaximumWidth(80)
        city_row.addWidget(self._zip_edit)
        
        address_layout.addRow("City/State/ZIP:", city_row)
        address_group.setLayout(address_layout)
        layout.addWidget(address_group)

        # Ship-To Address group
        ship_group = QGroupBox("Ship-To Address")
        ship_layout = QFormLayout()

        self._ship_address_edit = QLineEdit()
        ship_layout.addRow("Address:", self._ship_address_edit)

        ship_city_row = QHBoxLayout()
        self._ship_city_edit = QLineEdit()
        self._ship_city_edit.setPlaceholderText("City")
        ship_city_row.addWidget(self._ship_city_edit, 2)

        self._ship_state_edit = QLineEdit()
        self._ship_state_edit.setPlaceholderText("ST")
        self._ship_state_edit.setMaximumWidth(50)
        ship_city_row.addWidget(self._ship_state_edit)

        self._ship_zip_edit = QLineEdit()
        self._ship_zip_edit.setPlaceholderText("ZIP")
        self._ship_zip_edit.setMaximumWidth(80)
        ship_city_row.addWidget(self._ship_zip_edit)

        ship_layout.addRow("City/State/ZIP:", ship_city_row)

        copy_ship_btn = QPushButton("📋 Copy from Primary")
        copy_ship_btn.setToolTip("Copy primary address into ship-to")
        copy_ship_btn.clicked.connect(self._copy_primary_to_ship)
        ship_layout.addRow("", copy_ship_btn)

        ship_group.setLayout(ship_layout)
        layout.addWidget(ship_group)

        # Bill-To Address group
        bill_group = QGroupBox("Bill-To Address")
        bill_layout = QFormLayout()

        self._bill_address_edit = QLineEdit()
        bill_layout.addRow("Address:", self._bill_address_edit)

        bill_city_row = QHBoxLayout()
        self._bill_city_edit = QLineEdit()
        self._bill_city_edit.setPlaceholderText("City")
        bill_city_row.addWidget(self._bill_city_edit, 2)

        self._bill_state_edit = QLineEdit()
        self._bill_state_edit.setPlaceholderText("ST")
        self._bill_state_edit.setMaximumWidth(50)
        bill_city_row.addWidget(self._bill_state_edit)

        self._bill_zip_edit = QLineEdit()
        self._bill_zip_edit.setPlaceholderText("ZIP")
        self._bill_zip_edit.setMaximumWidth(80)
        bill_city_row.addWidget(self._bill_zip_edit)

        bill_layout.addRow("City/State/ZIP:", bill_city_row)

        copy_bill_btn = QPushButton("📋 Copy from Primary")
        copy_bill_btn.setToolTip("Copy primary address into bill-to")
        copy_bill_btn.clicked.connect(self._copy_primary_to_bill)
        bill_layout.addRow("", copy_bill_btn)

        bill_group.setLayout(bill_layout)
        layout.addWidget(bill_group)

        # Billing group
        billing_group = QGroupBox("Billing")
        billing_layout = QFormLayout()
        
        self._terms_combo = QComboBox()
        self._terms_combo.addItems([
            "Due on Receipt",
            "Net 15",
            "Net 30",
            "Net 45",
            "Net 60",
        ])
        self._terms_combo.setCurrentText("Net 30")
        billing_layout.addRow("Payment Terms:", self._terms_combo)
        
        self._tax_exempt_check = QCheckBox("Tax Exempt")
        billing_layout.addRow("", self._tax_exempt_check)

        self._tax_id_edit = QLineEdit()
        self._tax_id_edit.setPlaceholderText("EIN / Resale Cert # (optional)")
        billing_layout.addRow("Tax ID:", self._tax_id_edit)

        billing_group.setLayout(billing_layout)
        layout.addWidget(billing_group)

        # Notes
        notes_group = QGroupBox("Notes")
        notes_layout = QVBoxLayout()
        self._notes_edit = QTextEdit()
        self._notes_edit.setMaximumHeight(80)
        notes_layout.addWidget(self._notes_edit)
        notes_group.setLayout(notes_layout)
        layout.addWidget(notes_group)

        # Buttons
        btn_layout = QHBoxLayout()

        # "Pull from QBO" — only meaningful when editing an existing
        # customer that has a qbo_customer_id link. We always show the
        # button when editing (it'll surface a clear error if the link
        # is missing) and hide it on the New Customer path.
        if self._customer_id:
            self._qbo_pull_btn = QPushButton("⬇ Pull from QBO")
            self._qbo_pull_btn.setToolTip(
                "Refresh this customer's contact info, address, terms and "
                "balance from QuickBooks Online, and pull recent invoices."
            )
            self._qbo_pull_btn.clicked.connect(self._on_pull_from_qbo)
            btn_layout.addWidget(self._qbo_pull_btn)

        btn_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        save_btn = QPushButton("💾 Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(save_btn)
        
        layout.addLayout(btn_layout)

        # Wrap in scroll area
        scroll_content = QWidget()
        scroll_content.setLayout(layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(scroll_content)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.setLayout(outer)

    def _load_data(self) -> None:
        """Load existing customer data if editing."""
        if not self._customer:
            return
        
        self._name_edit.setText(self._customer.get("name", ""))
        self._company_edit.setText(self._customer.get("company", "") or "")
        self._email_edit.setText(self._customer.get("email", "") or "")
        self._phone_edit.setText(self._customer.get("phone", "") or "")
        self._address_edit.setText(self._customer.get("address", "") or "")
        self._city_edit.setText(self._customer.get("city", "") or "")
        self._state_edit.setText(self._customer.get("state", "") or "")
        self._zip_edit.setText(self._customer.get("zip", "") or "")

        # Ship-to
        self._ship_address_edit.setText(self._customer.get("ship_to_address", "") or "")
        self._ship_city_edit.setText(self._customer.get("ship_to_city", "") or "")
        self._ship_state_edit.setText(self._customer.get("ship_to_state", "") or "")
        self._ship_zip_edit.setText(self._customer.get("ship_to_zip", "") or "")

        # Bill-to
        self._bill_address_edit.setText(self._customer.get("bill_to_address", "") or "")
        self._bill_city_edit.setText(self._customer.get("bill_to_city", "") or "")
        self._bill_state_edit.setText(self._customer.get("bill_to_state", "") or "")
        self._bill_zip_edit.setText(self._customer.get("bill_to_zip", "") or "")
        
        terms = self._customer.get("payment_terms", "Net 30")
        idx = self._terms_combo.findText(terms)
        if idx >= 0:
            self._terms_combo.setCurrentIndex(idx)
        
        self._tax_exempt_check.setChecked(bool(self._customer.get("tax_exempt")))
        self._tax_id_edit.setText(self._customer.get("tax_id", "") or "")
        self._notes_edit.setPlainText(self._customer.get("notes", "") or "")

    # ------------------------------------------------------------------
    # QBO pull
    # ------------------------------------------------------------------
    def _on_pull_from_qbo(self) -> None:
        """Refresh this customer from QuickBooks Online.

        Pulls the customer record (contact info, address, terms, balance)
        and the most recent invoices, then re-loads the dialog so the
        user can save or review the merged result.
        """
        if not self._customer_id:
            return
        qbo_id = (self._customer or {}).get("qbo_customer_id")
        if not qbo_id:
            QMessageBox.warning(
                self, "Not Linked to QBO",
                "This customer is not linked to a QuickBooks Online record yet. "
                "Use 'Sync Customers \u2192QBO' in Settings first, or 'Import "
                "Customers \u2190QBO' to link by name.",
            )
            return

        self._qbo_pull_btn.setEnabled(False)
        self._qbo_pull_btn.setText("Pulling\u2026")
        try:
            from qbo.customers import fetch_qbo_customers, _apply_qbo_customer_to_local
            from qbo.invoices import import_qbo_invoices_to_db

            matches = [c for c in fetch_qbo_customers() if str(c.get("id")) == str(qbo_id)]
            if not matches:
                QMessageBox.warning(
                    self, "Not Found in QBO",
                    f"No QBO customer with id {qbo_id} was returned. The link "
                    "may be stale.",
                )
                return
            _apply_qbo_customer_to_local(self._customer_id, matches[0])

            inv_result = import_qbo_invoices_to_db(customer_qbo_id=str(qbo_id))

            # Reload customer + repopulate the form fields.
            from db.inventory import get_customer
            cust = get_customer(self._customer_id)
            self._customer = dict(cust) if cust else None
            self._load_data()

            QMessageBox.information(
                self, "Pulled from QBO",
                "Customer record refreshed from QuickBooks Online.\n\n"
                f"Invoices created: {inv_result.get('created', 0)}\n"
                f"Invoices updated: {inv_result.get('updated', 0)}\n"
                f"Errors:           {len(inv_result.get('errors') or [])}",
            )
        except Exception as exc:
            QMessageBox.critical(self, "QBO Pull Failed", str(exc))
        finally:
            self._qbo_pull_btn.setEnabled(True)
            self._qbo_pull_btn.setText("\u2b07 Pull from QBO")

    def _on_save(self) -> None:
        """Save the customer."""
        import time
        
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Validation Error", "Name is required.")
            self._name_edit.setFocus()
            return
        
        data = {
            "name": name,
            "company": self._company_edit.text().strip() or None,
            "email": self._email_edit.text().strip() or None,
            "phone": self._phone_edit.text().strip() or None,
            "address": self._address_edit.text().strip() or None,
            "city": self._city_edit.text().strip() or None,
            "state": self._state_edit.text().strip() or None,
            "zip": self._zip_edit.text().strip() or None,
            "payment_terms": self._terms_combo.currentText(),
            "tax_exempt": self._tax_exempt_check.isChecked(),
            "tax_id": self._tax_id_edit.text().strip() or None,
            "notes": self._notes_edit.toPlainText().strip() or None,
            "ship_to_address": self._ship_address_edit.text().strip() or None,
            "ship_to_city": self._ship_city_edit.text().strip() or None,
            "ship_to_state": self._ship_state_edit.text().strip() or None,
            "ship_to_zip": self._ship_zip_edit.text().strip() or None,
            "bill_to_address": self._bill_address_edit.text().strip() or None,
            "bill_to_city": self._bill_city_edit.text().strip() or None,
            "bill_to_state": self._bill_state_edit.text().strip() or None,
            "bill_to_zip": self._bill_zip_edit.text().strip() or None,
        }
        
        # Clear any existing customer reference
        self._customer = None
        
        try:
            # Use direct sqlite3 connection to bypass shared connection cursor issues
            if self._customer_id:
                _direct_update_customer(self._customer_id, data)
            else:
                self._customer_id = _direct_create_customer(data)
            
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save customer:\n{e}")

    def _copy_primary_to_ship(self) -> None:
        """Copy primary address fields into ship-to fields."""
        self._ship_address_edit.setText(self._address_edit.text())
        self._ship_city_edit.setText(self._city_edit.text())
        self._ship_state_edit.setText(self._state_edit.text())
        self._ship_zip_edit.setText(self._zip_edit.text())

    def _copy_primary_to_bill(self) -> None:
        """Copy primary address fields into bill-to fields."""
        self._bill_address_edit.setText(self._address_edit.text())
        self._bill_city_edit.setText(self._city_edit.text())
        self._bill_state_edit.setText(self._state_edit.text())
        self._bill_zip_edit.setText(self._zip_edit.text())

    def get_customer_id(self) -> int:
        """Return the customer ID (useful after creating new customer)."""
        return self._customer_id
